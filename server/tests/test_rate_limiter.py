"""Tests for LinearRateLimiter — virtual-time token bucket.

All tests use a fake monotonic clock and a fake asyncio.sleep so that no
real-time waits happen. The fake sleep advances the fake clock by its
`seconds` argument so the bucket's refill loop makes progress without
spinning forever.
"""

from __future__ import annotations

import asyncio

import pytest

from claude_mobile.linear import rate_limiter
from claude_mobile.linear.rate_limiter import LinearRateLimiter


class FakeClock:
    """Deterministic replacement for time.monotonic."""

    def __init__(self) -> None:
        self.t = 1000.0  # start at a nonzero value to catch "assumes 0" bugs

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def fake_clock(monkeypatch):
    clock = FakeClock()
    # Patch the `time` module that rate_limiter imported — we swap the
    # `monotonic` attribute on the imported module object.
    monkeypatch.setattr(rate_limiter.time, "monotonic", clock)
    return clock


@pytest.fixture
def sleep_spy(monkeypatch, fake_clock):
    """Record asyncio.sleep calls and advance the fake clock by the arg.

    Advancing the clock on each fake sleep mirrors real wall time — the
    bucket's while-loop retries, re-reads time.monotonic, sees elapsed
    time, refills tokens, and proceeds. Without this advance, the loop
    spins forever and pytest-timeout kills the test.
    """
    calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        calls.append(seconds)
        fake_clock.advance(seconds)

    monkeypatch.setattr(rate_limiter.asyncio, "sleep", fake_sleep)
    return calls


class TestBucketStartsFull:
    async def test_capacity_acquires_without_sleeping(
        self, fake_clock, sleep_spy
    ):
        del fake_clock  # installed via fixture, not used directly
        limiter = LinearRateLimiter(capacity=5, per_seconds=5)

        for _ in range(5):
            await limiter.acquire("k")

        assert sleep_spy == []


class TestOverCapacityBlocks:
    async def test_sixth_acquire_calls_sleep_with_computed_wait(
        self, fake_clock, sleep_spy
    ):
        del fake_clock
        limiter = LinearRateLimiter(capacity=5, per_seconds=5)
        # refill_per_sec = 5 / 5 = 1.0 tokens/sec → wait_for of 1 token = 1.0s

        for _ in range(5):
            await limiter.acquire("k")

        assert sleep_spy == []

        await limiter.acquire("k")

        assert len(sleep_spy) == 1
        assert sleep_spy[0] == pytest.approx(1.0, abs=1e-9)


class TestPerKeyIsolation:
    async def test_two_keys_do_not_share_budget(
        self, fake_clock, sleep_spy
    ):
        del fake_clock
        limiter = LinearRateLimiter(capacity=2, per_seconds=2)

        await limiter.acquire("keyA")
        await limiter.acquire("keyA")
        await limiter.acquire("keyB")
        await limiter.acquire("keyB")

        assert sleep_spy == []
        assert "keyA" in limiter._buckets
        assert "keyB" in limiter._buckets


class TestRefillOverTime:
    async def test_elapsed_time_replenishes_tokens(
        self, fake_clock, sleep_spy
    ):
        limiter = LinearRateLimiter(capacity=2, per_seconds=2)
        # refill_per_sec = 1.0

        await limiter.acquire("k")
        await limiter.acquire("k")

        # Advance virtual time by 1.5s → 1.5 tokens refilled (capped at 2).
        fake_clock.advance(1.5)

        await limiter.acquire("k")

        assert sleep_spy == []


class TestRefillCappedAtCapacity:
    async def test_idle_hour_does_not_exceed_capacity(
        self, fake_clock, sleep_spy
    ):
        limiter = LinearRateLimiter(capacity=3, per_seconds=3)

        # Drain the initial full bucket.
        for _ in range(3):
            await limiter.acquire("k")
        assert sleep_spy == []

        # Idle for an hour — if uncapped, tokens would explode.
        fake_clock.advance(3600.0)

        # Exactly `capacity` more should succeed without sleeping…
        for _ in range(3):
            await limiter.acquire("k")
        assert sleep_spy == []

        # …but the next one must block.
        await limiter.acquire("k")
        assert len(sleep_spy) == 1
        # refill_per_sec = 1.0 → 1 token costs 1.0s of wait.
        assert sleep_spy[0] == pytest.approx(1.0, abs=1e-9)


class TestConcurrentSameKey:
    async def test_concurrent_acquires_serialize_on_shared_bucket(
        self, fake_clock, sleep_spy
    ):
        del fake_clock
        # Huge per_seconds so refill is effectively zero over the test
        # window — concurrent acquires must contend for the 3 starting
        # tokens, not rely on refill.
        limiter = LinearRateLimiter(capacity=3, per_seconds=10_000_000.0)

        results = await asyncio.gather(
            limiter.acquire("k"),
            limiter.acquire("k"),
            limiter.acquire("k"),
        )

        assert results == [None, None, None]
        assert sleep_spy == []
        # All three shared the same bucket object.
        assert len(limiter._buckets) == 1

        # Two more on the same key — bucket is drained, so both sleep.
        await asyncio.gather(
            limiter.acquire("k"),
            limiter.acquire("k"),
        )

        assert len(sleep_spy) == 2


class TestDoubleCheckedLockingForNewKey:
    async def test_concurrent_new_key_creates_exactly_one_bucket(
        self, fake_clock, sleep_spy
    ):
        del fake_clock, sleep_spy
        limiter = LinearRateLimiter(capacity=10, per_seconds=10)

        await asyncio.gather(
            limiter.acquire("newkey"),
            limiter.acquire("newkey"),
            limiter.acquire("newkey"),
            limiter.acquire("newkey"),
            limiter.acquire("newkey"),
        )

        assert len(limiter._buckets) == 1
        assert "newkey" in limiter._buckets


class TestBucketsPerKeyAreDistinct:
    async def test_different_keys_map_to_different_bucket_objects(
        self, fake_clock, sleep_spy
    ):
        del fake_clock, sleep_spy
        limiter = LinearRateLimiter(capacity=4, per_seconds=4)

        await limiter.acquire("keyA")
        await limiter.acquire("keyB")

        bucket_a = limiter._buckets["keyA"]
        bucket_b = limiter._buckets["keyB"]
        assert bucket_a is not bucket_b


class TestDefaultConstructor:
    def test_default_capacity_and_refill_rate(self):
        limiter = LinearRateLimiter()

        assert limiter._capacity == 1400.0
        assert limiter._refill_per_sec == pytest.approx(
            1400.0 / 3600.0, abs=1e-9
        )
        assert limiter._buckets == {}


class TestInterleavedKeys:
    async def test_sequential_acquires_across_keys_do_not_interfere(
        self, fake_clock, sleep_spy
    ):
        del fake_clock
        limiter = LinearRateLimiter(capacity=2, per_seconds=2)

        await limiter.acquire("a")
        await limiter.acquire("b")
        await limiter.acquire("a")
        await limiter.acquire("b")

        # Both buckets exhausted exactly at capacity; no sleeping needed.
        assert sleep_spy == []

        # One more on either key now blocks.
        await limiter.acquire("a")
        assert len(sleep_spy) == 1
        assert sleep_spy[0] == pytest.approx(1.0, abs=1e-9)
