from __future__ import annotations

import asyncio
import os
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
from .classify import classify, explain_ddg_response
from .parse import extract_next_page_payload, parse_results

ENDPOINT = "https://html.duckduckgo.com/html/"
MAX_ATTEMPTS = 3
MAX_QUERY_LENGTH = 499  # DDG rejects queries of 500 characters or more
TIMEOUT = 15.0

# Sec-Fetch headers: SearXNG notes DDG's bot detection looks at Sec-Fetch-Mode.
# httpx sends headers exactly as written. Whether DDG needs these on a plain
# GET is not verified, check on real traffic.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",  # br needs the brotli extra
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

# Operators such as site: tend to end in a CAPTCHA, so strip the prefixes
# (SearXNG does the same). The word after the prefix stays in the query.
OPERATOR_PREFIX = re.compile(r"^(site|intitle|inurl|filetype):", re.I)

def normalize_query(query: str) -> str:
    words = (OPERATOR_PREFIX.sub("", w) for w in query.split())
    return " ".join(w for w in words if w)

def build_url(query: str, region: str) -> str:
    return f"{ENDPOINT}?{urlencode({'q': query, 'b': '', 'kl': region})}"

class DuckDuckGoEngine:
    name = "duckduckgo"

    def __init__(
        self,
        pool: ProxyPool,
        cache_ttl: float = 6 * 60 * 60,
        backoff_base: float = 1.0,
        method: str | None = None,
    ) -> None:
        self._pool = pool
        self._cache = TtlCache(cache_ttl)
        self._backoff_base = backoff_base
        self._method = (method or os.environ.get("DDG_METHOD", "GET")).upper()

    async def search(self, q: SearchQuery) -> SearchResponse:
        # DDG requires the previous page's vqd token to paginate.
        # Direct jump to page > 1 without scraping page 1 first is refused.
        if q.page > 1 and q.max_pages <= 1:
            raise NotImplementedError(
                "Direct jump to page > 1 is not supported without the previous page's vqd token. "
                "Use max_pages to scrape sequentially."
            )

        total_pages = max(1, q.max_pages)
        query = normalize_query(q.query)
        key = f"{query}|{q.region}|{q.page}|{total_pages}"
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

        if not query or len(query) > MAX_QUERY_LENGTH:
            return finish("error", [], 0, 0)

        all_results: list[SearchResult] = []
        current_payload: dict[str, str] = {"q": query, "b": "", "kl": q.region}
        total_attempts = 0
        pages_done = 0
        last_status: SearchStatus = "error"

        for page_idx in range(1, total_pages + 1):
            page_attempts = 0
            page_status: SearchStatus = "error"
            page_results: list[SearchResult] = []
            next_payload: dict[str, str] | None = None

            while page_attempts < MAX_ATTEMPTS:
                page_attempts += 1
                total_attempts += 1

                try:
                    entry, wait = self._pool.acquire()  # a different proxy each attempt
                except PoolExhaustedError:
                    print(f"[{self.name.upper()}] [ERROR] Page {page_idx}/{total_pages}: PoolExhaustedError - all proxies are cooling down", flush=True)
                    page_status = "error"
                    break
                if wait > 0:
                    await asyncio.sleep(wait)

                req_method = "POST" if (self._method == "POST" or page_idx > 1) else "GET"
                target_url = ENDPOINT if req_method == "POST" else build_url(query, q.region)
                print(
                    f"[{self.name.upper()}] Page {page_idx}/{total_pages} (attempt {page_attempts}/{MAX_ATTEMPTS}) "
                    f"[{entry.url}] -> {req_method} {target_url}",
                    flush=True,
                )

                try:
                    if req_method == "POST":
                        res = await entry.client.post(
                            ENDPOINT, data=current_payload, headers=HEADERS, timeout=TIMEOUT
                        )
                    else:
                        res = await entry.client.get(target_url, headers=HEADERS, timeout=TIMEOUT)
                    html = res.text
                    page_results = parse_results(html)
                    page_status, reason = explain_ddg_response(res.status_code, html, len(page_results))

                    snippet = re.sub(r"\s+", " ", html[:160]).strip()
                    if page_status == "ok":
                        print(
                            f"[{self.name.upper()}] [SUCCESS] HTTP {res.status_code} ({len(html):,} bytes) "
                            f"-> Parsed {len(page_results)} results",
                            flush=True,
                        )
                        next_payload = extract_next_page_payload(html)
                    else:
                        print(
                            f"[{self.name.upper()}] [{page_status.upper()}] Could not scrape page: {reason} "
                            f"(Server responded with HTTP {res.status_code}, {len(html):,} bytes) | Body: \"{snippet}...\"",
                            flush=True,
                        )
                except httpx.HTTPError as ex:
                    page_status = "error"  # includes timeouts
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
                        f"[{self.name.upper()}] Retrying page {page_idx} in {delay:.1f}s "
                        f"(next attempt {page_attempts + 1}/{MAX_ATTEMPTS})...",
                        flush=True,
                    )
                    await asyncio.sleep(delay)

            last_status = page_status
            if page_status != "ok":
                break

            # Append results with contiguous positions
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

            if not next_payload or page_idx >= total_pages:
                break

            current_payload = next_payload

        final_status = "ok" if all_results else (last_status if pages_done == 0 else "ok")
        response = finish(final_status, all_results, total_attempts, pages_done)

        print(
            f"[{self.name.upper()}] Completed search query='{q.query}': status='{final_status}' | "
            f"pages={pages_done}/{total_pages} | results={len(all_results)} | total_attempts={total_attempts} | "
            f"latency={response.latency_ms}ms",
            flush=True,
        )

        # only cache successful answers
        if final_status in ("ok", "empty"):
            self._cache.set(key, response)
        return response
