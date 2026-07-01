"""Launcher for the Google Gemini CLI (``gemini``).

Sibling of :mod:`codex_launcher`, sharing the CLI-agnostic primitives in
:mod:`cli_launcher`. Like Codex it is thinner than Claude (no attention hooks, no
``.mcp.json`` project files) and supports git worktrees on stored projects,
resolved the same way Claude's Standard mode does (``create_standard_worktree``).
Flags are verified against ``geminicli.com/docs/cli/cli-reference``.

Auth is DETECTION-only here (:func:`gemini_auth_present`): Gemini has no
``login``/``--with-api-key`` subcommand and its "Sign in with Google" OAuth needs
a browser, so there is no in-app login path — the operator authenticates ``gemini``
on the host and the launcher raises :class:`GeminiAuthRequiredError` when no
credential is found. In-app key entry is tracked separately.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .cli_launcher import (
    CliAuthRequiredError,
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
from .gemini_auth import gemini_auth_present
from .projects_config import ProjectConfig

if TYPE_CHECKING:
    from mobaide.linear.worktree import WorktreeManager

_GEMINI_BIN_ENV_VAR = "MOBAIDE_GEMINI_BIN"


class GeminiBinaryNotFoundError(CliBinaryNotFoundError):
    """Raised when the Gemini CLI cannot be located on the host."""

    cli_name = "gemini"
    _INSTALL_HINT = (
        "Gemini CLI not found. Install it with:\n"
        "    npm install -g @google/gemini-cli\n"
        "or set MOBAIDE_GEMINI_BIN to its absolute path in the agent's env file."
    )


class GeminiAuthRequiredError(CliAuthRequiredError):
    """Raised when the Gemini CLI is installed but not authenticated.

    Guards the spawn path: launching an unauthenticated ``gemini`` would surface
    its interactive "Sign in with Google" screen inside the PTY relay. The
    router/relay map this to a ``gemini_auth_required`` marker so the app can tell
    the operator to authenticate on the host (in-app key entry is deferred).
    """

    cli_name = "gemini"
    _AUTH_HINT = (
        "Gemini is not authenticated. Set GEMINI_API_KEY (or configure Vertex AI "
        "/ sign in with 'gemini') on the agent host, then retry."
    )


def _posix_fallback_candidates() -> list[str]:
    return posix_npm_fallbacks("gemini")


def _windows_fallback_candidates() -> list[str]:
    return windows_npm_fallbacks("gemini")


def _resolve_gemini_binary(override: str | None = None) -> str:
    """Locate the Gemini CLI executable via the shared resolver (mirrors Codex)."""
    if sys.platform == "win32":
        which_names = ("gemini", "gemini.cmd", "gemini.exe")
        fallbacks = _windows_fallback_candidates()
    else:
        which_names = ("gemini",)
        fallbacks = _posix_fallback_candidates()
    return resolve_cli_binary(
        override=override,
        env_var=_GEMINI_BIN_ENV_VAR,
        which_names=which_names,
        fallbacks=fallbacks,
        error_cls=GeminiBinaryNotFoundError,
    )


@dataclass(frozen=True)
class GeminiOptions:
    """Gemini CLI launch options.

    Defaults launch an interactive session with Gemini's own defaults (model
    ``auto``, ``--approval-mode default``); the Android presets map to
    ``plan`` / ``auto_edit`` / ``yolo``. ``--approval-mode`` replaces the
    deprecated ``--yolo`` flag. ``sandbox`` toggles ``--sandbox``;
    ``include_directories`` adds extra workspace roots. ``worktree`` /
    ``branch_name`` drive the shared Standard-mode worktree resolver exactly like
    Claude/Codex; they are ignored for ad-hoc launches.
    """

    model: str | None = None
    approval_mode: str = "default"
    include_directories: tuple[str, ...] = ()
    sandbox: bool = False
    worktree: bool = False
    branch_name: str | None = None


class GeminiLauncher:
    """Build a ``gemini`` invocation (argv / env / cwd) from a target and options."""

    def __init__(
        self,
        gemini_bin_override: str | None = None,
        worktree_mgr: WorktreeManager | None = None,
    ) -> None:
        self._gemini_bin_override = gemini_bin_override
        self._worktree_mgr = worktree_mgr
        self._resolved_binary: str | None = None

    def _resolve_binary(self) -> str:
        if self._resolved_binary is None:
            self._resolved_binary = _resolve_gemini_binary(
                override=self._gemini_bin_override,
            )
        return self._resolved_binary

    def resolve_binary(self) -> str:
        """Public access to the resolved Gemini binary (honours the configured
        override), satisfying the ``CliLauncher`` Protocol and the availability
        probe behind ``/api/version`` capabilities."""
        return self._resolve_binary()

    async def build(
        self,
        target: LaunchTarget,
        options: GeminiOptions,
        session_id: str | None = None,
    ) -> LaunchSpec:
        # ``session_id`` is accepted for ``CliLauncher`` parity but unused: Gemini
        # has no per-session hooks. Build argv (resolves the binary) and verify
        # auth before any cwd work, so a missing CLI (503) or an unauthenticated
        # one (409) aborts before a worktree is created. Detection is a pure
        # env/filesystem probe (Gemini has no ``login status`` subcommand), so it
        # reads the built launch env the session would use.
        argv = self._build_argv(options)
        env = self._build_env(target)
        if not gemini_auth_present(env):
            raise GeminiAuthRequiredError()
        cwd = await self._resolve_cwd(target, options)
        return LaunchSpec(argv=argv, cwd=cwd, env=env, created_files=[])

    def _build_argv(self, options: GeminiOptions) -> list[str]:
        argv: list[str] = [self._resolve_binary()]
        if options.model:
            argv += ["--model", options.model]
        argv += ["--approval-mode", options.approval_mode]
        if options.sandbox:
            argv.append("--sandbox")
        for directory in options.include_directories:
            # Repeat the flag per directory (the CLI accepts comma-separated or
            # multiple flags) so a path containing a comma can't corrupt the list.
            argv += ["--include-directories", directory]
        return argv

    async def _resolve_cwd(self, target: LaunchTarget, options: GeminiOptions) -> str:
        # A project session runs in the project root, or — when worktree=True — in
        # a freshly created git worktree (the same Standard-mode resolution Claude
        # uses); an ad-hoc session always runs in its bare cwd. Shared guards live
        # in cli_launcher so the per-CLI modules stay thin.
        return await resolve_standard_worktree_cwd(
            target,
            worktree=options.worktree,
            branch_name=options.branch_name,
            worktree_mgr=self._worktree_mgr,
            fallback_prefix="gemini-session",
        )

    def _build_env(self, target: LaunchTarget) -> dict[str, str]:
        env = os.environ.copy()
        env.setdefault("TERM", "xterm-256color")
        env["PYTHONIOENCODING"] = "utf-8"

        if isinstance(target, ProjectConfig) and target.conda_env:
            conda_bin = conda_env_bin(target.conda_env)
            if conda_bin:
                sep = os.pathsep
                env["PATH"] = f"{conda_bin}{sep}{env.get('PATH', '')}"

        try:
            gemini_dir = os.path.dirname(self._resolve_binary())
        except CliBinaryNotFoundError:
            gemini_dir = ""
        prepend_path_dir(env, gemini_dir)
        return env
