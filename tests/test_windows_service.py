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


def test_spawn_launcher_redirects_stdout_to_agent_out_log(tmp_path: Path) -> None:
    """``_spawn_launcher`` must redirect the launcher's stdout to
    ``<prefix>/logs/agent.out.log`` (not DEVNULL).

    structlog's queue listener writes its records to ``sys.stdout``;
    routing stdout to DEVNULL silently discarded every
    ``pairing_monitor.*`` / ``relay_client.*`` / ``http.request.*``
    log line. That gap is what hid the original "RELAY_AGENT_*
    stays empty after pairing" failure mode — operators saw an
    empty err log and had no signal pointing at which step of
    the pairing flow had failed.

    Pinning the redirect here so a regression that reverts to
    DEVNULL surfaces immediately. Note: separate file from
    agent.err.log so a noisy structlog stream (every HTTP request
    is one line) doesn't push real tracebacks off the err log
    tail that operators check first.
    """
    captured_kwargs: dict[str, object] = {}

    class _FakePopen:
        def __init__(self, *args: object, **kwargs: object) -> None:
            captured_kwargs.update(kwargs)

    with mock.patch.object(windows_service.subprocess, "Popen", _FakePopen):
        windows_service._spawn_launcher(Path(r"C:\fake\python.exe"), tmp_path)

    stdout = captured_kwargs.get("stdout")
    assert stdout is not windows_service.subprocess.DEVNULL, (
        "stdout must NOT be DEVNULL — every structlog line going to "
        "/dev/null was the systemic blocker on every prior debugging "
        "attempt of the empty-.env pairing failure mode"
    )
    out_log = tmp_path / "logs" / "agent.out.log"
    assert out_log.exists(), (
        "logs/agent.out.log must be created on the parent side; "
        f"missing at {out_log}"
    )


def test_spawn_launcher_rotates_oversized_out_log(tmp_path: Path) -> None:
    """Pre-existing ``agent.out.log`` larger than 5 MB must be
    rotated to ``agent.out.log.1`` so structlog's per-request lines
    can't fill the disk on a long-running agent.
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    out_log_path = log_dir / "agent.out.log"
    out_log_path.write_bytes(b"\x00" * (6 * 1024 * 1024))

    class _FakePopen:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

    with mock.patch.object(windows_service.subprocess, "Popen", _FakePopen):
        windows_service._spawn_launcher(Path(r"C:\fake\python.exe"), tmp_path)

    assert (log_dir / "agent.out.log.1").exists(), (
        "oversized stdout log must be rotated to .1 backup"
    )
    assert out_log_path.exists(), "fresh out log file must exist after rotation"
    assert out_log_path.stat().st_size < 6 * 1024 * 1024


def test_spawn_launcher_closes_parent_file_handle_after_popen(
    tmp_path: Path,
) -> None:
    """The parent's file wrapper opened by ``_open_log`` must be
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


def test_spawn_launcher_closes_parent_stdout_handle_after_popen(
    tmp_path: Path,
) -> None:
    """Symmetric companion to
    ``test_spawn_launcher_closes_parent_file_handle_after_popen``: the
    parent's stdout wrapper opened for ``agent.out.log`` must also be
    closed once ``subprocess.Popen`` returns.

    The pre-fix code only redirected stderr to a file; the new
    redirect-stdout path opens a SECOND file handle (for
    ``agent.out.log``). The existing close-after-Popen test only
    verifies the stderr handle. Without an explicit pin on stdout, a
    regression that drops the ``out_log.close()`` call in
    ``_spawn_launcher``'s ``finally`` block would re-introduce the
    file-pin-on-Windows rotation bug for the out log specifically:
    next service-host boot would see oversized ``agent.out.log``,
    call ``log_path.replace(backup)``, and fail with
    ``WinError 32: file is being used by another process`` because
    the prior wrapper handle was leaked.
    """
    captured_stdout: list[object] = []

    class _FakePopen:
        def __init__(self, *args: object, **kwargs: object) -> None:
            captured_stdout.append(kwargs.get("stdout"))

    with mock.patch.object(windows_service.subprocess, "Popen", _FakePopen):
        windows_service._spawn_launcher(Path(r"C:\fake\python.exe"), tmp_path)

    assert captured_stdout, "Popen was not invoked"
    fh = captured_stdout[0]
    assert hasattr(fh, "closed"), (
        f"expected stdout to be a file-like object, got {type(fh).__name__}"
    )
    assert getattr(fh, "closed", None) is True, (
        "parent's stdout wrapper for agent.out.log must be closed after "
        "Popen returns — leaving it open pins the out log file on "
        "Windows and blocks the next boot's rotation rename. "
        "The fix lives in _spawn_launcher's ``finally: out_log.close()``"
    )


