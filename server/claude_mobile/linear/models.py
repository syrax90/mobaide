from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
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
    identifier: str  # e.g. "CLA-8"
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


@dataclass
class ScanEvent:
    """Event emitted by the auto-scan service for WebSocket relay."""

    event_type: str  # scan_started, scan_completed, issue_found, issue_processed, error
    timestamp: float = field(default_factory=time.time)
    data: dict[str, Any] | None = None
