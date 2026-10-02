from __future__ import annotations

import asyncio
import json
import os

from .ddg.engine import DuckDuckGoEngine
from .models import SearchQuery
from .net import ProxyPool

async def main() -> None:
    with open("queries.txt", encoding="utf8") as f:
        queries = [line.strip() for line in f if line.strip()]

    proxies = [p for p in os.environ.get("PROXY_URLS", "").split(",") if p]
    concurrency = int(os.environ.get("CONCURRENCY", "3"))
    min_interval = float(os.environ.get("MIN_INTERVAL_S", "1.5"))  # per proxy
    cooldown = float(os.environ.get("COOLDOWN_S", str(30 * 60)))

    pool = ProxyPool(proxies, min_interval, cooldown)
    engine = DuckDuckGoEngine(pool)
    sem = asyncio.Semaphore(concurrency)

    async def one(query: str):
        async with sem:
            return await engine.search(SearchQuery(query, region="us-en"))

    try:
        out = await asyncio.gather(*(one(q) for q in queries))
    finally:
        await pool.aclose()

    total = len(out)
    if total == 0:
        print("queries.txt is empty")
        return

    def count(status: str) -> int:
        return sum(1 for r in out if r.status == status)

    latencies = sorted(r.latency_ms for r in out)
    summary = {
        "total": total,
        "ok": count("ok"),
        "empty": count("empty"),
        "blocked": count("blocked"),
        "error": count("error"),
        "success_rate": (count("ok") + count("empty")) / total,
        "avg_attempts": sum(r.attempts for r in out) / total,
        "p50_latency_ms": latencies[total // 2],
        "p95_latency_ms": latencies[min(total - 1, int(total * 0.95))],
        "proxies_cooling": pool.cooling_count(),
    }
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