def test_open_log_overwrites_existing_backup(tmp_path: Path) -> None:
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


def test_open_log_creates_logs_dir_when_missing(tmp_path: Path) -> None:
    """``_open_log`` must create the ``logs/`` subdirectory on a
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
        "_open_log must create <prefix>/logs/ when absent "
        "(install only guarantees <prefix> itself)"
    )
    assert (tmp_path / "logs" / "agent.err.log").exists()


def test_exit_restart_requested_constant_matches_agent_updater() -> None:
    """The service host's local ``_EXIT_RESTART_REQUESTED`` constant MUST
    equal ``mobaide.agent.updater.EXIT_RESTART_REQUESTED``.

    The svc venv intentionally cannot import ``mobaide`` (it ships only
    pywin32 + stdlib, by design — the wrapper has to survive a
    half-staged or rolled-back release), so the constant is duplicated.
    A drift between the two breaks the post-pairing restart silently:
    the agent exits with the canonical value, the service host sees a
    different number, falls through to "stop normally", and the service
    stays stopped. This test imports the canonical definition from the
    in-tree source and asserts equality.
    """
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "mobaide_agent"))
    try:
        from mobaide.agent.updater import (  # type: ignore[import-not-found]
            EXIT_RESTART_REQUESTED,
        )
    finally:
        sys.path.pop(0)
    assert windows_service._EXIT_RESTART_REQUESTED == EXIT_RESTART_REQUESTED, (
        f"windows_service._EXIT_RESTART_REQUESTED="
        f"{windows_service._EXIT_RESTART_REQUESTED!r} must equal "
        f"mobaide.agent.updater.EXIT_RESTART_REQUESTED="
        f"{EXIT_RESTART_REQUESTED!r} — see the comment on the duplicated "
        "constant in deploy/windows_service.py for why this is duplicated "
        "and why drift is a silent break"
    )


class _StubProc:
    """Minimal stand-in for ``subprocess.Popen`` used by SvcDoRun tests.

    ``poll`` returns ``None`` for the first ``poll_none_count`` calls,
    then ``returncode``; ``returncode`` mirrors what the launcher
    subprocess would have exited with.
    """

    def __init__(self, returncode: int) -> None:
        self.returncode = returncode
        self.pid = 12345

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        pass

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode

    def kill(self) -> None:
        pass


def _install_svcdorun_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    exit_codes: list[int],
    stop_after_iteration: int | None = None,
):
    """Wire the SvcDoRun loop's collaborators with deterministic stubs.

    Returns ``(spawn_log, log_messages)`` for assertions. ``exit_codes``
    is consumed one entry per spawned launcher; if exhausted, the test
    fails (indicates an unexpected re-spawn). ``stop_after_iteration``
    causes the stop event WaitForSingleObject to return WAIT_OBJECT_0
    before the n-th re-spawn check (0-indexed), simulating ``Stop-Service``
    arriving between launcher exit and the re-spawn decision.
    """
    spawn_log: list[Path] = []
    log_messages: list[tuple[str, object]] = []
    iteration = [0]

    def fake_resolve(prefix: Path) -> Path:
        return prefix / "fake_python.exe"

    def fake_spawn(python_path: Path, prefix: Path):
        if not exit_codes:
            raise AssertionError(
                "unexpected re-spawn: exit_codes list exhausted; SvcDoRun "
                "looped more times than the test expected"
            )
        spawn_log.append(python_path)
        rc = exit_codes.pop(0)
        return _StubProc(rc)

    def fake_wait_single(event: object, timeout: object) -> int:
        # Called from two places in SvcDoRun:
        #   (a) inside _wait_until_stop_or_exit (handled separately —
        #       we drive that path by making poll() return immediately,
        #       so _wait_until_stop_or_exit only checks the stop event
        #       once with the tick timeout and returns when poll yields
        #       a returncode); the timeout argument there is _WAIT_TICK_MS.
        #   (b) the post-_wait_until_stop_or_exit stop-event check with
        #       timeout=0.
        # Stub: never signal at (a); signal at (b) only when the test
        # requested it for this iteration.
        if timeout == 0:
            if (
                stop_after_iteration is not None
                and iteration[0] == stop_after_iteration
            ):
                return 0  # WAIT_OBJECT_0
            iteration[0] += 1
            return 258  # WAIT_TIMEOUT
        return 258  # WAIT_TIMEOUT

    def fake_log_info(msg: str) -> None:
        log_messages.append(("info", msg))

    def fake_log_warning(msg: str) -> None:
        log_messages.append(("warning", msg))

    def fake_log_error(msg: str) -> None:
        log_messages.append(("error", msg))

    def fake_log_msg(*args: object, **kwargs: object) -> None:
        log_messages.append(("msg", args))

    monkeypatch.setattr(
        windows_service, "_resolve_release_python", fake_resolve
    )
    monkeypatch.setattr(windows_service, "_spawn_launcher", fake_spawn)
    monkeypatch.setattr(
        windows_service.win32event, "WaitForSingleObject", fake_wait_single
    )
    monkeypatch.setattr(windows_service.win32event, "WAIT_OBJECT_0", 0)
    monkeypatch.setattr(
        windows_service.servicemanager, "LogInfoMsg", fake_log_info
    )
    monkeypatch.setattr(
        windows_service.servicemanager, "LogWarningMsg", fake_log_warning
    )
    monkeypatch.setattr(
        windows_service.servicemanager, "LogErrorMsg", fake_log_error
    )
    monkeypatch.setattr(
        windows_service.servicemanager, "LogMsg", fake_log_msg
    )
    monkeypatch.setattr(
        windows_service.servicemanager, "EVENTLOG_INFORMATION_TYPE", 4
    )
    monkeypatch.setattr(
        windows_service.servicemanager, "PYS_SERVICE_STARTED", 7
    )
    return spawn_log, log_messages


def _make_service_instance(monkeypatch: pytest.MonkeyPatch):
    """Construct a ``MobaideAgentService`` for SvcDoRun unit tests.

    ``ServiceFramework.__init__`` would normally allocate SCM-side state;
    sidestep that and instantiate the bare class so the test can invoke
    ``SvcDoRun`` directly without an SCM dispatcher.
    """
    instance = windows_service.MobaideAgentService.__new__(
        windows_service.MobaideAgentService
    )
    instance._stop_event = object()
    instance._proc = None
    return instance


def test_svcdorun_respawns_on_exit_restart_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Windows counterpart to systemd's ``RestartForceExitStatus=75``.

    Agent exits 75 after a successful pair (or after a staged
    auto-update); the service host must re-spawn the launcher so the
    next boot reads the just-written ``RELAY_AGENT_ID`` /
    ``RELAY_AGENT_TOKEN`` and brings up the relay client. Without this
    re-spawn loop, SvcDoRun would return on first exit, ServiceFramework
    would report SERVICE_STOPPED, and the user would be forced to
    ``Restart-Service MobaideAgent`` manually — exactly the
    "no additional actions" gap the loop was added to close.
    """
    spawn_log, log_messages = _install_svcdorun_stubs(
        monkeypatch,
        exit_codes=[
            windows_service._EXIT_RESTART_REQUESTED,
            0,  # second spawn exits cleanly; loop terminates
        ],
    )
    monkeypatch.setenv(
        "MOBAIDE_AGENT_PREFIX", r"C:\Program Files\MobaideAgent"
    )
    instance = _make_service_instance(monkeypatch)

    instance.SvcDoRun()

    assert len(spawn_log) == 2, (
        f"expected exactly 2 spawns (initial + re-spawn after exit 75), "
        f"got {len(spawn_log)}"
    )
    info_messages = [
        str(msg) for level, msg in log_messages if level == "info"
    ]
    assert any(
        "restart requested" in msg for msg in info_messages
    ), (
        f"SvcDoRun must emit an Event-Log info line when re-spawning "
        f"after exit 75 so operators can correlate the restart with "
        f"the agent's pairing-complete event; got logs: {log_messages!r}"
    )


