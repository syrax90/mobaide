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
_AGENT_LOG_ROTATE_BYTES = 5 * 1024 * 1024

# Canonical in ``mobaide.agent.updater.EXIT_RESTART_REQUESTED``. Duplicated
# here because this supervisor is a standalone stdlib script — importing
# ``mobaide`` would defeat the "survives a half-staged or rolled-back
# release" guarantee. Drift between the two breaks the post-pairing
# restart: agent exits 75, this side fails to recognise it, supervisor
# exits to Task Scheduler instead of respawning immediately.
_EXIT_RESTART_REQUESTED = 75


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


def _resolve_release_python(prefix: Path) -> Path:
    """Read ``current_version`` and return the active release's ``python.exe``.

    Raises FileNotFoundError with an operator-friendly message for any of:
    missing/empty ``current_version`` file, missing release dir, missing
    interpreter. Callers print the message to stderr so it lands in the
    captured ``agent.err.log``.
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
      * Resolve ``current_version`` → release ``python.exe`` (errors print
        to stderr and exit code 70).
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
    try:
        while True:
            try:
                release_python = _resolve_release_python(prefix)
            except FileNotFoundError as exc:
                sys.stderr.write(
                    f"mobaide_launcher: cannot resolve release interpreter: {exc}\n"
                )
                return 70
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
    return _supervise(prefix)


if __name__ == "__main__":
    sys.exit(main())
