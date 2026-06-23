"""CLI-agnostic launcher primitives shared by every managed CLI integration.

The agent can launch more than one coding CLI (Claude Code, Codex CLI). Each
gets its own launcher module (``claude_launcher`` / ``codex_launcher``), but the
generic scaffolding — the ``LaunchSpec`` result shape, the ad-hoc launch target,
binary resolution (override → env → PATH → npm fallbacks), and the
``CliBinaryNotFoundError`` base — lives here so the per-CLI modules stay thin and
do not duplicate the platform-probing logic.
"""
from __future__ import annotations

import glob
import os
import platform
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .projects_config import ProjectConfig


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
    into a :class:`LaunchSpec` (argv/cwd/env). ``ClaudeLauncher`` and
    ``CodexLauncher`` both conform; ``SessionManager`` keeps a registry keyed by
    CLI name and dispatches by ``SessionKind``.
    """

    async def build(
        self,
        target: LaunchTarget,
        options: Any,
        session_id: str | None = None,
    ) -> LaunchSpec: ...


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
