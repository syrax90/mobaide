from __future__ import annotations

import asyncio
import logging

import orjson
from typing import TYPE_CHECKING

from mobaide.core.models import SessionEvent, SessionInfo, session_to_wire_dict

if TYPE_CHECKING:
    from mobaide.core.session import TerminalSession

logger = logging.getLogger(__name__)

_STATE_EVENTS = frozenset({"started", "stopped", "error"})
_TITLE_EVENT = "title"
_ATTENTION_EVENT = "attention"


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
                if event.event_type == _TITLE_EVENT and self._get_sessions:
                    if self._event_bus is not None:
                        sessions_now = self._get_sessions()
                        changed = next(
                            (s for s in sessions_now if s.session_id == self._session.session_id),
                            None,
                        )
                        if changed is not None:
                            self._event_bus.publish(
                                "session.updated",
                                {"session": session_to_wire_dict(changed)},
                            )
                    continue
                if (
                    event.event_type == _ATTENTION_EVENT
                    and self._get_sessions
                    and self._event_bus is not None
                ):
                    changed = next(
                        (s for s in self._get_sessions()
                         if s.session_id == self._session.session_id),
                        None,
                    )
                    if changed is not None:
                        wire = session_to_wire_dict(changed)
                        # A set reason is a one-shot notification trigger; a
                        # clear (attention=None) only refreshes the snapshot so
                        # connected clients drop the badge.
                        if event.attention is not None:
                            self._event_bus.publish(
                                "session.attention", {"session": wire},
                            )
                        self._event_bus.publish(
                            "session.updated", {"session": wire},
                        )
                    continue
                # Build once; reused by broadcaster and event bus to avoid double serialization.
                if event.event_type in _STATE_EVENTS and self._get_sessions:
                    sessions_now = self._get_sessions()
                    session_dicts = [session_to_wire_dict(s) for s in sessions_now]
                    if self._broadcaster:
                        await self._broadcaster.notify_serialized(session_dicts)
                    if self._event_bus is not None:
                        self._event_bus.publish(
                            "sessions.changed", {"sessions": session_dicts}
                        )
                        # Also publish a single-session delta for the changed one.
                        changed = next(
                            (d for d in session_dicts if d["session_id"] == self._session.session_id),
                            None,
                        )
                        if changed is not None:
                            self._event_bus.publish(
                                "session.updated", {"session": changed},
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

    async def notify_serialized(self, session_dicts: list[dict]) -> None:
        """Push pre-built session dicts to all subscribers (orjson, single pass)."""
        data = orjson.dumps(session_dicts).decode("utf-8")
        for q in list(self._subscribers):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass

    async def notify(self, sessions: list[SessionInfo]) -> None:
        """Serialize session list and push to all subscribers."""
        await self.notify_serialized(
            [session_to_wire_dict(s) for s in sessions],
        )
