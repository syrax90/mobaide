"""Launcher for the OpenAI Codex CLI (``codex``).

Sibling of :mod:`claude_launcher`, sharing the CLI-agnostic primitives in
:mod:`cli_launcher`. Codex is thinner than Claude: no attention hooks (Codex has
no ``--settings`` equivalent) and no Linear-MCP / ``.mcp.json`` project files. It
does support git worktrees on stored projects, resolved the same way Claude's
Standard mode does (``create_standard_worktree``). Flags are verified against
``developers.openai.com/codex/cli/reference``.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .cli_launcher import (
    WINDOWS_NO_WINDOW,
    CliAuthRequiredError,
    CliBinaryNotFoundError,
    LaunchSpec,
    LaunchTarget,
    conda_env_bin,
    posix_npm_fallbacks,
    prepend_path_dir,
    resolve_cli_binary,
    resolve_standard_worktree_cwd,
    shim_safe_argv,
    windows_npm_fallbacks,
)
from .codex_auth import codex_auth_present
from .projects_config import ProjectConfig

if TYPE_CHECKING:
    from mobaide.linear.worktree import WorktreeManager

_CODEX_BIN_ENV_VAR = "MOBAIDE_CODEX_BIN"
# ``codex debug models --json`` is a fast local listing; bound it so a wedged
# probe can never stall the startup model-cache warm task indefinitely.
_MODELS_TIMEOUT = 10.0
# Served when enumeration fails for any reason (missing binary, non-zero exit,
# unparseable JSON): ``debug models`` has no stability contract (a stable
# top-level listing was declined in openai/codex#8871), so a new agent always
# reports Codex models — at worst this curated set.
_CODEX_FALLBACK_MODELS = ("gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex-spark")


class CodexBinaryNotFoundError(CliBinaryNotFoundError):
    """Raised when the Codex CLI cannot be located on the host."""

    cli_name = "codex"
    _INSTALL_HINT = (
        "Codex CLI not found. Install it with:\n"
        "    npm install -g @openai/codex\n"
        "or set MOBAIDE_CODEX_BIN to its absolute path in the agent's env file."
    )


class CodexAuthRequiredError(CliAuthRequiredError):
    """Raised when the Codex CLI is installed but not authenticated.

    Guards the spawn path: launching an unauthenticated ``codex`` would surface
    its interactive login screen inside the PTY relay. The router/relay map this
    to a ``codex_auth_required`` marker so the app can prompt for an API-key
    login instead.
    """

    cli_name = "codex"
    _AUTH_HINT = (
        "Codex is not authenticated. Sign in from the app with an OpenAI API "
        "key, or run 'codex login' on the agent host."
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


def _run_codex_models_json(binary: str) -> str | None:
    """Run ``codex debug models --json`` and return stdout, ``None`` on non-zero
    exit. Module-level so tests can monkeypatch the subprocess seam (mirrors
    ``_resolve_codex_binary``). Blocking — callers offload via
    ``asyncio.to_thread`` (see ``codex_auth`` for the Windows-event-loop
    rationale).
    """
    completed = subprocess.run(
        shim_safe_argv(binary, "debug", "models", "--json"),
        capture_output=True,
        timeout=_MODELS_TIMEOUT,
        creationflags=WINDOWS_NO_WINDOW,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.decode(errors="replace") if completed.stdout else ""


def _parse_codex_models(output: str) -> list[str] | None:
    """Best-effort parse of ``codex debug models --json`` output.

    A debug-namespace command carries no output-stability promise, so every
    plausible shape is accepted: a bare JSON list of strings, a list of objects
    (first of ``id``/``name``/``model``/``slug`` wins), or an object wrapping
    either under ``"models"``. Anything else yields ``None`` and the caller
    serves the curated fallback list.
    """
    try:
        payload = json.loads(output)
    except ValueError:
        return None
    if isinstance(payload, dict):
        payload = payload.get("models")
    if not isinstance(payload, list):
        return None
    models: list[str] = []
    for item in payload:
        name = ""
        if isinstance(item, str):
            name = item.strip()
        elif isinstance(item, dict):
            for key in ("id", "name", "model", "slug"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    name = value.strip()
                    break
        if name:
            models.append(name)
    return models or None


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
    # Worktree intent for a stored git project, resolved by ``_resolve_cwd``
    # exactly like Claude's Standard mode; ignored for ad-hoc launches.
    worktree: bool = False
    branch_name: str | None = None


class CodexLauncher:
    """Build a ``codex`` invocation (argv / env / cwd) from a target and options."""

    def __init__(
        self,
        codex_bin_override: str | None = None,
        worktree_mgr: WorktreeManager | None = None,
    ) -> None:
        self._codex_bin_override = codex_bin_override
        self._worktree_mgr = worktree_mgr
        self._resolved_binary: str | None = None
        self._cached_models: tuple[str, ...] | None = None

    def _resolve_binary(self) -> str:
        if self._resolved_binary is None:
            self._resolved_binary = _resolve_codex_binary(
                override=self._codex_bin_override,
            )
        return self._resolved_binary

    def resolve_binary(self) -> str:
        """Public access to the resolved Codex binary (honours the configured
        override) so the login endpoint runs the same ``codex`` sessions use."""
        return self._resolve_binary()

    @property
    def cached_models(self) -> tuple[str, ...] | None:
        """Model names enumerated by a prior :meth:`list_models` — never spawns."""
        return self._cached_models

    async def list_models(self) -> list[str]:
        """Enumerate Codex models once and memoize the result.

        Run from the startup warm task (``warm_cli_model_caches``) — never from
        the ``/api/version`` request path. ANY failure (missing binary,
        non-zero exit, timeout, unparseable output) memoizes the curated
        ``_CODEX_FALLBACK_MODELS`` instead, so capabilities from a new agent
        always carry a Codex model list.
        """
        if self._cached_models is not None:
            return list(self._cached_models)
        models: list[str] | None = None
        try:
            binary = self._resolve_binary()
        except CliBinaryNotFoundError:
            binary = None
        if binary is not None:
            try:
                output = await asyncio.to_thread(_run_codex_models_json, binary)
            except (OSError, subprocess.SubprocessError):
                output = None
            if output is not None:
                models = _parse_codex_models(output)
        self._cached_models = tuple(models) if models else _CODEX_FALLBACK_MODELS
        return list(self._cached_models)

    async def build(
        self,
        target: LaunchTarget,
        options: CodexOptions,
        session_id: str | None = None,
    ) -> LaunchSpec:
        # ``session_id`` is accepted for ``CliLauncher`` parity but unused: Codex
        # has no per-session hooks. Build argv (resolves the binary) and verify
        # auth before any cwd work, so a missing CLI (503) or an unauthenticated
        # one (409) aborts before a worktree is created. The auth check reads the
        # built launch env (same CODEX_HOME / OPENAI_API_KEY / keyring the session
        # would use) and may probe ``codex login status``, so it needs the binary.
        argv = self._build_argv(options)
        env = self._build_env(target)
        if not await codex_auth_present(self._resolve_binary(), env):
            raise CodexAuthRequiredError()
        cwd = await self._resolve_cwd(target, options)
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

    async def _resolve_cwd(self, target: LaunchTarget, options: CodexOptions) -> str:
        # A project session runs in the project root, or — when worktree=True — in
        # a freshly created git worktree (the same Standard-mode resolution Claude
        # uses); an ad-hoc session always runs in its bare cwd. Shared guards live
        # in cli_launcher so the per-CLI modules stay thin.
        return await resolve_standard_worktree_cwd(
            target,
            worktree=options.worktree,
            branch_name=options.branch_name,
            worktree_mgr=self._worktree_mgr,
            fallback_prefix="codex-session",
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
            codex_dir = os.path.dirname(self._resolve_binary())
        except CliBinaryNotFoundError:
            codex_dir = ""
        prepend_path_dir(env, codex_dir)
        return env
