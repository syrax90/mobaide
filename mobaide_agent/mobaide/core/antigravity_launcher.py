"""Launcher for the Google Antigravity CLI (``agy``).

Sibling of :mod:`codex_launcher`, sharing the CLI-agnostic primitives in
:mod:`cli_launcher`. Antigravity is the thinnest managed CLI: no attention
hooks, no project files, and no pre-spawn auth probe — ``agy`` authenticates
via Google Sign-In / the system keyring with no env-var or config-file marker
to detect, so binary presence is the only availability signal and an
unauthenticated ``agy`` simply renders its login prompt inside the PTY. It
supports git worktrees on stored projects, resolved the same way Codex does
(``create_standard_worktree``). Flags are verified against ``agy --help``
(https://antigravity.google/docs/cli/reference): only the curated interactive
set is exposed — ``--model`` / ``--dangerously-skip-permissions`` /
``--sandbox`` / repeatable ``--add-dir``.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .cli_launcher import (
    WINDOWS_NO_WINDOW,
    CliBinaryNotFoundError,
    LaunchSpec,
    LaunchTarget,
    conda_env_bin,
    prepend_path_dir,
    resolve_cli_binary,
    resolve_standard_worktree_cwd,
    shim_safe_argv,
)
from .projects_config import ProjectConfig

if TYPE_CHECKING:
    from mobaide.linear.worktree import WorktreeManager

_AGY_BIN_ENV_VAR = "MOBAIDE_AGY_BIN"
# ``agy models`` is a fast local listing; bound it so a wedged probe can never
# stall the startup model-cache warm task indefinitely.
_MODELS_TIMEOUT = 10.0


class AntigravityBinaryNotFoundError(CliBinaryNotFoundError):
    """Raised when the Antigravity CLI cannot be located on the host."""

    cli_name = "antigravity"
    _INSTALL_HINT = (
        "Antigravity CLI not found. Install the standalone `agy` binary from\n"
        "    https://antigravity.google/docs/cli\n"
        "or set MOBAIDE_AGY_BIN to its absolute path in the agent's env file."
    )


def _posix_fallback_candidates() -> list[str]:
    # ``agy`` is a standalone binary (not npm): its installer lands in
    # ``~/.local/bin``; the system paths cover manual/Homebrew placement.
    home = Path.home()
    return [
        str(home / ".local" / "bin" / "agy"),
        "/usr/local/bin/agy",
        "/opt/homebrew/bin/agy",
    ]


def _windows_fallback_candidates() -> list[str]:
    candidates: list[str] = []
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        candidates.append(os.path.join(user_profile, ".local", "bin", "agy.exe"))
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(
            os.path.join(local_app_data, "Programs", "Antigravity", "agy.exe")
        )
    program_files = os.environ.get("ProgramFiles")
    if program_files:
        candidates.append(os.path.join(program_files, "Antigravity", "agy.exe"))
    return candidates


def _resolve_agy_binary(override: str | None = None) -> str:
    """Locate the Antigravity CLI executable via the shared resolver."""
    if sys.platform == "win32":
        which_names = ("agy", "agy.exe", "agy.cmd")
        fallbacks = _windows_fallback_candidates()
    else:
        which_names = ("agy",)
        fallbacks = _posix_fallback_candidates()
    return resolve_cli_binary(
        override=override,
        env_var=_AGY_BIN_ENV_VAR,
        which_names=which_names,
        fallbacks=fallbacks,
        error_cls=AntigravityBinaryNotFoundError,
    )


def _run_agy_models(binary: str) -> str | None:
    """Run ``agy models`` and return its stdout, or ``None`` on non-zero exit.

    Module-level so tests can monkeypatch the subprocess seam (mirrors
    ``_resolve_agy_binary``). Blocking — callers offload via
    ``asyncio.to_thread`` (``create_subprocess_exec`` raises
    ``NotImplementedError`` under the WindowsSelectorEventLoop the agent runs
    uvicorn on; see ``codex_auth`` for the precedent).
    """
    completed = subprocess.run(
        shim_safe_argv(binary, "models"),
        capture_output=True,
        timeout=_MODELS_TIMEOUT,
        creationflags=WINDOWS_NO_WINDOW,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.decode(errors="replace") if completed.stdout else ""


def agy_model_name(line: str) -> str:
    """The name ``--model`` takes from one ``agy models`` line: newer builds
    print the model id, a tab and the display name; older ones the name alone."""
    return line.split("\t", 1)[-1].strip()


@dataclass(frozen=True)
class AntigravityOptions:
    """Antigravity CLI launch options.

    ``model`` carries the full display name ``agy models`` prints (e.g.
    ``Gemini 3.1 Pro (Low)`` — spaces and parens included) and is forwarded
    verbatim as a single argv element. ``skip_permissions`` maps to
    ``--dangerously-skip-permissions`` (same wire naming as the Claude option
    for the same flag). ``skip_permissions`` and ``sandbox`` are mutually
    exclusive: skip-permissions auto-approves ``bypassSandbox`` actions, so the
    pair would silently defeat the sandbox (google-antigravity/antigravity-cli
    #36). The wire DTO rejects the combination with a 422; this guard is
    defense-in-depth for internally constructed options. ``model`` and
    ``add_dirs`` are also rejected when they look like a CLI flag (leading
    ``-``), so a flag-shaped value can't smuggle a curated-out flag (e.g. the
    blocked ``--dangerously-skip-permissions``) into argv.
    """

    model: str | None = None
    skip_permissions: bool = False
    sandbox: bool = False
    add_dirs: tuple[str, ...] = ()
    # Worktree intent for a stored git project, resolved by ``_resolve_cwd``
    # exactly like Codex; ignored for ad-hoc launches.
    worktree: bool = False
    branch_name: str | None = None

    def __post_init__(self) -> None:
        if self.skip_permissions and self.sandbox:
            raise ValueError(
                "skip_permissions and sandbox are mutually exclusive: "
                "--dangerously-skip-permissions auto-approves sandbox escapes"
            )
        if self.model is not None:
            # A choice saved from an older agent may hold a whole ``agy
            # models`` line (id, tab, name): keep the name, checked below.
            object.__setattr__(self, "model", agy_model_name(self.model) or None)
        if self.model is not None and self.model.startswith("-"):
            raise ValueError("model must not look like a CLI flag (leading '-')")
        if any(d.startswith("-") for d in self.add_dirs):
            raise ValueError(
                "add_dirs entries must not look like CLI flags (leading '-')"
            )


class AntigravityLauncher:
    """Build an ``agy`` invocation (argv / env / cwd) from a target and options."""

    def __init__(
        self,
        agy_bin_override: str | None = None,
        worktree_mgr: WorktreeManager | None = None,
    ) -> None:
        self._agy_bin_override = agy_bin_override
        self._worktree_mgr = worktree_mgr
        self._resolved_binary: str | None = None
        self._cached_models: tuple[str, ...] | None = None

    def _resolve_binary(self) -> str:
        if self._resolved_binary is None:
            self._resolved_binary = _resolve_agy_binary(
                override=self._agy_bin_override,
            )
        return self._resolved_binary

    def resolve_binary(self) -> str:
        """Public access to the resolved ``agy`` binary (honours the configured
        override) so availability probes match what a session spawn would use."""
        return self._resolve_binary()

    @property
    def cached_models(self) -> tuple[str, ...] | None:
        """Model names enumerated by a prior :meth:`list_models` — never spawns."""
        return self._cached_models

    async def list_models(self) -> list[str] | None:
        """Enumerate ``agy models`` once and memoize the result.

        Run from the startup warm task (``warm_cli_model_caches``) — never from
        the ``/api/version`` request path. Any failure (missing binary,
        non-zero exit, timeout, empty output) returns ``None`` without
        memoizing, so capabilities omit the ``models`` key and the client falls
        back to its hardcoded list.
        """
        if self._cached_models is not None:
            return list(self._cached_models)
        try:
            binary = self._resolve_binary()
        except CliBinaryNotFoundError:
            return None
        try:
            output = await asyncio.to_thread(_run_agy_models, binary)
        except (OSError, subprocess.SubprocessError):
            return None
        if output is None:
            return None
        models = [agy_model_name(line) for line in output.splitlines() if line.strip()]
        models = [name for name in models if name]
        if not models:
            return None
        self._cached_models = tuple(models)
        return models

    async def build(
        self,
        target: LaunchTarget,
        options: AntigravityOptions,
        session_id: str | None = None,
    ) -> LaunchSpec:
        # ``session_id`` is accepted for ``CliLauncher`` parity but unused:
        # Antigravity has no per-session hooks. There is deliberately no
        # pre-spawn auth probe (unlike Codex) — ``agy`` exposes no detectable
        # credential marker, so an unauthenticated CLI renders its login prompt
        # inside the PTY instead of being blocked with a 409.
        argv = self._build_argv(options)
        env = self._build_env(target)
        cwd = await self._resolve_cwd(target, options)
        return LaunchSpec(argv=argv, cwd=cwd, env=env, created_files=[])

    def _build_argv(self, options: AntigravityOptions) -> list[str]:
        argv: list[str] = [self._resolve_binary()]
        if options.model:
            # Full display names contain spaces/parens; safe as one argv
            # element because the spawn execs argv directly, never via a shell.
            argv += ["--model", options.model]
        if options.skip_permissions:
            argv.append("--dangerously-skip-permissions")
        if options.sandbox:
            argv.append("--sandbox")
        for directory in options.add_dirs:
            argv += ["--add-dir", directory]
        return argv

    async def _resolve_cwd(
        self, target: LaunchTarget, options: AntigravityOptions
    ) -> str:
        # A project session runs in the project root, or — when worktree=True —
        # in a freshly created git worktree; an ad-hoc session always runs in
        # its bare cwd. Shared guards live in cli_launcher so the per-CLI
        # modules stay thin.
        return await resolve_standard_worktree_cwd(
            target,
            worktree=options.worktree,
            branch_name=options.branch_name,
            worktree_mgr=self._worktree_mgr,
            fallback_prefix="antigravity-session",
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
            agy_dir = os.path.dirname(self._resolve_binary())
        except CliBinaryNotFoundError:
            agy_dir = ""
        prepend_path_dir(env, agy_dir)
        return env
