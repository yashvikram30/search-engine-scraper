from __future__ import annotations

import asyncio
import hashlib
import random
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus, unquote, urlparse

from playwright.async_api import Playwright, async_playwright

from ..models import SearchQuery, SearchResponse, SearchResult, SearchStatus
from ..net import PoolExhaustedError, ProxyPool, TtlCache
from .classify import explain_google_response
from .parse import parse_google_results

ENDPOINT = "https://www.google.com/search"
GOOGLE_MAX_PAGES = 10  # adjust after testing
NAV_TIMEOUT_MS = 20000
PAGE_DELAY = (3.0, 7.0)  # seconds to wait between pages


def _region_parts(region: str) -> tuple[str, str]:
    """Map a DDG style region like 'us-en' or 'jp-jp' to Google's (gl, hl)."""
    gl, _, lang = region.partition("-")
    hl = {"jp": "ja"}.get(lang, lang) or "en"
    return (gl or "us"), hl


def _proxy_dict(url: str) -> dict[str, str]:
    p = urlparse(url)
    port = f":{p.port}" if p.port else ""
    d = {"server": f"{p.scheme or 'http'}://{p.hostname}{port}"}
    if p.username:
        d["username"] = unquote(p.username)
    if p.password:
        d["password"] = unquote(p.password)
    return d


class GooglePlaywrightEngine:
    """Google search through a real Chrome with a persistent profile.

    Google requires JavaScript to render results, so a plain HTTP client does
    not work. One search uses one browser session for all its pages (cookies
    persist), moves between pages by clicking Next, and stops on the first block.
    """

    name = "google"

    def __init__(
        self,
        pool: ProxyPool,
        headless: bool = False,
        cache_ttl: float = 6 * 60 * 60,
        profile_dir: str = "g_profile",
    ) -> None:
        self._pool = pool
        self._headless = headless
        self._profile_dir = profile_dir
        self._cache = TtlCache(cache_ttl)
        self._pw: Playwright | None = None
        self._lock = asyncio.Lock()  # one profile can only be open in one Chrome at a time

    async def search(self, q: SearchQuery) -> SearchResponse:
        query = q.query.strip()
        total_pages = min(GOOGLE_MAX_PAGES, max(1, q.max_pages))
        key = f"google|{query}|{q.region}|{q.page}|{total_pages}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        async with self._lock:
            cached = self._cache.get(key)  # another request may have filled it while we waited
            if cached is not None:
                return cached  # type: ignore[return-value]
            resp = await self._run(q, query, total_pages)

        if resp.status in ("ok", "empty"):
            self._cache.set(key, resp)
        return resp

    async def _run(self, q: SearchQuery, query: str, total_pages: int) -> SearchResponse:
        started = time.monotonic()

        def finish(
            status: SearchStatus, results: list[SearchResult], attempts: int, pages: int
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
                pages_scraped=pages,
            )

        if not query:
            return finish("error", [], 0, 0)

        gl, hl = _region_parts(q.region)
        profile = self._profile_dir
        launch_kwargs: dict[str, Any] = {
            "headless": self._headless,
            "locale": f"{hl}-{gl.upper()}",
            "viewport": {"width": 1366, "height": 850},
            "ignore_default_args": ["--enable-automation"],
            "args": ["--disable-blink-features=AutomationControlled"],
        }

        entry = None
        if not self._pool._direct:  # one proxy (and one profile) for the whole search
            try:
                entry, wait = self._pool.acquire()
                if wait > 0:
                    await asyncio.sleep(wait)
                if entry.url and entry.url != "direct":
                    launch_kwargs["proxy"] = _proxy_dict(entry.url)
                    tag = hashlib.md5(entry.url.encode()).hexdigest()[:8]
                    profile = f"{self._profile_dir}_{tag}"
            except PoolExhaustedError:
                print("[GOOGLE] [ERROR] All proxies are cooling down", flush=True)
                return finish("error", [], 0, 0)

        try:
            if self._pw is None:
                self._pw = await async_playwright().start()
            try:  # real Chrome first, bundled Chromium as the fallback
                context = await self._pw.chromium.launch_persistent_context(
                    profile, channel="chrome", **launch_kwargs
                )
            except Exception:
                context = await self._pw.chromium.launch_persistent_context(profile, **launch_kwargs)
        except Exception as ex:
            print(f"[GOOGLE] [ERROR] Browser launch failed: {ex}", flush=True)
            return finish("error", [], 1, 0)

        start = (q.page - 1) * 10 if q.page > 1 and q.max_pages <= 1 else 0
        url = f"{ENDPOINT}?q={quote_plus(query)}&hl={hl}&gl={gl}" + (f"&start={start}" if start else "")

        all_results: list[SearchResult] = []
        seen: set[str] = set()
        pages_done = 0
        status: SearchStatus = "error"

        try:
            page = context.pages[0] if context.pages else await context.new_page()
            res = await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            http_code = res.status if res else 200

            for page_idx in range(total_pages):
                print(f"[GOOGLE] Page {page_idx + 1}/{total_pages} -> {page.url[:100]}", flush=True)
                try:
                    await page.wait_for_selector("#search a h3, #captcha-form", timeout=8000)
                except Exception:
                    pass  # may be an empty or challenge page; classify below

                html = await page.content()
                items = parse_google_results(html)
                status, reason = explain_google_response(http_code, page.url, html, len(items))

                if status != "ok":
                    snippet = re.sub(r"\s+", " ", html[:160]).strip()
                    print(f"[GOOGLE] [{status.upper()}] {reason} | Body: \"{snippet}...\"", flush=True)
                    with open("debug_google.html", "w", encoding="utf-8") as f:
                        f.write(html)
                    break

                new = [i for i in items if i.url not in seen]
                if not new:
                    break  # Google repeated itself
                for i in new:
                    seen.add(i.url)
                    all_results.append(
                        SearchResult(
                            position=len(all_results) + 1,
                            title=i.title,
                            url=i.url,
                            snippet=i.snippet,
                        )
                    )
                pages_done += 1
                print(f"[GOOGLE] [SUCCESS] Parsed {len(new)} results", flush=True)

                if page_idx == total_pages - 1:
                    break
                nxt = page.locator("#pnnext")
                if not await nxt.count():
                    break  # no Next button: end of results
                await asyncio.sleep(random.uniform(*PAGE_DELAY))
                await nxt.click()
                await page.wait_for_load_state("domcontentloaded")
                http_code = 200  # only the first navigation reports a real status
        except Exception as ex:
            status = "error"
            print(f"[GOOGLE] [ERROR] {type(ex).__name__}: {ex}", flush=True)
        finally:
            await context.close()

        final: SearchStatus = "ok" if pages_done > 0 else status
        if entry is not None:
            self._pool.report(entry, final)

        resp = finish(final, all_results, 1, pages_done)
        print(
            f"[GOOGLE] Completed query='{q.query}': status='{final}' | pages={pages_done}/{total_pages} "
            f"| results={len(all_results)} | latency={resp.latency_ms}ms",
            flush=True,
        )
        return resp

    async def aclose(self) -> None:
        async with self._lock:
            if self._pw is not None:
                try:
                    await self._pw.stop()
                except Exception:
                    pass
                self._pw = None