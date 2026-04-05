from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime


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


@dataclass
class SessionInfo:
    session_id: str
    state: SessionState
    created_at: datetime
    pid: int | None
    command: list[str]
    cwd: str | None


@dataclass
class SessionEvent:
    event_type: str  # "output", "started", "stopped", "error"
    session_id: str
    data: str | None = None
    timestamp: float = field(default_factory=time.time)
