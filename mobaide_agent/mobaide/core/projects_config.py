"""Immutable per-project launcher snapshot."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProjectConfig:
    """Provide a launcher-facing, immutable view of a single project.

    The launcher captures this snapshot at session-start so subsequent
    in-flight mutations to the store do not affect an active session.
    """

    name: str
    path: str
    linear_api_key: str | None = None
    conda_env: str | None = None
    default_flags: dict[str, object] = field(default_factory=dict)
