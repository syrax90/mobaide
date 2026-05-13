"""Tests for ``deploy/windows_service.py``.

The module hosts the Windows service that replaces the pre-fix
``cmd.exe /c launcher.cmd`` design (real root cause of the
``Start-Service : Cannot start service MobaideAgent on computer '.'``
failure). It imports ``pywin32`` unconditionally, so this file
auto-skips on hosts where the import is not satisfiable (any
non-Windows CI, or a Windows CI without pywin32 in the env).

Lives under ``deploy/`` rather than inside the mobaide package because
it is deploy infrastructure, not agent runtime — nothing in mobaide.*
imports it. install.ps1 fetches it from get.mobaide.com (gh-pages)
alongside install.ps1 itself, so the two cannot desync.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

import pytest

pytest.importorskip(
    "win32serviceutil",
    reason="pywin32 not available — windows_service module is Windows-only",
)

sys.path.insert(
    0,
    str(Path(__file__).resolve().parent.parent / "deploy"),
)

import windows_service  # noqa: E402


def test_default_prefix_honours_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """``MOBAIDE_AGENT_PREFIX`` env var must override the default."""
    monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", r"D:\custom\mobaide")
    assert windows_service._default_prefix() == Path(r"D:\custom\mobaide")


def test_default_prefix_uses_program_files_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the env var, the default prefix is
    ``%ProgramFiles%\\MobaideAgent``.

    Mirrors the resolution in install.ps1's ``$Script:Prefix`` default
    and ``mobaide.launcher._default_prefix``. Drift between the three
    silently breaks the service start (it would read the wrong
    ``current_version`` file).
    """
    monkeypatch.delenv("MOBAIDE_AGENT_PREFIX", raising=False)
    monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
    assert (
        windows_service._default_prefix()
        == Path(r"C:\Program Files") / "MobaideAgent"
    )


def test_resolve_release_python_returns_release_interpreter(tmp_path: Path) -> None:
    """Happy path: ``current_version`` -> ``releases/<v>/venv/Scripts/python.exe``."""
    (tmp_path / "current_version").write_text("1.2.3\n", encoding="utf-8")
    release_scripts = tmp_path / "releases" / "1.2.3" / "venv" / "Scripts"
    release_scripts.mkdir(parents=True)
    (release_scripts / "python.exe").write_bytes(b"\x4d\x5a")
    resolved = windows_service._resolve_release_python(tmp_path)
    assert resolved == release_scripts / "python.exe"


def test_resolve_release_python_raises_on_missing_current_version(
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError, match="cannot read"):
        windows_service._resolve_release_python(tmp_path)


def test_resolve_release_python_raises_on_empty_current_version(
    tmp_path: Path,
) -> None:
    (tmp_path / "current_version").write_text("   \n", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="empty"):
        windows_service._resolve_release_python(tmp_path)


def test_resolve_release_python_raises_on_missing_interpreter(
    tmp_path: Path,
) -> None:
    (tmp_path / "current_version").write_text("9.9.9", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="missing release interpreter"):
        windows_service._resolve_release_python(tmp_path)


def test_main_install_verb_calls_install_service() -> None:
    """``mobaide_service.py install`` must route to ``_install_service``."""
    with mock.patch.object(windows_service, "_install_service") as install_mock:
        rc = windows_service.main(["mobaide_service.py", "install"])
    assert rc == 0
    install_mock.assert_called_once_with()


def test_main_remove_verb_calls_remove_service() -> None:
    with mock.patch.object(windows_service, "_remove_service") as remove_mock:
        rc = windows_service.main(["mobaide_service.py", "remove"])
    assert rc == 0
    remove_mock.assert_called_once_with()


def test_main_unknown_verb_returns_nonzero() -> None:
    """An unrecognised verb prints a usage message and returns non-zero."""
    rc = windows_service.main(["mobaide_service.py", "frobnicate"])
    assert rc != 0


