from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServerEvent:
    """A typed event envelope pushed to all WebSocket subscribers.

    ``type`` is one of:
        - ``sessions.changed``  — payload: {"sessions": [...]}
        - ``session.updated``   — payload: {"session": {...}}
        - ``projects.changed``  — payload: {"projects": [...]}
        - ``autoscan.status``   — payload: <full status dict>
        - ``autoscan.event``    — payload: {"event_type": ..., "timestamp": ..., "data": ...}
    """

    type: str
    payload: dict[str, Any]


class EventBus:
    """In-process asyncio pub/sub.

    Slow consumers are silently dropped (queue full → no-op). This is safe
    because every snapshot envelope (``sessions.changed``, ``projects.changed``,
    ``autoscan.status``) carries the full state, so a client that misses one
    event will resync on the next published snapshot or on reconnect.
    """

    def __init__(self) -> None:
        self._subs: set[asyncio.Queue[ServerEvent]] = set()

    def subscribe(self, maxsize: int = 256) -> asyncio.Queue[ServerEvent]:
        q: asyncio.Queue[ServerEvent] = asyncio.Queue(maxsize=maxsize)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[ServerEvent]) -> None:
        self._subs.discard(q)

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        event = ServerEvent(type=event_type, payload=payload)
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.debug("event_bus.drop type=%s (subscriber queue full)", event_type)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)
