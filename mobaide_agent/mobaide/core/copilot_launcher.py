"""Launcher for the GitHub Copilot CLI (``copilot``).

Needs no hooks and no sign-in probe: terminal sessions report attention through
OSC 7501 (Copilot 1.0.106+), and only Copilot knows whether it is signed in
(own login, GitHub CLI login or environment token). The model is picked inside
the session, as no command lists it.
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

_COPILOT_BIN_ENV_VAR = "MOBAIDE_COPILOT_BIN"


class CopilotBinaryNotFoundError(CliBinaryNotFoundError):
    """Raised when the GitHub Copilot CLI cannot be located on the host."""

    cli_name = "copilot"
    _INSTALL_HINT = (
        "GitHub Copilot CLI not found. Install it with\n"
        "    npm install -g @github/copilot\n"
        "or set MOBAIDE_COPILOT_BIN to its absolute path in the agent's env file."
    )


def _posix_fallback_candidates() -> list[str]:
    # npm, Homebrew and the install script (``~/.local/bin``) all land in the
    # npm fallback locations.
    return posix_npm_fallbacks("copilot")


def _windows_fallback_candidates() -> list[str]:
    candidates = windows_npm_fallbacks("copilot")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        # ``winget install GitHub.Copilot`` links the executable here.
        candidates.append(
            os.path.join(local_app_data, "Microsoft", "WinGet", "Links", "copilot.exe")
        )
    return candidates


def _resolve_copilot_binary(override: str | None = None) -> str:
    """Locate the GitHub Copilot CLI executable via the shared resolver."""
    if sys.platform == "win32":
        which_names: tuple[str, ...] = ("copilot", "copilot.exe", "copilot.cmd")
        fallbacks = _windows_fallback_candidates()
    else:
        which_names = ("copilot",)
        fallbacks = _posix_fallback_candidates()
    return resolve_cli_binary(
        override=override,
        env_var=_COPILOT_BIN_ENV_VAR,
        which_names=which_names,
        fallbacks=fallbacks,
        error_cls=CopilotBinaryNotFoundError,
    )


@dataclass(frozen=True)
class CopilotOptions:
    """GitHub Copilot CLI launch options.

    ``allow_all`` maps to ``--allow-all``: Copilot then runs tools and uses
    paths and URLs without asking.
    """

    allow_all: bool = False
    # Worktree intent for a stored git project, resolved by ``resolve_cwd``
    # exactly like Codex; ignored for ad-hoc launches.
    worktree: bool = False
    branch_name: str | None = None
    base_branch: str | None = None


class CopilotLauncher:
    """Build a ``copilot`` invocation (argv / env / cwd) from a target and options."""

    def __init__(
        self,
        copilot_bin_override: str | None = None,
        worktree_mgr: WorktreeManager | None = None,
    ) -> None:
        self._copilot_bin_override = copilot_bin_override
        self._worktree_mgr = worktree_mgr
        self._resolved_binary: str | None = None

    def resolve_binary(self) -> str:
        """The resolved ``copilot`` binary (honours the configured override),
        so availability probes match what a session spawn would use."""
        if self._resolved_binary is None:
            self._resolved_binary = _resolve_copilot_binary(
                override=self._copilot_bin_override,
            )
        return self._resolved_binary

    async def build(
        self,
        target: LaunchTarget,
        options: CopilotOptions,
        session_id: str | None = None,
    ) -> LaunchSpec:
        # ``session_id`` is accepted for ``CliLauncher`` parity but unused:
        # Copilot has no per-session hooks.
        argv = self._build_argv(options)
        env = self.build_env(target)
        cwd = await self.resolve_cwd(target, options)
        return LaunchSpec(argv=argv, cwd=cwd, env=env, created_files=[])

    def _build_argv(self, options: CopilotOptions) -> list[str]:
        argv = [self.resolve_binary()]
        if options.allow_all:
            argv.append("--allow-all")
        return argv

    async def resolve_cwd(self, target: LaunchTarget, options: CopilotOptions) -> str:
        """The session folder: the project root, a new git worktree when
        ``worktree`` is set on a stored git project, or the bare ad-hoc cwd."""
        return await resolve_standard_worktree_cwd(
            target,
            worktree=options.worktree,
            branch_name=options.branch_name,
            base_branch=options.base_branch,
            worktree_mgr=self._worktree_mgr,
            fallback_prefix="copilot-session",
        )

    def build_env(self, target: LaunchTarget) -> dict[str, str]:
        """The environment of a Copilot process for ``target``. Does not need
        the binary, so a chat session can use it before Copilot is looked up."""
        env = os.environ.copy()
        env.setdefault("TERM", "xterm-256color")
        env["PYTHONIOENCODING"] = "utf-8"

        if isinstance(target, ProjectConfig) and target.conda_env:
            conda_bin = conda_env_bin(target.conda_env)
            if conda_bin:
                sep = os.pathsep
                env["PATH"] = f"{conda_bin}{sep}{env.get('PATH', '')}"

        try:
            copilot_dir = os.path.dirname(self.resolve_binary())
        except CliBinaryNotFoundError:
            copilot_dir = ""
        prepend_path_dir(env, copilot_dir)
        return env
