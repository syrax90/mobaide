from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class IssueState:
    """Linear workflow state."""

    id: str
    name: str


@dataclass
class IssueLabel:
    """Linear issue label."""

    id: str
    name: str


@dataclass
class LinearIssue:
    """A Linear issue returned by the auto-scan query."""

    id: str  # Linear internal UUID
    identifier: str  # e.g. "TEAM-8"
    title: str
    description: str | None
    state: IssueState
    labels: list[IssueLabel]
    team_states: list[IssueState]  # workflow states from the issue's team


class ScanStatus(enum.Enum):
    IDLE = "idle"
    SCANNING = "scanning"
    STOPPED = "stopped"
    ERROR = "error"


SCAN_EVENT_VERSION = 1


@dataclass
class ScanPhasePayload:
    phase: str  # "scan_started" | "scan_completed" | "scan_stopped"
    cycle_number: int
    next_scan_at: str | None = None  # ISO8601 UTC
    issues_processed: int = 0  # count of issues processed in the just-completed cycle


@dataclass
class ScanIssuePayload:
    identifier: str
    title: str
    step: str  # "state_update" | "label_removal" | "worktree_create" | "session_spawn"
    status: str  # "started" | "ok" | "failed"
    error: str | None = None


@dataclass
class ScanWorktreePayload:
    identifier: str
    path: str


@dataclass
class ScanSessionPayload:
    identifier: str
    session_id: str
    pid: int | None = None


@dataclass
class ScanLogPayload:
    line: str
    level: str = "info"  # info | warn | error
    ansi: bool = False


@dataclass
class ScanEvent:
    """Structured event emitted by an AutoScanInstance.

    Wire format: { version, seq, ts, type, payload }.
    `type` discriminates `payload`: phase|issue|worktree|session|log.
    """

    seq: int
    ts: str  # ISO8601 UTC
    type: str  # "phase" | "issue" | "worktree" | "session" | "log"
    payload: dict[str, Any]
    version: int = SCAN_EVENT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "seq": self.seq,
            "ts": self.ts,
            "type": self.type,
            "payload": self.payload,
        }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
