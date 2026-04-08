from __future__ import annotations

import json
import logging
import os
import platform
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

from typing import TYPE_CHECKING

from .projects_config import ProjectConfig

if TYPE_CHECKING:
    from claude_mobile.linear.worktree import WorktreeManager


@dataclass(frozen=True)
class ClaudeOptions:
    """Options that mirror the flags handled by ``Start-StandardMode`` /
    ``Start-ClaudeInWorktree`` in ``claude_auto_executor.ps1``."""

    plan_mode: bool = False
    skip_permissions: bool = False
    no_memory: bool = False
    initial_prompt: str | None = None
    worktree: bool = False
    branch_name: str | None = None
    identifier: str | None = None
    title: str | None = None


@dataclass
class LaunchSpec:
    """Result of building a launch — argv/cwd/env ready to hand to a PTY."""

    argv: list[str]
    cwd: str
    env: dict[str, str]
    created_files: list[str] = field(default_factory=list)


# Mirrors PS1 lines 470-480.
_LINEAR_MCP_TEMPLATE = {
    "mcpServers": {
        "linear": {
            "type": "http",
            "url": "https://mcp.linear.app/mcp",
            "headers": {"Authorization": "Bearer {API_KEY}"},
        }
    }
}

# Mirrors PS1 lines 486-488.
_LOCAL_SETTINGS_TEMPLATE = {
    "enabledPlugins": {"linear@claude-plugins-official": False},
}


class ClaudeLauncher:
    """Single source of truth for building a ``claude`` invocation.

    Used by both ``POST /api/sessions`` (mobile) and the Linear auto-scan
    pipeline so the two paths produce identical argv / env / cwd, mirroring
    what ``Start-StandardMode`` would produce in ``claude_auto_executor.ps1``.
    """

    def __init__(
        self,
        worktree_mgr: WorktreeManager | None = None,
    ) -> None:
        self._worktree_mgr: WorktreeManager | None = worktree_mgr

    async def build(
        self,
        project: ProjectConfig,
        options: ClaudeOptions,
    ) -> LaunchSpec:
        cwd = await self._resolve_cwd(project, options)
        argv = self._build_argv(options)
        env = self._build_env(project, options)
        created = self._write_project_files(project, cwd)
        return LaunchSpec(argv=argv, cwd=cwd, env=env, created_files=created)

    # -- argv --

    @staticmethod
    def _build_argv(options: ClaudeOptions) -> list[str]:
        argv: list[str] = ["claude", "--debug"]
        if options.plan_mode:
            argv += ["--permission-mode", "plan"]
        if options.skip_permissions:
            argv.append("--allow-dangerously-skip-permissions")
        if options.initial_prompt:
            argv += ["--", options.initial_prompt]
        return argv

    # -- env --

    @staticmethod
    def _build_env(project: ProjectConfig, options: ClaudeOptions) -> dict[str, str]:
        env = os.environ.copy()
        if project.linear_api_key:
            env["LINEAR_API_KEY"] = project.linear_api_key
        if options.no_memory:
            env["CLAUDE_NO_MEMORY"] = "1"
        env.setdefault("TERM", "xterm-256color")
        env["PYTHONIOENCODING"] = "utf-8"

        if project.conda_env:
            conda_bin = ClaudeLauncher._conda_env_bin(project.conda_env)
            if conda_bin:
                sep = os.pathsep
                env["PATH"] = f"{conda_bin}{sep}{env.get('PATH', '')}"
        return env

    @staticmethod
    def _conda_env_bin(conda_env: str) -> str | None:
        """Resolve the bin / Scripts dir for a conda env name or absolute path.

        Returns None if it cannot be resolved — the caller will simply skip
        the PATH prepend rather than break the launch.
        """
        candidate = Path(conda_env)
        if not candidate.is_absolute():
            base = os.environ.get("CONDA_PREFIX") or os.environ.get("CONDA_ROOT")
            if base:
                candidate = Path(base).parent / conda_env
            else:
                return None
        sub = "Scripts" if platform.system() == "Windows" else "bin"
        resolved = candidate / sub
        return str(resolved) if resolved.exists() else str(resolved)

    # -- cwd / worktree --

    async def _resolve_cwd(
        self, project: ProjectConfig, options: ClaudeOptions
    ) -> str:
        if not options.worktree:
            return project.path
        # CLA-20: a stale client may still send worktree=true for a non-git
        # project. Log it for audit and fall back to the project root.
        if not (Path(project.path) / ".git").exists():
            logger.warning(
                "worktree=true requested for non-git project %r; ignoring",
                project.name,
            )
            return project.path
        if self._worktree_mgr is None:
            return project.path
        # Lazy import to avoid a hard dep when worktree path is unused.
        from claude_mobile.linear.worktree import convert_to_worktree_slug

        identifier = options.identifier or "session"
        title = options.title or (options.branch_name or "")
        slug = convert_to_worktree_slug(identifier, title)
        result = await self._worktree_mgr.create_worktree(slug, project.path)
        if result is None:
            return project.path
        return result.path

    # -- project file generation --

    @staticmethod
    def _write_project_files(project: ProjectConfig, cwd: str) -> list[str]:
        """Write ``.mcp.json`` and ``.claude/settings.local.json`` if needed.

        Mirrors PS1 lines 464-491. Only fires when the project has a Linear
        API key (the only thing currently injected via MCP).
        """
        if not project.linear_api_key:
            return []
        created: list[str] = []
        cwd_path = Path(cwd)
        try:
            cwd_path.mkdir(parents=True, exist_ok=True)
        except OSError:
            return []

        mcp_path = cwd_path / ".mcp.json"
        mcp_doc = json.loads(json.dumps(_LINEAR_MCP_TEMPLATE))  # deep copy
        mcp_doc["mcpServers"]["linear"]["headers"][
            "Authorization"
        ] = f"Bearer {project.linear_api_key}"
        try:
            mcp_path.write_text(
                json.dumps(mcp_doc, indent=2), encoding="utf-8"
            )
            created.append(str(mcp_path))
        except OSError:
            pass

        settings_dir = cwd_path / ".claude"
        settings_path = settings_dir / "settings.local.json"
        if not settings_path.exists():
            try:
                settings_dir.mkdir(parents=True, exist_ok=True)
                settings_path.write_text(
                    json.dumps(_LOCAL_SETTINGS_TEMPLATE, indent=2),
                    encoding="utf-8",
                )
                created.append(str(settings_path))
            except OSError:
                pass
        return created
