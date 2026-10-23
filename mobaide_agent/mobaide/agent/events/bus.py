from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentEvent:
    """A typed event envelope pushed to all WebSocket subscribers.

    ``type`` is one of:
        - ``sessions.changed``  — payload: {"sessions": [...]}
        - ``session.updated``   — payload: {"session": {...}}
        - ``session.attention`` — payload: {"session": {...}} (one-shot: the
          session needs the user; reason in the session's ``attention`` field)
        - ``projects.changed``  — payload: {"projects": [...]}
        - ``git.changed``       — payload: {"project_name": ..., "toplevel":
          <abs path|null>, "common_dir": <abs path|null>} (pointer, not a
          snapshot; ``toplevel``/``common_dir`` let a session whose cwd is a
          linked worktree match the event)
        - ``git.job.progress``  — payload: <full GitJobResponse, status
          "running"> (throttled to 4/s per job; every frame is the whole
          state, so a dropped one costs nothing)
        - ``git.job.done``      — payload: <full GitJobResponse, status
          "done"> (once per job, followed by ``git.changed``)
        - ``autoscan.status``   — payload: <full AutoScanInstance.to_dto()>
        - ``autoscan.event``    — payload: {"project_name": ..., "event": {...}}
        - ``autoscan.list.changed`` — payload: <full AutoScanSessionManager.list_response()>
        - ``voice.changed``     — payload: <full VoiceStatusResponse> (throttled
          to 2/s while downloading; state changes are sent at once)
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
        self._subs: set[asyncio.Queue[AgentEvent]] = set()

    def subscribe(self, maxsize: int = 256) -> asyncio.Queue[AgentEvent]:
        q: asyncio.Queue[AgentEvent] = asyncio.Queue(maxsize=maxsize)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[AgentEvent]) -> None:
        self._subs.discard(q)

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        event = AgentEvent(type=event_type, payload=payload)
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.debug("event_bus.drop type=%s (subscriber queue full)", event_type)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)
