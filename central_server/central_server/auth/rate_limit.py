"""In-memory rate limiter for the verification-resend endpoint.

Single-instance only. Uses ``time.monotonic()`` so wall-clock jumps can't
break the cooldown window. On process restart the dict is cleared and all
cooldowns reset — acceptable for the current single-instance deployment;
document the migration path (SQLite table or Redis) if we ever scale out.

The key is normalised with ``strip().lower()``. Most callers key by email;
the per-IP instances key by ``ip:<address>`` (see ``app.py``).
"""
from __future__ import annotations

import asyncio
import time


class ResendRateLimiter:
    """Enforce per-email cooldown + hourly cap for verification resends."""

    # Default soft cap on the number of distinct keys we track (``max_keys``
    # overrides it). Prevents unbounded growth if an attacker sprays unique
    # keys; see ``_evict_if_full`` for what goes first.
    _MAX_EMAILS = 10_000

    def __init__(
        self,
        *,
        cooldown_seconds: int,
        hourly_cap: int,
        window_seconds: int = 3600,
        max_keys: int | None = None,
    ) -> None:
        self._cooldown = max(0, cooldown_seconds)
        self._hourly_cap = max(0, hourly_cap)
        self._window = max(1, window_seconds)
        # Eviction resets a key's history, so a limiter that guards secrets
        # needs a bound an attacker cannot cheaply flush past.
        self._max_keys = max_keys
        self._state: dict[str, list[float]] = {}
        self._last_access: dict[str, float] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _normalise(email: str) -> str:
        return email.strip().lower()

    def _prune_entry(self, key: str, now: float) -> None:
        window_start = now - self._window
        entry = self._state.get(key, [])
        fresh = [t for t in entry if t >= window_start]
        if fresh:
            self._state[key] = fresh
        else:
            self._state.pop(key, None)
            self._last_access.pop(key, None)

    def _evict_if_full(self, now: float) -> None:
        limit = self._max_keys if self._max_keys is not None else self._MAX_EMAILS
        if len(self._state) <= limit:
            return
        for key in list(self._state):
            self._prune_entry(key, now)
        # Evict down to three quarters of the bound, not to the bound, so
        # one full pass pays for many inserts instead of running on each.
        target = limit - limit // 4
        excess = len(self._state) - target
        if excess <= 0:
            return
        # Emptiest buckets first, then the least recently touched: a spray
        # of one-hit keys goes before a bucket near its cap, so a flood of
        # junk keys cannot cheaply wipe a victim's count.
        ordered = sorted(
            self._last_access.items(),
            key=lambda kv: (len(self._state.get(kv[0], ())), kv[1]),
        )
        for key, _ in ordered[:excess]:
            self._state.pop(key, None)
            self._last_access.pop(key, None)

    async def try_acquire(self, email: str) -> int | None:
        """Reserve a resend slot for ``email``.

        Returns:
            ``None`` if the send is allowed (slot reserved).
            An ``int`` seconds-to-wait if the send is blocked:
              - ``1..cooldown`` seconds for cooldown
              - ``window_seconds`` (or remaining) for hourly-cap
        """
        key = self._normalise(email)
        now = time.monotonic()

        async with self._lock:
            self._prune_entry(key, now)
            entry = self._state.get(key, [])

            if entry:
                last = entry[-1]
                elapsed = now - last
                if elapsed < self._cooldown:
                    return max(1, int(self._cooldown - elapsed) or 1)

            if len(entry) >= self._hourly_cap and self._hourly_cap > 0:
                # Next slot frees up when the oldest timestamp leaves the window.
                oldest = entry[0]
                wait = int(self._window - (now - oldest))
                return max(1, wait or 1)

            entry.append(now)
            self._state[key] = entry
            self._last_access[key] = now
            self._evict_if_full(now)
            return None

    async def peek(self, email: str) -> int | None:
        """Report whether ``email`` is out of quota, without reserving a slot.

        The read-only twin of ``try_acquire``. A caller that charges the
        bucket only on some outcomes (the device-code circuit breaker
        charges failed redeems, not successful pairings) still has to
        refuse while the bucket is full — and using ``try_acquire`` for
        that check would charge every caller, which is exactly what it is
        trying to avoid.

        Returns seconds-to-wait when exhausted, else ``None``.
        """
        key = self._normalise(email)
        now = time.monotonic()
        async with self._lock:
            self._prune_entry(key, now)
            entry = self._state.get(key, [])
            if len(entry) >= self._hourly_cap and self._hourly_cap > 0:
                oldest = entry[0]
                wait = int(self._window - (now - oldest))
                return max(1, wait or 1)
            return None

    async def refund(self, email: str) -> None:
        """Give back one slot that ``try_acquire`` reserved for ``email``.

        For a caller that charges only some outcomes: reserving up front and
        refunding the rest keeps the check and the charge atomic, so a burst
        of concurrent requests cannot all pass a ``peek`` before any of them
        is charged.
        """
        key = self._normalise(email)
        async with self._lock:
            entry = self._state.get(key)
            if not entry:
                return
            entry.pop()
            if not entry:
                self._state.pop(key, None)
                self._last_access.pop(key, None)

    async def reset(self, email: str) -> None:
        """Clear the rate-limit state for an email."""
        key = self._normalise(email)
        async with self._lock:
            self._state.pop(key, None)
            self._last_access.pop(key, None)
