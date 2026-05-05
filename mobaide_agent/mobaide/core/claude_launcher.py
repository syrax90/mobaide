from __future__ import annotations

import glob
import json
import logging
import os
import platform
import shutil
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

from typing import TYPE_CHECKING

from .projects_config import ProjectConfig
from .projects_store import safe_path_exists

if TYPE_CHECKING:
    from mobaide.linear.worktree import WorktreeManager


class ClaudeBinaryNotFoundError(RuntimeError):
    """Raised when the Claude Code CLI cannot be located on the host."""

    _INSTALL_HINT = (
        "Claude CLI not found. Install it with:\n"
        "    npm install -g @anthropic-ai/claude-code\n"
        "or set MOBAIDE_CLAUDE_BIN to its absolute path in the agent's env file."
    )

    def __init__(self, searched_paths: list[str]) -> None:
        self.searched_paths = list(searched_paths)
        searched = ", ".join(self.searched_paths) if self.searched_paths else "(none)"
        super().__init__(f"{self._INSTALL_HINT}\nSearched: {searched}")


_CLAUDE_BIN_ENV_VAR = "MOBAIDE_CLAUDE_BIN"
_resolved_claude_bin: str | None = None


def _is_executable_file(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def _nvm_version_key(path: str) -> tuple[int, ...]:
    """Parse the ``vX.Y.Z`` segment of an nvm path into a comparable tuple.

    nvm directories are named like ``v18.20.4``; lexicographic sort would
    rank ``v9.x`` after ``v10.x`` because ``'1' < '9'``. Falling back to
    ``(0,)`` for unparseable names keeps such entries below any real
    semver-named one without crashing.
    """
    try:
        version_dir = Path(path).parent.parent.name
        parts = version_dir.lstrip("v").split(".")
        return tuple(int(p) for p in parts)
    except (ValueError, IndexError):
        return (0,)


def _posix_fallback_candidates() -> list[str]:
    home = Path.home()
    candidates = [
        str(home / ".npm-global" / "bin" / "claude"),
        str(home / ".local" / "bin" / "claude"),
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
    ]
    nvm_matches = glob.glob(
        str(home / ".nvm" / "versions" / "node" / "*" / "bin" / "claude")
    )
    if nvm_matches:
        candidates.append(max(nvm_matches, key=_nvm_version_key))
    return candidates


def _resolve_claude_binary() -> str:
    """Locate the Claude Code CLI executable.

    Resolution order:
      1. ``MOBAIDE_CLAUDE_BIN`` env var (operator override).
      2. ``shutil.which`` against the current PATH.
      3. (POSIX) common npm/nvm/homebrew install locations.

    Result is cached for the process lifetime in ``_resolved_claude_bin``.
    Raises :class:`ClaudeBinaryNotFoundError` with an actionable message
    listing every path that was probed.
    """
    global _resolved_claude_bin
    if _resolved_claude_bin is not None:
        return _resolved_claude_bin

    searched: list[str] = []

    override = os.environ.get(_CLAUDE_BIN_ENV_VAR)
    if override:
        override_path = os.path.abspath(override)
        searched.append(f"{_CLAUDE_BIN_ENV_VAR}={override_path}")
        if _is_executable_file(override_path):
            _resolved_claude_bin = override_path
            return _resolved_claude_bin

    if sys.platform == "win32":
        for name in ("claude", "claude.cmd", "claude.exe"):
            hit = shutil.which(name)
            searched.append(f"PATH:{name}")
            if hit:
                _resolved_claude_bin = os.path.abspath(hit)
                return _resolved_claude_bin
    else:
        hit = shutil.which("claude")
        searched.append("PATH:claude")
        if hit:
            _resolved_claude_bin = os.path.abspath(hit)
            return _resolved_claude_bin

        for candidate in _posix_fallback_candidates():
            searched.append(candidate)
            if _is_executable_file(candidate):
                _resolved_claude_bin = candidate
                return _resolved_claude_bin

    raise ClaudeBinaryNotFoundError(searched)


@dataclass(frozen=True)
class ClaudeOptions:
    """Options that mirror the flags handled by ``Start-StandardMode`` /
    ``Start-ClaudeInWorktree`` in ``claude_auto_executor.ps1``."""

    plan_mode: bool = False
    no_memory: bool = False
    skip_permissions: bool = True
    initial_prompt: str | None = None
    worktree: bool = False
    branch_name: str | None = None
    identifier: str | None = None
    title: str | None = None
    max_effort: bool = True


@dataclass
class LaunchSpec:
    """Result of building a launch — argv/cwd/env ready to hand to a PTY."""

    argv: list[str]
    cwd: str
    env: dict[str, str]
    created_files: list[str] = field(default_factory=list)


_LINEAR_MCP_TEMPLATE = {
    "mcpServers": {
        "linear": {
            "type": "http",
            "url": "https://mcp.linear.app/mcp",
            "headers": {"Authorization": "Bearer {API_KEY}"},
        }
    }
}

_LOCAL_SETTINGS_TEMPLATE = {
    "enabledPlugins": {"linear@claude-plugins-official": False},
}


# Preset environment applied to every Claude CLI session the agent spawns —
# interactive, Auto Scan, or worktree alike. Values here are authoritative:
# they override anything the operator's shell has set.
#
# ENABLE_PROMPT_CACHING_1H=1
#     Opt into the 1-hour prompt-cache TTL introduced in claude-code v2.1.108
#     (default is 5 minutes). Long / repeated sessions stop re-billing the
#     system prompt, CLAUDE.md, and MCP config every ~5 min. Older CLIs
#     simply ignore the variable, so this is safe across versions.
#     https://github.com/anthropics/claude-code/releases/tag/v2.1.108
CLAUDE_SESSION_PRESET_ENV: dict[str, str] = {
    "ENABLE_PROMPT_CACHING_1H": "1",
}


class ClaudeLauncher:
    """Build a ``claude`` invocation (argv / env / cwd) from a project config and options."""

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
        # Resolve the binary FIRST so a ``ClaudeBinaryNotFoundError`` aborts
        # before ``_resolve_cwd`` runs ``git worktree add`` — otherwise a
        # missing CLI would leak an orphan worktree directory on every
        # scan cycle.
        argv = self._build_argv(options)
        cwd = await self._resolve_cwd(project, options)
        env = self._build_env(project, options)
        created = self._write_project_files(project, cwd)
        return LaunchSpec(argv=argv, cwd=cwd, env=env, created_files=created)

    @staticmethod
    def _build_argv(options: ClaudeOptions) -> list[str]:
        argv: list[str] = [_resolve_claude_binary(), "--debug"]
        if options.plan_mode and options.skip_permissions:
            argv += ["--permission-mode", "plan",
                     "--allow-dangerously-skip-permissions"]
        elif options.plan_mode:
            argv += ["--permission-mode", "plan"]
        elif options.skip_permissions:
            argv.append("--dangerously-skip-permissions")
        if options.max_effort:
            argv += ["--effort", "max"]
        if options.initial_prompt:
            argv += ["--", options.initial_prompt]
        return argv

    @staticmethod
    def _build_env(project: ProjectConfig, options: ClaudeOptions) -> dict[str, str]:
        env = os.environ.copy()
        env.update(CLAUDE_SESSION_PRESET_ENV)
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

        try:
            claude_dir = os.path.dirname(_resolve_claude_binary())
        except ClaudeBinaryNotFoundError:
            claude_dir = ""
        if claude_dir:
            sep = os.pathsep
            existing = env.get("PATH", "")
            parts = existing.split(sep) if existing else []
            if claude_dir not in parts:
                env["PATH"] = f"{claude_dir}{sep}{existing}" if existing else claude_dir
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

    async def _resolve_cwd(
        self, project: ProjectConfig, options: ClaudeOptions
    ) -> str:
        if not options.worktree:
            return self._check_exists(project.path)
        # A stale client may still send worktree=true for a non-git project;
        # fall back to the project root.
        if not safe_path_exists(Path(project.path) / ".git"):
            logger.warning(
                "worktree=true requested for non-git project %r; ignoring",
                project.name,
            )
            return self._check_exists(project.path)
        if self._worktree_mgr is None:
            raise RuntimeError(
                "worktree=true requested but no WorktreeManager is configured; "
                "this is a server misconfiguration"
            )
        from mobaide.linear.worktree import convert_to_worktree_slug

        if options.identifier:
            # Auto Scan path: derive slug + prefixed branch from the Linear
            # issue identifier and title. The WorktreeManager applies its
            # configured ``branch_prefix`` (e.g. ``feature/claude/<slug>``).
            slug = convert_to_worktree_slug(
                options.identifier, options.title or ""
            )
            result = await self._worktree_mgr.create_worktree(slug, project.path)
        else:
            # Standard path: honour the user's entered branch name verbatim.
            # Empty branch_name gets a unique auto-generated default so two
            # worktree-on Standard sessions never collide on the same dir.
            branch = options.branch_name or (
                f"claude-session-{uuid.uuid4().hex[:8]}"
            )
            slug = convert_to_worktree_slug("", branch)
            result = await self._worktree_mgr.create_worktree(
                slug, project.path, branch_name=branch,
            )
        return self._check_exists(result.path)

    @staticmethod
    def _check_exists(cwd: str) -> str:
        """Raise ``FileNotFoundError`` with a clean ``filename`` if ``cwd`` is missing.

        This pre-empts ``pywinpty.PtyProcess.spawn`` / ``subprocess.Popen`` on
        bad paths so the session router can map the error to a 400 with a
        readable, unescaped path — rather than letting Starlette's default
        500 handler surface a ``repr``-escaped filename.
        """
        if not Path(cwd).exists():
            raise FileNotFoundError(2, "No such file or directory", cwd)
        return cwd

    @staticmethod
    def _write_project_files(project: ProjectConfig, cwd: str) -> list[str]:
        """Write ``.mcp.json`` and ``.claude/settings.local.json`` when a Linear API key is present."""
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
