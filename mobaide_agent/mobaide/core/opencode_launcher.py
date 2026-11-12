"""Launcher for the OpenCode CLI (``opencode``).

Needs no hooks, project files or sign-in probe; terminal sessions get attention
from the OSC 7501 status reports that OpenCode 2 writes. Only ``--auto`` and
``--prompt`` are used: both OpenCode lines accept them, and OpenCode 2 has no
``--model`` or ``--agent`` flag for its TUI.
"""
from __future__ import annotations

import json
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
    posix_npm_fallbacks,
    prepend_path_dir,
    resolve_cli_binary,
    resolve_standard_worktree_cwd,
    windows_npm_fallbacks,
)
from .projects_config import ProjectConfig

if TYPE_CHECKING:
    from mobaide.linear.worktree import WorktreeManager

_OPENCODE_BIN_ENV_VAR = "MOBAIDE_OPENCODE_BIN"
# Inline JSON that OpenCode 2 merges over the user's ``cli.json`` (TUI settings).
_CLI_CONFIG_ENV_VAR = "OPENCODE_CLI_CONFIG_CONTENT"
# Longest first prompt handed to ``--prompt``: far above a real prompt, far
# below the argv limit of any supported OS.
INITIAL_PROMPT_MAX = 32_000


class OpenCodeBinaryNotFoundError(CliBinaryNotFoundError):
    """Raised when the OpenCode CLI cannot be located on the host."""

    cli_name = "opencode"
    _INSTALL_HINT = (
        "OpenCode CLI not found. Install it from\n"
        "    https://opencode.ai/docs/\n"
        "or set MOBAIDE_OPENCODE_BIN to its absolute path in the agent's env file."
    )


def _posix_fallback_candidates() -> list[str]:
    # The install script lands in ``~/.opencode/bin``; npm, Homebrew and Bun
    # cover the package-manager installs.
    home = Path.home()
    return [
        str(home / ".opencode" / "bin" / "opencode"),
        *posix_npm_fallbacks("opencode"),
        str(home / ".bun" / "bin" / "opencode"),
    ]


def _windows_fallback_candidates() -> list[str]:
    candidates: list[str] = []
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        candidates.append(os.path.join(user_profile, ".opencode", "bin", "opencode.exe"))
    candidates += windows_npm_fallbacks("opencode")
    if user_profile:
        candidates.append(os.path.join(user_profile, ".bun", "bin", "opencode.exe"))
    return candidates


def _resolve_opencode_binary(override: str | None = None) -> str:
    """Locate the OpenCode CLI executable via the shared resolver."""
    if sys.platform == "win32":
        which_names: tuple[str, ...] = ("opencode", "opencode.exe", "opencode.cmd")
        fallbacks = _windows_fallback_candidates()
    else:
        which_names = ("opencode",)
        fallbacks = _posix_fallback_candidates()
    return resolve_cli_binary(
        override=override,
        env_var=_OPENCODE_BIN_ENV_VAR,
        which_names=which_names,
        fallbacks=fallbacks,
        error_cls=OpenCodeBinaryNotFoundError,
    )


def _turn_tabs_off(env: dict[str, str]) -> None:
    """Make the OpenCode TUI show one session, not the folder's tab set.

    The status report covers every tab of the folder, so a request waiting in
    one terminal session would raise "permission needed" in every other session
    of the project. The user's own tabs choice in this variable wins.
    """
    raw = env.get(_CLI_CONFIG_ENV_VAR, "")
    try:
        settings = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return
    if not isinstance(settings, dict) or "tabs" in settings:
        return
    settings["tabs"] = {"mode": "off"}
    env[_CLI_CONFIG_ENV_VAR] = json.dumps(settings)


