"""Launcher for the Pi coding agent (``pi``).

Needs no hooks and no sign-in probe: terminal sessions report attention through
OSC 7501, and Pi works with whichever of its many model providers the user
signed in to. The model and the thinking level are picked inside the session.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .cli_launcher import (
    CliBinaryNotFoundError,
    LaunchSpec,
    LaunchTarget,
    conda_env_bin,
    posix_npm_fallbacks,
    prepend_path_dir,
    resolve_cli_binary,
    resolve_standard_worktree_cwd,
    windows_npm_fallbacks,
)
from .projects_config import ProjectConfig

if TYPE_CHECKING:
    from mobaide.linear.worktree import WorktreeManager

_PI_BIN_ENV_VAR = "MOBAIDE_PI_BIN"


class PiBinaryNotFoundError(CliBinaryNotFoundError):
    """Raised when the Pi coding agent cannot be located on the host."""

    cli_name = "pi"
    _INSTALL_HINT = (
        "Pi not found. Install it from https://pi.dev/docs/latest/quickstart\n"
        "or set MOBAIDE_PI_BIN to its absolute path in the agent's env file."
    )


def _managed_bin_dir() -> str:
    # Pi's own installer keeps its launcher here and links it into a folder
    # on PATH, which a service started before the install does not have.
    return os.path.join(os.path.expanduser("~"), ".pi", "agent", "bin")


def _posix_fallback_candidates() -> list[str]:
    return [os.path.join(_managed_bin_dir(), "pi"), *posix_npm_fallbacks("pi")]


def _windows_fallback_candidates() -> list[str]:
    return [os.path.join(_managed_bin_dir(), "pi.cmd"), *windows_npm_fallbacks("pi")]


def _resolve_pi_binary(override: str | None = None) -> str:
    """Locate the Pi executable via the shared resolver."""
    if sys.platform == "win32":
        which_names: tuple[str, ...] = ("pi", "pi.exe", "pi.cmd")
        fallbacks = _windows_fallback_candidates()
    else:
        which_names = ("pi",)
        fallbacks = _posix_fallback_candidates()
    return resolve_cli_binary(
        override=override,
        env_var=_PI_BIN_ENV_VAR,
        which_names=which_names,
        fallbacks=fallbacks,
        error_cls=PiBinaryNotFoundError,
    )


@dataclass(frozen=True)
class PiOptions:
    """Pi launch options.

    Pi has no approval mode to choose: it runs its tools without asking, in
    the terminal and in the chat alike.
    """

    # Worktree intent for a stored git project, resolved by ``resolve_cwd``
    # exactly like Codex; ignored for ad-hoc launches.
    worktree: bool = False
    branch_name: str | None = None
    base_branch: str | None = None


class PiLauncher:
    """Build a ``pi`` invocation (argv / env / cwd) from a target and options."""

    def __init__(
        self,
        pi_bin_override: str | None = None,
        worktree_mgr: WorktreeManager | None = None,
    ) -> None:
        self._pi_bin_override = pi_bin_override
        self._worktree_mgr = worktree_mgr
        self._resolved_binary: str | None = None

    def resolve_binary(self) -> str:
        """The resolved ``pi`` binary (honours the configured override),
        so availability probes match what a session spawn would use."""
        if self._resolved_binary is None:
            self._resolved_binary = _resolve_pi_binary(
                override=self._pi_bin_override,
            )
        return self._resolved_binary

    async def build(
        self,
        target: LaunchTarget,
        options: PiOptions,
        session_id: str | None = None,
    ) -> LaunchSpec:
        # ``session_id`` is accepted for ``CliLauncher`` parity but unused:
        # Pi has no per-session hooks.
        argv = [self.resolve_binary()]
        env = self.build_env(target)
        cwd = await self.resolve_cwd(target, options)
        return LaunchSpec(argv=argv, cwd=cwd, env=env, created_files=[])

    async def resolve_cwd(self, target: LaunchTarget, options: PiOptions) -> str:
        """The session folder: the project root, a new git worktree when
        ``worktree`` is set on a stored git project, or the bare ad-hoc cwd."""
        return await resolve_standard_worktree_cwd(
            target,
            worktree=options.worktree,
            branch_name=options.branch_name,
            base_branch=options.base_branch,
            worktree_mgr=self._worktree_mgr,
            fallback_prefix="pi-session",
        )

    def build_env(self, target: LaunchTarget) -> dict[str, str]:
        """The environment of a Pi process for ``target``. Does not need
        the binary, so a chat session can use it before Pi is looked up."""
        env = os.environ.copy()
        env.setdefault("TERM", "xterm-256color")
        env["PYTHONIOENCODING"] = "utf-8"

        if isinstance(target, ProjectConfig) and target.conda_env:
            conda_bin = conda_env_bin(target.conda_env)
            if conda_bin:
                sep = os.pathsep
                env["PATH"] = f"{conda_bin}{sep}{env.get('PATH', '')}"

        try:
            pi_dir = os.path.dirname(self.resolve_binary())
        except CliBinaryNotFoundError:
            pi_dir = ""
        prepend_path_dir(env, pi_dir)
        return env
