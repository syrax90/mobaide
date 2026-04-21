"""Unit tests for :class:`ResendRateLimiter`.

Scoped to the class's public contract (``try_acquire`` / ``reset``) and
internal invariants that are security-relevant: email normalisation, the
cooldown/hourly-cap interplay, window-based pruning, and the soft-cap
eviction. Wall-clock is swapped for a fake monotonic so the tests don't
sleep.
"""
from __future__ import annotations

from typing import Iterator

import pytest

from central_server.auth.rate_limit import ResendRateLimiter


class _FakeClock:
    """Monotonic stand-in we can tick forward deterministically."""

    def __init__(self, start: float = 10_000.0) -> None:
        self.now = start

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def __call__(self) -> float:  # duck-types ``time.monotonic``
        return self.now


@pytest.fixture
def clock(monkeypatch) -> Iterator[_FakeClock]:
    fake = _FakeClock()
    # ``ResendRateLimiter`` imports ``time`` at module load and calls
    # ``time.monotonic()`` directly, so patch the attribute on the module
    # namespace where it's looked up.
    monkeypatch.setattr(
        "central_server.auth.rate_limit.time.monotonic", fake
    )
    yield fake


class TestTryAcquireCooldown:
    async def test_first_acquire_always_allowed(self, clock: _FakeClock):
        limiter = ResendRateLimiter(cooldown_seconds=60, hourly_cap=3)
        assert await limiter.try_acquire("a@x.com") is None

    async def test_second_acquire_within_cooldown_is_blocked(
        self, clock: _FakeClock
    ):
        limiter = ResendRateLimiter(cooldown_seconds=60, hourly_cap=3)
        assert await limiter.try_acquire("a@x.com") is None

        clock.advance(10)  # still inside the 60 s cooldown
        wait = await limiter.try_acquire("a@x.com")
        assert wait is not None
        # Window remaining is 60 - 10 = 50. The limiter rounds down via
        # ``int()`` so we expect exactly 50; a tight tolerance still catches
        # off-by-one bugs.
        assert wait == 50

    async def test_acquire_after_cooldown_elapses_is_allowed(
        self, clock: _FakeClock
    ):
        limiter = ResendRateLimiter(cooldown_seconds=60, hourly_cap=3)
        assert await limiter.try_acquire("a@x.com") is None

        clock.advance(60)  # exactly at the boundary -> cooldown expired
        assert await limiter.try_acquire("a@x.com") is None

    async def test_cooldown_wait_is_floor_clamped_to_one(
        self, clock: _FakeClock
    ):
        # ``int()`` truncates fractional seconds; the limiter must never
        # return 0 when the slot is actually still blocked.
        limiter = ResendRateLimiter(cooldown_seconds=60, hourly_cap=3)
        await limiter.try_acquire("a@x.com")

        clock.advance(59.5)  # 0.5 s remaining -> int() -> 0 -> clamp to 1
        wait = await limiter.try_acquire("a@x.com")
        assert wait == 1


class TestTryAcquireHourlyCap:
    async def test_hourly_cap_blocks_after_n_acquires(self, clock: _FakeClock):
        # cooldown=0 means only the hourly cap gates.
        limiter = ResendRateLimiter(cooldown_seconds=0, hourly_cap=3)
        assert await limiter.try_acquire("a@x.com") is None
        assert await limiter.try_acquire("a@x.com") is None
        assert await limiter.try_acquire("a@x.com") is None

        # 4th within the hour is capped.
        wait = await limiter.try_acquire("a@x.com")
        assert wait is not None
        assert wait >= 1

    async def test_hourly_cap_reports_time_until_oldest_leaves_window(
        self, clock: _FakeClock
    ):
        limiter = ResendRateLimiter(
            cooldown_seconds=0, hourly_cap=3, window_seconds=3600
        )
        await limiter.try_acquire("a@x.com")  # ts = 10_000
        clock.advance(10)
        await limiter.try_acquire("a@x.com")  # ts = 10_010
        clock.advance(10)
        await limiter.try_acquire("a@x.com")  # ts = 10_020

        # Oldest ts is 10_000; now is 10_020; window is 3600. Slot frees
        # at 13_600, so the block should report ~3580 s remaining.
        wait = await limiter.try_acquire("a@x.com")
        assert wait == 3580

    async def test_hourly_cap_one_forbids_any_second_acquire_in_window(
        self, clock: _FakeClock
    ):
        limiter = ResendRateLimiter(
            cooldown_seconds=0, hourly_cap=1, window_seconds=3600
        )
        assert await limiter.try_acquire("a@x.com") is None
        # Same tick, same window -> blocked.
        assert await limiter.try_acquire("a@x.com") is not None

    async def test_window_slide_frees_up_a_slot(self, clock: _FakeClock):
        limiter = ResendRateLimiter(
            cooldown_seconds=0, hourly_cap=3, window_seconds=60
        )
        await limiter.try_acquire("a@x.com")
        await limiter.try_acquire("a@x.com")
        await limiter.try_acquire("a@x.com")
        assert await limiter.try_acquire("a@x.com") is not None

        # Slide past the window: all three old timestamps are now stale.
        clock.advance(120)
        assert await limiter.try_acquire("a@x.com") is None


