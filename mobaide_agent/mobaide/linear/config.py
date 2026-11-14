from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from mobaide.core.projects_config import ProjectConfig

__all__ = ["ProjectConfig", "AutoScanConfig"]

_DEFAULT_AUTO_SCAN_PROMPT = (
    "Read the Linear issue matching the identifier in the current worktree name. "
    "Analyze the issue deeply. Resolve the issue (task)."
)

_TRUTHY = frozenset({"1", "true", "yes", "on"})


class AutoScanConfig(BaseSettings):
    """Configuration for the Linear auto-scan engine.

    Holds only the non-project settings (intervals, prompt, worktree folders,
    Linear workflow state names). The list of projects eligible for auto-scan
    is read live from :class:`mobaide.core.projects_store.ProjectsStore`
    by :class:`mobaide.linear.scanner.AutoScanSessionManager` — adding
    a project with a ``linear_api_key`` after boot makes it immediately
    available for auto-scan without a restart.
    """

    # Folder that holds every worktree, one sub-folder per project. Empty
    # means ``~/.mobaide/worktrees`` (``%USERPROFILE%\\.mobaide\\worktrees``
    # on Windows); see :meth:`resolved_worktrees_dir`.
    worktrees_dir: str | None = Field(
        default=None, validation_alias="MOBAIDE_WORKTREES_DIR",
    )
    # Folder inside the project root where earlier agent versions put
    # worktrees. New worktrees never go there; an old one is still reused.
    worktree_base: str = "mobaide_worktree"
    scan_interval_seconds: int = 300
    branch_prefix: str = ""
    main_branch: str = "main"
    auto_scan_prompt: str | None = _DEFAULT_AUTO_SCAN_PROMPT
    auto_remove_worktrees: bool = True
    state_dir: str = Field(default="state", validation_alias="AGENT_STATE_DIR")

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=None,
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        case_sensitive=False,
        # Without this flag, pydantic-settings honors only ``validation_alias``
        # values (e.g. ``AGENT_STATE_DIR`` for ``state_dir``) and silently
        # ignores constructor kwargs that use the field's Python name.
        # Production code (``AutoScanSessionManager``) and tests both
        # construct ``AutoScanConfig`` by name, so ``populate_by_name`` must
        # remain True.
        populate_by_name=True,
    )

    @field_validator("worktrees_dir", mode="before")
    @classmethod
    def _empty_dir_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    def resolved_worktrees_dir(self) -> str:
        """Absolute folder that holds the worktrees of every project.

        ``~`` and environment variables in a configured value are expanded,
        and a relative value is taken from the home folder. With no value,
        an absolute ``worktree_base`` is used, else ``~/.mobaide/worktrees``;
        a ``MOBAIDE_ENV=test`` agent uses its disposable state folder instead,
        so tests never write into the real home folder.
        """
        if self.worktrees_dir is not None:
            path = os.path.expanduser(os.path.expandvars(self.worktrees_dir.strip()))
            if not os.path.isabs(path):
                path = os.path.join(os.path.expanduser("~"), path)
            return os.path.abspath(path)
        legacy = self.worktree_base
        if os.path.isabs(legacy):
            # An operator who moved worktrees out of the project with an
            # absolute WORKTREE_BASE before this setting existed keeps that
            # folder.
            return os.path.abspath(legacy)
        from mobaide.agent.install_paths import is_test_mode, state_prefix

        if is_test_mode():
            return str(state_prefix(test_mode=True) / "worktrees")
        return str(Path.home() / ".mobaide" / "worktrees")

    @field_validator("auto_remove_worktrees", mode="before")
    @classmethod
    def _parse_bool(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in _TRUTHY
        return False

    @field_validator("auto_scan_prompt", mode="before")
    @classmethod
    def _resolve_prompt(cls, value: Any) -> str | None:
        if value is None:
            return _DEFAULT_AUTO_SCAN_PROMPT
        if isinstance(value, str):
            if value == "":
                return _DEFAULT_AUTO_SCAN_PROMPT
            return value.replace("\\n", "\n")
        return value

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "AutoScanConfig":
        """Build config from environment variables and/or an ``env_file``."""
        kwargs: dict = {}
        if env_file is not None:
            kwargs["_env_file"] = str(env_file)
        return cls(**kwargs)
