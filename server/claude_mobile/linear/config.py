from __future__ import annotations

import os
from dataclasses import dataclass

from claude_mobile.core.projects_config import ProjectConfig, ProjectsConfig

__all__ = ["ProjectConfig", "AutoScanConfig"]


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
    auto_scan_prompt: str | None = None
    processed_items_path: str = "processed_items.json"

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
        prompt = prompt_raw.replace("\\n", "\n") if prompt_raw else None

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
            processed_items_path=os.getenv(
                "PROCESSED_ITEMS_PATH", "processed_items.json"
            ),
        )
