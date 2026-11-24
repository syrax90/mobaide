"""A hand-written sleep-lock backend for the keep-awake tests.

Shared by ``test_agent_settings_service.py`` and
``test_agent_settings_router.py``: it stands in for the macOS, Windows and
Linux backends, so no test ever takes a real OS sleep lock.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable


class FakeBackend:
    """``refuse`` makes every acquire raise it; with a ``gate`` an acquire
    waits on it, like a slow inhibitor. ``lose`` and ``regain`` act like
    the OS taking the lock away and giving it back. ``bridges`` counts the
    restart bridges."""

    def __init__(self) -> None:
        self.on_change: Callable[[], None] | None = None
        self.held = False
        self.error: str | None = None
        self.note: str | None = None
        self.refuse: Exception | None = None
        self.gate: asyncio.Event | None = None
        self.acquires = 0
        self.cancelled = 0
        self.bridges = 0

    async def acquire(self) -> None:
        self.acquires += 1
        if self.gate is not None:
            try:
                await self.gate.wait()
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
        if self.refuse is not None:
            raise self.refuse
        self.held = True

    async def release(self) -> None:
        self.held = False

    async def bridge(self) -> None:
        self.bridges += 1

    def lose(self, error: str) -> None:
        self.held, self.error = False, error
        assert self.on_change is not None
        self.on_change()

    def regain(self) -> None:
        self.held, self.error = True, None
        assert self.on_change is not None
        self.on_change()