def test_svcdorun_does_not_respawn_on_clean_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit code 0 (clean stop) must NOT trigger a re-spawn.

    Only ``_EXIT_RESTART_REQUESTED`` (75) is the restart signal; other
    codes mean the launcher decided to stop and the service host should
    not paper over that (let SCM observe a normal stop).
    """
    spawn_log, _ = _install_svcdorun_stubs(
        monkeypatch,
        exit_codes=[0],
    )
    monkeypatch.setenv(
        "MOBAIDE_AGENT_PREFIX", r"C:\Program Files\MobaideAgent"
    )
    instance = _make_service_instance(monkeypatch)

    instance.SvcDoRun()

    assert len(spawn_log) == 1, (
        f"clean-exit (rc=0) must not trigger a re-spawn; got "
        f"{len(spawn_log)} spawns"
    )


def test_svcdorun_does_not_respawn_on_crash_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Crash exit codes (e.g. 70 from the launcher's missing-interpreter
    path) must NOT trigger a re-spawn — those are real failures the
    operator needs to see, and the launcher's own crash-loop rollback
    handles repeated failures across full service-restart cycles.
    """
    spawn_log, _ = _install_svcdorun_stubs(
        monkeypatch,
        exit_codes=[70],
    )
    monkeypatch.setenv(
        "MOBAIDE_AGENT_PREFIX", r"C:\Program Files\MobaideAgent"
    )
    instance = _make_service_instance(monkeypatch)

    instance.SvcDoRun()

    assert len(spawn_log) == 1, (
        f"crash exit (rc=70) must not trigger a re-spawn; got "
        f"{len(spawn_log)} spawns"
    )


