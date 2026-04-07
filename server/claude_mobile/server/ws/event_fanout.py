from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from claude_mobile.core.models import SessionEvent, SessionInfo

if TYPE_CHECKING:
    from claude_mobile.core.session import TerminalSession

logger = logging.getLogger(__name__)

_STATE_EVENTS = frozenset({"started", "stopped", "error"})


class SessionEventFanout:
    """Reads events from a TerminalSession's queue and fans out to N subscribers.

    Also notifies the SessionListBroadcaster on state-change events.
    """

    def __init__(
        self,
        session: TerminalSession,
        broadcaster: SessionListBroadcaster | None = None,
        get_sessions: callable | None = None,
        event_bus=None,
    ) -> None:
        self._session = session
        self._broadcaster = broadcaster
        self._get_sessions = get_sessions
        self._event_bus = event_bus
        self._subscribers: set[asyncio.Queue[SessionEvent]] = set()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._pump(),
                name=f"fanout-{self._session.session_id}",
            )

    async def _pump(self) -> None:
        try:
            while True:
                event = await self._session.event_queue.get()
                for q in list(self._subscribers):
                    try:
                        q.put_nowait(event)
                    except asyncio.QueueFull:
                        pass  # drop for slow consumers
                # Notify session-list broadcaster on state changes
                if event.event_type in _STATE_EVENTS and self._get_sessions:
                    sessions_now = self._get_sessions()
                    if self._broadcaster:
                        await self._broadcaster.notify(sessions_now)
                    if self._event_bus is not None:
                        serialized = [
                            {
                                "session_id": s.session_id,
                                "state": s.state.value,
                                "created_at": s.created_at.isoformat(),
                                "pid": s.pid,
                                "command": s.command,
                                "cwd": s.cwd,
                            }
                            for s in sessions_now
                        ]
                        self._event_bus.publish(
                            "sessions.changed", {"sessions": serialized}
                        )
                        # Also publish a single-session delta for the changed one.
                        changed = next(
                            (s for s in sessions_now if s.session_id == self._session.session_id),
                            None,
                        )
                        if changed is not None:
                            self._event_bus.publish(
                                "session.updated",
                                {
                                    "session": {
                                        "session_id": changed.session_id,
                                        "state": changed.state.value,
                                        "created_at": changed.created_at.isoformat(),
                                        "pid": changed.pid,
                                        "command": changed.command,
                                        "cwd": changed.cwd,
                                    }
                                },
                            )
        except asyncio.CancelledError:
            return

    def subscribe(self) -> asyncio.Queue[SessionEvent]:
        q: asyncio.Queue[SessionEvent] = asyncio.Queue(maxsize=1000)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[SessionEvent]) -> None:
        self._subscribers.discard(q)

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    @property
    def has_subscribers(self) -> bool:
        return len(self._subscribers) > 0


class SessionListBroadcaster:
    """Pub-sub hub that pushes session list updates to WebSocket subscribers."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        self._subscribers.discard(q)

    async def notify(self, sessions: list[SessionInfo]) -> None:
        """Serialize session list and push to all subscribers."""
        data = json.dumps(
            [
                {
                    "session_id": s.session_id,
                    "state": s.state.value,
                    "created_at": s.created_at.isoformat(),
                    "pid": s.pid,
                    "command": s.command,
                    "cwd": s.cwd,
                }
                for s in sessions
            ],
        )
        for q in list(self._subscribers):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass
