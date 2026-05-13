"""Mobaide agent Windows service wrapper.

Bridges the Windows Service Control Manager (SCM) to the existing
``mobaide.launcher`` chain. Installed at
``<prefix>/svc/mobaide_service.py`` (outside any release directory) so
the registered service binary path stays stable across upgrades and
rollbacks.

Imports stdlib + ``pywin32`` only — never ``mobaide`` — so the wrapper
survives a partially-staged or rolled-back release. The version-pinned
``mobaide.launcher`` (running inside the active release's venv as a
subprocess of this service) handles crash-loop detection and rollback
by rewriting ``<prefix>/current_version``; the next service start
re-reads that file and spawns the rolled-back version's launcher.

Why this module exists at all
=============================
A Windows service binary must implement the SCM ABI:
``StartServiceCtrlDispatcher``, ``ServiceMain``, ``SERVICE_CONTROL_*``
handlers, and ``SetServiceStatus(SERVICE_RUNNING)`` within ~30 s of
launch. The pre-fix design registered ``cmd.exe /c launcher.cmd`` —
cmd.exe is a console interpreter and does none of those, so SCM timed
out with "Cannot start service MobaideAgent on computer '.'" (error
1053). ``win32serviceutil.ServiceFramework`` supplies all the ABI
plumbing; this module subclasses it.

Service binary path
===================
Registered with ``exeName = <svc_venv>/Scripts/python.exe`` and
``exeArgs = "-u <this_file>"``, not the default ``pythonservice.exe``.
Reason: ``pythonservice.exe`` links against ``python<N>.dll`` and uses
the standard DLL search path, which on a venv install does not
include the base interpreter's directory. The venv's ``python.exe``
is a venv-aware launcher that finds ``python<N>.dll`` via
``pyvenv.cfg``. Workaround documented in pywin32 issues #1450 / #2032.

Importability
=============
This module imports pywin32 unconditionally; on a non-Windows host or
in a venv without pywin32 the import raises ``ImportError``. The
install pipeline copies this file to the svc-venv host without ever
importing it; the wheel installs it for distribution only. Tests
that need the helpers should ``pytest.importorskip("win32serviceutil")``
before importing this module.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

import servicemanager
import win32event
import win32service
import win32serviceutil


_SERVICE_NAME = "MobaideAgent"
_SERVICE_DISPLAY_NAME = "Mobaide Agent"
_SERVICE_DESCRIPTION = (
    "Mobaide agent — bridges a paired mobile app to a local terminal "
    "session manager via the Mobaide relay."
)

_DEFAULT_PROGRAM_FILES = "C:\\Program Files"
_DEFAULT_INSTALL_DIR_NAME = "MobaideAgent"

_STOP_GRACE_SECONDS = 25
_WAIT_TICK_MS = 5000

_AGENT_LOG_DIR_NAME = "logs"
_AGENT_LOG_FILE_NAME = "agent.err.log"
_AGENT_LOG_BACKUP_NAME = "agent.err.log.1"
_AGENT_LOG_ROTATE_BYTES = 5 * 1024 * 1024


def _default_prefix() -> Path:
    """Mirror of ``mobaide.launcher._default_prefix`` (kept self-contained).

    The service module cannot import ``mobaide`` (the svc venv has only
    ``pywin32`` installed), so the resolution logic is duplicated here.
    Drift between the two would surface as the service spawning a
    launcher that reads a different ``current_version`` file from the
    one the install script wrote, so any change here must also be made
    in ``mobaide/launcher/__main__.py``.
    """
    explicit = os.environ.get("MOBAIDE_AGENT_PREFIX")
    if explicit:
        return Path(explicit)
    program_files = os.environ.get("ProgramFiles", _DEFAULT_PROGRAM_FILES)
    return Path(program_files) / _DEFAULT_INSTALL_DIR_NAME


def _resolve_release_python(prefix: Path) -> Path:
    """Read ``current_version`` and return the active release's ``python.exe``.

    Raises FileNotFoundError with a service-log-friendly message for any
    of: missing/empty ``current_version`` file, missing release dir,
    missing interpreter. Callers log the message via
    ``servicemanager.LogErrorMsg`` so the failure shows up in the
    Windows Event Log under ``Application`` rather than as an opaque
    service exit code.
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
    python = prefix / "releases" / version / "venv" / "Scripts" / "python.exe"
    if not python.exists():
        raise FileNotFoundError(
            f"missing release interpreter {python} (current_version={version!r})"
        )
    return python


def _open_err_log(prefix: Path):
    """Open ``<prefix>/logs/agent.err.log`` for the launcher's stderr.

    Rotates at boot if the existing log is ≥ 5 MB (keep one backup
    at ``agent.err.log.1``). Returns a binary append-mode file
    object; the caller passes it to ``Popen`` as ``stderr`` and
    closes its own wrapper after ``Popen`` returns — subprocess
    has already dup'd the underlying OS handle into the child, and
    leaving the parent wrapper open would pin the file open and
    prevent the next boot's rotation.
    """
    log_dir = prefix / _AGENT_LOG_DIR_NAME
    log_path = log_dir / _AGENT_LOG_FILE_NAME
    log_dir.mkdir(parents=True, exist_ok=True)
    try:
        if log_path.stat().st_size >= _AGENT_LOG_ROTATE_BYTES:
            backup = log_dir / _AGENT_LOG_BACKUP_NAME
            try:
                backup.unlink()
            except FileNotFoundError:
                pass
            log_path.replace(backup)
    except FileNotFoundError:
        pass
    return log_path.open("ab")


