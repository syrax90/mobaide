"""Immutable per-workspace launcher snapshot."""
from __future__ import annotations

from dataclasses import dataclass, field


def _default_state_names() -> list[str]:
    return ["Proposals"]


def _default_label_names() -> list[str]:
    return ["AutoPipeline"]


@dataclass(frozen=True)
class AutoScanFilter:
    """Per-workspace Linear filter for Auto Scan Mode.

    Five orthogonal knobs configurable per workspace:

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
class GitHubScanFilter:
    """Per-workspace GitHub filter for Auto Scan Mode.

    * ``labels``: an open issue of the workspace's repository that carries at
      least one of these labels is picked up. The scan removes them from the
      issue when it takes it, so the issue is not picked up again.
    * ``assignee``: ``None`` takes issues of any assignee; ``@me`` takes the
      issues assigned to the signed-in ``gh`` account; any other value is a
      GitHub login.
    * ``active_label``: added to the issue when the scan takes it; ``None``
      adds nothing.
    * ``issue_types``: when not empty, only issues of one of these GitHub
      issue types (Bug, Task, Feature…) are taken; names match without
      regard to case. Empty takes issues of any type, and issues with none.

    A closed issue is done: its worktree is removed.
    """

    labels: list[str] = field(default_factory=list)
    assignee: str | None = None
    active_label: str | None = None
    issue_types: list[str] = field(default_factory=list)


AUTO_SCAN_SOURCES = ("linear", "github")
# How an Auto Scan opens each issue's session: the terminal or the chat view.
AUTO_SCAN_UIS = ("terminal", "chat")
# The wait between two Auto Scan cycles a client may choose, in minutes.
AUTO_SCAN_INTERVAL_MIN_MINUTES = 1
AUTO_SCAN_INTERVAL_MAX_MINUTES = 1440


def valid_auto_scan_interval(value: object) -> int | None:
    """``value`` when it is a whole number of minutes a scan may wait, else ``None``."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if AUTO_SCAN_INTERVAL_MIN_MINUTES <= value <= AUTO_SCAN_INTERVAL_MAX_MINUTES:
        return value
    return None

# The launch options a workspace remembers per agent kind for Auto Scan, with
# the JSON type each takes: ``bool``, ``text`` (a string or ``null``) or
# ``texts`` (a list of strings). The agent's wire models validate the values;
# this table only keeps a stored record readable.
AUTO_SCAN_OPTION_FIELDS: dict[str, dict[str, str]] = {
    "claude": {
        "plan_mode": "bool", "no_memory": "bool", "skip_permissions": "bool",
        "model": "text", "effort": "text", "fast_mode": "bool", "ultracode": "bool",
        "append_system_prompt": "text", "debug": "bool", "focus_mode": "bool",
        "code_mode": "bool",
    },
    "codex": {
        "model": "text", "sandbox": "text", "approval": "text",
        "reasoning_effort": "text", "full_auto": "bool",
    },
    "antigravity": {
        "model": "text", "skip_permissions": "bool", "sandbox": "bool", "add_dirs": "texts",
    },
    "opencode": {"auto_approve": "bool", "code_mode": "bool"},
    "copilot": {"allow_all": "bool"},
    "cursor": {"force": "bool"},
    "junie": {"plan_mode": "bool", "brave": "bool"},
    "pi": {"code_mode": "bool"},
}


def clean_auto_scan_options(cli: str, data: object) -> dict[str, object] | None:
    """The fields of ``data`` that ``AUTO_SCAN_OPTION_FIELDS[cli]`` lists, with
    the right JSON type; ``None`` for an unknown kind or a non-object."""
    fields = AUTO_SCAN_OPTION_FIELDS.get(cli)
    if fields is None or not isinstance(data, dict):
        return None
    clean: dict[str, object] = {}
    for key, kind in fields.items():
        value = data.get(key)
        if kind == "bool" and isinstance(value, bool):
            clean[key] = value
        elif kind == "text" and (value is None or isinstance(value, str)) and key in data:
            clean[key] = value
        elif kind == "texts" and isinstance(value, list) and all(
            isinstance(item, str) for item in value
        ):
            clean[key] = list(value)
    return clean


@dataclass(frozen=True)
class WorkspaceConfig:
    """Provide a launcher-facing, immutable view of a single workspace.

    The launcher captures this snapshot at session-start so subsequent
    in-flight mutations to the store do not affect an active session.
    The ``auto_scan_filter`` field is consumed by the Linear scanner;
    that scanner re-reads it from the workspaces store at the top of each
    cycle, so filter changes apply on the next scan iteration without
    needing a restart.

    ``auto_scan_prompt`` is the per-workspace Auto Scan initial prompt, saved
    when a scan is started so the next scan for this workspace pre-seeds it.
    ``None`` means "fall back to the engine-wide default prompt".

    The last Auto Scan start also leaves ``auto_scan_source`` (``linear`` or
    ``github``), ``auto_scan_cli`` (the agent kind), the source's filter and,
    in ``auto_scan_options``, that agent's launch options (by agent kind),
    ``auto_scan_ui`` (``terminal`` or ``chat``) and
    ``auto_scan_interval_minutes`` (the wait between cycles).
    ``linear_filter_set`` is ``False`` while the Linear filter holds only the
    legacy defaults nobody chose; ``auto_scan_filter`` then still carries them
    for clients and scans that predate the choice.
    """

    name: str
    path: str
    linear_api_key: str | None = None
    conda_env: str | None = None
    default_flags: dict[str, object] = field(default_factory=dict)
    auto_scan_filter: AutoScanFilter = field(default_factory=AutoScanFilter)
    auto_scan_prompt: str | None = None
    linear_filter_set: bool = False
    github_scan_filter: GitHubScanFilter | None = None
    auto_scan_source: str | None = None
    auto_scan_cli: str | None = None
    auto_scan_options: dict[str, dict[str, object]] = field(default_factory=dict)
    auto_scan_ui: str | None = None
    auto_scan_interval_minutes: int | None = None


__all__ = [
    "AUTO_SCAN_INTERVAL_MAX_MINUTES",
    "AUTO_SCAN_INTERVAL_MIN_MINUTES",
    "AUTO_SCAN_OPTION_FIELDS",
    "AUTO_SCAN_SOURCES",
    "AUTO_SCAN_UIS",
    "AutoScanFilter",
    "GitHubScanFilter",
    "WorkspaceConfig",
    "clean_auto_scan_options",
    "valid_auto_scan_interval",
]
