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


class TestRefund:
    async def test_refund_gives_back_the_reserved_slot(self, clock: _FakeClock):
        limiter = ResendRateLimiter(cooldown_seconds=0, hourly_cap=2)
        assert await limiter.try_acquire("a@x.com") is None
        assert await limiter.try_acquire("a@x.com") is None
        assert await limiter.try_acquire("a@x.com") is not None

        await limiter.refund("a@x.com")
        assert await limiter.try_acquire("a@x.com") is None
        assert await limiter.try_acquire("a@x.com") is not None

    async def test_refund_keeps_older_charges(self, clock: _FakeClock):
        limiter = ResendRateLimiter(cooldown_seconds=0, hourly_cap=2)
        await limiter.try_acquire("a@x.com")
        await limiter.try_acquire("a@x.com")
        await limiter.refund("a@x.com")

        # One charge is still on the books, so only one more slot is free.
        assert await limiter.try_acquire("a@x.com") is None
        assert await limiter.try_acquire("a@x.com") is not None


class TestEviction:

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

        # 5 keys is over the cap of 4, so eviction frees down to the
        # low-water mark 4 - 4//4 = 3. The oldest entries go first.
        assert len(limiter._state) == 3  # noqa: SLF001
        # The oldest (user0) is the most likely victim.
        assert "user0@x.com" not in limiter._state  # noqa: SLF001

    async def test_max_keys_overrides_the_soft_cap(
        self, clock: _FakeClock, monkeypatch
    ):
        monkeypatch.setattr(ResendRateLimiter, "_MAX_EMAILS", 4)
        limiter = ResendRateLimiter(
            cooldown_seconds=0, hourly_cap=1, window_seconds=3600, max_keys=10
        )
        await limiter.try_acquire("victim@x.com")
        for i in range(8):
            clock.advance(1)
            await limiter.try_acquire(f"spray{i}@x.com")

        # Nine keys is past the class cap but under ``max_keys``: the oldest
        # bucket keeps its charge and is still blocked.
        assert "victim@x.com" in limiter._state  # noqa: SLF001
        assert await limiter.try_acquire("victim@x.com") is not None


    async def test_full_bucket_survives_a_spray_of_one_hit_keys(
        self, clock: _FakeClock
    ):
        limiter = ResendRateLimiter(
            cooldown_seconds=0, hourly_cap=3, window_seconds=3600, max_keys=4
        )
        for _ in range(3):
            assert await limiter.try_acquire("victim@x.com") is None
        assert await limiter.try_acquire("victim@x.com") is not None

        # The victim is the least recently touched key, so a pure LRU
        # eviction would drop it first and hand back a fresh quota.
        for i in range(5):
            clock.advance(1)
            assert await limiter.try_acquire(f"spray{i}@x.com") is None

        assert len(limiter._state["victim@x.com"]) == 3  # noqa: SLF001
        assert await limiter.try_acquire("victim@x.com") is not None

    async def test_expired_keys_are_dropped_before_any_live_key(
        self, clock: _FakeClock
    ):
        limiter = ResendRateLimiter(
            cooldown_seconds=0, hourly_cap=3, window_seconds=60, max_keys=4
        )
        # Stale buckets with more hits than any live key: the count-based
        # pass alone would keep them and drop live one-hit keys instead.
        stale = ["stale0@x.com", "stale1@x.com"]
        for email in stale:
            for _ in range(3):
                await limiter.try_acquire(email)
        clock.advance(120)

        # Five keys against a bound of four; the low-water mark is three, so
        # dropping both expired buckets is enough and no live key goes.
        live = [f"live{i}@x.com" for i in range(3)]
        for email in live:
            clock.advance(1)
            assert await limiter.try_acquire(email) is None

        for email in stale:
            assert email not in limiter._state  # noqa: SLF001
            assert email not in limiter._last_access  # noqa: SLF001
        assert sorted(limiter._state) == sorted(live)  # noqa: SLF001

    async def test_equal_count_keys_evict_least_recently_touched_first(
        self, clock: _FakeClock
    ):
        limiter = ResendRateLimiter(
            cooldown_seconds=0, hourly_cap=100, window_seconds=3600, max_keys=8
        )
        keys = [f"k{i}@x.com" for i in range(8)]
        for email in keys:
            clock.advance(1)
            await limiter.try_acquire(email)
        # Second hit in reverse order: k7 was inserted last but is now the
        # least recently touched of the two-hit keys.
        for email in reversed(keys):
            clock.advance(1)
            await limiter.try_acquire(email)

        clock.advance(1)
        await limiter.try_acquire("new@x.com")

        # Nine keys down to the low-water mark of six: the one-hit newcomer,
        # then the two least recently touched two-hit keys. k7 and k6 were
        # inserted last, so insertion order would have kept them.
        assert "new@x.com" not in limiter._state  # noqa: SLF001
        assert "k7@x.com" not in limiter._state  # noqa: SLF001
        assert "k6@x.com" not in limiter._state  # noqa: SLF001
        assert sorted(limiter._state) == sorted(keys[:6])  # noqa: SLF001

    async def test_eviction_frees_down_to_the_low_water_mark(
        self, clock: _FakeClock
    ):
        limiter = ResendRateLimiter(
            cooldown_seconds=0, hourly_cap=100, window_seconds=3600, max_keys=8
        )
        keys = [f"k{i}@x.com" for i in range(9)]
        for email in keys:
            clock.advance(1)
            assert await limiter.try_acquire(email) is None

        # One pass past the bound of eight frees max_keys // 4 slots, not one.
        assert len(limiter._state) <= 8 - 8 // 4  # noqa: SLF001
        assert sorted(limiter._state) == sorted(keys[3:])  # noqa: SLF001

        # The freed headroom absorbs the next inserts without another pass.
        later = ["a@x.com", "b@x.com"]
        for email in later:
            clock.advance(1)
            assert await limiter.try_acquire(email) is None
        assert sorted(limiter._state) == sorted(keys[3:] + later)  # noqa: SLF001

    async def test_ranking_uses_live_count_not_expired_history(
        self, clock: _FakeClock
    ):
        limiter = ResendRateLimiter(
            cooldown_seconds=0, hourly_cap=100, window_seconds=60, max_keys=4
        )
        for _ in range(3):
            await limiter.try_acquire("noisy@x.com")  # t = 0, expires by t = 70
        clock.advance(40)
        for _ in range(2):
            await limiter.try_acquire("pair@x.com")
        fillers = ["f0@x.com", "f1@x.com"]
        for email in fillers:
            for _ in range(3):
                await limiter.try_acquire(email)
        clock.advance(10)
        # Fresh hit while the old three are still in the window, so the
        # bucket holds four timestamps and is touched after ``pair``.
        await limiter.try_acquire("noisy@x.com")
        assert len(limiter._state["noisy@x.com"]) == 4  # noqa: SLF001

        clock.advance(20)
        await limiter.try_acquire("trigger@x.com")

        # ``noisy`` has one live hit and ``pair`` two. Ranking by stored
        # length (4 vs 2) or by last access would both drop ``pair``.
        assert "noisy@x.com" not in limiter._state  # noqa: SLF001
        assert len(limiter._state["pair@x.com"]) == 2  # noqa: SLF001
        assert sorted(limiter._state) == sorted(  # noqa: SLF001
            ["pair@x.com", *fillers]
        )


