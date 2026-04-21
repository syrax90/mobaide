"""In-memory rate limiter for the verification-resend endpoint.

Single-instance only. Uses ``time.monotonic()`` so wall-clock jumps can't
break the cooldown window. On process restart the dict is cleared and all
cooldowns reset — acceptable for the current single-instance deployment;
document the migration path (SQLite table or Redis) if we ever scale out.

The key is the normalised email address (``email.strip().lower()``). Rate
limits are per-email, not per-IP, which means an abuser can flood someone
else's inbox. IP-based limiting is tracked as a follow-up.
"""
from __future__ import annotations

import asyncio
import time


class ResendRateLimiter:
    """Enforce per-email cooldown + hourly cap for verification resends."""

    # Soft cap on the number of distinct emails we track. Prevents unbounded
    # growth if an attacker sprays unique emails. Oldest-touched entries get
    # pruned when exceeded.
    _MAX_EMAILS = 10_000

    def __init__(
        self,
        *,
        cooldown_seconds: int,
        hourly_cap: int,
        window_seconds: int = 3600,
    ) -> None:
        self._cooldown = max(0, cooldown_seconds)
        self._hourly_cap = max(0, hourly_cap)
        self._window = max(1, window_seconds)
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

    def _evict_if_full(self) -> None:
        if len(self._state) <= self._MAX_EMAILS:
            return
        # Drop the quarter of entries that haven't been touched recently.
        ordered = sorted(self._last_access.items(), key=lambda kv: kv[1])
        to_drop = max(1, len(ordered) // 4)
        for key, _ in ordered[:to_drop]:
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
            self._evict_if_full()
            return None

    async def reset(self, email: str) -> None:
        """Clear the rate-limit state for an email (e.g. on successful verify)."""
        key = self._normalise(email)
        async with self._lock:
            self._state.pop(key, None)
            self._last_access.pop(key, None)
