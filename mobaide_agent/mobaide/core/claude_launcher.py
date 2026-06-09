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

from .hook_settings import write_session_hooks
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


def _is_executable_file(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def _normalise_user_path(path: str) -> str:
    return os.path.abspath(os.path.expandvars(os.path.expanduser(path)))


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


def _windows_fallback_candidates() -> list[str]:
    """Common Windows install locations for ``claude.cmd``.

    Last-ditch guess for environments where neither ``MOBAIDE_CLAUDE_BIN``
    nor PATH resolves ``claude.cmd``: probe the default
    ``%APPDATA%\\npm\\`` landing site for
    ``npm install -g @anthropic-ai/claude-code``, plus the relocated
    variants under ``%LOCALAPPDATA%`` / ``%USERPROFILE%`` and the
    machine-scope ``%ProgramData%\\npm`` / ``%ProgramFiles%\\nodejs``
    locations some operators use. The agent now runs in the installing
    user's session (Scheduled Task with ``-LogonType Interactive``), so
    these env vars resolve correctly and PATH usually contains the npm
    dir already; this fallback is belt-and-suspenders for non-standard
    installs (chocolatey, relocated npm prefix, etc.).
    """
    candidates: list[str] = []
    for var in ("APPDATA", "LOCALAPPDATA", "USERPROFILE"):
        base = os.environ.get(var)
        if not base:
            continue
        if var == "USERPROFILE":
            for suffix in (
                r"AppData\Roaming\npm\claude.cmd",
                r"AppData\Roaming\npm\claude.exe",
            ):
                candidates.append(os.path.join(base, suffix))
        else:
            for suffix in (r"npm\claude.cmd", r"npm\claude.exe"):
                candidates.append(os.path.join(base, suffix))
    program_data = os.environ.get("ProgramData") or r"C:\ProgramData"
    candidates.append(os.path.join(program_data, "npm", "claude.cmd"))
    program_files = os.environ.get("ProgramFiles")
    if program_files:
        candidates.append(os.path.join(program_files, "nodejs", "claude.cmd"))
    return candidates


def _resolve_claude_binary(override: str | None = None) -> str:
    """Locate the Claude Code CLI executable.

    Resolution order (high → low):
      1. ``override`` arg (config-supplied ``MOBAIDE_CLAUDE_BIN``).
      2. ``MOBAIDE_CLAUDE_BIN`` env var (direct shell export).
      3. ``shutil.which`` against the current PATH.
      4. Platform-specific fallback candidates (Windows: ``%APPDATA%\\npm``
         and friends; POSIX: ``~/.npm-global/bin``, ``/usr/local/bin``,
         nvm).

    Stateless — caching is the caller's responsibility. ``ClaudeLauncher``
    caches the result for the lifetime of one launcher instance, which is
    the natural unit because the launcher is constructed once per app
    boot from a fixed ``AgentConfig``.

    Raises :class:`ClaudeBinaryNotFoundError` with an actionable message
    listing every path that was probed, each tagged with its source
    (``override:``, ``env:``, ``PATH:``, ``fallback:``) so the mobile
    client can tell the operator which lookup channel was tried.
    """
    searched: list[str] = []

    typed_candidates: list[tuple[str, str]] = []
    if override:
        typed_candidates.append(("override", _normalise_user_path(override)))
    env_val = os.environ.get(_CLAUDE_BIN_ENV_VAR)
    if env_val:
        typed_candidates.append(("env", _normalise_user_path(env_val)))

    for label, path in typed_candidates:
        searched.append(f"{label}:{path}")
        if _is_executable_file(path):
            return path

    if sys.platform == "win32":
        for name in ("claude", "claude.cmd", "claude.exe"):
            hit = shutil.which(name)
            searched.append(f"PATH:{name}")
            if hit:
                return os.path.abspath(hit)
        for candidate in _windows_fallback_candidates():
            searched.append(f"fallback:{candidate}")
            if _is_executable_file(candidate):
                return candidate
    else:
        hit = shutil.which("claude")
        searched.append("PATH:claude")
        if hit:
            return os.path.abspath(hit)

        for candidate in _posix_fallback_candidates():
            searched.append(f"fallback:{candidate}")
            if _is_executable_file(candidate):
                return candidate

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


@dataclass(frozen=True)
class AdHocLaunch:
    """Ad-hoc Claude launch target: a bare cwd with no stored project context.

    Used by the custom-path entry on the mobile picker to spawn a one-off
    Claude session against an arbitrary folder. No Linear key, no conda env,
    no project files written. The log "source" is recorded as ``<ad-hoc>``
    rather than synthesising a fake :class:`ProjectConfig`.
    """

    cwd: str


LaunchTarget = ProjectConfig | AdHocLaunch


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
        claude_bin_override: str | None = None,
        hook_port: int | None = None,
        hook_token: str | None = None,
    ) -> None:
        self._worktree_mgr: WorktreeManager | None = worktree_mgr
        self._claude_bin_override = claude_bin_override
        self._resolved_binary: str | None = None
        # When both are set, interactive sessions (those built with a
        # ``session_id``) get a per-session ``--settings`` file wiring Claude
        # Code attention hooks back to the agent's ``/internal/hook`` endpoint.
        self._hook_port = hook_port
        self._hook_token = hook_token

    def _resolve_binary(self) -> str:
        if self._resolved_binary is None:
            self._resolved_binary = _resolve_claude_binary(
                override=self._claude_bin_override,
            )
        return self._resolved_binary

    async def build(
        self,
        target: LaunchTarget,
        options: ClaudeOptions,
        session_id: str | None = None,
    ) -> LaunchSpec:
        # Resolve the binary FIRST so a ``ClaudeBinaryNotFoundError`` aborts
        # before ``_resolve_cwd`` runs ``git worktree add`` — otherwise a
        # missing CLI would leak an orphan worktree directory on every
        # scan cycle.
        #
        # ``session_id`` is supplied only by the interactive/ad-hoc create path
        # (``SessionManager``); the Auto Scan scanner omits it, so autonomous
        # sessions never get attention hooks and never raise notifications.
        hooks_path = self._maybe_write_hooks(session_id)
        argv = self._build_argv(options, hooks_path)
        cwd = await self._resolve_cwd(target, options)
        env = self._build_env(target, options, session_id)
        created = self._write_project_files(target, cwd)
        if hooks_path is not None:
            created.append(str(hooks_path))
        return LaunchSpec(argv=argv, cwd=cwd, env=env, created_files=created)

    def _maybe_write_hooks(self, session_id: str | None) -> Path | None:
        """Write the per-session attention-hook settings file, if configured.

        Returns the file path, or ``None`` when hooks are not configured
        (no ``session_id``, or the launcher was built without a hook port/token
        — e.g. the scanner) or the write failed (a hookless session still runs).
        """
        if not session_id or self._hook_port is None or not self._hook_token:
            return None
        try:
            return write_session_hooks(session_id, self._hook_port, self._hook_token)
        except OSError:
            logger.warning(
                "claude_launcher.hook_settings_write_failed session=%s",
                session_id,
                exc_info=True,
            )
            return None

    def _build_argv(
        self, options: ClaudeOptions, hooks_path: Path | None = None,
    ) -> list[str]:
        argv: list[str] = [self._resolve_binary(), "--debug"]
        if hooks_path is not None:
            argv += ["--settings", str(hooks_path)]
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

    def _build_env(
        self,
        target: LaunchTarget,
        options: ClaudeOptions,
        session_id: str | None = None,
    ) -> dict[str, str]:
        env = os.environ.copy()
        env.update(CLAUDE_SESSION_PRESET_ENV)
        # Belt-and-suspenders: the hook URLs already bake in session_id+token,
        # so correlation never depends on these. They aid debugging and keep a
        # ``command``-hook fallback trivial if ever needed.
        if session_id and self._hook_port is not None and self._hook_token:
            env["MOBAIDE_SESSION_ID"] = session_id
            env["MOBAIDE_HOOK_URL"] = f"http://127.0.0.1:{self._hook_port}/internal/hook"
            env["MOBAIDE_HOOK_TOKEN"] = self._hook_token
        is_project = isinstance(target, ProjectConfig)
        if is_project and target.linear_api_key:
            env["LINEAR_API_KEY"] = target.linear_api_key
        if options.no_memory:
            env["CLAUDE_NO_MEMORY"] = "1"
        env.setdefault("TERM", "xterm-256color")
        env["PYTHONIOENCODING"] = "utf-8"

        if is_project and target.conda_env:
            conda_bin = ClaudeLauncher._conda_env_bin(target.conda_env)
            if conda_bin:
                sep = os.pathsep
                env["PATH"] = f"{conda_bin}{sep}{env.get('PATH', '')}"

        try:
            claude_dir = os.path.dirname(self._resolve_binary())
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
        self, target: LaunchTarget, options: ClaudeOptions
    ) -> str:
        if isinstance(target, AdHocLaunch):
            # Ad-hoc launches have no git/project context — never create a
            # worktree. Log if the client still sent worktree=true so the
            # silent downgrade is auditable.
            if options.worktree:
                logger.warning(
                    "worktree=true requested for ad-hoc launch %r; ignoring",
                    "<ad-hoc>",
                )
            return self._check_exists(target.cwd)
        if not options.worktree:
            return self._check_exists(target.path)
        # A stale client may still send worktree=true for a non-git project;
        # fall back to the project root.
        if not safe_path_exists(Path(target.path) / ".git"):
            logger.warning(
                "worktree=true requested for non-git project %r; ignoring",
                target.name,
            )
            return self._check_exists(target.path)
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
            result = await self._worktree_mgr.create_worktree(slug, target.path)
        else:
            # Standard path: honour the user's entered branch name verbatim.
            # Empty branch_name gets a unique auto-generated default so two
            # worktree-on Standard sessions never collide on the same dir.
            branch = options.branch_name or (
                f"claude-session-{uuid.uuid4().hex[:8]}"
            )
            slug = convert_to_worktree_slug("", branch)
            result = await self._worktree_mgr.create_worktree(
                slug, target.path, branch_name=branch,
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
    def _write_project_files(target: LaunchTarget, cwd: str) -> list[str]:
        """Write ``.mcp.json`` and ``.claude/settings.local.json`` when a Linear API key is present.

        Ad-hoc launches skip this entirely — they have no stored project,
        no Linear key, and writing into an arbitrary user-supplied folder
        would pollute it with files the user did not opt in to.
        """
        if not isinstance(target, ProjectConfig) or not target.linear_api_key:
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
        ] = f"Bearer {target.linear_api_key}"
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
