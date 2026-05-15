"""Resolve the current release dir and exec into its venv's Python.

Beyond the bare exec, the launcher tracks supervisor restart frequency
in a small boot journal and rolls back to the previous good release if
the active version crashes 3 times within 60 seconds. Supervisor units
(systemd, launchd, Windows Service) keep restarting; the launcher is
the persistent entry point that observes the loop and breaks it by
rewriting ``current_version`` to a sibling release. The next supervisor
restart then boots the rolled-back version.

Self-contained on purpose — this module runs across release directories
(it's installed at ``<prefix>/bin/mobaide-agent-launcher``, not inside
any ``releases/<v>/venv``) and must not import ``mobaide`` so it
survives a partially-staged or corrupt release.

POSIX vs Windows hand-off
-------------------------
On Linux/macOS the launcher ``os.execv``-s into the release venv's
Python — the kernel replaces the current process image in place, so
the supervisor's PID watch still tracks the same PID as before.

On Windows ``os.execv`` is not what the docs claim. CPython delegates
to MSVC ``_wexecv``, which is implemented as ``CreateProcess`` +
``ExitProcess``: the new process gets a *different* PID, and the
calling process exits immediately (Python issue #9148). A Windows
service supervising the launcher via ``subprocess.Popen.poll()`` then
sees the launcher die and marks the service STOPPED — even though
the agent is running fine under a new orphan PID. To preserve the
supervisor's PID watch, the Windows branch spawns the agent as a
subprocess and waits for it, holding a Job Object with
``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` so the agent dies cleanly when
the launcher itself is terminated.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn

# Crash-loop window. A version that is exec'd this many times within
# this many seconds is considered failing, and the launcher reverts to
# the highest-versioned sibling release (if any) on the next boot.
ROLLBACK_THRESHOLD = 3
ROLLBACK_WINDOW_SECONDS = 60.0
JOURNAL_KEEP_LINES = 100
JOURNAL_NAME = ".boot_journal"


def _default_prefix() -> Path:
    explicit = os.environ.get("MOBAIDE_AGENT_PREFIX")
    if explicit:
        return Path(explicit)
    if sys.platform == "win32":
        program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
        return Path(program_files) / "MobaideAgent"
    if sys.platform == "darwin":
        return Path("/usr/local/mobaide")
    return Path("/opt/mobaide")


def _resolve_python(release_dir: Path) -> Path:
    if sys.platform == "win32":
        return release_dir / "venv" / "Scripts" / "python.exe"
    return release_dir / "venv" / "bin" / "python"


def _read_current_version(prefix: Path) -> str | None:
    try:
        return (prefix / "current_version").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _write_current_version(prefix: Path, version: str) -> None:
    target = prefix / "current_version"
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(version + "\n", encoding="utf-8")
    os.replace(tmp, target)


def _append_boot_journal(prefix: Path, version: str, now: float) -> list[tuple[float, str]]:
    """Append ``now version`` to the journal and return the trimmed entries.

    Best-effort: journal failures never block boot — they only disable
    the rollback heuristic for this attempt.
    """
    journal = prefix / JOURNAL_NAME
    entries: list[tuple[float, str]] = _read_journal(journal)
    entries.append((now, version))
    if len(entries) > JOURNAL_KEEP_LINES:
        entries = entries[-JOURNAL_KEEP_LINES:]
    try:
        prefix.mkdir(parents=True, exist_ok=True)
        tmp = journal.with_suffix(journal.suffix + ".tmp")
        tmp.write_text(
            "".join(f"{ts:.3f} {v}\n" for ts, v in entries),
            encoding="utf-8",
        )
        os.replace(tmp, journal)
    except OSError:
        pass
    return entries


def _read_journal(journal: Path) -> list[tuple[float, str]]:
    try:
        text = journal.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[tuple[float, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ts_str, version = line.split(" ", 1)
            out.append((float(ts_str), version.strip()))
        except (ValueError, IndexError):
            continue
    return out


def _crash_count(entries: list[tuple[float, str]], version: str, now: float) -> int:
    cutoff = now - ROLLBACK_WINDOW_SECONDS
    return sum(1 for ts, v in entries if v == version and ts >= cutoff)


def _parse_version(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in v.split("."))
    except ValueError:
        return ()


def _find_rollback_target(
    prefix: Path,
    current_version: str,
    journal_entries: list[tuple[float, str]] | None = None,
    now: float | None = None,
) -> str | None:
    """Pick the highest-versioned sibling release with a usable venv.

    A candidate that has itself crossed the crash-loop threshold within
    the same window is excluded — without this, a deployment where two
    consecutive releases are both broken oscillates between them until
    the supervisor's hard cap finally fires, leaving an unpredictable
    ``current_version`` on disk.

    Returns ``None`` when no candidate qualifies (fresh install, only
    the failing release on disk, or every sibling is also crash-looping).
    """
    releases_dir = prefix / "releases"
    try:
        candidates = [p for p in releases_dir.iterdir() if p.is_dir()]
    except OSError:
        return None
    eligible: list[tuple[tuple[int, ...], str]] = []
    for candidate in candidates:
        version = candidate.name
        if version == current_version:
            continue
        if not _resolve_python(candidate).exists():
            continue
        if (
            journal_entries is not None
            and now is not None
            and _crash_count(journal_entries, version, now) >= ROLLBACK_THRESHOLD
        ):
            continue
        eligible.append((_parse_version(version), version))
    if not eligible:
        return None
    eligible.sort(reverse=True)
    return eligible[0][1]


def _exec_release(prefix: Path, version: str) -> None:
    release_dir = prefix / "releases" / version
    python = _resolve_python(release_dir)
    if not python.exists():
        sys.stderr.write(
            f"mobaide-agent-launcher: missing interpreter {python}\n"
        )
        sys.exit(70)
    args = [str(python), "-m", "mobaide", "run", *sys.argv[1:]]
    _run_release(python, args)


def _run_release(python: Path, args: list[str]) -> NoReturn:
    """Hand control to the release-venv Python and never return.

    Single seam the launcher tests monkeypatch: capturing this call
    avoids replacing ``os.execv`` directly (which would also break
    every other caller in the process for the lifetime of the test).
    """
    if sys.platform == "win32":
        _spawn_and_wait_windows(args)
    else:
        os.execv(str(python), args)
    raise RuntimeError("unreachable: _run_release branches both terminate")


_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_INFO_CLASS_EXTENDED_LIMIT = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


def _spawn_and_wait_windows(args: list[str]) -> NoReturn:
    """Spawn the agent and propagate its exit code. Windows-only.

    ``os.execv`` on Windows does NOT preserve the calling process's
    PID (Python issue #9148: MSVC ``_wexecv`` is internally
    ``CreateProcess`` + ``ExitProcess``). The launcher runs under the
    Scheduled-Task supervisor (``deploy/windows_launcher.py``), which
    watches the launcher PID via ``Popen.poll()`` to drive its
    exit-75 respawn loop; an ``os.execv`` here would make the
    supervisor see the launcher exit instantly while the agent ran
    orphaned. Spawning + waiting keeps the launcher PID alive for the
    agent's lifetime, so the supervisor's poll tracks the actual
    agent.

    A Job Object with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` is
    attached to the agent. When the launcher exits (cleanly or via
    Task Scheduler's ``TerminateProcess`` during
    ``Stop-ScheduledTask``), all its handles close, the job closes,
    and Windows tears down the agent atomically — no orphan to fight
    over port 8000 on the next restart. Nested jobs are required (the
    supervisor itself may be in a job); they're guaranteed on Windows
    8.1+, and the project pins Python 3.12 (``pyproject.toml:9``)
    which mandates that floor.

    Fail-soft: if any ctypes call fails (older Windows, restrictive
    job-policy, ctypes import failure), skip the job assignment and
    still ``Popen``-and-``wait`` so the primary PID-preservation fix
    holds even in degraded-teardown mode.

    ``creationflags=CREATE_NO_WINDOW`` suppresses console allocation
    for the spawned agent. The Scheduled-Task supervisor runs under
    ``pythonw.exe`` so it has no parent console to inherit; without
    this flag, Windows allocates a fresh console window for the
    console-subsystem ``python.exe`` child. The flag is mutually
    distinct from ``CREATE_NEW_PROCESS_GROUP`` (which would set
    ``CREATE_BREAKAWAY_FROM_JOB`` semantics and break the
    KILL_ON_JOB_CLOSE teardown contract above).
    """
    import ctypes
    from ctypes import wintypes

    class _JobObjectBasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _JobObjectExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JobObjectBasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.restype = wintypes.BOOL

    job_handle = kernel32.CreateJobObjectW(None, None)
    if job_handle:
        info = _JobObjectExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        if not kernel32.SetInformationJobObject(
            job_handle,
            _JOB_INFO_CLASS_EXTENDED_LIMIT,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            kernel32.CloseHandle(job_handle)
            job_handle = None

    proc = subprocess.Popen(
        args, close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW
    )

    if job_handle:
        child = kernel32.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, proc.pid
        )
        if child:
            try:
                kernel32.AssignProcessToJobObject(job_handle, child)
            finally:
                kernel32.CloseHandle(child)

    try:
        rc = proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        rc = proc.wait()
    sys.exit(rc)


def main() -> None:
    prefix = _default_prefix()
    version = _read_current_version(prefix)
    if version is None:
        sys.stderr.write(
            f"mobaide-agent-launcher: cannot read {prefix / 'current_version'}\n"
        )
        sys.exit(70)

    now = time.time()
    entries = _append_boot_journal(prefix, version, now)

    if _crash_count(entries, version, now) >= ROLLBACK_THRESHOLD:
        rollback = _find_rollback_target(prefix, version, entries, now)
        if rollback is not None:
            try:
                _write_current_version(prefix, rollback)
            except OSError as exc:
                sys.stderr.write(
                    f"mobaide-agent-launcher: rollback write failed: {exc}\n"
                )
            else:
                sys.stderr.write(
                    f"mobaide-agent-launcher: ERROR — version {version} "
                    f"crashed {ROLLBACK_THRESHOLD}+ times in "
                    f"{ROLLBACK_WINDOW_SECONDS:.0f}s; rolling back to {rollback}\n"
                )
                version = rollback

    _exec_release(prefix, version)


if __name__ == "__main__":
    main()
