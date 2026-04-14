from __future__ import annotations

import os
from dataclasses import dataclass

from claude_mobile.core.projects_config import ProjectConfig, ProjectsConfig

__all__ = ["ProjectConfig", "AutoScanConfig"]

_DEFAULT_AUTO_SCAN_PROMPT = (
    "Read the Linear issue matching the identifier in the current worktree name. "
    "Analyze the issue deeply. Resolve the issue (task). WORK in GOP Development Mode."
)

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _parse_bool(raw: str | None, *, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


@dataclass(frozen=True)
class AutoScanConfig:
    """Configuration for the Linear auto-scan engine.

    Loaded from environment variables, mirroring the PS1 script's .env config.
    """

    projects: dict[str, ProjectConfig]
    worktree_base: str = "claude_worktree"
    scan_interval_seconds: int = 300
    branch_prefix: str = ""
    main_branch: str = "main"
    linear_active_state: str = "In Progress"
    auto_scan_prompt: str | None = _DEFAULT_AUTO_SCAN_PROMPT
    auto_remove_worktrees: bool = True
    linear_done_state: str = "Done"
    state_dir: str = "state"

    @classmethod
    def from_env(cls) -> AutoScanConfig | None:
        """Build config from environment variables.

        Returns None if PROJECTS is not set (auto-scan not configured) or
        if no project carries a Linear API key (auto-scan needs one).
        """
        projects_config = ProjectsConfig.from_env()
        if projects_config is None:
            return None
        # Auto-scan only operates on projects that expose a Linear key.
        projects: dict[str, ProjectConfig] = {
            name: p
            for name, p in projects_config.projects.items()
            if p.linear_api_key
        }
        if not projects:
            return None

        prompt_raw = os.getenv("AUTO_SCAN_PROMPT")
        prompt = prompt_raw.replace("\\n", "\n") if prompt_raw else _DEFAULT_AUTO_SCAN_PROMPT

        return cls(
            projects=projects,
            worktree_base=os.getenv("WORKTREE_BASE", "claude_worktree"),
            scan_interval_seconds=int(
                os.getenv("SCAN_INTERVAL_SECONDS", "300"),
            ),
            branch_prefix=os.getenv("BRANCH_PREFIX", ""),
            main_branch=os.getenv("MAIN_BRANCH", "main"),
            linear_active_state=os.getenv("LINEAR_ACTIVE_STATE", "In Progress"),
            auto_scan_prompt=prompt,
            auto_remove_worktrees=_parse_bool(
                os.getenv("AUTO_REMOVE_WORKTREES"), default=True,
            ),
            linear_done_state=os.getenv("LINEAR_DONE_STATE", "Done"),
            state_dir=os.getenv("AGENT_STATE_DIR", "state"),
        )
