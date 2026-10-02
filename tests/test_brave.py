import asyncio
from pathlib import Path

import httpx
import pytest

from ddg_scraper.brave.classify import classify_brave
from ddg_scraper.brave.engine import BraveEngine
from ddg_scraper.brave.parse import parse_brave_results
from ddg_scraper.models import SearchQuery
from ddg_scraper.net import ProxyPool

FIXTURES = Path(__file__).parent / "fixtures"
BRAVE_OK = (FIXTURES / "brave_ok.html").read_text(encoding="utf8")
BRAVE_BLOCKED = (FIXTURES / "brave_blocked.html").read_text(encoding="utf8")

def make_brave_engine(replies):
    seen: list[httpx.Request] = []
    queue = list(replies)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    pool = ProxyPool([], min_interval=0.01, transport=httpx.MockTransport(handler))
    return BraveEngine(pool, backoff_base=0.01), seen

def run_search(engine, query, **kw):
    return asyncio.run(engine.search(SearchQuery(query, **kw)))

def test_parse_brave_results():
    results = parse_brave_results(BRAVE_OK)
    # Total valid results: 5 (sponsored ad and brave feedback excluded)
    assert len(results) == 5
    assert results[0].position == 1
    assert results[0].title == "Welcome to Python.org"
    assert results[0].url == "https://www.python.org/"
    assert "Python Programming Language" in results[0].snippet

    urls = [r.url for r in results]
    assert "https://example.com/sponsored" not in urls
    assert "https://search.brave.com/feedback" not in urls

    positions = [r.position for r in results]
    assert positions == [1, 2, 3, 4, 5]

def test_classify_brave():
    assert classify_brave(200, BRAVE_OK, 5) == "ok"
    assert classify_brave(200, "<html>no results found</html>", 0) == "empty"
    assert classify_brave(429, BRAVE_BLOCKED, 0) == "blocked"
    assert classify_brave(200, BRAVE_BLOCKED, 0) == "blocked"
    assert classify_brave(500, "server error", 0) == "error"

def test_brave_engine_single_page():
    engine, seen = make_brave_engine([httpx.Response(200, text=BRAVE_OK)])
    resp = run_search(engine, "python programming")
    assert resp.status == "ok"
    assert resp.engine == "brave"
    assert len(resp.results) == 5
    assert resp.attempts == 1
    assert resp.pages_scraped == 1
    assert seen[0].url.params["q"] == "python programming"
    assert "offset" not in seen[0].url.params

def test_brave_engine_multi_page():
    engine, seen = make_brave_engine(
        [httpx.Response(200, text=BRAVE_OK), httpx.Response(200, text=BRAVE_OK)]
    )
    resp = run_search(engine, "python", max_pages=2)
    assert resp.status == "ok"
    assert resp.pages_scraped == 2
    assert len(resp.results) == 10
    # Contiguous positions across pages
    assert [r.position for r in resp.results] == list(range(1, 11))
    assert len(seen) == 2
    assert "offset" not in seen[0].url.params
    assert seen[1].url.params["offset"] == "1"

def test_brave_engine_deep_pagination():
    engine, seen = make_brave_engine(
        [
            httpx.Response(200, text=BRAVE_OK),
            httpx.Response(200, text=BRAVE_OK),
            httpx.Response(200, text=BRAVE_OK),
            httpx.Response(200, text=BRAVE_OK),
        ]
    )
    resp = run_search(engine, "deep crawl", max_pages=4)
    assert resp.status == "ok"
    assert resp.pages_scraped == 4
    assert len(resp.results) == 20
    assert resp.results[-1].position == 20
    assert len(seen) == 4
    assert seen[3].url.params["offset"] == "3"

def test_brave_engine_challenge_and_retry():
    engine, seen = make_brave_engine(
        [
            httpx.Response(429, text=BRAVE_BLOCKED),
            httpx.Response(200, text=BRAVE_OK),
        ]
    )
    resp = run_search(engine, "python")
    assert resp.status == "ok"
    assert resp.attempts == 2
    assert len(resp.results) == 5

def test_brave_engine_empty_query():
    engine, seen = make_brave_engine([httpx.Response(200, text=BRAVE_OK)])
    resp = run_search(engine, "   ")
    assert resp.status == "error"
    assert len(resp.results) == 0
    assert len(seen) == 0
