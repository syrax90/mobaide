"""Launcher for the Cursor CLI (``cursor-agent``).

Needs no hooks and no sign-in probe: only Cursor knows whether it is signed in
(own login or an API key in the environment), and a terminal session that is
not signed in shows Cursor's own login prompt. The model is picked inside the
session.

The terminal UI of Cursor does not report its state to the terminal, so a
terminal session raises no "your turn" alert. A chat session does.
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
from .projects_config import ProjectConfig

if TYPE_CHECKING:
    from mobaide.linear.worktree import WorktreeManager

_CURSOR_BIN_ENV_VAR = "MOBAIDE_CURSOR_BIN"
# The installer also links the short name ``agent``. Only the full name is
# looked up: another program can own a command that is just ``agent``.
_CURSOR_BIN_NAME = "cursor-agent"


class CursorBinaryNotFoundError(CliBinaryNotFoundError):
    """Raised when the Cursor CLI cannot be located on the host."""

    cli_name = "cursor"
    _INSTALL_HINT = (
        "Cursor CLI not found. Install it with\n"
        "    curl https://cursor.com/install -fsS | bash\n"
        "(on Windows, in PowerShell: irm 'https://cursor.com/install?win32=true' | iex)\n"
        "or set MOBAIDE_CURSOR_BIN to its absolute path in the agent's env file."
    )


def _posix_fallback_candidates() -> list[str]:
    # The install script links the executable into ``~/.local/bin``.
    return [
        str(Path.home() / ".local" / "bin" / _CURSOR_BIN_NAME),
        f"/usr/local/bin/{_CURSOR_BIN_NAME}",
        f"/opt/homebrew/bin/{_CURSOR_BIN_NAME}",
    ]


def _windows_fallback_candidates() -> list[str]:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return []
    # The install script puts its launchers in this folder.
    root = os.path.join(local_app_data, "cursor-agent")
    return [
        os.path.join(root, f"{_CURSOR_BIN_NAME}.exe"),
        os.path.join(root, f"{_CURSOR_BIN_NAME}.cmd"),
    ]


def _resolve_cursor_binary(override: str | None = None) -> str:
    """Locate the Cursor CLI executable via the shared resolver."""
    if sys.platform == "win32":
        which_names: tuple[str, ...] = (
            _CURSOR_BIN_NAME,
            f"{_CURSOR_BIN_NAME}.exe",
            f"{_CURSOR_BIN_NAME}.cmd",
        )
        fallbacks = _windows_fallback_candidates()
    else:
        which_names = (_CURSOR_BIN_NAME,)
        fallbacks = _posix_fallback_candidates()
    return resolve_cli_binary(
        override=override,
        env_var=_CURSOR_BIN_ENV_VAR,
        which_names=which_names,
        fallbacks=fallbacks,
        error_cls=CursorBinaryNotFoundError,
    )


@dataclass(frozen=True)
class CursorOptions:
    """Cursor CLI launch options.

    ``force`` maps to ``--force`` (Cursor's "Run Everything"): Cursor then runs
    commands without asking, except the ones its settings deny.
    """

    force: bool = False
    # Worktree intent for a stored git project, resolved by ``resolve_cwd``
    # exactly like Codex; ignored for ad-hoc launches.
    worktree: bool = False
    branch_name: str | None = None
    base_branch: str | None = None


class CursorLauncher:
    """Build a ``cursor-agent`` invocation (argv / env / cwd) from a target and options."""

    def __init__(
        self,
        cursor_bin_override: str | None = None,
        worktree_mgr: WorktreeManager | None = None,
    ) -> None:
        self._cursor_bin_override = cursor_bin_override
        self._worktree_mgr = worktree_mgr
        self._resolved_binary: str | None = None

    def resolve_binary(self) -> str:
        """The resolved ``cursor-agent`` binary (honours the configured override),
        so availability probes match what a session spawn would use."""
        if self._resolved_binary is None:
            self._resolved_binary = _resolve_cursor_binary(
                override=self._cursor_bin_override,
            )
        return self._resolved_binary

    async def build(
        self,
        target: LaunchTarget,
        options: CursorOptions,
        session_id: str | None = None,
    ) -> LaunchSpec:
        # ``session_id`` is accepted for ``CliLauncher`` parity but unused:
        # Cursor has no per-session hooks.
        argv = self._build_argv(options)
        env = self.build_env(target)
        cwd = await self.resolve_cwd(target, options)
        return LaunchSpec(argv=argv, cwd=cwd, env=env, created_files=[])

    def _build_argv(self, options: CursorOptions) -> list[str]:
        argv = [self.resolve_binary()]
        if options.force:
            argv.append("--force")
        return argv

    async def resolve_cwd(self, target: LaunchTarget, options: CursorOptions) -> str:
        """The session folder: the project root, a new git worktree when
        ``worktree`` is set on a stored git project, or the bare ad-hoc cwd."""
        return await resolve_standard_worktree_cwd(
            target,
            worktree=options.worktree,
            branch_name=options.branch_name,
            base_branch=options.base_branch,
            worktree_mgr=self._worktree_mgr,
            fallback_prefix="cursor-session",
        )

    def build_env(self, target: LaunchTarget) -> dict[str, str]:
        """The environment of a Cursor process for ``target``. Does not need
        the binary, so a chat session can use it before Cursor is looked up."""
        env = os.environ.copy()
        env.setdefault("TERM", "xterm-256color")
        env["PYTHONIOENCODING"] = "utf-8"

        if isinstance(target, ProjectConfig) and target.conda_env:
            conda_bin = conda_env_bin(target.conda_env)
            if conda_bin:
                sep = os.pathsep
                env["PATH"] = f"{conda_bin}{sep}{env.get('PATH', '')}"

        try:
            cursor_dir = os.path.dirname(self.resolve_binary())
        except CliBinaryNotFoundError:
            cursor_dir = ""
        prepend_path_dir(env, cursor_dir)
        return env
