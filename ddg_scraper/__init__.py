"""DuckDuckGo and Brave multi-engine search scraper package."""
from .brave.engine import BraveEngine
from .brave.playwright_engine import BravePlaywrightEngine
from .ddg.engine import DuckDuckGoEngine
from .crawler_strategy import CurlCffiHTTPStrategy, build_curl_cffi_strategy
from .models import SearchEngine, SearchQuery, SearchResponse, SearchResult, SearchStatus
from .net import HAS_CURL_CFFI, CurlCffiClient, ProxyPool

__all__ = [
    "BraveEngine",
    "BravePlaywrightEngine",
    "CurlCffiClient",
    "CurlCffiHTTPStrategy",
    "DuckDuckGoEngine",
    "HAS_CURL_CFFI",
    "ProxyPool",
    "SearchEngine",
    "SearchQuery",
    "SearchResponse",
    "SearchResult",
    "SearchStatus",
    "build_curl_cffi_strategy",
]
