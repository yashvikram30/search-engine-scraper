import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from ddg_scraper.crawler_strategy import (
    AsyncHTTPCrawlerStrategy,
    CurlCffiHTTPStrategy,
    build_curl_cffi_strategy,
    HAS_CURL_CFFI,
)
from ddg_scraper.net import CurlCffiClient, ProxyPool


def test_build_curl_cffi_strategy():
    strategy = build_curl_cffi_strategy()
    if HAS_CURL_CFFI:
        assert isinstance(strategy, CurlCffiHTTPStrategy)
    else:
        assert isinstance(strategy, AsyncHTTPCrawlerStrategy)


def test_build_curl_cffi_strategy_fallback_when_disabled():
    with patch("ddg_scraper.crawler_strategy.HAS_CURL_CFFI", False):
        strat = build_curl_cffi_strategy()
        assert type(strat) is AsyncHTTPCrawlerStrategy


def test_curl_cffi_strategy_crawl_success():
    mock_resp = MagicMock()
    mock_resp.text = "<html>Test Output</html>"
    mock_resp.status_code = 200

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=mock_resp)

    mock_session_cls = MagicMock()
    mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=None)

    with patch("ddg_scraper.crawler_strategy.AsyncSession", mock_session_cls), \
         patch("ddg_scraper.crawler_strategy.HAS_CURL_CFFI", True):
        strategy = CurlCffiHTTPStrategy()
        ns = asyncio.run(strategy.crawl("https://example.com/test", headers={"User-Agent": "TestUA"}))

        assert ns.status_code == 200
        assert ns.success is True
        assert ns.html == "<html>Test Output</html>"
        assert ns.cleaned_html == "<html>Test Output</html>"
        assert ns.error_message is None
        mock_session.get.assert_awaited_once_with(
            "https://example.com/test",
            headers={"User-Agent": "TestUA"},
            timeout=30,
        )


def test_curl_cffi_strategy_fallback_on_exception():
    mock_session_cls = MagicMock()
    mock_session_cls.return_value.__aenter__ = AsyncMock(side_effect=RuntimeError("connection error"))
    mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=None)

    strategy = CurlCffiHTTPStrategy()

    with patch("ddg_scraper.crawler_strategy.AsyncSession", mock_session_cls), \
         patch("ddg_scraper.crawler_strategy.HAS_CURL_CFFI", True), \
         patch.object(AsyncHTTPCrawlerStrategy, "crawl", new=AsyncMock(return_value="fallback_called")) as mock_super:
        result = asyncio.run(strategy.crawl("https://example.com"))
        assert result == "fallback_called"
        assert mock_super.called


def test_curl_cffi_client_requests():
    mock_resp_get = MagicMock()
    mock_resp_get.status_code = 200
    mock_resp_get.text = "OK GET"

    mock_resp_post = MagicMock()
    mock_resp_post.status_code = 200
    mock_resp_post.text = "OK POST"

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=mock_resp_get)
    mock_session.post = AsyncMock(return_value=mock_resp_post)
    mock_session.close = AsyncMock()

    with patch("ddg_scraper.net.AsyncSession", return_value=mock_session), \
         patch("ddg_scraper.net.HAS_CURL_CFFI", True):
        client = CurlCffiClient(proxy="http://127.0.0.1:8080", impersonate="chrome120")

        res_get = asyncio.run(client.get("https://example.com", headers={"X-Test": "1"}, timeout=10.0))
        assert res_get.text == "OK GET"
        assert res_get.status_code == 200

        res_post = asyncio.run(client.post("https://example.com", data={"q": "foo"}, headers={"X-Test": "1"}, timeout=10.0))
        assert res_post.text == "OK POST"
        assert res_post.status_code == 200

        asyncio.run(client.aclose())
        assert mock_session.close.called


def test_curl_cffi_client_raises_httpx_error():
    mock_session = AsyncMock()
    mock_session.get = AsyncMock(side_effect=Exception("Curl network timeout"))

    with patch("ddg_scraper.net.AsyncSession", return_value=mock_session), \
         patch("ddg_scraper.net.HAS_CURL_CFFI", True):
        client = CurlCffiClient()
        with pytest.raises(httpx.HTTPError) as exc_info:
            asyncio.run(client.get("https://fail.com"))
        assert "Curl network timeout" in str(exc_info.value)


def test_proxy_pool_curl_cffi_selection():
    if not HAS_CURL_CFFI:
        pytest.skip("curl_cffi not installed")

    # 1. Default should use curl_cffi when installed
    pool = ProxyPool(["http://proxy1:8080"])
    assert pool.is_curl_cffi is True
    entry, _ = pool.acquire()
    assert isinstance(entry.client, CurlCffiClient)
    asyncio.run(pool.aclose())

    # 2. Explicit use_curl_cffi=False should use httpx
    pool_httpx = ProxyPool(["http://proxy1:8080"], min_interval=0.01, use_curl_cffi=False)
    assert pool_httpx.is_curl_cffi is False
    entry_httpx, _ = pool_httpx.acquire()
    assert isinstance(entry_httpx.client, httpx.AsyncClient)
    asyncio.run(pool_httpx.aclose())

    # 3. Providing a transport (e.g. for testing) must always force httpx
    pool_mock = ProxyPool([], min_interval=0.01, transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert pool_mock.is_curl_cffi is False
    entry_mock, _ = pool_mock.acquire()
    assert isinstance(entry_mock.client, httpx.AsyncClient)
    asyncio.run(pool_mock.aclose())
