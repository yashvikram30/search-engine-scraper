"""DuckDuckGo and Brave multi-engine search scraper package."""
from .brave.engine import BraveEngine
from .ddg.engine import DuckDuckGoEngine
from .models import SearchEngine, SearchQuery, SearchResponse, SearchResult, SearchStatus
from .net import ProxyPool

__all__ = [
    "BraveEngine",
    "DuckDuckGoEngine",
    "ProxyPool",
    "SearchEngine",
    "SearchQuery",
    "SearchResponse",
    "SearchResult",
    "SearchStatus",
]
