"""CLI-agnostic launcher primitives shared by every managed CLI integration.

The agent can launch more than one coding CLI (Claude Code, Codex CLI,
Antigravity CLI, GitHub Copilot CLI). Each gets its own launcher module
(``claude_launcher`` / ``codex_launcher`` / ``antigravity_launcher`` /
``copilot_launcher``), but the generic scaffolding —
the ``LaunchSpec`` result shape, the ad-hoc launch target,
binary resolution (override → env → PATH → npm fallbacks), and the
``CliBinaryNotFoundError`` base — lives here so the per-CLI modules stay thin and
do not duplicate the platform-probing logic.
"""
from __future__ import annotations

import asyncio
import glob
import logging
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from .projects_config import ProjectConfig

if TYPE_CHECKING:
    from mobaide.linear.worktree import WorktreeManager

logger = logging.getLogger(__name__)

# CREATE_NO_WINDOW on Windows so one-shot CLI probes never flash a console.
WINDOWS_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def shim_safe_argv(cli_bin: str, *args: str) -> list[str]:
    """``<cli> <args>`` argv, wrapping a Windows ``.cmd``/``.bat`` shim in
    ``cmd /c`` (a shim is not directly CreateProcess-able). Shared by the Codex
    auth/login probes and the per-CLI model-enumeration probes; secrets are
    never passed as argv elements.
    """
    base = [cli_bin, *args]
    if sys.platform == "win32" and cli_bin.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", *base]
    return base


class CliBinaryNotFoundError(RuntimeError):
    """Base error when a managed CLI's executable cannot be located.

    Subclasses set ``cli_name`` (the wire/log discriminator) and
    ``_INSTALL_HINT`` (the CLI-specific, actionable install message). The
    router/relay layers catch this base type and derive their
    ``<cli>_cli_missing`` marker from ``cli_name`` so Claude and Codex map to a
    uniform 503 without duplicated per-CLI handlers.
    """

    cli_name: str = "cli"
    _INSTALL_HINT: str = "CLI not found."

    def __init__(self, searched_paths: list[str]) -> None:
        self.searched_paths = list(searched_paths)
        searched = ", ".join(self.searched_paths) if self.searched_paths else "(none)"
        super().__init__(f"{self._INSTALL_HINT}\nSearched: {searched}")


class CliAuthRequiredError(RuntimeError):
    """Base error when a managed CLI is installed but not authenticated.

    Parallel to :class:`CliBinaryNotFoundError`: subclasses set ``cli_name`` and
    an actionable ``_AUTH_HINT``, and the router/relay layers derive a uniform
    ``<cli>_auth_required`` marker from ``cli_name`` so the mobile client can
    surface a login prompt instead of spawning a session that would hang on the
    CLI's interactive login screen. Distinct from a missing binary (503): an
    unauthenticated-but-present CLI is client-actionable and surfaced as 409 (not
    401, which the app's JWT-refresh interceptor would intercept and retry).
    """

    cli_name: str = "cli"
    _AUTH_HINT: str = "CLI authentication required."

    def __init__(self) -> None:
        super().__init__(self._AUTH_HINT)


@dataclass
class LaunchSpec:
    """Result of building a launch — argv/cwd/env ready to hand to a PTY."""

    argv: list[str]
    cwd: str
    env: dict[str, str]
    created_files: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AdHocLaunch:
    """Ad-hoc launch target: a bare cwd with no stored project context.

    Used by the custom-path entry on the mobile picker to spawn a one-off
    session against an arbitrary folder. No Linear key, no conda env, no project
    files written. The log "source" is recorded as ``<ad-hoc>`` rather than
    synthesising a fake :class:`ProjectConfig`.
    """

    cwd: str


LaunchTarget = ProjectConfig | AdHocLaunch