class TestNormalisation:
    async def test_case_and_whitespace_are_coalesced(self, clock: _FakeClock):
        # Critical security property: a user cannot bypass their own
        # cooldown by tweaking the email casing.
        limiter = ResendRateLimiter(cooldown_seconds=60, hourly_cap=3)
        assert await limiter.try_acquire("Alice@Example.COM") is None

        # Same email, different capitalisation / whitespace — still the
        # same bucket.
        wait = await limiter.try_acquire("  alice@example.com  ")
        assert wait is not None
        assert wait >= 1

    async def test_distinct_emails_have_independent_buckets(
        self, clock: _FakeClock
    ):
        limiter = ResendRateLimiter(cooldown_seconds=60, hourly_cap=3)
        assert await limiter.try_acquire("a@x.com") is None
        # Different email; untouched bucket.
        assert await limiter.try_acquire("b@x.com") is None


class TestReset:
    async def test_reset_clears_cooldown(self, clock: _FakeClock):
        limiter = ResendRateLimiter(cooldown_seconds=60, hourly_cap=3)
        await limiter.try_acquire("a@x.com")
        assert await limiter.try_acquire("a@x.com") is not None

        await limiter.reset("a@x.com")
        # Post-reset the email is as-new — no cooldown, full cap.
        assert await limiter.try_acquire("a@x.com") is None

    async def test_reset_uses_normalised_key(self, clock: _FakeClock):
        limiter = ResendRateLimiter(cooldown_seconds=60, hourly_cap=3)
        await limiter.try_acquire("alice@x.com")
        await limiter.reset("  ALICE@X.com  ")
        assert await limiter.try_acquire("alice@x.com") is None

    async def test_reset_for_unknown_email_is_noop(self, clock: _FakeClock):
        limiter = ResendRateLimiter(cooldown_seconds=60, hourly_cap=3)
        # Must not raise, must not corrupt the state.
        await limiter.reset("never-seen@x.com")
        assert await limiter.try_acquire("never-seen@x.com") is None


class TestEviction:
    async def test_prune_drops_entries_outside_window(self, clock: _FakeClock):
        limiter = ResendRateLimiter(
            cooldown_seconds=0, hourly_cap=1_000, window_seconds=60
        )
        # Seed several emails at different points in the past.
        await limiter.try_acquire("a@x.com")
        clock.advance(120)  # push a@x.com past the window

        # Touching any email triggers _prune_entry on the normalised key.
        # After pruning, the stale ``a@x.com`` bucket should be gone from
        # internal state, and a fresh acquire succeeds cleanly.
        assert await limiter.try_acquire("a@x.com") is None
        assert "a@x.com" in limiter._state  # noqa: SLF001
        assert len(limiter._state["a@x.com"]) == 1  # noqa: SLF001

    async def test_soft_cap_evicts_oldest_entries(
        self, clock: _FakeClock, monkeypatch
    ):
        # Shrink the soft cap so we can trigger eviction in a unit test.
        monkeypatch.setattr(ResendRateLimiter, "_MAX_EMAILS", 4)
        limiter = ResendRateLimiter(
            cooldown_seconds=0, hourly_cap=100, window_seconds=3600
        )
        # Fill slightly over the cap so ``_evict_if_full`` engages.
        emails = [f"user{i}@x.com" for i in range(5)]
        for email in emails:
            clock.advance(1)  # ensure distinct last-access timestamps
            assert await limiter.try_acquire(email) is None

        # One quarter of 5 is floor(1.25) -> 1, so the oldest entry should
        # be evicted. ``len()`` settles at <= _MAX_EMAILS.
        assert len(limiter._state) <= 4  # noqa: SLF001
        # The oldest (user0) is the most likely victim.
        assert "user0@x.com" not in limiter._state  # noqa: SLF001


class TestConstructorClamping:
    async def test_negative_cooldown_is_clamped_to_zero(
        self, clock: _FakeClock
    ):
        # Defensive: config shouldn't produce negative values, but if it does,
        # the limiter must degrade to "no cooldown" rather than misbehaving.
        limiter = ResendRateLimiter(cooldown_seconds=-5, hourly_cap=3)
        assert await limiter.try_acquire("a@x.com") is None
        # No cooldown -> immediate second acquire also allowed (only hourly
        # cap gates).
        assert await limiter.try_acquire("a@x.com") is None

    async def test_window_floor_is_one_second(self, clock: _FakeClock):
        # ``max(1, window_seconds)`` guarantees we never divide by or
        # compare against zero.
        limiter = ResendRateLimiter(
            cooldown_seconds=0, hourly_cap=1, window_seconds=0
        )
        assert await limiter.try_acquire("a@x.com") is None
        # 1-second window: advancing by 2 must free up the slot.
        clock.advance(2)
        assert await limiter.try_acquire("a@x.com") is None