@dataclass(frozen=True)
class OpenCodeOptions:
    """OpenCode CLI launch options.

    ``auto_approve`` maps to ``--auto``: OpenCode then accepts every permission
    request that its rules do not deny. ``initial_prompt`` is sent as the first
    message (``--prompt``). ``connect`` runs ``opencode auth login`` in place
    of the TUI, so the user can sign in to a model provider from the terminal
    view; the other options do not apply to it.
    """

    auto_approve: bool = False
    initial_prompt: str | None = None
    connect: bool = False
    # Worktree intent for a stored git project, resolved by ``resolve_cwd``
    # exactly like Codex; ignored for ad-hoc launches.
    worktree: bool = False
    branch_name: str | None = None
    base_branch: str | None = None

    def __post_init__(self) -> None:
        if self.initial_prompt is not None and len(self.initial_prompt) > INITIAL_PROMPT_MAX:
            raise ValueError(
                f"initial_prompt must be at most {INITIAL_PROMPT_MAX} characters"
            )


class OpenCodeLauncher:
    """Build an ``opencode`` invocation (argv / env / cwd) from a target and options."""

    def __init__(
        self,
        opencode_bin_override: str | None = None,
        worktree_mgr: WorktreeManager | None = None,
    ) -> None:
        self._opencode_bin_override = opencode_bin_override
        self._worktree_mgr = worktree_mgr
        self._resolved_binary: str | None = None

    def resolve_binary(self) -> str:
        """The resolved ``opencode`` binary (honours the configured override),
        so availability probes match what a session spawn would use."""
        if self._resolved_binary is None:
            self._resolved_binary = _resolve_opencode_binary(
                override=self._opencode_bin_override,
            )
        return self._resolved_binary

    async def build(
        self,
        target: LaunchTarget,
        options: OpenCodeOptions,
        session_id: str | None = None,
    ) -> LaunchSpec:
        # ``session_id`` is accepted for ``CliLauncher`` parity but unused:
        # OpenCode has no per-session hooks.
        argv = self._build_argv(options)
        env = self.build_env(target)
        _turn_tabs_off(env)
        cwd = await self.resolve_cwd(target, options)
        # Parts of OpenCode take the folder from ``PWD``, not from the real cwd.
        env["PWD"] = cwd
        return LaunchSpec(argv=argv, cwd=cwd, env=env, created_files=[])

    def _build_argv(self, options: OpenCodeOptions) -> list[str]:
        argv = [self.resolve_binary()]
        if options.connect:
            return [*argv, "auth", "login"]
        if options.auto_approve:
            argv.append("--auto")
        if options.initial_prompt:
            # One element with ``=``: a prompt that starts with ``-`` can
            # never be read as another flag.
            argv.append(f"--prompt={options.initial_prompt}")
        return argv

    async def resolve_cwd(self, target: LaunchTarget, options: OpenCodeOptions) -> str:
        """The session folder: the project root, a new git worktree when
        ``worktree`` is set on a stored git project, or the bare ad-hoc cwd."""
        return await resolve_standard_worktree_cwd(
            target,
            # A sign-in changes nothing in the project: it needs no worktree.
            worktree=options.worktree and not options.connect,
            branch_name=options.branch_name,
            base_branch=options.base_branch,
            worktree_mgr=self._worktree_mgr,
            fallback_prefix="opencode-session",
        )

    def build_env(self, target: LaunchTarget) -> dict[str, str]:
        """The environment of an OpenCode process for ``target``. Does not
        need the binary, so a chat session can use it before OpenCode is
        looked up."""
        env = os.environ.copy()
        env.setdefault("TERM", "xterm-256color")
        env["PYTHONIOENCODING"] = "utf-8"

        if isinstance(target, ProjectConfig) and target.conda_env:
            conda_bin = conda_env_bin(target.conda_env)
            if conda_bin:
                sep = os.pathsep
                env["PATH"] = f"{conda_bin}{sep}{env.get('PATH', '')}"

        try:
            opencode_dir = os.path.dirname(self.resolve_binary())
        except CliBinaryNotFoundError:
            opencode_dir = ""
        prepend_path_dir(env, opencode_dir)
        return env
