"""Launcher for the Junie CLI (``junie``), JetBrains' coding agent.

Needs no sign-in probe: a signed-out terminal session shows Junie's own welcome
screen. A terminal session raises its "your turn", approval and activity alerts
through Junie's command hooks: the launcher passes a per-session hook config
with ``--config-location`` (see :func:`.hook_settings.write_junie_session_hooks`).
That flag adds to the user's own ``~/.junie/config.json`` rather than replacing
it. A Junie without the flag, and Windows, launch with no hooks.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .cli_launcher import (
    CliBinaryNotFoundError,
    LaunchSpec,
    LaunchTarget,
    cli_help_text,
    conda_env_bin,
    prepend_path_dir,
    resolve_cli_binary,
    resolve_standard_worktree_cwd,
)
from .hook_settings import HOOK_TOKEN_ENV, remove_session_hooks, write_junie_session_hooks
from .workspaces_config import WorkspaceConfig

if TYPE_CHECKING:
    from mobaide.linear.worktree import WorktreeManager

logger = logging.getLogger(__name__)

_JUNIE_BIN_ENV_VAR = "MOBAIDE_JUNIE_BIN"
_JUNIE_BIN_NAME = "junie"

# An older Junie exits with "no such option" on this flag, so the launcher
# reads ``--help`` before it adds the flag.
_CONFIG_LOCATION_FLAG = "--config-location"
# The JVM starts in about a second; the shim may apply a pending update first.
_HELP_PROBE_TIMEOUT_SECONDS = 20.0
# How long a "no flag" or failed probe stands. The shim path does not change
# when Junie updates itself (it switches a ``current`` link), so a negative
# answer must expire; a positive one stays, since a newer Junie keeps the flag.
_HELP_PROBE_RETRY_SECONDS = 300.0


class JunieBinaryNotFoundError(CliBinaryNotFoundError):
    """Raised when the Junie CLI cannot be located on the host."""

    cli_name = "junie"
    _INSTALL_HINT = (
        "Junie CLI not found. Install it with\n"
        "    curl -fsSL https://junie.jetbrains.com/install.sh | bash\n"
        "(on Windows, in PowerShell: iex (irm 'https://junie.jetbrains.com/install.ps1'))\n"
        "or set MOBAIDE_JUNIE_BIN to its absolute path in the agent's env file."
    )


def _posix_fallback_candidates() -> list[str]:
    # The install script puts its launcher in ``~/.local/bin``.
    return [
        str(Path.home() / ".local" / "bin" / _JUNIE_BIN_NAME),
        f"/usr/local/bin/{_JUNIE_BIN_NAME}",
        f"/opt/homebrew/bin/{_JUNIE_BIN_NAME}",
    ]


def _windows_fallback_candidates() -> list[str]:
    # The install script puts its launcher in ``%USERPROFILE%\.local\bin``.
    root = os.path.join(str(Path.home()), ".local", "bin")
    return [
        os.path.join(root, f"{_JUNIE_BIN_NAME}.bat"),
        os.path.join(root, f"{_JUNIE_BIN_NAME}.cmd"),
        os.path.join(root, f"{_JUNIE_BIN_NAME}.exe"),
    ]


def _resolve_junie_binary(override: str | None = None) -> str:
    """Locate the Junie CLI executable via the shared resolver."""
    if sys.platform == "win32":
        which_names: tuple[str, ...] = (
            _JUNIE_BIN_NAME,
            f"{_JUNIE_BIN_NAME}.bat",
            f"{_JUNIE_BIN_NAME}.cmd",
            f"{_JUNIE_BIN_NAME}.exe",
        )
        fallbacks = _windows_fallback_candidates()
    else:
        which_names = (_JUNIE_BIN_NAME,)
        fallbacks = _posix_fallback_candidates()
    return resolve_cli_binary(
        override=override,
        env_var=_JUNIE_BIN_ENV_VAR,
        which_names=which_names,
        fallbacks=fallbacks,
        error_cls=JunieBinaryNotFoundError,
    )


@dataclass(frozen=True)
class JunieOptions:
    """Junie CLI launch options.

    ``plan_mode`` starts the session in Junie's Plan mode: Junie prepares a
    plan and asks before it changes anything. ``brave`` turns on Brave mode
    for this session only: Junie then runs commands without asking.
    """

    plan_mode: bool = False
    brave: bool = False
    # Worktree intent for a stored git workspace, resolved by ``resolve_cwd``
    # exactly like Codex; ignored for ad-hoc launches.
    worktree: bool = False
    branch_name: str | None = None
    base_branch: str | None = None
    # First message, sent with ``--prompt`` (Auto Scan only): Junie starts
    # interactive with it already submitted.
    initial_prompt: str | None = None


class JunieLauncher:
    """Build a ``junie`` invocation (argv / env / cwd) from a target and options."""

    def __init__(
        self,
        junie_bin_override: str | None = None,
        worktree_mgr: WorktreeManager | None = None,
        hook_port: int | None = None,
        hook_token: str | None = None,
    ) -> None:
        self._junie_bin_override = junie_bin_override
        self._worktree_mgr = worktree_mgr
        self._resolved_binary: str | None = None
        # When both are set, terminal sessions get attention hooks that post
        # to the agent's ``/internal/hook`` endpoint.
        self._hook_port = hook_port
        self._hook_token = hook_token
        # Binary paths whose ``--help`` lists the flag, and the monotonic time
        # until which a negative or failed probe stands.
        self._config_flag_supported: set[str] = set()
        self._config_flag_retry_at: dict[str, float] = {}
        # One probe at a time: concurrent launches wait for its answer.
        self._config_flag_lock = asyncio.Lock()

    def resolve_binary(self) -> str:
        """The resolved ``junie`` binary (honours the configured override),
        so availability probes match what a session spawn would use."""
        if self._resolved_binary is None or not os.path.isfile(self._resolved_binary):
            self._resolved_binary = _resolve_junie_binary(
                override=self._junie_bin_override,
            )
        return self._resolved_binary

    async def build(
        self,
        target: LaunchTarget,
        options: JunieOptions,
        session_id: str | None = None,
    ) -> LaunchSpec:
        argv = self._build_argv(options)
        env = self.build_env(target)
        hooks_path = await self._maybe_write_hooks(session_id, env)
        try:
            cwd = await self.resolve_cwd(target, options)
        except BaseException:
            # The session manager deletes the file when a session goes; a
            # session that never spawned never gets there.
            if hooks_path is not None and session_id is not None:
                self.discard_session_hooks(session_id)
            raise
        created: list[str] = []
        if hooks_path is not None and session_id is not None:
            argv.insert(1, f"{_CONFIG_LOCATION_FLAG}={hooks_path}")
            env["MOBAIDE_SESSION_ID"] = session_id
            env[HOOK_TOKEN_ENV] = self._hook_token or ""
            created.append(str(hooks_path))
        return LaunchSpec(argv=argv, cwd=cwd, env=env, created_files=created)

    async def _maybe_write_hooks(
        self, session_id: str | None, env: Mapping[str, str],
    ) -> Path | None:
        """Write the session's hook config, or ``None`` to launch without hooks.

        No hooks without a session id or a hook endpoint, on Windows (its
        ``cmd /c`` quoting is unproven), on a Junie without
        ``--config-location``, or when the write fails: the session still runs.
        """
        if not session_id or self._hook_port is None or not self._hook_token:
            return None
        if sys.platform == "win32":
            return None
        if not await self.supports_config_location(env):
            return None
        try:
            return write_junie_session_hooks(session_id, self._hook_port)
        except OSError:
            logger.warning(
                "junie_launcher.hook_config_write_failed session=%s",
                session_id,
                exc_info=True,
            )
            return None

    def discard_session_hooks(self, session_id: str) -> None:
        """Best-effort removal of a session's hook config: when the session
        goes, or when it was built but never spawned."""
        if self._hook_port is None:
            return
        remove_session_hooks(session_id, self._hook_port)

    async def supports_config_location(self, env: Mapping[str, str]) -> bool:
        """Whether the resolved ``junie`` lists ``--config-location`` in its
        help. A negative or failed probe is asked again after
        ``_HELP_PROBE_RETRY_SECONDS``."""
        try:
            binary = self.resolve_binary()
        except CliBinaryNotFoundError:
            return False
        async with self._config_flag_lock:
            if binary in self._config_flag_supported:
                return True
            if time.monotonic() < self._config_flag_retry_at.get(binary, 0.0):
                return False
            help_text = await asyncio.to_thread(
                cli_help_text, binary, env, _HELP_PROBE_TIMEOUT_SECONDS,
            )
            if help_text is not None and _CONFIG_LOCATION_FLAG in help_text:
                self._config_flag_supported.add(binary)
                return True
            logger.info(
                "junie_launcher.no_config_location binary=%s probe_failed=%s",
                binary, help_text is None,
            )
            self._config_flag_retry_at[binary] = (
                time.monotonic() + _HELP_PROBE_RETRY_SECONDS
            )
            return False

    def _build_argv(self, options: JunieOptions) -> list[str]:
        argv = [self.resolve_binary()]
        if options.plan_mode:
            argv.append("--plan")
        if options.brave:
            argv.append("--brave")
        if options.initial_prompt:
            # One element with ``=``: a prompt that starts with ``-`` can
            # never be read as another flag.
            argv.append(f"--prompt={options.initial_prompt}")
        return argv

    async def resolve_cwd(self, target: LaunchTarget, options: JunieOptions) -> str:
        """The session folder: the workspace root, a new git worktree when
        ``worktree`` is set on a stored git workspace, or the bare ad-hoc cwd."""
        return await resolve_standard_worktree_cwd(
            target,
            worktree=options.worktree,
            branch_name=options.branch_name,
            base_branch=options.base_branch,
            worktree_mgr=self._worktree_mgr,
            fallback_prefix="junie-session",
        )

    def build_env(self, target: LaunchTarget) -> dict[str, str]:
        """The environment of a Junie process for ``target``. Does not need
        the binary, so a chat session can use it before Junie is looked up."""
        env = os.environ.copy()
        env.setdefault("TERM", "xterm-256color")
        env["PYTHONIOENCODING"] = "utf-8"

        if isinstance(target, WorkspaceConfig) and target.conda_env:
            conda_bin = conda_env_bin(target.conda_env)
            if conda_bin:
                sep = os.pathsep
                env["PATH"] = f"{conda_bin}{sep}{env.get('PATH', '')}"

        try:
            junie_dir = os.path.dirname(self.resolve_binary())
        except CliBinaryNotFoundError:
            junie_dir = ""
        prepend_path_dir(env, junie_dir)
        return env