def test_main_no_args_starts_service_ctrl_dispatcher() -> None:
    """When invoked by SCM (no argv beyond argv[0]), the module must enter
    the SCM-dispatch path.

    This is the path SCM takes after spawning ``python.exe -u
    mobaide_service.py``. The dispatch chain
    (``Initialize`` -> ``PrepareToHostSingle`` ->
    ``StartServiceCtrlDispatcher``) hands control to the SCM
    framework which then calls ``SvcDoRun``.
    """
    with (
        mock.patch.object(windows_service, "servicemanager") as svcman,
        mock.patch.object(windows_service, "win32serviceutil"),
    ):
        rc = windows_service.main(["mobaide_service.py"])
    assert rc == 0
    svcman.Initialize.assert_called_once_with()
    svcman.PrepareToHostSingle.assert_called_once_with(
        windows_service.MobaideAgentService
    )
    svcman.StartServiceCtrlDispatcher.assert_called_once_with()


def test_install_service_uses_python_exe_not_pythonservice_exe() -> None:
    """``_install_service`` must register with ``exeName=sys.executable``
    and ``exeArgs='-u <module path>'``, NOT pywin32's default
    ``pythonservice.exe``.

    Rationale (pywin32 #1450, #2032): pythonservice.exe links against
    python<N>.dll and uses standard DLL search, which does not find
    the DLL inside a venv (it's in the base interpreter's install
    dir). The venv's python.exe is venv-aware (reads pyvenv.cfg) and
    finds the DLL correctly. Registering with python.exe sidesteps
    the venv-DLL-search problem.
    """
    with mock.patch.object(
        windows_service.win32serviceutil, "InstallService"
    ) as install:
        windows_service._install_service()
    assert install.call_count == 1
    kwargs = install.call_args.kwargs
    assert kwargs["exeName"] == sys.executable
    assert windows_service.__file__ is not None
    assert os.path.abspath(windows_service.__file__) in kwargs["exeArgs"]
    assert kwargs["serviceName"] == "MobaideAgent"
    assert kwargs["startType"] == windows_service.win32service.SERVICE_AUTO_START


def test_install_service_pythonClassString_targets_service_class() -> None:
    """The registered ``pythonClassString`` must resolve to
    ``MobaideAgentService`` so SCM dispatch reaches our handler.

    pywin32 reads the PythonClass key from the registry on service
    start and instantiates that class. If the key points to the
    wrong module:class pair, the service starts pythonservice.exe,
    fails to find the class, and exits.
    """
    with mock.patch.object(
        windows_service.win32serviceutil, "InstallService"
    ) as install:
        windows_service._install_service()
    class_string = install.call_args.kwargs["pythonClassString"]
    assert class_string.endswith(".MobaideAgentService")


def test_service_class_constants() -> None:
    """The service-name constants must match what install.ps1 expects.

    install.ps1's ``$Script:ServiceName = "MobaideAgent"`` is the SCM
    key the rest of the installer uses (``sc.exe failure $ServiceName
    ...``, ``Start-Service -Name $ServiceName``). If the Python side
    registers under a different name, ``Start-Service`` cannot find
    the service.
    """
    assert windows_service.MobaideAgentService._svc_name_ == "MobaideAgent"
    assert windows_service.MobaideAgentService._svc_display_name_ == "Mobaide Agent"


def test_spawn_launcher_redirects_stderr_to_agent_err_log(tmp_path: Path) -> None:
    """``_spawn_launcher`` must redirect the launcher's stderr to
    ``<prefix>/logs/agent.err.log`` (not DEVNULL).

    Before the redirect, every Python traceback from the launcher
    or the agent (config error, missing dep, port conflict) was
    discarded — operators only saw the eventvwr
    ``servicemanager.LogMsg`` line, never the real error. The fix
    routes stderr to a rotated log file install.ps1's
    ``Wait-AgentHealthy`` can point at.
    """
    captured_kwargs: dict[str, object] = {}

    class _FakePopen:
        def __init__(self, *args: object, **kwargs: object) -> None:
            captured_kwargs.update(kwargs)

    with mock.patch.object(windows_service.subprocess, "Popen", _FakePopen):
        windows_service._spawn_launcher(Path(r"C:\fake\python.exe"), tmp_path)

    stderr = captured_kwargs.get("stderr")
    assert stderr is not windows_service.subprocess.DEVNULL, (
        "stderr must NOT be DEVNULL — a silent launcher crash is the "
        "exact diagnostic gap install.ps1's Wait-AgentHealthy was "
        "added to expose"
    )
    log_path = tmp_path / "logs" / "agent.err.log"
    assert log_path.exists(), (
        "logs/agent.err.log must be created on the parent side; "
        f"missing at {log_path}"
    )


