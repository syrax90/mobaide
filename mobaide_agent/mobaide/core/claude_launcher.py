from __future__ import annotations

import json
import logging
import os

# ``shutil`` is imported solely so ``test_claude_resolver`` can monkeypatch
# ``claude_launcher.shutil.which``; the shared ``resolve_cli_binary`` reads the
# same global ``shutil``. Binary resolution itself is delegated below, so the
# name is otherwise unused in this module.
import shutil  # noqa: F401
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

from typing import TYPE_CHECKING

from .cli_launcher import (
    AdHocLaunch,
    CliBinaryNotFoundError,
    LaunchSpec,
    LaunchTarget,
    check_cwd_exists,
    conda_env_bin,
    posix_npm_fallbacks,
    prepend_path_dir,
    resolve_cli_binary,
    resolve_standard_worktree_cwd,
    windows_npm_fallbacks,
)

# Re-exported (explicit ``as`` alias) only so ``test_claude_resolver`` can
# reference ``claude_launcher._nvm_version_key`` directly.
from .cli_launcher import _nvm_version_key as _nvm_version_key
from .hook_settings import remove_session_hooks, write_session_hooks
from .projects_config import ProjectConfig
from .projects_store import safe_path_exists

if TYPE_CHECKING:
    from mobaide.linear.worktree import WorktreeManager

# ``AdHocLaunch`` / ``LaunchSpec`` / ``LaunchTarget`` / ``check_cwd_exists`` are
# imported above from ``cli_launcher`` and re-exported here so the existing
# ``from mobaide.core.claude_launcher import ...`` sites keep working.
# ``_nvm_version_key`` is referenced as ``claude_launcher._nvm_version_key`` by
# ``test_claude_resolver``.


class ClaudeBinaryNotFoundError(CliBinaryNotFoundError):
    """Raised when the Claude Code CLI cannot be located on the host."""

    cli_name = "claude"
    _INSTALL_HINT = (
        "Claude CLI not found. Install it with:\n"
        "    npm install -g @anthropic-ai/claude-code\n"
        "or set MOBAIDE_CLAUDE_BIN to its absolute path in the agent's env file."
    )


_CLAUDE_BIN_ENV_VAR = "MOBAIDE_CLAUDE_BIN"


def _posix_fallback_candidates() -> list[str]:
    return posix_npm_fallbacks("claude")


def _windows_fallback_candidates() -> list[str]:
    return windows_npm_fallbacks("claude")


def _resolve_claude_binary(override: str | None = None) -> str:
    """Locate the Claude Code CLI executable via the shared resolver.

    Stateless — caching is the caller's responsibility. ``ClaudeLauncher``
    caches the result for the lifetime of one launcher instance, the natural
    unit because the launcher is constructed once per app boot from a fixed
    ``AgentConfig``. The module-local ``_*_fallback_candidates`` wrappers are
    kept so existing tests can monkeypatch them by name.
    """
    if sys.platform == "win32":
        which_names = ("claude", "claude.cmd", "claude.exe")
        fallbacks = _windows_fallback_candidates()
    else:
        which_names = ("claude",)
        fallbacks = _posix_fallback_candidates()
    return resolve_cli_binary(
        override=override,
        env_var=_CLAUDE_BIN_ENV_VAR,
        which_names=which_names,
        fallbacks=fallbacks,
        error_cls=ClaudeBinaryNotFoundError,
    )


