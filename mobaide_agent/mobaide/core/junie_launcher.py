"""Launcher for the Junie CLI (``junie``), JetBrains' coding agent.

Needs no hooks and no sign-in probe: a signed-out terminal session shows
Junie's own welcome screen. Junie's terminal UI reports state only to terminal
apps it recognizes, so a terminal session raises no "your turn" alert; a chat does.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .cli_launcher import (
    CliBinaryNotFoundError,
    LaunchSpec,
    LaunchTarget,
    conda_env_bin,
    prepend_path_dir,
    resolve_cli_binary,
    resolve_standard_worktree_cwd,
)
from .workspaces_config import WorkspaceConfig

if TYPE_CHECKING:
    from mobaide.linear.worktree import WorktreeManager

_JUNIE_BIN_ENV_VAR = "MOBAIDE_JUNIE_BIN"
_JUNIE_BIN_NAME = "junie"


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
    ) -> None:
        self._junie_bin_override = junie_bin_override
        self._worktree_mgr = worktree_mgr
        self._resolved_binary: str | None = None

    def resolve_binary(self) -> str:
        """The resolved ``junie`` binary (honours the configured override),
        so availability probes match what a session spawn would use."""
        if self._resolved_binary is None:
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
        # ``session_id`` is accepted for ``CliLauncher`` parity but unused:
        # Junie has no per-session hooks.
        argv = self._build_argv(options)
        env = self.build_env(target)
        cwd = await self.resolve_cwd(target, options)
        return LaunchSpec(argv=argv, cwd=cwd, env=env, created_files=[])

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
