"""Local HTTP Server for testing DuckDuckGo and Brave Search Scrapers via Postman or curl."""
from __future__ import annotations

import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from ddg_scraper.brave.engine import BraveEngine
from ddg_scraper.ddg.engine import DuckDuckGoEngine
from ddg_scraper.models import SearchQuery
from ddg_scraper.net import ProxyPool

# Dedicated persistent asyncio event loop for the server lifetime
async_loop = asyncio.new_event_loop()
threading.Thread(target=async_loop.run_forever, daemon=True).start()

# Configure pool and engines
proxies = [p for p in os.environ.get("PROXY_URLS", "").split(",") if p]
pool = ProxyPool(proxies, min_interval=float(os.environ.get("MIN_INTERVAL_S", "1.5")))
engine_ddg_post = DuckDuckGoEngine(pool, method="POST")
engine_ddg_get = DuckDuckGoEngine(pool, method="GET")
engine_brave = BraveEngine(pool)


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
        if engine_name in ("brave", "bravesearch"):
            return engine_brave
        if engine_name in ("ddg", "duckduckgo", ""):
            return engine_ddg_post if method.upper() == "POST" else engine_ddg_get
        return None

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json(200, {"status": "ok", "cooling_proxies": pool.cooling_count()})
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
                self._send_json(400, {"error": f"Unsupported engine '{engine_name}'. Use 'ddg' or 'brave'."})
                return

            future = asyncio.run_coroutine_threadsafe(
                eng.search(SearchQuery(query=query, region=region, max_pages=pages)),
                async_loop,
            )
            res = future.result()

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
                self._send_json(400, {"error": f"Unsupported engine '{engine_name}'. Use 'ddg' or 'brave'."})
                return

            future = asyncio.run_coroutine_threadsafe(
                eng.search(SearchQuery(query=query, region=region, max_pages=pages)),
                async_loop,
            )
            res = future.result()

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
    print(f"  - Health check: http://localhost:{port}/health")
    print(f"  - DDG Search (GET): http://localhost:{port}/search?q=python&engine=ddg")
    print(f"  - Brave Search (GET): http://localhost:{port}/search?q=python&engine=brave")
    print(f"  - Search (POST): http://localhost:{port}/search (JSON body with engine='ddg'|'brave')")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        try:
            asyncio.run_coroutine_threadsafe(pool.aclose(), async_loop).result(timeout=2.0)
        except Exception:
            pass
        async_loop.call_soon_threadsafe(async_loop.stop)


if __name__ == "__main__":
    run()
