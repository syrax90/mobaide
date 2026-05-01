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
    """Async token bucket that releases its lock before sleeping.

    The lock is dropped before each ``asyncio.sleep`` so that other coroutines
    waiting on the same bucket can refill and check tokens without being
    blocked for the entire sleep duration.
    """

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
    """Manage per-API-key token buckets for Linear GraphQL requests."""

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
        """Block until one token is available for ``api_key``.

        Uses double-checked locking: a fast unsynchronised lookup avoids
        acquiring ``_buckets_lock`` on every call once the bucket exists.
        """
        bucket = self._buckets.get(api_key)
        if bucket is None:
            async with self._buckets_lock:
                bucket = self._buckets.get(api_key)
                if bucket is None:
                    bucket = _TokenBucket(self._capacity, self._refill_per_sec)
                    self._buckets[api_key] = bucket
        await bucket.acquire()