def _spawn_launcher(release_python: Path, prefix: Path) -> subprocess.Popen[bytes]:
    """Spawn the release-pinned launcher and capture its stderr.

    stdout stays DEVNULL (uvicorn access logs are noisy; the app
    uses structlog for its own sinks). stderr is redirected to
    ``<prefix>/logs/agent.err.log`` instead of DEVNULL so a silent
    crash at SvcDoRun — config error, missing dep, port conflict —
    surfaces in a file ``install.ps1`` can point operators at.
    Without this redirect, every diagnostic past the eventvwr
    ``servicemanager.LogMsg`` write is lost (Python tracebacks
    go to stderr, not the Event Log).

    No ``CREATE_NEW_PROCESS_GROUP`` — Ctrl+Break delivery requires
    a console, which the service host doesn't have. The launcher
    handles its own teardown via a Job Object with
    ``KILL_ON_JOB_CLOSE``; SCM's ``Stop-Service`` calls
    ``TerminateProcess`` on the launcher, which closes its handles,
    which closes the job, which kills the agent atomically. Adding
    CREATE_NEW_PROCESS_GROUP here would put the launcher in its
    own group AND complicate that teardown — drop it.
    """
    err_log = _open_err_log(prefix)
    try:
        return subprocess.Popen(
            [str(release_python), "-m", "mobaide.launcher"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=err_log,
            close_fds=True,
        )
    finally:
        err_log.close()


class MobaideAgentService(win32serviceutil.ServiceFramework):
    """Windows service host for the Mobaide agent.

    Lifecycle:
      1. SCM invokes ``python.exe -u mobaide_service.py``.
      2. ``ServiceFramework`` reports ``SERVICE_START_PENDING``,
         then ``SERVICE_RUNNING`` once ``SvcDoRun`` is entered.
      3. ``SvcDoRun`` reads ``current_version``, spawns
         ``release_python -m mobaide.launcher``, and waits for
         either a stop event (``SvcStop``) or subprocess exit.
      4. Subprocess exit triggers SCM's recovery policy
         (3 × 5000 ms restarts, configured by install.ps1) — which
         composes with the launcher's own crash-loop rollback so a
         third bad boot picks up the previous release.
      5. ``SvcStop`` reports ``SERVICE_STOP_PENDING``, signals the
         event, terminates the subprocess (best-effort
         ``TerminateProcess`` since services run without a console).
    """

    _svc_name_ = _SERVICE_NAME
    _svc_display_name_ = _SERVICE_DISPLAY_NAME
    _svc_description_ = _SERVICE_DESCRIPTION

    def __init__(self, args: Sequence[str]) -> None:
        super().__init__(args)
        self._stop_event = win32event.CreateEvent(None, 0, 0, None)
        self._proc: subprocess.Popen[bytes] | None = None

    def SvcStop(self) -> None:
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self._stop_event)
        self._terminate_subprocess()

    def SvcDoRun(self) -> None:
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )
        prefix = _default_prefix()
        try:
            release_python = _resolve_release_python(prefix)
        except FileNotFoundError as exc:
            servicemanager.LogErrorMsg(
                f"{self._svc_name_}: cannot resolve release interpreter: {exc}"
            )
            return
        try:
            self._proc = _spawn_launcher(release_python, prefix)
        except OSError as exc:
            servicemanager.LogErrorMsg(
                f"{self._svc_name_}: failed to spawn launcher "
                f"{release_python}: {exc}"
            )
            return
        self._wait_until_stop_or_exit()
        self._terminate_subprocess()

    def _wait_until_stop_or_exit(self) -> None:
        assert self._proc is not None
        while True:
            rc = win32event.WaitForSingleObject(self._stop_event, _WAIT_TICK_MS)
            if rc == win32event.WAIT_OBJECT_0:
                return
            if self._proc.poll() is not None:
                servicemanager.LogWarningMsg(
                    f"{self._svc_name_}: launcher subprocess exited with code "
                    f"{self._proc.returncode}; SCM recovery policy will apply."
                )
                return

    def _terminate_subprocess(self) -> None:
        proc = self._proc
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


def _install_service() -> None:
    """Register the service with SCM.

    Pinned to ``sys.executable`` (the svc venv's ``python.exe``) and
    ``-u <this_file>`` rather than the pywin32 default
    ``pythonservice.exe``. See the module docstring for rationale.
    """
    exe_name = sys.executable
    exe_args = '-u "' + os.path.abspath(__file__) + '"'
    win32serviceutil.InstallService(
        pythonClassString=f"{MobaideAgentService.__module__}.{MobaideAgentService.__name__}",
        serviceName=_SERVICE_NAME,
        displayName=_SERVICE_DISPLAY_NAME,
        description=_SERVICE_DESCRIPTION,
        startType=win32service.SERVICE_AUTO_START,
        exeName=exe_name,
        exeArgs=exe_args,
    )


def _remove_service() -> None:
    win32serviceutil.RemoveService(_SERVICE_NAME)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv if argv is None else argv)
    if len(args) <= 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(MobaideAgentService)
        servicemanager.StartServiceCtrlDispatcher()
        return 0
    verb = args[1]
    if verb == "install":
        _install_service()
        return 0
    if verb == "remove":
        _remove_service()
        return 0
    if verb in {"start", "stop", "restart", "update", "debug", "status"}:
        win32serviceutil.HandleCommandLine(MobaideAgentService, argv=args)
        return 0
    sys.stderr.write(
        f"mobaide_service: unknown verb {verb!r}. "
        "Valid: install, remove, start, stop, restart, update, debug, status.\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
