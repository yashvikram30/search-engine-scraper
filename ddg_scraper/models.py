from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

SearchStatus = Literal["ok", "empty", "blocked", "error"]

@dataclass
class SearchQuery:
    query: str
    region: str = "us-en"  # DDG kl value, for example "us-en" or "jp-jp"
    page: int = 1  # 1 based
    max_pages: int = 1  # number of pages to scrape (e.g. 1, 2, 3...)

@dataclass
class SearchResult:
    position: int  # position within the page or cumulative
    title: str
    url: str
    snippet: str

@dataclass
class SearchResponse:
    engine: str
    query: str
    region: str
    page: int
    status: SearchStatus
    results: list[SearchResult]
    attempts: int
    latency_ms: int
    fetched_at: str
    pages_scraped: int = 1

class SearchEngine(Protocol):
    name: str

    async def search(self, q: SearchQuery) -> SearchResponse: ...
