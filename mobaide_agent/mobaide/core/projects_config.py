"""Immutable per-project launcher snapshot."""
from __future__ import annotations

from dataclasses import dataclass, field


def _default_state_names() -> list[str]:
    return ["Proposals"]


def _default_label_names() -> list[str]:
    return ["AutoPipeline"]


@dataclass(frozen=True)
class AutoScanFilter:
    """Per-project Linear filter for Auto Scan Mode.

    Five orthogonal knobs configurable per project:

    * ``state_names`` and ``label_names`` form the **trigger** filter — issues
      with `state.name in state_names` AND `labels.name in label_names` are
      picked up by ``viewer.assignedIssues`` on each scan cycle.
    * ``active_state`` is set on the issue when the scanner claims it.
    * ``done_state`` is the terminal state that triggers worktree cleanup.
    * ``removal_label`` is stripped from the issue when claimed; it is
      conventionally also one of ``label_names`` so the issue stops matching
      the trigger filter, but the two are independent so users can preserve
      the trigger label and remove a different one.

    Defaults reproduce the historical hardcoded behavior: trigger
    ``Proposals``-state issues with the ``AutoPipeline`` label, transition to
    ``In Progress``, finish at ``Done``, strip the ``AutoPipeline`` label.
    """

    state_names: list[str] = field(default_factory=_default_state_names)
    label_names: list[str] = field(default_factory=_default_label_names)
    active_state: str = "In Progress"
    done_state: str = "Done"
    removal_label: str = "AutoPipeline"


@dataclass(frozen=True)
class ProjectConfig:
    """Provide a launcher-facing, immutable view of a single project.

    The launcher captures this snapshot at session-start so subsequent
    in-flight mutations to the store do not affect an active session.
    The ``auto_scan_filter`` field is consumed by the Linear scanner;
    that scanner re-reads it from the projects store at the top of each
    cycle, so filter changes apply on the next scan iteration without
    needing a restart.

    ``auto_scan_prompt`` is the per-project Auto Scan initial prompt, saved
    when a scan is started so the next scan for this project pre-seeds it.
    ``None`` means "fall back to the engine-wide default prompt".
    """

    name: str
    path: str
    linear_api_key: str | None = None
    conda_env: str | None = None
    default_flags: dict[str, object] = field(default_factory=dict)
    auto_scan_filter: AutoScanFilter = field(default_factory=AutoScanFilter)
    auto_scan_prompt: str | None = None


__all__ = ["AutoScanFilter", "ProjectConfig"]
