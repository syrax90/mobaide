"""Hardened ``git`` runner for the read-only session status/diff API.

Distinct from :func:`mobaide.linear.worktree._run_git` on purpose: that
helper's argv and kwargs are pinned by its callers and tests, and it has no
timeout, no output cap and no environment scrubbing. This runner is the one
the Changes viewer relies on, so it is bounded on every axis:

* bytes only — text mode would rewrite CRLF inside diff content;
* two reader threads drain stdout/stderr concurrently (a bare ``read()``
  deadlocks once the other pipe fills);
* ``max_bytes`` and ``timeout`` are enforced by killing the whole process
  tree — on Windows ``shutil.which("git")`` usually resolves to the
  ``cmd\\git.exe`` wrapper, which spawns the real ``git.exe`` and waits, so
  ``Popen.kill()`` alone would orphan a child that keeps the pipes open;
* the environment is copied from the agent with every inherited ``GIT_*``
  variable removed.

``asyncio.create_subprocess_exec`` is never used: it raises under the
``WindowsSelectorEventLoop`` the agent runs on.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

# Suppress the transient ``git`` console window the Scheduled-Task-spawned
# agent (no parent console under ``pythonw.exe``) would otherwise pop.
_WINDOWS_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

MIN_GIT_VERSION: tuple[int, int, int] = (2, 15, 0)

_CHUNK = 65536
_STDERR_CAP = 64 * 1024
_REAP_TIMEOUT = 10.0

# Every inherited ``GIT_*`` variable is dropped: ``GIT_DIR``/``GIT_WORK_TREE``/
# ``GIT_COMMON_DIR``/``GIT_OBJECT_DIRECTORY`` redirect the repository,
# ``GIT_CONFIG_PARAMETERS`` and ``GIT_CONFIG_COUNT``/``KEY_n``/``VALUE_n`` inject
# config, ``GIT_EXTERNAL_DIFF``/``GIT_PAGER`` run programs and ``GIT_TRACE*``
# write files. Local read-only status/diff needs none of them.
_SCRUBBED_ENV_PREFIX = "GIT_"
_FORCED_ENV = {
    "LC_ALL": "C",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
}
GIT_ARGV_PREFIX: tuple[str, ...] = (
    "--no-optional-locks",
    "--literal-pathspecs",
    "-c",
    "core.quotePath=false",
    "-c",
    "core.pager=cat",
)

_VERSION_RE = re.compile(rb"(\d+)\.(\d+)(?:\.(\d+))?")


class GitUnavailableError(Exception):
    """No ``git`` binary could be found or executed."""


class GitCwdMissingError(Exception):
    """The requested working directory vanished before ``git`` could start."""


@dataclass(frozen=True)
class CmdResult:
    rc: int
    stdout: bytes
    stderr: bytes
    truncated: bool = False
    timed_out: bool = False


def git_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = {
        k: v for k, v in os.environ.items() if not k.startswith(_SCRUBBED_ENV_PREFIX)
    }
    env.update(_FORCED_ENV)
    if extra:
        env.update(extra)
    return env


_cache_lock = threading.Lock()
_binary_cache: str | None = None
_version_cache: tuple[int, int, int] | None = None


def git_binary() -> str | None:
    """Resolve ``git`` on PATH; only a hit is cached so a later install is seen."""
    global _binary_cache
    with _cache_lock:
        if _binary_cache is not None:
            return _binary_cache
    found = shutil.which("git")
    if found:
        with _cache_lock:
            _binary_cache = found
    return found


def reset_caches() -> None:
    global _binary_cache, _version_cache
    with _cache_lock:
        _binary_cache = None
        _version_cache = None


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_WINDOWS_NO_WINDOW,
                timeout=_REAP_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


def run_capped(
    argv: Sequence[str],
    *,
    cwd: str | None,
    timeout: float,
    max_bytes: int,
    env: Mapping[str, str] | None = None,
) -> CmdResult:
    """Run ``argv`` with bounded runtime and bounded stdout (blocking).

    Raises ``OSError`` when the process cannot be spawned (missing binary,
    missing cwd) — callers translate that into their own error vocabulary.
    """
    popen_kwargs: dict[str, object] = {
        "cwd": cwd,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "env": dict(env) if env is not None else None,
        "creationflags": _WINDOWS_NO_WINDOW,
    }
    if sys.platform != "win32":
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(list(argv), **popen_kwargs)  # type: ignore[call-overload]
    assert proc.stdout is not None and proc.stderr is not None

    out_chunks: list[bytes] = []
    err_chunks: list[bytes] = []
    state = {"out": 0, "err": 0, "truncated": False}

    def read_out() -> None:
        try:
            while True:
                chunk = proc.stdout.read1(_CHUNK)  # type: ignore[union-attr]
                if not chunk:
                    return
                out_chunks.append(chunk)
                state["out"] += len(chunk)
                if state["out"] > max_bytes:
                    state["truncated"] = True
                    return
        except (OSError, ValueError):
            return

    def read_err() -> None:
        try:
            while True:
                chunk = proc.stderr.read1(_CHUNK)  # type: ignore[union-attr]
                if not chunk:
                    return
                if state["err"] < _STDERR_CAP:
                    err_chunks.append(chunk)
                    state["err"] += len(chunk)
        except (OSError, ValueError):
            return

    t_out = threading.Thread(target=read_out, name="git-stdout", daemon=True)
    t_err = threading.Thread(target=read_err, name="git-stderr", daemon=True)
    deadline = time.monotonic() + timeout
    t_out.start()
    t_err.start()

    t_out.join(timeout)
    timed_out = t_out.is_alive()
    if timed_out or state["truncated"]:
        _kill_tree(proc)
    try:
        proc.wait(timeout=max(0.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        try:
            proc.wait(timeout=_REAP_TIMEOUT)
        except subprocess.TimeoutExpired:
            pass
    t_out.join(_REAP_TIMEOUT)
    t_err.join(_REAP_TIMEOUT)
    for stream in (proc.stdout, proc.stderr):
        try:
            stream.close()
        except OSError:
            pass

    stdout = b"".join(out_chunks)
    if state["truncated"]:
        stdout = stdout[:max_bytes]
    rc = proc.returncode if proc.returncode is not None else -1
    return CmdResult(
        rc=rc,
        stdout=stdout,
        stderr=b"".join(err_chunks)[:_STDERR_CAP],
        truncated=bool(state["truncated"]),
        timed_out=timed_out,
    )


async def run_git(
    args: Sequence[str],
    *,
    cwd: str,
    timeout: float,
    max_bytes: int,
    env_extra: Mapping[str, str] | None = None,
) -> CmdResult:
    """Run ``git <prefix> *args`` in ``cwd``; callers put paths after ``--``."""
    binary = git_binary()
    if binary is None:
        raise GitUnavailableError("git binary not on PATH")
    argv = [binary, *GIT_ARGV_PREFIX, *args]
    try:
        return await asyncio.to_thread(
            run_capped,
            argv,
            cwd=cwd,
            timeout=timeout,
            max_bytes=max_bytes,
            env=git_env(env_extra),
        )
    except OSError as exc:
        if not os.path.isdir(cwd):
            raise GitCwdMissingError(cwd) from exc
        raise GitUnavailableError(str(exc)) from exc


async def git_version() -> tuple[int, int, int] | None:
    """``git --version`` parsed to a tuple, cached once it parses."""
    global _version_cache
    with _cache_lock:
        if _version_cache is not None:
            return _version_cache
    binary = git_binary()
    if binary is None:
        raise GitUnavailableError("git binary not on PATH")
    try:
        result = await asyncio.to_thread(
            run_capped,
            [binary, "--version"],
            cwd=None,
            timeout=10.0,
            max_bytes=4096,
            env=git_env(),
        )
    except OSError as exc:
        raise GitUnavailableError(str(exc)) from exc
    match = _VERSION_RE.search(result.stdout)
    if result.rc != 0 or match is None:
        return None
    parsed = (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3) or 0),
    )
    with _cache_lock:
        _version_cache = parsed
    return parsed
