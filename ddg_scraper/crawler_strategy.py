from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

log = logging.getLogger(__name__)

# Try importing crawl4ai base strategy; if missing, define a compatible base class
try:
    from crawl4ai.async_crawler_strategy import AsyncHTTPCrawlerStrategy
except ImportError:
    class AsyncHTTPCrawlerStrategy:  # type: ignore[no-redef]
        """Fallback base class when crawl4ai is not installed."""

        async def crawl(self, url: str, **kwargs: Any) -> SimpleNamespace:
            raise NotImplementedError(
                "crawl4ai is not installed. Install crawl4ai to use AsyncHTTPCrawlerStrategy."
            )

try:
    from curl_cffi.requests import AsyncSession

    HAS_CURL_CFFI = True
except ImportError:
    AsyncSession = None  # type: ignore[assignment]
    HAS_CURL_CFFI = False
    log.warning("curl_cffi not available; falling back to standard AsyncHTTPCrawlerStrategy")


class CurlCffiHTTPStrategy(AsyncHTTPCrawlerStrategy):
    """HTTP crawler strategy backed by curl_cffi for real Chrome 120 TLS fingerprints."""

    async def crawl(self, url: str, **kwargs: Any) -> SimpleNamespace:
        if not HAS_CURL_CFFI or AsyncSession is None:
            return await super().crawl(url, **kwargs)

        try:
            async with AsyncSession(impersonate="chrome120") as session:
                resp = await session.get(
                    url,
                    headers=kwargs.get("headers", {}),
                    timeout=kwargs.get("timeout", 30),
                )
                ns = SimpleNamespace()
                ns.html = resp.text
                ns.status_code = resp.status_code
                ns.success = 200 <= resp.status_code < 400
                ns.error_message = None
                ns.markdown = ""
                ns.cleaned_html = resp.text
                return ns
        except Exception:
            # Transparently fall back to standard strategy
            return await super().crawl(url, **kwargs)


def build_curl_cffi_strategy() -> AsyncHTTPCrawlerStrategy:
    """Builds a curl_cffi-backed HTTP strategy for Chrome 120 TLS fingerprinting."""
    if HAS_CURL_CFFI:
        return CurlCffiHTTPStrategy()
    return AsyncHTTPCrawlerStrategy()
