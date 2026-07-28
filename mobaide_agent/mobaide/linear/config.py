from __future__ import annotations

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

    Holds only the non-project settings (intervals, prompt, worktree base,
    Linear workflow state names). The list of projects eligible for auto-scan
    is read live from :class:`mobaide.core.projects_store.ProjectsStore`
    by :class:`mobaide.linear.scanner.AutoScanSessionManager` — adding
    a project with a ``linear_api_key`` after boot makes it immediately
    available for auto-scan without a restart.
    """

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
