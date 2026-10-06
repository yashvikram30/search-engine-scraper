from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

from playwright.async_api import Browser, Playwright, async_playwright

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
DEFAULT_TIMEOUT_MS = 20000

STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
});
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5],
});
Object.defineProperty(navigator, 'languages', {
    get: () => ['en-US', 'en'],
});
window.chrome = {
    runtime: {},
    loadTimes: function() {},
    csi: function() {},
    app: {},
};
try {
    if (navigator.userAgentData) {
        Object.defineProperty(navigator.userAgentData, 'brands', {
            get: () => [
                { brand: 'Chromium', version: '130' },
                { brand: 'Google Chrome', version: '130' },
                { brand: 'Not?A_Brand', version: '99' }
            ]
        });
    }
} catch (e) {}
"""


class BravePlaywrightEngine:
    """Brave Search engine backed by a headless Chromium browser via Playwright.

    Executes client-side JavaScript, solves Turnstile / JS hydration challenges,
    and returns parsed, structured SearchResults matching the SearchEngine protocol.
    """

    name = "brave-playwright"

    def __init__(
        self,
        pool: ProxyPool,
        headless: bool = True,
        cache_ttl: float = 6 * 60 * 60,
        backoff_base: float = 1.0,
    ) -> None:
        self._pool = pool
        self._headless = headless
        self._cache = TtlCache(cache_ttl)
        self._backoff_base = backoff_base
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

    async def _get_browser(self) -> Browser:
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                if self._pw is None:
                    self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(
                    headless=self._headless,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--disable-infobars",
                    ],
                )
            return self._browser

    async def search(self, q: SearchQuery) -> SearchResponse:
        total_pages = max(1, q.max_pages)
        query = q.query.strip()
        key = f"brave-pw|{query}|{q.region}|{q.page}|{total_pages}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        started = time.monotonic()

        def finish(
            status: SearchStatus,
            results: list[SearchResult],
            attempts: int,
            pages_scraped: int,
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

        try:
            browser = await self._get_browser()
        except Exception as e:
            # Playwright launch error (e.g. missing OS dependencies)
            return finish("error", [], 1, 0)

        all_results: list[SearchResult] = []
        total_attempts = 0
        pages_done = 0
        last_status: SearchStatus = "error"
        start_offset = (q.page - 1) if q.page > 1 and q.max_pages <= 1 else 0

        for page_idx in range(total_pages):
            current_offset = start_offset + page_idx
            page_attempts = 0
            page_status: SearchStatus = "error"
            page_results: list[SearchResult] = []

            while page_attempts < MAX_ATTEMPTS:
                page_attempts += 1
                total_attempts += 1

                context_kwargs: dict[str, Any] = {
                    "user_agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
                    ),
                    "viewport": {"width": 1280, "height": 800},
                    "locale": "en-US",
                    "ignore_https_errors": True,
                }

                # Retrieve proxy if configured in pool
                entry = None
                if not self._pool._direct:
                    try:
                        entry, wait = self._pool.acquire()
                        if wait > 0:
                            await asyncio.sleep(wait)
                        if entry.url and entry.url != "direct":
                            parsed = urlparse(entry.url)
                            port_str = f":{parsed.port}" if parsed.port else ""
                            scheme = parsed.scheme or "http"
                            proxy_dict: dict[str, str] = {
                                "server": f"{scheme}://{parsed.hostname}{port_str}"
                            }
                            if parsed.username:
                                proxy_dict["username"] = unquote(parsed.username)
                            if parsed.password:
                                proxy_dict["password"] = unquote(parsed.password)
                            context_kwargs["proxy"] = proxy_dict
                    except PoolExhaustedError:
                        print(f"[{self.name.upper()}] [ERROR] Page {page_idx + 1}/{total_pages}: PoolExhaustedError - all proxies are cooling down", flush=True)
                        page_status = "error"
                        break
                    except Exception:
                        pass

                proxy_label = entry.url if (entry and entry.url) else "direct"
                url = f"{ENDPOINT}?q={query}"
                if current_offset > 0:
                    url += f"&offset={current_offset}"

                print(
                    f"[{self.name.upper()}] Page {page_idx + 1}/{total_pages} (offset={current_offset}, "
                    f"attempt {page_attempts}/{MAX_ATTEMPTS}) [{proxy_label}] -> GET {url}",
                    flush=True,
                )

                context = await browser.new_context(**context_kwargs)
                await context.add_init_script(STEALTH_SCRIPT)

                try:
                    page = await context.new_page()

                    res = await page.goto(url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)

                    # Wait for search results container to populate
                    try:
                        await page.wait_for_selector(
                            ".snippet, div[data-type='search'], #results",
                            timeout=8000,
                        )
                    except Exception:
                        # Continue even if selector wait timed out (might be empty or challenge page)
                        pass

                    html = await page.content()
                    page_results = parse_brave_results(html)
                    http_status_val = getattr(res, "status", 200) if res else 200
                    http_code = http_status_val if isinstance(http_status_val, int) else 200
                    page_status, reason = explain_brave_response(http_code, html, len(page_results))

                    snippet = re.sub(r"\s+", " ", html[:160]).strip()
                    if page_status == "ok":
                        print(
                            f"[{self.name.upper()}] [SUCCESS] HTTP {http_code} ({len(html):,} bytes HTML) "
                            f"-> Parsed {len(page_results)} results",
                            flush=True,
                        )
                    else:
                        print(
                            f"[{self.name.upper()}] [{page_status.upper()}] Could not scrape page: {reason} "
                            f"(Browser received HTTP {http_code}, {len(html):,} bytes HTML) | Body: \"{snippet}...\"",
                            flush=True,
                        )
                except Exception as ex:
                    page_status = "error"
                    print(
                        f"[{self.name.upper()}] [ERROR] Browser exception on attempt {page_attempts}/{MAX_ATTEMPTS} "
                        f"via [{proxy_label}]: {type(ex).__name__}: {ex}",
                        flush=True,
                    )
                finally:
                    await context.close()

                if entry is not None:
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

    async def aclose(self) -> None:
        async with self._lock:
            if self._browser is not None:
                try:
                    await self._browser.close()
                except Exception:
                    pass
                self._browser = None
            if self._pw is not None:
                try:
                    await self._pw.stop()
                except Exception:
                    pass
                self._pw = None