class CliLauncher(Protocol):
    """Structural contract every managed-CLI launcher satisfies.

    A launcher turns a :class:`LaunchTarget` plus a CLI-specific options object
    into a :class:`LaunchSpec` (argv/cwd/env). ``ClaudeLauncher``,
    ``CodexLauncher``, ``AntigravityLauncher`` and ``CopilotLauncher`` all conform;
    ``SessionManager`` keeps a registry keyed by CLI name and dispatches by
    ``SessionKind``. Launchers MAY additionally expose model enumeration
    (``list_models()`` + a non-spawning ``cached_models``) — discovered by
    duck-typing in :func:`warm_cli_model_caches` / :func:`build_cli_capabilities`
    rather than widening this Protocol, so launchers without it stay untouched.
    """

    async def build(
        self,
        target: LaunchTarget,
        options: Any,
        session_id: str | None = None,
    ) -> LaunchSpec: ...

    def resolve_binary(self) -> str:
        """Resolve the managed CLI's executable path (override → env → PATH →
        fallbacks), raising :class:`CliBinaryNotFoundError` when absent. Lets
        callers probe availability without building a full launch."""
        ...


def check_cwd_exists(cwd: str) -> str:
    """Raise ``FileNotFoundError`` with a clean ``filename`` if ``cwd`` is missing.

    Pre-empts ``pywinpty.PtyProcess.spawn`` / ``subprocess.Popen`` on bad paths
    so the session router/relay can map the error to a 400 with a readable,
    unescaped path — rather than letting Starlette's default 500 handler surface
    a ``repr``-escaped filename. Shared by the CLI launchers' cwd resolution and
    ``SessionManager`` Terminal-mode worktree resolution.
    """
    if not Path(cwd).exists():
        raise FileNotFoundError(2, "No such file or directory", cwd)
    return cwd


async def resolve_standard_worktree_cwd(
    target: LaunchTarget,
    *,
    worktree: bool,
    branch_name: str | None,
    worktree_mgr: WorktreeManager | None,
    fallback_prefix: str,
    base_branch: str | None = None,
) -> str:
    """Worktree-aware cwd resolution shared by the managed CLI launchers.

    Centralises the guard sequence both ``ClaudeLauncher`` (Standard mode) and
    ``CodexLauncher`` apply: an ad-hoc launch, a ``worktree=False`` request, or a
    worktree request against a non-git project all resolve to the bare cwd (a
    requested-but-unhonorable worktree is logged as a downgrade); a worktree
    request on a git project creates one via ``create_standard_worktree`` under
    ``fallback_prefix`` (e.g. ``claude-session`` / ``codex-session``). A missing
    ``WorktreeManager`` is a server misconfiguration and fails loud.

    Claude's Auto Scan (``identifier``) path is NOT routed here — it derives the
    branch from a Linear slug and calls the manager directly — so that launcher
    keeps its slug branch and delegates only the non-identifier case.
    """
    from .projects_store import safe_path_exists

    if isinstance(target, AdHocLaunch):
        if worktree:
            logger.warning(
                "worktree=true requested for ad-hoc launch %r; ignoring", "<ad-hoc>",
            )
        return check_cwd_exists(target.cwd)
    if not worktree:
        return check_cwd_exists(target.path)
    if not safe_path_exists(Path(target.path) / ".git"):
        logger.warning(
            "worktree=true requested for non-git project %r; ignoring", target.name,
        )
        return check_cwd_exists(target.path)
    if worktree_mgr is None:
        raise RuntimeError(
            "worktree=true requested but no WorktreeManager is configured; "
            "this is a server misconfiguration"
        )
    from mobaide.linear.worktree import create_standard_worktree

    result = await create_standard_worktree(
        worktree_mgr,
        target.path,
        branch_name,
        fallback_prefix=fallback_prefix,
        base_branch=base_branch,
    )
    return check_cwd_exists(result.path)


