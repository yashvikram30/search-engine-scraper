import json
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer

import pytest

import server


def test_select_engine():
    handler = server.ScraperHandler.__new__(server.ScraperHandler)
    assert handler._select_engine("brave", "GET") == server.engine_brave
    assert handler._select_engine("BraveSearch", "GET") == server.engine_brave
    assert handler._select_engine("ddg", "POST") == server.engine_ddg_post
    assert handler._select_engine("ddg", "GET") == server.engine_ddg_get
    assert handler._select_engine("duckduckgo", "GET") == server.engine_ddg_get
    assert handler._select_engine("invalid", "GET") is None


@pytest.fixture(scope="module")
def running_server():
    httpd = HTTPServer(("127.0.0.1", 0), server.ScraperHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()


def test_server_health(running_server):
    req = urllib.request.Request(f"{running_server}/health")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode())
        assert data["status"] == "ok"
        assert "cooling_proxies" in data


def test_server_search_missing_q(running_server):
    req = urllib.request.Request(f"{running_server}/search")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 400
    data = json.loads(exc_info.value.read().decode())
    assert "error" in data


def test_server_search_unsupported_engine(running_server):
    req = urllib.request.Request(f"{running_server}/search?q=test&engine=askjeeves")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 400
    data = json.loads(exc_info.value.read().decode())
    assert "Unsupported engine" in data["error"]


def test_server_post_invalid_json(running_server):
    req = urllib.request.Request(
        f"{running_server}/search",
        data=b"invalid-json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 400
    data = json.loads(exc_info.value.read().decode())
    assert "Invalid JSON" in data["error"]
