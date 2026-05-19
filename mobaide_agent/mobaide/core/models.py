from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .claude_launcher import ClaudeOptions  # noqa: F401


class SessionState(enum.Enum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    CRASHED = "crashed"


@dataclass
class SessionConfig:
    session_id: str
    command: list[str]
    cwd: str | None = None
    env: dict[str, str] | None = None
    rows: int = 24
    cols: int = 80
    scrollback_size: int = 10_000
    # When set, ``SessionManager`` resolves the project and uses
    # ``ClaudeLauncher`` to build argv/cwd/env, overwriting the raw
    # fields above before the PTY is spawned.
    project_name: str | None = None
    claude_options: ClaudeOptions | None = None


@dataclass
class SessionInfo:
    session_id: str
    state: SessionState
    created_at: datetime
    pid: int | None
    command: list[str]
    cwd: str | None
    title: str | None = None
    project_name: str | None = None


@dataclass
class SessionEvent:
    event_type: str  # "output", "started", "stopped", "error", "title"
    session_id: str
    # Output events carry raw PTY bytes; other event types leave this None.
    data: bytes | None = None
    timestamp: float = field(default_factory=time.time)


def session_to_wire_dict(info: SessionInfo) -> dict[str, Any]:
    """Canonical wire shape for a single session, shared by every producer.

    Every websocket envelope that contains a session object (``sessions.changed``,
    ``session.updated``, the ``/ws/sessions`` snapshot, the ``/sessions`` REST
    list, the SessionManager publish path) MUST build the dict through this
    helper. Adding or renaming a field here updates every producer in one
    place, preventing the drift that previously hid behind five duplicated
    literal dict-comprehensions.
    """
    return {
        "session_id": info.session_id,
        "state": info.state.value,
        "created_at": info.created_at.isoformat(),
        "pid": info.pid,
        "command": info.command,
        "cwd": info.cwd,
        "title": info.title,
        "project_name": info.project_name,
    }
