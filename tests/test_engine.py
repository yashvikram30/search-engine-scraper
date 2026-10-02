import asyncio
import gzip
from pathlib import Path

import httpx
import pytest

from ddg_scraper.ddg.engine import DuckDuckGoEngine
from ddg_scraper.models import SearchQuery
from ddg_scraper.net import ProxyPool

FIXTURES = Path(__file__).parent / "fixtures"
OK = (FIXTURES / "ddg_ok.html").read_text(encoding="utf8")
BLOCKED = (FIXTURES / "ddg_blocked.html").read_text(encoding="utf8")

def make_engine(replies):
    """Engine wired to a fake server that answers with `replies` in order."""
    seen: list[httpx.Request] = []
    queue = list(replies)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    pool = ProxyPool([], min_interval=0.01, transport=httpx.MockTransport(handler))
    return DuckDuckGoEngine(pool, backoff_base=0.01), seen

def search(engine, query, **kw):
    return asyncio.run(engine.search(SearchQuery(query, **kw)))

def test_good_page():
    engine, seen = make_engine([httpx.Response(200, text=OK)])
    r = search(engine, "hello world", region="jp-jp")
    assert (r.status, r.attempts, len(r.results)) == ("ok", 1, 6)
    assert seen[0].url.params["kl"] == "jp-jp"
    assert seen[0].url.params["q"] == "hello world"

def test_sec_fetch_headers_go_out_as_written():
    engine, seen = make_engine([httpx.Response(200, text=OK)])
    search(engine, "q")
    assert seen[0].headers["sec-fetch-mode"] == "navigate"
    assert seen[0].headers["sec-fetch-dest"] == "document"

def test_gzip_body_is_decoded():
    body = gzip.compress(OK.encode())
    engine, _ = make_engine(
        [httpx.Response(200, content=body, headers={"content-encoding": "gzip"})]
    )
    assert search(engine, "q").status == "ok"

def test_captcha_then_ok_retries():
    engine, seen = make_engine(
        [httpx.Response(200, text=BLOCKED), httpx.Response(200, text=OK)]
    )
    r = search(engine, "q")
    assert (r.status, r.attempts) == ("ok", 2)
    assert len(seen) == 2

def test_redirects_are_blocked_not_followed():
    engine, seen = make_engine([httpx.Response(303, headers={"location": "/x"})])
    r = search(engine, "q")
    assert (r.status, r.attempts) == ("blocked", 3)
    assert len(seen) == 3  # never followed the redirect

def test_server_errors_are_errors():
    engine, _ = make_engine([httpx.Response(503)])
    assert search(engine, "q").status == "error"

def test_operators_are_stripped():
    engine, seen = make_engine([httpx.Response(200, text=OK)])
    search(engine, "site:example.com intitle:foo bar")
    assert seen[0].url.params["q"] == "example.com foo bar"

def test_over_long_query_sends_nothing():
    engine, seen = make_engine([httpx.Response(200, text=OK)])
    r = search(engine, "x" * 600)
    assert (r.status, r.attempts) == ("error", 0)
    assert seen == []

def test_page_two_is_refused():
    engine, seen = make_engine([httpx.Response(200, text=OK)])
    with pytest.raises(NotImplementedError):
        search(engine, "q", page=2)
    assert seen == []

def test_successful_answers_are_cached():
    engine, seen = make_engine([httpx.Response(200, text=OK)])
    search(engine, "same query")
    search(engine, "same query")
    assert len(seen) == 1

def test_max_pages_scrapes_multiple_pages():
    PAGE2 = """
    <div class="result">
        <a class="result__a" href="https://example.com/p2_res1">Page 2 Result 1</a>
        <div class="result__snippet">Snippet 1 page 2</div>
    </div>
    <div class="result">
        <a class="result__a" href="https://example.com/p2_res2">Page 2 Result 2</a>
        <div class="result__snippet">Snippet 2 page 2</div>
    </div>
    """
    engine, seen = make_engine([httpx.Response(200, text=OK), httpx.Response(200, text=PAGE2)])
    r = asyncio.run(engine.search(SearchQuery("hello world", max_pages=2)))
    assert r.status == "ok"
    assert r.pages_scraped == 2
    assert len(r.results) == 8  # 6 from page 1 + 2 from page 2
    assert r.results[0].position == 1
    assert r.results[6].position == 7
    assert r.results[7].position == 8
    assert len(seen) == 2

