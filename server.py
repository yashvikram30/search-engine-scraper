"""Local HTTP Server for testing DuckDuckGo, Brave and Google Search Scrapers via Postman or curl."""
from __future__ import annotations

import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from ddg_scraper.brave.engine import BraveEngine
from ddg_scraper.brave.playwright_engine import BravePlaywrightEngine
from ddg_scraper.ddg.engine import DuckDuckGoEngine
from ddg_scraper.google.engine import GooglePlaywrightEngine
from ddg_scraper.models import SearchQuery
from pathlib import Path
from ddg_scraper.net import ProxyPool


def load_proxies() -> list[str]:
    # 1. Environment variable PROXY_URLS
    env_val = os.environ.get("PROXY_URLS", "")
    if env_val.strip():
        return [p.strip() for p in env_val.split(",") if p.strip()]

    # 2. Check .env file in project root
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip("'\"")
                if key == "PROXY_URLS" and val:
                    return [p.strip() for p in val.split(",") if p.strip()]

    # 3. Check proxies.txt file
    file_path = Path(__file__).parent / "proxies.txt"
    if file_path.exists():
        lines = [l.strip() for l in file_path.read_text(encoding="utf-8").splitlines()]
        return [l for l in lines if l and not l.startswith("#")]

    return []


# Dedicated persistent asyncio event loop for the server lifetime
async_loop = asyncio.new_event_loop()
threading.Thread(target=async_loop.run_forever, daemon=True).start()

# Configure pool and engines
proxies = load_proxies()
default_cooldown = 0.0 if len(proxies) <= 1 else 300.0
cooldown_s = float(os.environ.get("COOLDOWN_S", str(default_cooldown)))
pool = ProxyPool(proxies, min_interval=float(os.environ.get("MIN_INTERVAL_S", "1.5")), cooldown=cooldown_s)
engine_ddg_post = DuckDuckGoEngine(pool, method="POST")
engine_ddg_get = DuckDuckGoEngine(pool, method="GET")
engine_brave = BraveEngine(pool)
engine_brave_playwright = BravePlaywrightEngine(pool)
# Google needs a real browser. Headed by default (headless is more likely to be blocked).
# Set GOOGLE_HEADLESS=1 to hide the window. The Chrome profile (cookies) lives in ./g_profile.
engine_google = GooglePlaywrightEngine(
    pool,
    headless=os.environ.get("GOOGLE_HEADLESS", "0") == "1",
    profile_dir=os.environ.get("GOOGLE_PROFILE_DIR", str(Path(__file__).parent / "g_profile")),
)

SUPPORTED_ENGINES = "'ddg', 'brave', 'brave-playwright', 'google'"


class ScraperHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, data: dict):
        body = json.dumps(data, indent=2).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _select_engine(self, engine_name: str, method: str):
        engine_name = engine_name.lower().strip()
        if engine_name in ("brave-playwright", "playwright", "brave_playwright", "pw"):
            return engine_brave_playwright
        if engine_name in ("brave", "bravesearch"):
            return engine_brave
        if engine_name in ("google", "google-playwright", "google_playwright", "g"):
            return engine_google
        if engine_name in ("ddg", "duckduckgo", ""):
            return engine_ddg_post if method.upper() == "POST" else engine_ddg_get
        return None

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json(200, {
                "status": "ok",
                "cooling_proxies": pool.cooling_count(),
                "curl_cffi": pool.is_curl_cffi,
            })
            return

        if parsed.path == "/search":
            qs = parse_qs(parsed.query)
            query = qs.get("q", [""])[0]
            region = qs.get("region", ["us-en"])[0]
            method = qs.get("method", ["POST"])[0].upper()
            engine_name = qs.get("engine", ["ddg"])[0]
            try:
                pages = int(qs.get("pages", qs.get("max_pages", ["1"]))[0])
            except ValueError:
                pages = 1

            if not query:
                self._send_json(400, {"error": "Missing 'q' query parameter"})
                return

            eng = self._select_engine(engine_name, method)
            if eng is None:
                err_msg = f"Unsupported engine '{engine_name}'. Supported: {SUPPORTED_ENGINES}."
                print(f"[SERVER] [400] {err_msg}", flush=True)
                self._send_json(400, {"error": err_msg})
                return

            print(
                f"\n[SERVER] ---> Incoming GET /search | query='{query}' engine='{engine_name}' method='{method}' pages={pages}",
                flush=True,
            )

            try:
                future = asyncio.run_coroutine_threadsafe(
                    eng.search(SearchQuery(query=query, region=region, max_pages=pages)),
                    async_loop,
                )
                res = future.result()
            except Exception as e:
                print(f"[SERVER] <--- [500] Search execution failed: {e}\n", flush=True)
                self._send_json(500, {"error": f"Search execution failed: {str(e)}"})
                return

            print(
                f"[SERVER] <--- Finished GET /search | status='{res.status}' results={len(res.results)} "
                f"pages={res.pages_scraped}/{pages} attempts={res.attempts} latency={res.latency_ms}ms\n",
                flush=True,
            )

            response_data = {
                "engine": res.engine,
                "query": res.query,
                "region": res.region,
                "status": res.status,
                "pages_requested": pages,
                "pages_scraped": res.pages_scraped,
                "attempts": res.attempts,
                "latency_ms": res.latency_ms,
                "fetched_at": res.fetched_at,
                "results_count": len(res.results),
                "results": [
                    {
                        "position": r.position,
                        "title": r.title,
                        "url": r.url,
                        "snippet": r.snippet,
                    }
                    for r in res.results
                ],
            }
            self._send_json(200, response_data)
            return

        self._send_json(404, {"error": "Endpoint not found. Use /search?q=your+query or /health"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/search":
            content_length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_length)
            try:
                data = json.loads(raw_body) if raw_body else {}
            except json.JSONDecodeError:
                self._send_json(400, {"error": "Invalid JSON payload"})
                return

            query = data.get("query") or data.get("q")
            region = data.get("region", "us-en")
            method = str(data.get("method", "POST")).upper()
            engine_name = str(data.get("engine", "ddg"))
            try:
                pages = int(data.get("pages") or data.get("max_pages") or 1)
            except ValueError:
                pages = 1

            if not query:
                self._send_json(400, {"error": "Missing 'query' in JSON body"})
                return

            eng = self._select_engine(engine_name, method)
            if eng is None:
                err_msg = f"Unsupported engine '{engine_name}'. Supported: {SUPPORTED_ENGINES}."
                print(f"[SERVER] [400] {err_msg}", flush=True)
                self._send_json(400, {"error": err_msg})
                return

            print(
                f"\n[SERVER] ---> Incoming POST /search | query='{query}' engine='{engine_name}' method='{method}' pages={pages}",
                flush=True,
            )

            try:
                future = asyncio.run_coroutine_threadsafe(
                    eng.search(SearchQuery(query=query, region=region, max_pages=pages)),
                    async_loop,
                )
                res = future.result()
            except Exception as e:
                print(f"[SERVER] <--- [500] Search execution failed: {e}\n", flush=True)
                self._send_json(500, {"error": f"Search execution failed: {str(e)}"})
                return

            print(
                f"[SERVER] <--- Finished POST /search | status='{res.status}' results={len(res.results)} "
                f"pages={res.pages_scraped}/{pages} attempts={res.attempts} latency={res.latency_ms}ms\n",
                flush=True,
            )

            response_data = {
                "engine": res.engine,
                "query": res.query,
                "region": res.region,
                "status": res.status,
                "pages_requested": pages,
                "pages_scraped": res.pages_scraped,
                "attempts": res.attempts,
                "latency_ms": res.latency_ms,
                "fetched_at": res.fetched_at,
                "results_count": len(res.results),
                "results": [
                    {
                        "position": r.position,
                        "title": r.title,
                        "url": r.url,
                        "snippet": r.snippet,
                    }
                    for r in res.results
                ],
            }
            self._send_json(200, response_data)
            return

        self._send_json(404, {"error": "Endpoint not found"})


def run(port: int | None = None):
    if port is None:
        port = int(os.environ.get("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), ScraperHandler)
    print(f"Scraper API Server listening at http://localhost:{port}")
    print(f"  - HTTP Backend: {'curl-cffi (Chrome TLS impersonation)' if pool.is_curl_cffi else 'httpx'}")
    print(f"  - Proxies active: {len(proxies)} ({'Direct mode / no proxies' if not proxies else 'Proxy pool active'})")
    print(f"  - Health check: http://localhost:{port}/health")
    print(f"  - DDG Search (GET): http://localhost:{port}/search?q=python&engine=ddg")
    print(f"  - Brave Search (GET): http://localhost:{port}/search?q=python&engine=brave")
    print(f"  - Brave Playwright (GET): http://localhost:{port}/search?q=python&engine=brave-playwright")
    print(f"  - Google (GET): http://localhost:{port}/search?q=python&engine=google")
    print(f"  - Search (POST): http://localhost:{port}/search (JSON body with engine='ddg'|'brave'|'brave-playwright'|'google')")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        try:
            asyncio.run_coroutine_threadsafe(engine_brave_playwright.aclose(), async_loop).result(timeout=2.0)
        except Exception:
            pass
        try:
            asyncio.run_coroutine_threadsafe(engine_google.aclose(), async_loop).result(timeout=2.0)
        except Exception:
            pass
        try:
            asyncio.run_coroutine_threadsafe(pool.aclose(), async_loop).result(timeout=2.0)
        except Exception:
            pass
        async_loop.call_soon_threadsafe(async_loop.stop)


if __name__ == "__main__":
    run()