"""Token-bucket rate limiter for the Linear GraphQL API.

Linear's documented complexity quota is ~1500 requests/hour per API token. We
keep a conservative cap of 1400/hour and refill continuously. The limiter is
keyed by API key so two scans sharing the same Linear token contend for one
bucket; scans on different tokens are independent.
"""

from __future__ import annotations

import asyncio
import time

__all__ = ["LinearRateLimiter"]


class _TokenBucket:
    def __init__(self, capacity: float, refill_per_sec: float) -> None:
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self.tokens = capacity
        self.updated = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self.lock:
                now = time.monotonic()
                elapsed = now - self.updated
                self.tokens = min(
                    self.capacity, self.tokens + elapsed * self.refill_per_sec
                )
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                deficit = 1 - self.tokens
                wait_for = deficit / self.refill_per_sec
            await asyncio.sleep(wait_for)


class LinearRateLimiter:
    """Per-API-key token bucket shared by all AutoScanInstances."""

    def __init__(
        self,
        capacity: float = 1400.0,
        per_seconds: float = 3600.0,
    ) -> None:
        self._capacity = capacity
        self._refill_per_sec = capacity / per_seconds
        self._buckets: dict[str, _TokenBucket] = {}
        self._buckets_lock = asyncio.Lock()

    async def acquire(self, api_key: str) -> None:
        bucket = self._buckets.get(api_key)
        if bucket is None:
            async with self._buckets_lock:
                bucket = self._buckets.get(api_key)
                if bucket is None:
                    bucket = _TokenBucket(self._capacity, self._refill_per_sec)
                    self._buckets[api_key] = bucket
        await bucket.acquire()
