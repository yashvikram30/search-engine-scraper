from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx

from ..models import (
    SearchQuery,
    SearchResponse,
    SearchResult,
    SearchStatus,
)
from ..net import PoolExhaustedError, ProxyPool, TtlCache, backoff
from .classify import classify_brave, explain_brave_response
from .parse import parse_brave_results

ENDPOINT = "https://search.brave.com/search"
MAX_ATTEMPTS = 3
TIMEOUT = 15.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

def build_brave_url(query: str, offset: int = 0) -> str:
    params: dict[str, str] = {"q": query}
    if offset > 0:
        params["offset"] = str(offset)
    return f"{ENDPOINT}?{urlencode(params)}"

class BraveEngine:
    name = "brave"

    def __init__(
        self,
        pool: ProxyPool,
        cache_ttl: float = 6 * 60 * 60,
        backoff_base: float = 1.0,
    ) -> None:
        self._pool = pool
        self._cache = TtlCache(cache_ttl)
        self._backoff_base = backoff_base

    async def search(self, q: SearchQuery) -> SearchResponse:
        total_pages = max(1, q.max_pages)
        query = q.query.strip()
        key = f"brave|{query}|{q.region}|{q.page}|{total_pages}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        started = time.monotonic()

        def finish(
            status: SearchStatus, results: list[SearchResult], attempts: int, pages_scraped: int
        ) -> SearchResponse:
            return SearchResponse(
                engine=self.name,
                query=q.query,
                region=q.region,
                page=q.page,
                status=status,
                results=results,
                attempts=attempts,
                latency_ms=int((time.monotonic() - started) * 1000),
                fetched_at=datetime.now(timezone.utc).isoformat(),
                pages_scraped=pages_scraped,
            )

        if not query:
            return finish("error", [], 0, 0)

        all_results: list[SearchResult] = []
        total_attempts = 0
        pages_done = 0
        last_status: SearchStatus = "error"

        # Brave pagination uses offset 0 for page 1, 1 for page 2, etc.
        # If user explicitly requested page > 1 with max_pages = 1, start at page - 1
        start_offset = (q.page - 1) if q.page > 1 and q.max_pages <= 1 else 0

        for page_idx in range(total_pages):
            current_offset = start_offset + page_idx
            page_attempts = 0
            page_status: SearchStatus = "error"
            page_results: list[SearchResult] = []

            while page_attempts < MAX_ATTEMPTS:
                page_attempts += 1
                total_attempts += 1

                try:
                    entry, wait = self._pool.acquire()
                except PoolExhaustedError:
                    print(f"[{self.name.upper()}] [ERROR] Page {page_idx + 1}/{total_pages}: PoolExhaustedError - all proxies are cooling down", flush=True)
                    page_status = "error"
                    break

                if wait > 0:
                    await asyncio.sleep(wait)

                url = build_brave_url(query, current_offset)
                print(
                    f"[{self.name.upper()}] Page {page_idx + 1}/{total_pages} (offset={current_offset}, "
                    f"attempt {page_attempts}/{MAX_ATTEMPTS}) [{entry.url}] -> GET {url}",
                    flush=True,
                )

                try:
                    res = await entry.client.get(url, headers=HEADERS, timeout=TIMEOUT)
                    html = res.text
                    page_results = parse_brave_results(html)
                    page_status, reason = explain_brave_response(res.status_code, html, len(page_results))

                    snippet = re.sub(r"\s+", " ", html[:160]).strip()
                    if page_status == "ok":
                        print(
                            f"[{self.name.upper()}] [SUCCESS] HTTP {res.status_code} ({len(html):,} bytes) "
                            f"-> Parsed {len(page_results)} results",
                            flush=True,
                        )
                    else:
                        print(
                            f"[{self.name.upper()}] [{page_status.upper()}] Could not scrape page: {reason} "
                            f"(Server responded with HTTP {res.status_code}, {len(html):,} bytes) | Body: \"{snippet}...\"",
                            flush=True,
                        )
                except httpx.HTTPError as ex:
                    page_status = "error"
                    print(
                        f"[{self.name.upper()}] [ERROR] Network/HTTP Exception on attempt {page_attempts}/{MAX_ATTEMPTS} "
                        f"via [{entry.url}]: {type(ex).__name__}: {ex}",
                        flush=True,
                    )

                self._pool.report(entry, page_status)

                if page_status in ("ok", "empty"):
                    break
                if page_attempts < MAX_ATTEMPTS:
                    delay = backoff(page_attempts, self._backoff_base)
                    print(
                        f"[{self.name.upper()}] Retrying page {page_idx + 1} in {delay:.1f}s "
                        f"(next attempt {page_attempts + 1}/{MAX_ATTEMPTS})...",
                        flush=True,
                    )
                    await asyncio.sleep(delay)

            last_status = page_status
            if page_status != "ok":
                break

            for item in page_results:
                all_results.append(
                    SearchResult(
                        position=len(all_results) + 1,
                        title=item.title,
                        url=item.url,
                        snippet=item.snippet,
                    )
                )

            pages_done += 1

        final_status: SearchStatus
        if pages_done > 0:
            final_status = "ok"
        elif last_status == "empty":
            final_status = "empty"
        else:
            final_status = last_status

        resp = finish(final_status, all_results, total_attempts, pages_done)
        print(
            f"[{self.name.upper()}] Completed search query='{q.query}': status='{final_status}' | "
            f"pages={pages_done}/{total_pages} | results={len(all_results)} | total_attempts={total_attempts} | "
            f"latency={resp.latency_ms}ms",
            flush=True,
        )

        if final_status in ("ok", "empty"):
            self._cache.set(key, resp)
        return resp