@dataclass(frozen=True)
class ClaudeOptions:
    """Options that mirror the flags handled by ``Start-StandardMode`` /
    ``Start-ClaudeInWorktree`` in ``claude_auto_executor.ps1``."""

    plan_mode: bool = False
    no_memory: bool = False
    # Stays True: this is the wire-absence fallback, not the mobile UI default
    # (that toggle now starts off — see CreateSessionUiState.skipPermissions /
    # CreateSessionModel.skipPermissions). Changing it here would make an
    # agent paired with a not-yet-updated mobile client silently misread that
    # client's omitted-because-matches-its-own-default `true` as `false`.
    skip_permissions: bool = True
    initial_prompt: str | None = None
    worktree: bool = False
    branch_name: str | None = None
    base_branch: str | None = None
    identifier: str | None = None
    title: str | None = None
    # Curated model alias (opus / sonnet / haiku / fable) or None for the
    # account default. effort is one of low/medium/high/xhigh/max, emitted as
    # --effort <level>; None omits the flag (the CLI's own default applies).
    # fast_mode / ultracode are emitted via a merged --settings dict, never an
    # --effort value.
    model: str | None = None
    effort: str | None = None
    fast_mode: bool = False
    ultracode: bool = False
    append_system_prompt: str | None = None
    # Emits --debug. Wire-absence default matches the clients' UI default (both
    # off), so an omitted field is unambiguous — unlike skip_permissions above.
    debug: bool = False
    # Claude Code transcript view: True → viewMode "focus", False → "default"
    # (explicit, so a sticky host-side /focus can't override the client's
    # choice), None (older client) → no viewMode key, CLI behaviour unchanged.
    focus_mode: bool | None = None


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
        # When both are set, sessions built with a ``session_id`` (interactive
        # and Auto Scan alike) get a per-session ``--settings`` file wiring
        # Claude Code attention hooks back to the agent's ``/internal/hook``
        # endpoint.
        self._hook_port = hook_port
        self._hook_token = hook_token

    def _resolve_binary(self) -> str:
        if self._resolved_binary is None:
            self._resolved_binary = _resolve_claude_binary(
                override=self._claude_bin_override,
            )
        return self._resolved_binary

    def resolve_binary(self) -> str:
        """Public access to the resolved Claude binary (honours the configured
        override) so availability probes use the same resolution sessions do."""
        return self._resolve_binary()

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
        # ``session_id`` is supplied by both the interactive/ad-hoc create path
        # (``SessionManager``) and the Auto Scan scanner; whenever the launcher
        # is hook-capable the session gets attention hooks and can raise
        # notifications.
        cli_settings = self._cli_settings(options)
        hooks_path = self._maybe_write_hooks(session_id, cli_settings)
        try:
            argv = self._build_argv(options, hooks_path, cli_settings)
            cwd = await self._resolve_cwd(target, options)
            env = self._build_env(target, options, session_id)
            created = self._write_project_files(target, cwd)
        except BaseException:
            # A build that dies after the hooks write (missing CLI binary,
            # worktree failure) must not orphan the settings file — the
            # ``SessionEnd`` hook that normally deletes it will never fire
            # for a session that never spawned. (session_id check is type
            # narrowing only: a written hooks_path implies a truthy id.)
            if hooks_path is not None and session_id is not None:
                self.discard_session_hooks(session_id)
            raise
        if hooks_path is not None:
            created.append(str(hooks_path))
        return LaunchSpec(argv=argv, cwd=cwd, env=env, created_files=created)

    @staticmethod
    def _cli_settings(options: ClaudeOptions) -> dict[str, bool | str]:
        """The ``--settings`` keys that ride alongside the attention hooks:
        fast mode, ultracode and the transcript view mode. Merged into the
        single hooks settings file when one is written, else emitted as one
        inline ``--settings`` JSON arg — so a launch never carries two
        ``--settings`` flags (Claude Code last-wins them).
        """
        settings: dict[str, bool | str] = {}
        if options.fast_mode:
            settings["fastMode"] = True
        if options.ultracode:
            settings["ultracode"] = True
        if options.focus_mode is not None:
            settings["viewMode"] = "focus" if options.focus_mode else "default"
        return settings

    def _maybe_write_hooks(
        self, session_id: str | None, extra: dict | None = None,
    ) -> Path | None:
        """Write the per-session attention-hook settings file, if configured.

        ``extra`` (the fast-mode / ultracode ``--settings`` keys) is merged into
        the same file so the launch needs only one ``--settings`` flag. Returns
        the file path, or ``None`` when hooks are not configured (no
        ``session_id``, or the launcher was built without a hook port/token)
        or the write failed (a hookless session still runs; the caller then
        emits ``extra`` as an inline ``--settings``).
        """
        if not session_id or self._hook_port is None or not self._hook_token:
            return None
        try:
            return write_session_hooks(
                session_id, self._hook_port, self._hook_token, extra=extra,
            )
        except OSError:
            logger.warning(
                "claude_launcher.hook_settings_write_failed session=%s",
                session_id,
                exc_info=True,
            )
            return None

    def discard_session_hooks(self, session_id: str) -> None:
        """Best-effort removal of a session's hooks settings file.

        For callers that build a spec but fail to spawn the session (e.g. the
        Auto Scan scanner): the ``SessionEnd`` hook that normally deletes the
        file never fires for a session that never started.
        """
        if self._hook_port is None:
            return
        remove_session_hooks(session_id, self._hook_port)

    def _build_argv(
        self,
        options: ClaudeOptions,
        hooks_path: Path | None = None,
        cli_settings: dict | None = None,
    ) -> list[str]:
        argv: list[str] = [self._resolve_binary()]
        if options.debug:
            argv.append("--debug")
        # Exactly one --settings source: the hooks file (fast/ultracode already
        # merged in) when hooks were written, else those keys inline.
        if hooks_path is not None:
            argv += ["--settings", str(hooks_path)]
        elif cli_settings:
            argv += ["--settings", json.dumps(cli_settings)]
        if options.plan_mode and options.skip_permissions:
            argv += ["--permission-mode", "plan",
                     "--allow-dangerously-skip-permissions"]
        elif options.plan_mode:
            argv += ["--permission-mode", "plan"]
        elif options.skip_permissions:
            argv.append("--dangerously-skip-permissions")
        if options.model:
            argv += ["--model", options.model]
        # Every explicit level is emitted, "high" included: with no flag the CLI
        # uses the operator's persisted effortLevel / per-model default, which
        # is not reliably "high". None (no client choice) leaves that default
        # alone. ultracode internally drives xhigh and is redundant with
        # --effort (precedence undocumented), so it suppresses an explicit effort.
        if options.effort and not options.ultracode:
            argv += ["--effort", options.effort]
        if options.append_system_prompt:
            argv += ["--append-system-prompt", options.append_system_prompt]
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
            conda_bin = conda_env_bin(target.conda_env)
            if conda_bin:
                sep = os.pathsep
                env["PATH"] = f"{conda_bin}{sep}{env.get('PATH', '')}"

        try:
            claude_dir = os.path.dirname(self._resolve_binary())
        except CliBinaryNotFoundError:
            claude_dir = ""
        prepend_path_dir(env, claude_dir)
        return env

    async def _resolve_cwd(
        self, target: LaunchTarget, options: ClaudeOptions
    ) -> str:
        # Auto Scan (``identifier`` set) derives the worktree branch from a Linear
        # slug and calls the manager directly, so resolve it here; every other
        # case (ad-hoc, no worktree, non-git, Standard branch) delegates to the
        # shared resolver in ``cli_launcher``.
        if (
            options.identifier
            and options.worktree
            and not isinstance(target, AdHocLaunch)
        ):
            # A stale client may still send worktree=true for a non-git project;
            # fall back to the project root.
            if not safe_path_exists(Path(target.path) / ".git"):
                logger.warning(
                    "worktree=true requested for non-git project %r; ignoring",
                    target.name,
                )
                return check_cwd_exists(target.path)
            if self._worktree_mgr is None:
                raise RuntimeError(
                    "worktree=true requested but no WorktreeManager is configured; "
                    "this is a server misconfiguration"
                )
            from mobaide.linear.worktree import convert_to_worktree_slug

            # The WorktreeManager applies its configured ``branch_prefix``
            # (e.g. ``feature/mobaide/<slug>``) to the issue-derived slug.
            slug = convert_to_worktree_slug(options.identifier, options.title or "")
            result = await self._worktree_mgr.create_worktree(slug, target.path)
            return check_cwd_exists(result.path)
        return await resolve_standard_worktree_cwd(
            target,
            worktree=options.worktree,
            branch_name=options.branch_name,
            base_branch=options.base_branch,
            worktree_mgr=self._worktree_mgr,
            fallback_prefix="claude-session",
        )

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