def test_spawn_launcher_rotates_oversized_log(tmp_path: Path) -> None:
    """Pre-existing ``agent.err.log`` larger than 5 MB must be
    rotated to ``agent.err.log.1`` so a crash-loop with verbose
    tracebacks can't fill the disk.
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_path = log_dir / "agent.err.log"
    log_path.write_bytes(b"\x00" * (6 * 1024 * 1024))

    class _FakePopen:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

    with mock.patch.object(windows_service.subprocess, "Popen", _FakePopen):
        windows_service._spawn_launcher(Path(r"C:\fake\python.exe"), tmp_path)

    assert (log_dir / "agent.err.log.1").exists(), (
        "oversized log must be rotated to .1 backup"
    )
    assert log_path.exists(), "fresh log file must exist after rotation"
    assert log_path.stat().st_size < 6 * 1024 * 1024


def test_spawn_launcher_does_not_set_create_new_process_group(
    tmp_path: Path,
) -> None:
    """``CREATE_NEW_PROCESS_GROUP`` is incompatible with the
    launcher's Job Object / ``KILL_ON_JOB_CLOSE`` teardown.

    The launcher now creates a job (see
    ``mobaide/launcher/__main__.py:_spawn_and_wait_windows``) and
    relies on SCM's ``TerminateProcess`` closing the launcher's
    handles → job closes → agent dies atomically. A new process
    group would put the launcher in its own group and complicate
    that teardown without giving anything in return — Ctrl+Break
    delivery requires a console, which services lack.
    """
    captured_kwargs: dict[str, object] = {}

    class _FakePopen:
        def __init__(self, *args: object, **kwargs: object) -> None:
            captured_kwargs.update(kwargs)

    with mock.patch.object(windows_service.subprocess, "Popen", _FakePopen):
        windows_service._spawn_launcher(Path(r"C:\fake\python.exe"), tmp_path)

    creationflags = captured_kwargs.get("creationflags", 0)
    assert (
        creationflags == 0
        or not (creationflags & windows_service.subprocess.CREATE_NEW_PROCESS_GROUP)
    ), (
        "creationflags must not include CREATE_NEW_PROCESS_GROUP — see "
        "the _spawn_launcher docstring for the Job Object rationale"
    )


def test_spawn_launcher_keeps_stdout_devnull(tmp_path: Path) -> None:
    """``_spawn_launcher`` must redirect stdout to ``DEVNULL`` — the
    stderr-to-file change deliberately does not touch stdout.

    Rationale from the production docstring: uvicorn access logs
    are noisy and the app already uses structlog for its own sinks,
    so a stdout file would just bloat the log directory with traffic
    noise. Verifying stdout stays DEVNULL guards against an
    over-eager future refactor that "unifies" both streams onto the
    same file and floods ``agent.err.log`` with uvicorn ``GET
    /api/health 200`` lines on every Wait-AgentHealthy poll.
    """
    captured_kwargs: dict[str, object] = {}

    class _FakePopen:
        def __init__(self, *args: object, **kwargs: object) -> None:
            captured_kwargs.update(kwargs)

    with mock.patch.object(windows_service.subprocess, "Popen", _FakePopen):
        windows_service._spawn_launcher(Path(r"C:\fake\python.exe"), tmp_path)

    assert captured_kwargs.get("stdout") is windows_service.subprocess.DEVNULL, (
        "stdout must remain DEVNULL — only stderr is re-routed. "
        "uvicorn access logs into the err log would defeat its "
        "diagnostic value (every health-check poll would push a real "
        "traceback off the tail)"
    )


def test_spawn_launcher_closes_parent_file_handle_after_popen(
    tmp_path: Path,
) -> None:
    """The parent's file wrapper opened by ``_open_err_log`` must be
    closed once ``subprocess.Popen`` has dup'd the underlying OS
    handle into the child.

    Keeping the parent wrapper open pins the file open across
    rotation: the next service-host boot would see the existing
    log >= 5 MB, call ``log_path.replace(backup)``, but the parent
    handle (held by the prior service-host process if it was still
    running, or by a stale reference) would prevent the rename on
    Windows (``WinError 32: file is being used by another process``).
    Closing the wrapper here is the symmetric pair to the dup
    semantics ``subprocess`` already gives us.
    """
    captured_stderr: list[object] = []

    class _FakePopen:
        def __init__(self, *args: object, **kwargs: object) -> None:
            captured_stderr.append(kwargs.get("stderr"))

    with mock.patch.object(windows_service.subprocess, "Popen", _FakePopen):
        windows_service._spawn_launcher(Path(r"C:\fake\python.exe"), tmp_path)

    assert captured_stderr, "Popen was not invoked"
    fh = captured_stderr[0]
    assert hasattr(fh, "closed"), (
        f"expected stderr to be a file-like object, got {type(fh).__name__}"
    )
    assert getattr(fh, "closed", None) is True, (
        "parent's file wrapper must be closed after Popen returns "
        "(subprocess has already dup'd the OS fd into the child); "
        "leaving the wrapper open pins the file and blocks the next "
        "boot's rotation rename on Windows"
    )


def test_open_err_log_overwrites_existing_backup(tmp_path: Path) -> None:
    """When ``agent.err.log`` is oversized AND ``agent.err.log.1``
    already exists, rotation must unlink the stale backup first,
    then replace the live log onto the backup name.

    Without the unlink, ``log_path.replace(backup)`` on Windows
    raises ``WinError 183: file already exists`` and the rotation
    aborts. The stale-backup case is the steady state on any host
    that has rolled over once — the test that exercises it from a
    pristine tmp dir (existing
    ``test_spawn_launcher_rotates_oversized_log``) only proves the
    fresh-rotation branch.
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_path = log_dir / "agent.err.log"
    backup_path = log_dir / "agent.err.log.1"
    log_path.write_bytes(b"NEW" * (2 * 1024 * 1024))
    backup_path.write_bytes(b"OLD")
    assert log_path.stat().st_size >= 5 * 1024 * 1024

    class _FakePopen:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

    with mock.patch.object(windows_service.subprocess, "Popen", _FakePopen):
        windows_service._spawn_launcher(Path(r"C:\fake\python.exe"), tmp_path)

    assert backup_path.exists()
    backup_content = backup_path.read_bytes()
    assert backup_content.startswith(b"NEW"), (
        "rotation must overwrite the stale backup with the just-rotated "
        "live log content — found bytes "
        f"{backup_content[:8]!r} (looks like the original 'OLD' "
        "backup, which means the rotation was aborted by the "
        "FileExistsError that ``replace()`` raises on Windows when "
        "the destination already exists)"
    )


def test_open_err_log_creates_logs_dir_when_missing(tmp_path: Path) -> None:
    """``_open_err_log`` must create the ``logs/`` subdirectory on a
    fresh prefix (no pre-existing dir).

    The MobaideAgent install flow only guarantees ``<prefix>`` and
    ``<prefix>/releases/<v>``; the ``logs/`` directory is allocated
    lazily on first service boot. Without the ``mkdir(parents=True,
    exist_ok=True)``, the first SvcDoRun would crash with
    ``FileNotFoundError`` opening the log, which (ironically) the
    operator would never see because stderr is going to that very
    missing file.
    """
    assert not (tmp_path / "logs").exists()

    class _FakePopen:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

    with mock.patch.object(windows_service.subprocess, "Popen", _FakePopen):
        windows_service._spawn_launcher(Path(r"C:\fake\python.exe"), tmp_path)

    assert (tmp_path / "logs").is_dir(), (
        "_open_err_log must create <prefix>/logs/ when absent "
        "(install only guarantees <prefix> itself)"
    )
    assert (tmp_path / "logs" / "agent.err.log").exists()
