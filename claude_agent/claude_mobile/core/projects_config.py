"""Immutable per-project launcher snapshot.

Historically this module also held a ``ProjectsConfig`` container that was
populated once from the ``PROJECTS`` env var at startup. That container was
removed by CLA-39 — the runtime source of truth is now
:class:`claude_mobile.core.projects_store.ProjectsStore`, which hands out
``ProjectConfig`` snapshots to the launcher and the auto-scan engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProjectConfig:
    """Launcher-facing, immutable view of a single project.

    Returned by ``ProjectsStore.get`` so the launcher can capture a coherent
    snapshot of the project at session-start time and not worry about
    subsequent in-flight mutations to the store.
    """

    name: str
    path: str
    linear_api_key: str | None = None
    conda_env: str | None = None
    default_flags: dict[str, object] = field(default_factory=dict)
