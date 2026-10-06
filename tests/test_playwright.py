import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ddg_scraper.brave.playwright_engine import BravePlaywrightEngine
from ddg_scraper.models import SearchQuery
from ddg_scraper.net import ProxyPool

FIXTURES = Path(__file__).parent / "fixtures"
BRAVE_OK = (FIXTURES / "brave_ok.html").read_text(encoding="utf8")


def run_pw_search(engine, query, **kw):
    return asyncio.run(engine.search(SearchQuery(query, **kw)))


def test_playwright_engine_empty_query():
    pool = ProxyPool([], min_interval=0.01)
    engine = BravePlaywrightEngine(pool)
    resp = run_pw_search(engine, "")
    assert resp.status == "error"
    assert len(resp.results) == 0


def test_playwright_engine_launch_failure_handled_gracefully():
    pool = ProxyPool([], min_interval=0.01)
    engine = BravePlaywrightEngine(pool)
    with patch.object(engine, "_get_browser", side_effect=RuntimeError("missing system libraries")):
        resp = run_pw_search(engine, "python programming")
        assert resp.status == "error"
        assert len(resp.results) == 0
        assert resp.attempts == 1


def test_playwright_engine_search_with_mock_page():
    pool = ProxyPool([], min_interval=0.01)
    engine = BravePlaywrightEngine(pool, backoff_base=0.01)

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.wait_for_selector = AsyncMock()
    mock_page.content = AsyncMock(return_value=BRAVE_OK)

    mock_context = AsyncMock()
    mock_context.add_init_script = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_context.close = AsyncMock()

    mock_browser = MagicMock()
    mock_browser.is_connected = MagicMock(return_value=True)
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    with patch.object(engine, "_get_browser", return_value=mock_browser):
        resp = run_pw_search(engine, "python asyncio")
        assert resp.status == "ok"
        assert resp.engine == "brave-playwright"
        assert len(resp.results) == 5
        assert resp.results[0].title == "Welcome to Python.org"
        assert mock_context.add_init_script.called
        assert mock_context.close.called


def test_playwright_engine_multi_page():
    pool = ProxyPool([], min_interval=0.01)
    engine = BravePlaywrightEngine(pool, backoff_base=0.01)

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.wait_for_selector = AsyncMock()
    mock_page.content = AsyncMock(return_value=BRAVE_OK)

    mock_context = AsyncMock()
    mock_context.add_init_script = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_context.close = AsyncMock()

    mock_browser = MagicMock()
    mock_browser.is_connected = MagicMock(return_value=True)
    mock_browser.new_context = AsyncMock(return_value=mock_context)

    with patch.object(engine, "_get_browser", return_value=mock_browser):
        resp = run_pw_search(engine, "python", max_pages=2)
        assert resp.status == "ok"
        assert resp.pages_scraped == 2
        assert len(resp.results) == 10
        assert [r.position for r in resp.results] == list(range(1, 11))
        # Verify page.goto was called with both offset 0 and offset 1
        urls_called = [call.args[0] for call in mock_page.goto.call_args_list]
        assert any("offset=1" in u for u in urls_called)


def test_playwright_engine_aclose():
    pool = ProxyPool([], min_interval=0.01)
    engine = BravePlaywrightEngine(pool)
    mock_browser = AsyncMock()
    mock_pw = AsyncMock()
    engine._browser = mock_browser
    engine._pw = mock_pw

    asyncio.run(engine.aclose())
    assert mock_browser.close.called
    assert mock_pw.stop.called
    assert engine._browser is None
    assert engine._pw is None


def test_playwright_engine_with_proxy():
    pool = ProxyPool(["http://user:secret@proxy.host.com:8000"], min_interval=0.01)
    engine = BravePlaywrightEngine(pool, backoff_base=0.01)

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.wait_for_selector = AsyncMock()
    mock_page.content = AsyncMock(return_value=BRAVE_OK)

    mock_context = AsyncMock()
    mock_context.add_init_script = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_context.close = AsyncMock()

    mock_browser = MagicMock()
    mock_browser.is_connected = MagicMock(return_value=True)
    mock_browser.new_context = AsyncMock(return_value=mock_context)

    with patch.object(engine, "_get_browser", return_value=mock_browser):
        resp = run_pw_search(engine, "test query")
        assert resp.status == "ok"
        kwargs = mock_browser.new_context.call_args.kwargs
        assert kwargs["ignore_https_errors"] is True
        assert kwargs["proxy"]["server"] == "http://proxy.host.com:8000"
        assert kwargs["proxy"]["username"] == "user"
        assert kwargs["proxy"]["password"] == "secret"
