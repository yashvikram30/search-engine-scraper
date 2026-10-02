from __future__ import annotations

import random
import time
from dataclasses import dataclass

import httpx

from .models import SearchStatus

def backoff(attempt: int, base: float = 1.0) -> float:
    """Exponential backoff with jitter, in seconds, capped at 30."""
    return min(30.0, base * 2**attempt) + random.random() * 0.5 * base

class PoolExhaustedError(Exception):
    def __init__(self) -> None:
        super().__init__("All proxies are cooling down")

@dataclass
class PoolEntry:
    url: str
    client: httpx.AsyncClient
    next_free_at: float = 0.0  # earliest time this proxy may send again
    cooldown_until: float = 0.0  # quarantine after a block
    consecutive_errors: int = 0

class ProxyPool:
    """Per proxy pacing and quarantine.

    Each proxy gets its own minimum gap between requests, so more proxies means
    more throughput. A proxy that gets blocked sits out for `cooldown` seconds,
    since DDG may keep an IP on a block list for a while (SearXNG needed about
    an hour). With no proxies it sends direct requests and never quarantines.

    Times are in seconds. `transport` is only for tests.
    """

    def __init__(
        self,
        urls: list[str],
        min_interval: float,
        cooldown: float = 30 * 60,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._direct = not urls
        self._min_interval = min_interval
        self._cooldown = cooldown
        sources: list[str | None] = [None] if self._direct else list(urls)
        self._entries = [
            PoolEntry(
                url=url or "direct",
                # no redirects: a redirect is a signal, not a route
                client=httpx.AsyncClient(
                    proxy=url, transport=transport, follow_redirects=False
                ),
            )
            for url in sources
        ]

    def acquire(self) -> tuple[PoolEntry, float]:
        """Pick the proxy that is free soonest and reserve its next slot.

        Returns the entry and how long the caller should wait before sending.
        There is no await in here, so concurrent tasks never get the same slot.
        """
        now = time.monotonic()
        live = [e for e in self._entries if e.cooldown_until <= now]
        if not live:
            raise PoolExhaustedError()

        best = min(live, key=lambda e: e.next_free_at)
        start_at = max(now, best.next_free_at)
        jitter = random.random() * self._min_interval * 0.5
        best.next_free_at = start_at + self._min_interval + jitter
        return best, start_at - now

    def report(self, entry: PoolEntry, status: SearchStatus) -> None:
        if status in ("ok", "empty"):
            entry.consecutive_errors = 0
            return
        if self._direct:
            return

        if status == "blocked":
            entry.cooldown_until = time.monotonic() + self._cooldown
            return
        # errors and timeouts: three in a row also sends the proxy to cooldown
        entry.consecutive_errors += 1
        if entry.consecutive_errors >= 3:
            entry.cooldown_until = time.monotonic() + self._cooldown
            entry.consecutive_errors = 0

    def cooling_count(self) -> int:
        now = time.monotonic()
        return sum(1 for e in self._entries if e.cooldown_until > now)

    async def aclose(self) -> None:
        for e in self._entries:
            await e.client.aclose()

class TtlCache:
    def __init__(self, ttl: float) -> None:
        self._ttl = ttl
        self._store: dict[str, tuple[object, float]] = {}

    def get(self, key: str):
        hit = self._store.get(key)
        if hit is None:
            return None
        value, expires = hit
        if expires < time.monotonic():
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: object) -> None:
        self._store[key] = (value, time.monotonic() + self._ttl)
