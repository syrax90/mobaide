"""Mobaide agent Windows supervisor entry point.

Invoked by the per-user Scheduled Task ``MobaideAgent`` (registered by
``deploy/install.ps1:Install-Service``). Lives at
``<prefix>/svc/mobaide_launcher.py`` — outside any release directory so the
registered task ``Execute`` path stays stable across upgrades and rollbacks.

The supervisor reads ``<prefix>/current_version``, resolves the active
release's ``python.exe`` and spawns ``<release_python> -m mobaide.launcher``
as a subprocess; its stdout / stderr are redirected to rotated log files
under ``<prefix>/logs/``. If the launcher subprocess exits with code
``75`` (``_EXIT_RESTART_REQUESTED`` — emitted on successful pair or staged
update) the supervisor re-resolves ``current_version`` and respawns
in-place. This in-process loop is what gives the user the immediate
post-pair restart; Task Scheduler's own ``-RestartCount`` /
``-RestartInterval`` (1-minute floor) is the outer fallback for the
crash-loop case.

Pure stdlib — never imports ``mobaide`` and never imports ``pywin32``. The
Scheduled Task action invokes it with whichever Python ``install.ps1``
resolved at install time; the supervisor must survive partially-staged or
rolled-back releases (the release-pinned ``mobaide.launcher`` does the
crash-loop / rollback work via ``<prefix>/current_version`` rewrites and
the next supervisor iteration picks up the new version on respawn).

Why a Scheduled Task and not an SCM service: see ``docs/deployment.md`` —
running as ``LocalSystem`` made ``%USERPROFILE%`` resolve to
``C:\\Windows\\system32\\config\\systemprofile``, so the Claude CLI in the
PTY couldn't see the user's ``~/.claude`` credentials or skills. The
Scheduled Task runs as the installing user (``-LogonType Interactive``)
which fixes both problems without password capture.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path

_DEFAULT_PROGRAM_FILES = "C:\\Program Files"
_DEFAULT_INSTALL_DIR_NAME = "MobaideAgent"

_STOP_GRACE_SECONDS = 25

_AGENT_LOG_DIR_NAME = "logs"
_AGENT_ERR_LOG_FILE_NAME = "agent.err.log"
_AGENT_ERR_LOG_BACKUP_NAME = "agent.err.log.1"
_AGENT_OUT_LOG_FILE_NAME = "agent.out.log"
_AGENT_OUT_LOG_BACKUP_NAME = "agent.out.log.1"
_LAUNCHER_ERR_LOG_FILE_NAME = "launcher.err.log"
_LAUNCHER_ERR_LOG_BACKUP_NAME = "launcher.err.log.1"
_AGENT_LOG_ROTATE_BYTES = 5 * 1024 * 1024

# ``subprocess.CREATE_NO_WINDOW`` — hardcoded literal so this stdlib-only
# script is independent of the agent's ``subprocess`` re-exports. The
# Scheduled Task runs the supervisor under ``pythonw.exe`` (GUI subsystem,
# no console allocated). Without this flag, ``Popen``-ing a console-
# subsystem ``python.exe`` child would still pop a fresh console window,
# defeating the whole hidden-process design. Compatible with
# ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` (it does not set
# ``CREATE_BREAKAWAY_FROM_JOB``).
_CREATE_NO_WINDOW = 0x08000000

# Canonical in ``mobaide.agent.updater.EXIT_RESTART_REQUESTED``. Duplicated
# here because this supervisor is a standalone stdlib script — importing
# ``mobaide`` would defeat the "survives a half-staged or rolled-back
# release" guarantee. Drift between the two breaks the post-pairing
# restart: agent exits 75, this side fails to recognise it, supervisor
# exits to Task Scheduler instead of respawning immediately.
_EXIT_RESTART_REQUESTED = 75

# Same values as ``mobaide.launcher._spawn_and_wait_windows``.
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_INFO_CLASS_EXTENDED_LIMIT = 9

# Held for the supervisor's lifetime: the kernel kills every process in the
# job once its last handle closes, which is when the supervisor exits or is
# terminated.
_descendant_job = None

# Cap on self-heal recoveries within a single supervisor process. A missing
# current-release interpreter rolls forward to a sibling (below); the cap
# bounds oscillation if recovery targets keep disappearing — once hit, the
# supervisor exits 70 and Task Scheduler's restart (1-minute floor) provides
# the outer reset.
_MAX_RECOVERY_ATTEMPTS = 5

# Marker the updater/installers drop into a release dir once pip + the wheel
# are installed. Canonical in ``mobaide.agent.install_paths.is_release_complete``;
# duplicated here because this supervisor is stdlib-only and importing
# ``mobaide`` would defeat the "survives a half-staged release" guarantee. Keep
# in sync. A release predating the sentinel is recognised by its agent console
# script (pip writes it only after a successful wheel install), so a venv
# swapped into place but not yet finalized is correctly treated as incomplete.
_RELEASE_SENTINEL = ".installed"


def _default_prefix(cli_prefix: str | None = None) -> Path:
    """Resolve the install prefix.

    Precedence: ``--prefix`` argv > ``MOBAIDE_AGENT_PREFIX`` env >
    ``%ProgramFiles%\\MobaideAgent`` default. The argv form is the
    standard path because the Scheduled Task action passes ``--prefix
    <prefix>`` explicitly (the task's environment doesn't get
    ``MOBAIDE_AGENT_PREFIX`` set automatically).
    """
    if cli_prefix:
        return Path(cli_prefix)
    explicit = os.environ.get("MOBAIDE_AGENT_PREFIX")
    if explicit:
        return Path(explicit)
    program_files = os.environ.get("ProgramFiles", _DEFAULT_PROGRAM_FILES)
    return Path(program_files) / _DEFAULT_INSTALL_DIR_NAME


def _venv_python_path(release_dir: Path) -> Path:
    """Path to a release dir's venv interpreter (Windows layout).

    Single source of truth for the ``venv/Scripts/python.exe`` layout so the
    ``_resolve_release_python`` happy path and the ``_find_recovery_version``
    scan cannot drift.
    """
    return release_dir / "venv" / "Scripts" / "python.exe"


def _is_release_complete(release_dir: Path) -> bool:
    """True once a release is fully installed (pip + wheel done).

    Keep in sync with ``mobaide.agent.install_paths.is_release_complete``. A
    release predating the sentinel is recognised by its agent console script,
    which pip writes only after a successful wheel install, so a venv swapped
    into place but not yet finalized is correctly excluded.
    """
    if (release_dir / _RELEASE_SENTINEL).exists():
        return True
    return (release_dir / "venv" / "Scripts" / "mobaide-agent.exe").exists()


def _resolve_release_python(prefix: Path) -> Path:
    """Read ``current_version`` and return the active release's ``python.exe``.

    Raises FileNotFoundError with an operator-friendly message for any of:
    missing/empty ``current_version`` file, missing release dir, missing
    interpreter, or a release that is not finalized (swapped into place but not
    yet pip-installed — no ``.installed`` sentinel / console script). Treating
    an unfinalized current release as unrunnable lets ``_supervise`` recover to
    a complete sibling instead of exec-ing a half-built venv. Callers print the
    message to stderr so it lands in the captured ``agent.err.log``.
    """
    version_file = prefix / "current_version"
    try:
        version = version_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise FileNotFoundError(
            f"cannot read {version_file}: {exc}"
        ) from exc
    if not version:
        raise FileNotFoundError(f"empty {version_file}")
    release_dir = prefix / "releases" / version
    python = _venv_python_path(release_dir)
    if not python.exists():
        raise FileNotFoundError(
            f"missing release interpreter {python} (current_version={version!r})"
        )
    if not _is_release_complete(release_dir):
        raise FileNotFoundError(
            f"release {version!r} not finalized (no {_RELEASE_SENTINEL} sentinel "
            f"or console script); recovering to a complete sibling"
        )
    return python


def _version_key(name: str) -> tuple[int, ...]:
    """Leading numeric dotted components of a release-dir name, else ``()``.

    Strict (like ``mobaide.launcher._parse_version``): any non-numeric
    component yields ``()``, so scratch dirs (``.staging-*`` / ``.old-*``) and
    other foreign names are never recovery candidates.
    """
    parts: list[int] = []
    for component in name.split("."):
        if not component.isdigit():
            return ()
        parts.append(int(component))
    return tuple(parts)


def _find_recovery_version(prefix: Path) -> str | None:
    """Newest sibling release with a runnable, finalized venv, or ``None``.

    The Windows counterpart to the POSIX launcher wrapper's self-heal: when the
    current release's interpreter is gone (or it is not finalized), roll forward
    to the newest sibling that has a complete venv so the supervisor stops
    crash-looping instead of exiting straight to Task Scheduler. A release
    swapped into place but not yet pip-installed (``_is_release_complete`` is
    False) is skipped. Stdlib-only — never imports ``mobaide``.
    """
    releases = prefix / "releases"
    try:
        entries = list(releases.iterdir())
    except OSError:
        return None
    best_key: tuple[int, ...] | None = None
    best_name: str | None = None
    for entry in entries:
        if not entry.is_dir():
            continue
        key = _version_key(entry.name)
        if not key:
            continue
        if not _venv_python_path(entry).exists():
            continue
        if not _is_release_complete(entry):
            continue
        if best_key is None or key > best_key:
            best_key = key
            best_name = entry.name
    return best_name


def _write_current_version(prefix: Path, version: str) -> None:
    """Atomically rewrite ``<prefix>/current_version`` (tmp + ``os.replace``)."""
    target = prefix / "current_version"
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(version + "\n", encoding="utf-8")
    os.replace(tmp, target)


def _open_log(prefix: Path, file_name: str, backup_name: str):
    """Open ``<prefix>/logs/<file_name>`` in append mode with boot-time rotation.

    Rotates if the existing log is ≥ 5 MB (keep one backup at
    ``<backup_name>``). Returns a binary append-mode file object; the
    caller passes it to ``Popen`` as ``stdout`` / ``stderr`` and closes
    its own wrapper after ``Popen`` returns — subprocess has already
    dup'd the underlying OS handle into the child, and leaving the
    parent wrapper open would pin the file and prevent the next boot's
    rotation.
    """
    log_dir = prefix / _AGENT_LOG_DIR_NAME
    log_path = log_dir / file_name
    log_dir.mkdir(parents=True, exist_ok=True)
    try:
        if log_path.stat().st_size >= _AGENT_LOG_ROTATE_BYTES:
            backup = log_dir / backup_name
            try:
                backup.unlink()
            except FileNotFoundError:
                pass
            log_path.replace(backup)
    except FileNotFoundError:
        pass
    return log_path.open("ab")


def _spawn_release_launcher(
    release_python: Path, prefix: Path
) -> subprocess.Popen[bytes]:
    """Spawn the release-pinned launcher and capture its stdout + stderr.

    Both streams are redirected to per-stream rotated log files under
    ``<prefix>/logs/`` rather than DEVNULL. structlog's queue listener
    writes its records to ``sys.stdout``; without this redirect every
    ``pairing_monitor.*`` / ``relay_client.*`` / ``http.request.*``
    diagnostic vanishes and operators have no way to determine why a
    pairing-pending agent never claimed its credentials.

    ``out_log`` is opened first; if the ``err_log`` open raises (disk
    full, permission denied, mid-supervise rotation race), the already-
    open ``out_log`` is closed before propagating so the supervisor
    doesn't leak a file handle for the rest of its lifetime.
    ``subprocess.Popen`` dups both handles into the child, so the
    parent wrappers are closed in a ``finally`` to free the parent-side
    reference and allow the next iteration's rotation to ``replace``
    the file.
    """
    out_log = _open_log(prefix, _AGENT_OUT_LOG_FILE_NAME, _AGENT_OUT_LOG_BACKUP_NAME)
    try:
        err_log = _open_log(prefix, _AGENT_ERR_LOG_FILE_NAME, _AGENT_ERR_LOG_BACKUP_NAME)
    except BaseException:
        out_log.close()
        raise
    try:
        return subprocess.Popen(
            [str(release_python), "-m", "mobaide.launcher"],
            stdin=subprocess.DEVNULL,
            stdout=out_log,
            stderr=err_log,
            close_fds=True,
            creationflags=_CREATE_NO_WINDOW,
        )
    finally:
        out_log.close()
        err_log.close()


def _terminate_subprocess(proc: subprocess.Popen[bytes] | None) -> None:
    """Best-effort graceful stop of the launcher subprocess.

    25 s grace via ``terminate`` (Windows ``TerminateProcess``), then
    fall through to ``kill``. Both calls swallow ``OSError`` because
    the process may already have exited between the ``poll`` and the
    signal — racing the supervisor's own teardown.
    """
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
    except OSError:
        pass
    try:
        proc.wait(timeout=_STOP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass


def _supervise(prefix: Path) -> int:
    """Spawn → wait → exit-75 respawn loop. Returns the final exit code.

    Lifecycle:
      * Resolve ``current_version`` → release ``python.exe``. If the
        interpreter is missing (a half-staged / corrupt current release),
        self-heal: roll forward to the newest sibling release that still has a
        venv, rewrite ``current_version``, and respawn — bounded by
        ``_MAX_RECOVERY_ATTEMPTS`` to stop oscillation. Only when no sibling is
        runnable does the supervisor print to stderr and exit code 70.
      * Spawn ``release_python -m mobaide.launcher`` with stdout/stderr
        captured to rotated log files.
      * Wait for subprocess exit.
      * If exit code == 75: re-read ``current_version`` (it may have
        been rewritten by the launcher's rollback heuristic) and
        respawn. This is the Windows counterpart to systemd's
        ``RestartForceExitStatus=75`` — the agent emits 75 after a
        successful pair (so the next boot reads the just-written
        ``RELAY_AGENT_ID``/``RELAY_AGENT_TOKEN``) and after a staged
        auto-update.
      * Any other exit code: return it to Task Scheduler, which decides
        whether to restart based on its ``-RestartCount`` /
        ``-RestartInterval`` settings.

    ``KeyboardInterrupt`` (CTRL_C / CTRL_BREAK from Task Scheduler's
    ``Stop-ScheduledTask``) terminates the subprocess and exits 0.
    """
    proc: subprocess.Popen[bytes] | None = None
    recovered: set[str] = set()
    recovery_attempts = 0
    try:
        while True:
            try:
                release_python = _resolve_release_python(prefix)
            except FileNotFoundError as exc:
                sys.stderr.write(
                    f"mobaide_launcher: cannot resolve release interpreter: {exc}\n"
                )
                target = _find_recovery_version(prefix)
                if (
                    target is None
                    or target in recovered
                    or recovery_attempts >= _MAX_RECOVERY_ATTEMPTS
                ):
                    return 70
                try:
                    _write_current_version(prefix, target)
                except OSError as werr:
                    sys.stderr.write(
                        f"mobaide_launcher: recovery write failed: {werr}\n"
                    )
                    return 70
                recovered.add(target)
                recovery_attempts += 1
                sys.stderr.write(
                    f"mobaide_launcher: current release unrunnable; recovered "
                    f"to {target!r}; respawning.\n"
                )
                continue
            try:
                proc = _spawn_release_launcher(release_python, prefix)
            except OSError as exc:
                sys.stderr.write(
                    f"mobaide_launcher: failed to spawn launcher "
                    f"{release_python}: {exc}\n"
                )
                return 70
            rc = proc.wait()
            proc = None
            if rc != _EXIT_RESTART_REQUESTED:
                return rc
            sys.stderr.write(
                f"mobaide_launcher: launcher exited with "
                f"{_EXIT_RESTART_REQUESTED} (restart requested — typically a "
                "successful pair or staged update); respawning.\n"
            )
    except KeyboardInterrupt:
        _terminate_subprocess(proc)
        return 0


def _redirect_supervisor_stderr(prefix: Path) -> None:
    """Reopen ``sys.stderr`` onto ``<prefix>/logs/launcher.err.log``.

    Under ``pythonw.exe`` (Scheduled Task hidden-process entry point) the
    interpreter's ``sys.stdout`` / ``sys.stderr`` are bound to a no-op
    writer — they accept ``.write`` calls but drop the bytes. The
    supervisor's own diagnostics ("cannot resolve release interpreter",
    "failed to spawn launcher") are emergency-only and operator-facing;
    silently discarding them turns "agent never started" into an opaque
    failure mode. Reopen ``sys.stderr`` onto a dedicated rotated log file
    so those messages always land somewhere a ``Get-Content`` can find.
    Distinct from ``agent.err.log`` (which captures the spawned launcher
    subprocess's stderr) so supervisor-level failures are not interleaved
    with release-pinned launcher / agent failures.

    Implementation note — fd ownership. ``_open_log`` returns a binary
    file wrapper that owns its OS fd; if we just pass its ``fileno()``
    to a second ``open(..., closefd=False)`` and let the original
    wrapper fall out of scope, the original's destructor closes the fd
    and the new ``sys.stderr`` writes raise ``OSError: Bad file
    descriptor``. ``os.dup`` gives the new wrapper an independent fd so
    both can manage their own lifecycle: the original ``fh`` closes its
    fd when GC'd at function exit, and the new ``sys.stderr`` owns the
    duplicate.
    """
    fh = _open_log(prefix, _LAUNCHER_ERR_LOG_FILE_NAME, _LAUNCHER_ERR_LOG_BACKUP_NAME)
    try:
        try:
            dup_fd = os.dup(fh.fileno())
        except OSError:
            return
    finally:
        fh.close()
    try:
        sys.stderr = os.fdopen(
            dup_fd, mode="w", encoding="utf-8", buffering=1
        )
    except Exception:
        os.close(dup_fd)


def _install_signal_handlers() -> None:
    """Translate Windows console-control signals into ``KeyboardInterrupt``.

    ``Stop-ScheduledTask`` delivers ``CTRL_BREAK_EVENT`` / process
    termination to the action's process tree. CTRL_C is already the
    Python default; we additionally install a SIGBREAK handler on
    Windows so a polite stop unwinds through the ``_supervise`` loop's
    ``KeyboardInterrupt`` branch.
    """
    if sys.platform != "win32":
        return
    try:
        signal.signal(signal.SIGBREAK, signal.default_int_handler)  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass


def _bind_descendants_to_supervisor() -> None:
    """Put the supervisor in a Job Object that kills its members on close.

    ``Stop-ScheduledTask`` / ``schtasks /End`` terminate only the task's own
    process, this supervisor. Without the job the launcher and agent it
    spawned outlive it: the orphaned agent keeps the port and the relay
    credentials it booted with, so the next task start cannot bind and exits
    while the stale agent keeps serving. Every process started after this call
    inherits the job, so terminating the supervisor takes the whole tree down.

    The supervisor joins the job itself, rather than assigning the launcher
    after ``Popen``, so no grandchild can be spawned before the assignment.
    Fail-soft: on any ctypes failure the supervisor runs without the job.
    """
    global _descendant_job
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        class _BasicLimitInformation(ctypes.Structure):
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

        class _ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimitInformation),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE, wintypes.HANDLE,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        info = _ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job, _JOB_INFO_CLASS_EXTENDED_LIMIT, ctypes.byref(info), ctypes.sizeof(info),
        ) or not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(job)
            raise OSError(error, "could not join a kill-on-close job")
    except Exception as exc:
        sys.stderr.write(
            f"mobaide_launcher: running without a kill-on-close job ({exc}); "
            "stopping the task may leave the agent running.\n"
        )
        return
    _descendant_job = job


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mobaide_launcher",
        description="Mobaide agent supervisor (Windows Scheduled Task entry point).",
    )
    parser.add_argument(
        "--prefix",
        default=None,
        help="Install prefix (overrides MOBAIDE_AGENT_PREFIX env var).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _install_signal_handlers()
    prefix = _default_prefix(args.prefix)
    _redirect_supervisor_stderr(prefix)
    _bind_descendants_to_supervisor()
    return _supervise(prefix)


if __name__ == "__main__":
    sys.exit(main())
