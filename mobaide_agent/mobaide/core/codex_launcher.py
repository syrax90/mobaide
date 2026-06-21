"""Launcher for the OpenAI Codex CLI (``codex``).

Sibling of :mod:`claude_launcher`, sharing the CLI-agnostic primitives in
:mod:`cli_launcher`. Codex is deliberately thinner than Claude: no git worktree,
no attention hooks (Codex has no ``--settings`` equivalent), and no Linear-MCP /
``.mcp.json`` project files. Flags are verified against
``developers.openai.com/codex/cli/reference``.
"""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass

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
    windows_npm_fallbacks,
)
from .projects_config import ProjectConfig

logger = logging.getLogger(__name__)

_CODEX_BIN_ENV_VAR = "MOBAIDE_CODEX_BIN"


class CodexBinaryNotFoundError(CliBinaryNotFoundError):
    """Raised when the Codex CLI cannot be located on the host."""

    cli_name = "codex"
    _INSTALL_HINT = (
        "Codex CLI not found. Install it with:\n"
        "    npm install -g @openai/codex\n"
        "or set MOBAIDE_CODEX_BIN to its absolute path in the agent's env file."
    )


def _posix_fallback_candidates() -> list[str]:
    return posix_npm_fallbacks("codex")


def _windows_fallback_candidates() -> list[str]:
    return windows_npm_fallbacks("codex")


def _resolve_codex_binary(override: str | None = None) -> str:
    """Locate the Codex CLI executable via the shared resolver (mirrors Claude)."""
    if sys.platform == "win32":
        which_names = ("codex", "codex.cmd", "codex.exe")
        fallbacks = _windows_fallback_candidates()
    else:
        which_names = ("codex",)
        fallbacks = _posix_fallback_candidates()
    return resolve_cli_binary(
        override=override,
        env_var=_CODEX_BIN_ENV_VAR,
        which_names=which_names,
        fallbacks=fallbacks,
        error_cls=CodexBinaryNotFoundError,
    )


@dataclass(frozen=True)
class CodexOptions:
    """Codex CLI launch options.

    Defaults reproduce the app's chosen v1 preset — sandboxed but non-interactive
    (``--sandbox workspace-write --ask-for-approval never``), because the mobile
    client cannot yet mediate Codex approval prompts (deferred to the control-key
    work). ``full_auto`` maps to ``--dangerously-bypass-approvals-and-sandbox``
    (Codex's ``--yolo``), the equivalent of Claude's ``--dangerously-skip-permissions``;
    when set, the sandbox/approval flags are omitted (they conflict with full bypass).
    """

    model: str | None = None
    sandbox: str = "workspace-write"
    approval: str = "never"
    reasoning_effort: str | None = "high"
    full_auto: bool = False
    initial_prompt: str | None = None


class CodexLauncher:
    """Build a ``codex`` invocation (argv / env / cwd) from a target and options."""

    def __init__(self, codex_bin_override: str | None = None) -> None:
        self._codex_bin_override = codex_bin_override
        self._resolved_binary: str | None = None

    def _resolve_binary(self) -> str:
        if self._resolved_binary is None:
            self._resolved_binary = _resolve_codex_binary(
                override=self._codex_bin_override,
            )
        return self._resolved_binary

    async def build(
        self,
        target: LaunchTarget,
        options: CodexOptions,
        session_id: str | None = None,
    ) -> LaunchSpec:
        # ``session_id`` is accepted for ``CliLauncher`` parity but unused: Codex
        # has no per-session hooks. Resolving the binary first (inside
        # ``_build_argv``) means a missing CLI aborts before any cwd work.
        argv = self._build_argv(options)
        cwd = self._resolve_cwd(target)
        env = self._build_env(target)
        return LaunchSpec(argv=argv, cwd=cwd, env=env, created_files=[])

    def _build_argv(self, options: CodexOptions) -> list[str]:
        argv: list[str] = [self._resolve_binary()]
        if options.model:
            argv += ["--model", options.model]
        if options.full_auto:
            argv.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            argv += ["--sandbox", options.sandbox]
            argv += ["--ask-for-approval", options.approval]
        if options.reasoning_effort:
            # ``-c key=value`` config override; the value parses as TOML, else as
            # a literal string — so no quoting is needed for a bare word, and the
            # argv is exec'd directly (never through a shell).
            argv += ["-c", f"model_reasoning_effort={options.reasoning_effort}"]
        if options.initial_prompt:
            # Codex takes the initial prompt as a trailing positional argument.
            argv.append(options.initial_prompt)
        return argv

    def _resolve_cwd(self, target: LaunchTarget) -> str:
        # Codex v1 has no worktree concept: a project session runs in the project
        # root, an ad-hoc session in its bare cwd. Both are existence-checked so a
        # bad path surfaces as a clean 400 rather than a pty spawn error.
        if isinstance(target, AdHocLaunch):
            return check_cwd_exists(target.cwd)
        return check_cwd_exists(target.path)

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
            codex_dir = os.path.dirname(self._resolve_binary())
        except CliBinaryNotFoundError:
            codex_dir = ""
        prepend_path_dir(env, codex_dir)
        return env