def _is_executable_file(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def _normalise_user_path(path: str) -> str:
    return os.path.abspath(os.path.expandvars(os.path.expanduser(path)))


def _nvm_version_key(path: str) -> tuple[int, ...]:
    """Parse the ``vX.Y.Z`` segment of an nvm path into a comparable tuple.

    nvm directories are named like ``v18.20.4``; lexicographic sort would rank
    ``v9.x`` after ``v10.x`` because ``'1' < '9'``. Falling back to ``(0,)`` for
    unparseable names keeps such entries below any real semver-named one without
    crashing.
    """
    try:
        version_dir = Path(path).parent.parent.name
        parts = version_dir.lstrip("v").split(".")
        return tuple(int(p) for p in parts)
    except (ValueError, IndexError):
        return (0,)


def posix_npm_fallbacks(bin_name: str) -> list[str]:
    """Common POSIX install locations for an npm-global CLI ``bin_name``."""
    home = Path.home()
    candidates = [
        str(home / ".npm-global" / "bin" / bin_name),
        str(home / ".local" / "bin" / bin_name),
        f"/usr/local/bin/{bin_name}",
        f"/opt/homebrew/bin/{bin_name}",
    ]
    nvm_matches = glob.glob(
        str(home / ".nvm" / "versions" / "node" / "*" / "bin" / bin_name)
    )
    if nvm_matches:
        candidates.append(max(nvm_matches, key=_nvm_version_key))
    return candidates


def windows_npm_fallbacks(bin_name: str) -> list[str]:
    """Common Windows install locations for an npm-global CLI ``bin_name``.

    Last-ditch guess for environments where neither the operator override nor
    PATH resolves ``<bin_name>.cmd``: probe the default ``%APPDATA%\\npm\\``
    landing site for ``npm install -g``, plus the relocated variants under
    ``%LOCALAPPDATA%`` / ``%USERPROFILE%`` and the machine-scope
    ``%ProgramData%\\npm`` / ``%ProgramFiles%\\nodejs`` locations some operators
    use.
    """
    candidates: list[str] = []
    for var in ("APPDATA", "LOCALAPPDATA", "USERPROFILE"):
        base = os.environ.get(var)
        if not base:
            continue
        if var == "USERPROFILE":
            for suffix in (
                rf"AppData\Roaming\npm\{bin_name}.cmd",
                rf"AppData\Roaming\npm\{bin_name}.exe",
            ):
                candidates.append(os.path.join(base, suffix))
        else:
            for suffix in (rf"npm\{bin_name}.cmd", rf"npm\{bin_name}.exe"):
                candidates.append(os.path.join(base, suffix))
    program_data = os.environ.get("ProgramData") or r"C:\ProgramData"
    candidates.append(os.path.join(program_data, "npm", f"{bin_name}.cmd"))
    program_files = os.environ.get("ProgramFiles")
    if program_files:
        candidates.append(os.path.join(program_files, "nodejs", f"{bin_name}.cmd"))
    return candidates


def resolve_cli_binary(
    *,
    override: str | None,
    env_var: str,
    which_names: tuple[str, ...],
    fallbacks: list[str],
    error_cls: type[CliBinaryNotFoundError],
) -> str:
    """Locate a managed-CLI executable via override → env → PATH → fallbacks.

    Resolution order (high → low):
      1. ``override`` arg (config-supplied bin path).
      2. ``env_var`` (a direct shell export, e.g. ``MOBAIDE_CODEX_BIN``).
      3. ``shutil.which`` against the current PATH for each of ``which_names``.
      4. ``fallbacks`` (platform-specific candidate paths the caller built).

    Stateless — caching is the caller's responsibility (each launcher caches the
    result for its lifetime). Raises ``error_cls`` with every probed path tagged
    by source (``override:`` / ``env:`` / ``PATH:name`` / ``fallback:``) so the
    mobile client can tell the operator which lookup channel was tried.
    """
    searched: list[str] = []

    typed_candidates: list[tuple[str, str]] = []
    if override:
        typed_candidates.append(("override", _normalise_user_path(override)))
    env_val = os.environ.get(env_var)
    if env_val:
        typed_candidates.append(("env", _normalise_user_path(env_val)))

    for label, path in typed_candidates:
        searched.append(f"{label}:{path}")
        if _is_executable_file(path):
            return path

    for name in which_names:
        hit = shutil.which(name)
        searched.append(f"PATH:{name}")
        if hit:
            return os.path.abspath(hit)

    for candidate in fallbacks:
        searched.append(f"fallback:{candidate}")
        if _is_executable_file(candidate):
            return candidate

    raise error_cls(searched)


def probe_cli_available(launcher: CliLauncher) -> bool:
    """Whether ``launcher``'s managed CLI binary resolves on this host.

    Wraps the launcher's own ``resolve_binary`` (override → env → PATH →
    fallbacks) so the verdict matches what a real session spawn would resolve —
    an enabled mode in the UI therefore implies a spawnable CLI. Never raises
    and never spawns a subprocess: resolution only reads PATH and stats
    candidate files, so a missing binary returns ``False``.
    """
    try:
        launcher.resolve_binary()
        return True
    except CliBinaryNotFoundError:
        return False


def build_cli_capabilities(
    launchers: Mapping[str, CliLauncher],
    *,
    models_pending: bool = False,
) -> dict[str, dict[str, object]]:
    """Map each registered CLI launcher to its capability entry.

    Every entry carries ``{"available": <bool>}``. Launchers whose model list
    has been enumerated (see :func:`warm_cli_model_caches`) additionally carry
    ``{"models": [...]}``, and — when the CLI's catalog exposes per-model
    reasoning levels — ``{"model_details": [{"id", "efforts", "default_effort"?},
    ...]}``. Both keys are omitted — never emitted empty — when enumeration is
    unsupported or produced nothing, so clients fall back to their hardcoded
    lists on the same absent/empty signal for old and new agents alike. Reads
    only in-memory caches: never spawns a subprocess, so ``GET /api/version``
    latency is unaffected.

    ``models_pending=True`` (the caller saw the startup warm task still
    running after its bounded wait) additionally marks each enumeration-
    capable launcher whose own cache is still cold with
    ``{"models_pending": true}`` — omitted, never false, otherwise. The marker
    tells clients the absence of ``models`` / ``model_details`` in that entry
    is a cache still warming, NOT an agent without enumeration: they keep
    persisted catalog selections instead of reconciling them away, and may
    re-probe. The per-entry cold-cache condition matters because the warm task
    probes launchers sequentially: a CLI that already memoized its result
    (Codex always does, fallback included) must not read as pending — and
    therefore un-reconciled — merely because a sibling's probe is still
    running. Gating on the running task (not cache state alone) is what keeps
    a CLI whose enumeration deliberately memoizes nothing on failure
    (Antigravity) from ever being reported as eternally pending.

    Shared by both transports that answer ``GET /api/version`` — the FastAPI
    handler (direct) and the relay client's proxy branch (relay) — so they
    report identical availability; one source here is what stops the two
    hand-maintained handlers drifting apart. Driven off the agent's
    ``cli_launchers`` registry, so a newly registered CLI is reported with no
    further change.
    """
    capabilities: dict[str, dict[str, object]] = {}
    for cli_id, launcher in (launchers or {}).items():
        entry: dict[str, object] = {"available": probe_cli_available(launcher)}
        models = getattr(launcher, "cached_models", None)
        if models:
            entry["models"] = list(models)
        details = getattr(launcher, "cached_model_details", None)
        if details:
            entry["model_details"] = [dict(detail) for detail in details]
        if (
            models_pending
            and models is None
            and callable(getattr(launcher, "list_models", None))
        ):
            entry["models_pending"] = True
        capabilities[cli_id] = entry
    return capabilities


# Upper bound on how long a version handler waits for the startup warm task.
# Generous vs the observed ~100ms enumeration, deliberately SHORTER than one
# legitimate probe attempt (each subprocess gets 10s, and the old-CLI path may
# take two): waiting out the full legitimate worst case (~30s across CLIs)
# would run ``GET /api/version`` into the mobile clients' 30s read deadlines.
# A warm task still running after this wait is not lied about — the response
# carries ``models_pending`` so clients treat the cold shape as provisional.
_MODEL_WARMUP_WAIT = 3.0


async def await_model_cache_warmup(
    warm_task: asyncio.Task | None, timeout: float = _MODEL_WARMUP_WAIT
) -> None:
    """Briefly await the startup model-cache warm task before answering
    ``GET /api/version``.

    A version probe that lands while the warm task is still running would see
    cold caches and emit no ``models`` / ``model_details`` keys — on the wire
    indistinguishable from an old agent without enumeration, which makes
    clients reconcile a persisted catalog reasoning effort down to the static
    default. Bounded and best-effort: a finished or absent task returns
    immediately, a wedged one falls through after ``timeout`` to today's
    absent-keys shape, and ``shield`` keeps the timeout from cancelling the
    warm task itself. Shared by both ``/api/version`` transports (direct
    FastAPI handler and relay proxy) so they stay in lockstep.
    """
    if warm_task is None or warm_task.done():
        return
    try:
        await asyncio.wait_for(asyncio.shield(warm_task), timeout=timeout)
    except TimeoutError:
        pass
    except asyncio.CancelledError:
        # The warm task dying at shutdown must not error the handler; the
        # handler's own cancellation still propagates.
        if not warm_task.cancelled():
            raise
    except Exception:
        # warm_cli_model_caches isolates per-CLI failures, so this is a bug
        # guard: never let it break the version endpoint.
        logger.warning("model warmup wait failed", exc_info=True)


async def warm_cli_model_caches(launchers: Mapping[str, CliLauncher]) -> None:
    """Populate each launcher's model-list cache once, off the request path.

    Kicked as a background task at agent startup so
    :func:`build_cli_capabilities` (and therefore ``GET /api/version``) only
    ever reads a warm in-memory cache. Duck-typed: launchers advertise support
    by exposing ``list_models()``; each probe is isolated so one broken CLI
    cannot stop the others from warming.
    """
    for cli_id, launcher in (launchers or {}).items():
        list_models = getattr(launcher, "list_models", None)
        if list_models is None:
            continue
        try:
            await list_models()
        except Exception:
            logger.warning("model enumeration failed for %s", cli_id, exc_info=True)


def prepend_path_dir(env: dict[str, str], directory: str) -> None:
    """Prepend ``directory`` to ``env['PATH']`` in place, skipping no-ops.

    Does nothing when ``directory`` is empty or already on PATH — so a launcher
    can unconditionally surface its resolved-binary dir without duplicating an
    entry. Shared by the per-CLI launchers' ``_build_env``.
    """
    if not directory:
        return
    sep = os.pathsep
    existing = env.get("PATH", "")
    parts = existing.split(sep) if existing else []
    if directory in parts:
        return
    env["PATH"] = f"{directory}{sep}{existing}" if existing else directory


def conda_env_bin(conda_env: str) -> str | None:
    """Resolve the bin / Scripts dir for a conda env name or absolute path.

    Returns None if it cannot be resolved — the caller skips the PATH prepend
    rather than break the launch.
    """
    candidate = Path(conda_env)
    if not candidate.is_absolute():
        base = os.environ.get("CONDA_PREFIX") or os.environ.get("CONDA_ROOT")
        if base:
            candidate = Path(base).parent / conda_env
        else:
            return None
    sub = "Scripts" if platform.system() == "Windows" else "bin"
    resolved = candidate / sub
    return str(resolved) if resolved.exists() else str(resolved)