def test_svcdorun_stop_event_short_circuits_respawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Stop-Service`` arriving between launcher exit and the re-spawn
    decision must terminate the service, not loop into another spawn.

    The re-spawn loop polls the stop event with timeout=0 right after
    ``_wait_until_stop_or_exit`` returns; without that check, a stop
    arriving in the narrow window after exit 75 but before the
    re-spawn would still be honoured eventually (next iteration of
    ``_wait_until_stop_or_exit`` would see the event) but only after
    a spurious extra agent boot. The check makes the stop deterministic.
    """
    spawn_log, _ = _install_svcdorun_stubs(
        monkeypatch,
        exit_codes=[
            windows_service._EXIT_RESTART_REQUESTED,
            # We never expect to consume this — the stop event short-
            # circuits between iterations. Provide one so an unwanted
            # re-spawn doesn't AssertionError before the test's own
            # spawn-count assertion runs.
            0,
        ],
        stop_after_iteration=0,
    )
    monkeypatch.setenv(
        "MOBAIDE_AGENT_PREFIX", r"C:\Program Files\MobaideAgent"
    )
    instance = _make_service_instance(monkeypatch)

    instance.SvcDoRun()

    assert len(spawn_log) == 1, (
        "stop event signaled after exit 75 must terminate without "
        f"re-spawning; got {len(spawn_log)} spawns"
    )


def test_svcdorun_resolves_release_python_each_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Between re-spawns, ``_resolve_release_python`` is called again so a
    ``current_version`` rewritten by the auto-updater between iterations
    is picked up on the next boot.

    Without this, a staged auto-update + ``exit 75`` would re-spawn
    the launcher pinned to the OLD release, defeating the staged
    rollout — the operator would see the agent restart but stay on
    the previous version until the SERVICE itself was restarted.
    """
    _install_svcdorun_stubs(
        monkeypatch,
        exit_codes=[
            windows_service._EXIT_RESTART_REQUESTED,
            windows_service._EXIT_RESTART_REQUESTED,
            0,
        ],
    )
    # Override the helper's fake_resolve with a counting variant so the
    # test can assert on the number of invocations across the loop.
    resolve_calls: list[Path] = []

    def counting_resolve(prefix: Path) -> Path:
        resolve_calls.append(prefix)
        return prefix / "fake_python.exe"

    monkeypatch.setattr(
        windows_service, "_resolve_release_python", counting_resolve
    )
    monkeypatch.setenv(
        "MOBAIDE_AGENT_PREFIX", r"C:\Program Files\MobaideAgent"
    )
    instance = _make_service_instance(monkeypatch)

    instance.SvcDoRun()

    assert len(resolve_calls) == 3, (
        f"_resolve_release_python must be called once per spawn (3 spawns "
        f"-> 3 resolves so a between-restart ``current_version`` rewrite "
        f"is picked up); got {len(resolve_calls)} calls"
    )
