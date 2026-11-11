"""Tests for ``deploy/windows_launcher.py``.

The per-user Scheduled Task supervisor: it resolves the active release,
spawns ``<release python> -m mobaide.launcher`` with its output in rotated
logs, respawns on exit 75 (the Windows counterpart of systemd's
``RestartForceExitStatus=75``), and self-heals to the newest complete sibling
release when the current one cannot run. Stdlib-only by design, so every
test except the Job Object one runs on any OS.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "deploy"))

import windows_launcher  # noqa: E402

_REAL_BIND_DESCENDANTS = windows_launcher._bind_descendants_to_supervisor
_FAKE_PYTHON = Path(r"C:\fake\python.exe")


@pytest.fixture(autouse=True)
def _no_kill_on_close_job(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep ``main`` from putting the pytest process in a kill-on-close job:
    joining one cannot be undone, and pytest's children would die with it."""
    monkeypatch.setattr(
        windows_launcher, "_bind_descendants_to_supervisor", lambda: None
    )


def _make_win_release(
    prefix: Path, version: str, *, complete: bool = True
) -> Path:
    """``releases/<version>/venv/Scripts/python.exe``, plus the ``.installed``
    sentinel when ``complete`` (a venv swapped in but not pip-installed has
    neither the sentinel nor the console script)."""
    release = prefix / "releases" / version
    scripts = release / "venv" / "Scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "python.exe").write_text("stub", encoding="utf-8")
    if complete:
        (release / ".installed").write_text(version, encoding="utf-8")
    return scripts / "python.exe"


class TestDefaultPrefix:
    """``--prefix`` argv > ``MOBAIDE_AGENT_PREFIX`` > ``%ProgramFiles%``.
    These only build ``Path`` objects; nothing touches the filesystem."""

    @pytest.mark.parametrize(
        ("cli", "env", "expected"),
        [
            (r"D:\from\argv", r"D:\from\env", Path(r"D:\from\argv")),
            (None, r"D:\from\env", Path(r"D:\from\env")),
            # Path("") is the cwd: an empty --prefix must fall through.
            ("", r"D:\from\env", Path(r"D:\from\env")),
            (None, None, Path(r"C:\Program Files") / "MobaideAgent"),
        ],
        ids=["argv-wins", "env", "empty-argv-falls-through", "program-files"],
    )
    def test_precedence(
        self,
        monkeypatch: pytest.MonkeyPatch,
        cli: str | None,
        env: str | None,
        expected: Path,
    ) -> None:
        monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
        if env is None:
            monkeypatch.delenv("MOBAIDE_AGENT_PREFIX", raising=False)
        else:
            monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", env)
        assert windows_launcher._default_prefix(cli) == expected


class TestResolveReleasePython:
    def test_resolves_the_current_release_ignoring_crlf_padding(
        self, tmp_path: Path
    ) -> None:
        python = _make_win_release(tmp_path, "1.2.3")
        (tmp_path / "current_version").write_text("  1.2.3  \r\n", encoding="utf-8")
        assert windows_launcher._resolve_release_python(tmp_path) == python

    @pytest.mark.parametrize(
        ("current_version", "match"),
        [
            (None, "cannot read"),
            ("   \n", "empty"),
            ("9.9.9", "missing release interpreter.*9.9.9"),
        ],
        ids=["missing-file", "empty", "missing-interpreter"],
    )
    def test_unresolvable_current_version_raises(
        self, tmp_path: Path, current_version: str | None, match: str
    ) -> None:
        if current_version is not None:
            (tmp_path / "current_version").write_text(
                current_version, encoding="utf-8"
            )
        with pytest.raises(FileNotFoundError, match=match):
            windows_launcher._resolve_release_python(tmp_path)

    def test_rejects_an_unfinalized_current_release(self, tmp_path: Path) -> None:
        """The interpreter exists but pip never finished: exec-ing it was the
        "bad interpreter" crash, so it must surface as unresolvable."""
        _make_win_release(tmp_path, "0.5.0", complete=False)
        (tmp_path / "current_version").write_text("0.5.0\n", encoding="utf-8")
        with pytest.raises(FileNotFoundError, match="0.5.0.* not finalized"):
            windows_launcher._resolve_release_python(tmp_path)


def _open_err_log(prefix: Path):
    return windows_launcher._open_log(
        prefix,
        windows_launcher._AGENT_ERR_LOG_FILE_NAME,
        windows_launcher._AGENT_ERR_LOG_BACKUP_NAME,
    )


class TestOpenLog:
    def test_creates_the_logs_dir_on_first_boot(self, tmp_path: Path) -> None:
        _open_err_log(tmp_path).close()
        assert (tmp_path / "logs" / "agent.err.log").is_file()

    def test_appends_to_a_small_log_without_rotating(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "agent.err.log").write_bytes(b"prior content\n")

        fh = _open_err_log(tmp_path)
        try:
            fh.write(b"new content\n")
        finally:
            fh.close()

        assert (log_dir / "agent.err.log").read_bytes() == (
            b"prior content\nnew content\n"
        )
        assert not (log_dir / "agent.err.log.1").exists()

    def test_rotates_an_oversized_log(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_path = log_dir / "agent.err.log"
        log_path.write_bytes(b"\x00" * (windows_launcher._AGENT_LOG_ROTATE_BYTES + 1))

        _open_err_log(tmp_path).close()

        assert (log_dir / "agent.err.log.1").exists()
        assert log_path.stat().st_size < windows_launcher._AGENT_LOG_ROTATE_BYTES

    def test_rotation_replaces_a_stale_backup(self, tmp_path: Path) -> None:
        """``replace()`` onto an existing backup raises WinError 183 unless the
        stale backup is unlinked first -- the steady state after one rollover."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "agent.err.log").write_bytes(b"NEW" * (2 * 1024 * 1024))
        (log_dir / "agent.err.log.1").write_bytes(b"OLD")

        _open_err_log(tmp_path).close()

        assert (log_dir / "agent.err.log.1").read_bytes().startswith(b"NEW")


class _FakePopen:
    """Records ``Popen``'s argv and kwargs instead of spawning."""

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        _FakePopen.calls.append((args, dict(kwargs)))


@pytest.fixture
def fake_popen():
    _FakePopen.calls = []
    with mock.patch.object(windows_launcher.subprocess, "Popen", _FakePopen):
        yield _FakePopen


class TestSpawnReleaseLauncher:
    def test_spawns_the_launcher_module_with_its_output_in_closed_log_handles(
        self, tmp_path: Path, fake_popen
    ) -> None:
        """stdout/stderr go to rotated logs, never DEVNULL (structlog writes to
        stdout; DEVNULL hid every pairing log line). The parent's wrappers must
        be closed after Popen, or the open handle blocks the next rotation."""
        windows_launcher._spawn_release_launcher(_FAKE_PYTHON, tmp_path)

        (args, kwargs), = fake_popen.calls
        assert args[0] == [str(_FAKE_PYTHON), "-m", "mobaide.launcher"]
        assert kwargs["stdin"] is subprocess.DEVNULL
        for stream in ("stdout", "stderr"):
            fh = kwargs[stream]
            assert fh is not subprocess.DEVNULL
            assert getattr(fh, "closed", None) is True, stream
        assert (tmp_path / "logs" / "agent.out.log").exists()
        assert (tmp_path / "logs" / "agent.err.log").exists()

    def test_hides_the_console_and_stays_inside_the_job(
        self, tmp_path: Path, fake_popen
    ) -> None:
        windows_launcher._spawn_release_launcher(_FAKE_PYTHON, tmp_path)

        flags = fake_popen.calls[0][1]["creationflags"]
        assert flags & 0x08000000, "CREATE_NO_WINDOW missing"
        assert not flags & 0x01000000, "CREATE_BREAKAWAY_FROM_JOB set"

    @pytest.mark.parametrize("name", ["agent.out.log", "agent.err.log"])
    def test_rotates_each_oversized_log(
        self, tmp_path: Path, fake_popen, name: str
    ) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / name).write_bytes(
            b"\x00" * (windows_launcher._AGENT_LOG_ROTATE_BYTES + 1)
        )

        windows_launcher._spawn_release_launcher(_FAKE_PYTHON, tmp_path)

        assert (log_dir / f"{name}.1").exists()

    @pytest.mark.parametrize(
        "error",
        [OSError(28, "simulated disk-full"), KeyboardInterrupt()],
        ids=["oserror", "keyboard-interrupt"],
    )
    def test_closes_the_out_log_when_opening_the_err_log_fails(
        self, tmp_path: Path, error: BaseException
    ) -> None:
        """A leaked out-log handle pins the file on Windows and breaks the next
        boot's rotation; the guard is ``except BaseException``."""
        opened: list[object] = []
        real_open_log = windows_launcher._open_log

        def fake_open_log(prefix: Path, file_name: str, backup_name: str):
            if not opened:
                opened.append(real_open_log(prefix, file_name, backup_name))
                return opened[0]
            raise error

        with mock.patch.object(windows_launcher, "_open_log", fake_open_log):
            with pytest.raises(type(error)):
                windows_launcher._spawn_release_launcher(_FAKE_PYTHON, tmp_path)

        assert getattr(opened[0], "closed", None) is True


_UNSET = object()


class _StubProc:
    """Stand-in for ``subprocess.Popen``; records method calls."""

    def __init__(
        self,
        returncode: int = 0,
        *,
        poll_returncode: object = _UNSET,
        wait_raises: BaseException | None = None,
        terminate_raises: BaseException | None = None,
    ) -> None:
        self.returncode = returncode
        self.pid = 12345
        self.calls: list[str] = []
        self._poll_returncode = (
            returncode if poll_returncode is _UNSET else poll_returncode
        )
        self._wait_raises = wait_raises
        self._terminate_raises = terminate_raises

    def poll(self) -> object:
        self.calls.append("poll")
        return self._poll_returncode

    def terminate(self) -> None:
        self.calls.append("terminate")
        if self._terminate_raises is not None:
            raise self._terminate_raises

    def wait(self, timeout: float | None = None) -> int:
        self.calls.append("wait")
        if self._wait_raises is not None:
            raise self._wait_raises
        return self.returncode

    def kill(self) -> None:
        self.calls.append("kill")


class TestTerminateSubprocess:
    def test_leaves_an_exited_process_alone(self) -> None:
        proc = _StubProc(returncode=0, poll_returncode=0)
        windows_launcher._terminate_subprocess(proc)
        assert proc.calls == ["poll"]

    def test_terminates_then_waits_without_killing(self) -> None:
        proc = _StubProc(poll_returncode=None)
        windows_launcher._terminate_subprocess(proc)
        assert proc.calls == ["poll", "terminate", "wait"]

    def test_kills_when_the_grace_period_runs_out(self) -> None:
        proc = _StubProc(
            poll_returncode=None,
            wait_raises=subprocess.TimeoutExpired(cmd="x", timeout=25),
        )
        windows_launcher._terminate_subprocess(proc)
        assert proc.calls == ["poll", "terminate", "wait", "kill"]

    def test_a_terminate_racing_the_exit_still_waits(self) -> None:
        proc = _StubProc(
            poll_returncode=None,
            terminate_raises=OSError("handle already closed"),
        )
        windows_launcher._terminate_subprocess(proc)
        assert proc.calls == ["poll", "terminate", "wait"]


def _install_supervise_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    exit_codes: list[int],
    resolve_raises: BaseException | None = None,
    spawn_raises: BaseException | None = None,
) -> tuple[list[Path], list[Path]]:
    """Stub resolve + spawn; each spawned launcher exits with the next code.
    Returns ``(spawn_log, resolve_log)``."""
    spawn_log: list[Path] = []
    resolve_log: list[Path] = []

    def fake_resolve(prefix: Path) -> Path:
        resolve_log.append(prefix)
        if resolve_raises is not None:
            raise resolve_raises
        return prefix / "fake_python.exe"

    def fake_spawn(python_path: Path, prefix: Path) -> _StubProc:
        if spawn_raises is not None:
            raise spawn_raises
        assert exit_codes, "unexpected re-spawn"
        spawn_log.append(python_path)
        return _StubProc(returncode=exit_codes.pop(0))

    monkeypatch.setattr(windows_launcher, "_resolve_release_python", fake_resolve)
    monkeypatch.setattr(windows_launcher, "_spawn_release_launcher", fake_spawn)
    return spawn_log, resolve_log


class TestSupervise:
    def test_respawns_on_exit_75_and_re_resolves_the_release_each_time(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Exit 75 follows a pair or a staged update; the respawn must re-read
        ``current_version`` so it boots the newly staged release."""
        restart = windows_launcher._EXIT_RESTART_REQUESTED
        spawn_log, resolve_log = _install_supervise_stubs(
            monkeypatch, exit_codes=[restart, restart, 0]
        )

        assert windows_launcher._supervise(tmp_path) == 0
        assert len(spawn_log) == 3
        assert len(resolve_log) == 3
        assert "restart requested" in capsys.readouterr().err

    @pytest.mark.parametrize("code", [0, 70, 137])
    def test_any_other_exit_code_is_returned_without_a_respawn(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, code: int
    ) -> None:
        """Task Scheduler never restarts a task on its exit code, so only the
        agent's explicit 75 may loop; everything else propagates verbatim."""
        spawn_log, _ = _install_supervise_stubs(monkeypatch, exit_codes=[code])
        assert windows_launcher._supervise(tmp_path) == code
        assert len(spawn_log) == 1

    @pytest.mark.parametrize(
        ("stubs", "message"),
        [
            (
                {"resolve_raises": FileNotFoundError("empty current_version")},
                "cannot resolve release interpreter",
            ),
            (
                {"spawn_raises": OSError(13, "permission denied")},
                "failed to spawn launcher",
            ),
        ],
        ids=["resolve-error", "spawn-error"],
    )
    def test_infrastructure_errors_exit_70_with_a_diagnostic(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        stubs: dict[str, BaseException],
        message: str,
    ) -> None:
        _install_supervise_stubs(monkeypatch, exit_codes=[], **stubs)
        assert windows_launcher._supervise(tmp_path) == 70
        assert message in capsys.readouterr().err

    def test_keyboard_interrupt_terminates_the_launcher_and_exits_0(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Stop-ScheduledTask: exit 0 so the task is not restarted, and the
        in-flight launcher must be terminated, not orphaned."""
        terminated: list[object] = []

        class _Interrupted(_StubProc):
            def wait(self, timeout: float | None = None) -> int:
                raise KeyboardInterrupt

        monkeypatch.setattr(
            windows_launcher, "_resolve_release_python", lambda p: p / "py.exe"
        )
        monkeypatch.setattr(
            windows_launcher,
            "_spawn_release_launcher",
            lambda py, p: _Interrupted(poll_returncode=None),
        )
        monkeypatch.setattr(
            windows_launcher, "_terminate_subprocess", terminated.append
        )

        assert windows_launcher._supervise(tmp_path) == 0
        assert len(terminated) == 1


def test_redirected_supervisor_stderr_lands_in_its_own_log(tmp_path: Path) -> None:
    """Under pythonw.exe stderr is a no-op writer; the supervisor's own
    diagnostics go to ``launcher.err.log``, never to the child's
    ``agent.err.log``."""
    original_stderr = sys.stderr
    try:
        windows_launcher._redirect_supervisor_stderr(tmp_path)
        sys.stderr.write("supervisor: cannot resolve release interpreter\n")
        sys.stderr.flush()
    finally:
        redirected = sys.stderr
        sys.stderr = original_stderr
        if redirected is not original_stderr:
            redirected.close()

    log_dir = tmp_path / "logs"
    assert "cannot resolve release interpreter" in (
        log_dir / "launcher.err.log"
    ).read_text(encoding="utf-8")
    assert not (log_dir / "agent.err.log").exists()


class TestMain:
    @pytest.fixture
    def order(self, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, object]]:
        """Stub every collaborator of ``main`` (none of them may touch the
        real process or filesystem) and record call order + arguments."""
        calls: list[tuple[str, object]] = []
        monkeypatch.setattr(
            windows_launcher,
            "_install_signal_handlers",
            lambda: calls.append(("signals", None)),
        )
        monkeypatch.setattr(
            windows_launcher,
            "_redirect_supervisor_stderr",
            lambda prefix: calls.append(("redirect", prefix)),
        )
        monkeypatch.setattr(
            windows_launcher,
            "_bind_descendants_to_supervisor",
            lambda: calls.append(("bind", None)),
        )

        def fake_supervise(prefix: Path) -> int:
            calls.append(("supervise", prefix))
            return 42

        monkeypatch.setattr(windows_launcher, "_supervise", fake_supervise)
        return calls

    def test_wires_the_supervisor_in_order_with_one_prefix(
        self, order: list[tuple[str, object]], tmp_path: Path
    ) -> None:
        """Signal handlers before the loop (else a stop is dropped); stderr
        redirect before bind and spawn (so their failures are logged); bind
        before the first spawn (so the launcher is born inside the job)."""
        prefix = tmp_path / "custom"

        assert windows_launcher.main(["--prefix", str(prefix)]) == 42
        assert order == [
            ("signals", None),
            ("redirect", prefix),
            ("bind", None),
            ("supervise", prefix),
        ]

    def test_falls_back_to_the_env_prefix(
        self,
        order: list[tuple[str, object]],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(tmp_path / "env"))

        windows_launcher.main([])

        assert order[-1] == ("supervise", tmp_path / "env")


class TestBindDescendantsToSupervisor:
    """``schtasks /End`` terminates only the supervisor; without the job the
    orphaned agent kept the port and stale relay credentials."""

    def test_failure_is_logged_not_raised(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import ctypes

        def refuse(*_args, **_kwargs):
            raise OSError("no jobs here")

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(ctypes, "WinDLL", refuse, raising=False)
        monkeypatch.setattr(windows_launcher, "_descendant_job", None)

        _REAL_BIND_DESCENDANTS()

        assert windows_launcher._descendant_job is None
        assert "without a kill-on-close job" in capsys.readouterr().err

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Objects")
    def test_terminating_the_supervisor_kills_what_it_started(self) -> None:
        psutil = pytest.importorskip("psutil")
        script = (
            "import subprocess, sys, time\n"
            f"sys.path.insert(0, {str(_REPO_ROOT / 'deploy')!r})\n"
            "import windows_launcher\n"
            "windows_launcher._bind_descendants_to_supervisor()\n"
            "assert windows_launcher._descendant_job\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            "print(child.pid, flush=True)\n"
            "time.sleep(120)\n"
        )
        supervisor = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        child = None
        try:
            assert supervisor.stdout is not None
            child = psutil.Process(int(supervisor.stdout.readline()))
            # TerminateProcess, as Task Scheduler's /End does.
            supervisor.kill()
            supervisor.wait(timeout=10)
            child.wait(timeout=10)
        finally:
            supervisor.kill()
            if child is not None and child.is_running():
                child.kill()


def test_exit_restart_requested_matches_the_agent_updater() -> None:
    """The supervisor is stdlib-only, so it duplicates the constant; drift
    turns every post-pair restart into a silent stop."""
    sys.path.insert(0, str(_REPO_ROOT / "mobaide_agent"))
    try:
        from mobaide.agent.updater import (  # type: ignore[import-not-found]
            EXIT_RESTART_REQUESTED,
        )
    finally:
        sys.path.pop(0)
    assert windows_launcher._EXIT_RESTART_REQUESTED == EXIT_RESTART_REQUESTED


class TestReleaseSelection:
    """Self-heal: when the current release cannot run, roll forward to the
    newest *complete* numeric sibling (``.installed`` sentinel, or the legacy
    ``mobaide-agent.exe`` console script pip writes last)."""

    @pytest.mark.parametrize(
        ("sentinel", "console_script", "complete"),
        [(True, False, True), (False, True, True), (False, False, False)],
        ids=["sentinel", "legacy-console-script", "unfinalized"],
    )
    def test_is_release_complete(
        self, tmp_path: Path, sentinel: bool, console_script: bool, complete: bool
    ) -> None:
        release = tmp_path / "releases" / "0.3.0"
        scripts = release / "venv" / "Scripts"
        scripts.mkdir(parents=True)
        (scripts / "python.exe").write_text("stub", encoding="utf-8")
        if sentinel:
            (release / ".installed").write_text("0.3.0", encoding="utf-8")
        if console_script:
            (scripts / "mobaide-agent.exe").write_bytes(b"MZ")
        assert windows_launcher._is_release_complete(release) is complete

    def test_picks_the_newest_numeric_release(self, tmp_path: Path) -> None:
        for v in ("0.2.0", "0.10.0", "0.9.0"):
            _make_win_release(tmp_path, v)
        assert windows_launcher._find_recovery_version(tmp_path) == "0.10.0"

    def test_skips_scratch_venvless_and_incomplete_releases(
        self, tmp_path: Path
    ) -> None:
        _make_win_release(tmp_path, ".staging-9.9.9-1")
        (tmp_path / "releases" / "0.3.0").mkdir(parents=True)
        _make_win_release(tmp_path, "0.10.0", complete=False)
        _make_win_release(tmp_path, "0.2.0")
        assert windows_launcher._find_recovery_version(tmp_path) == "0.2.0"

    def test_none_without_a_runnable_release(self, tmp_path: Path) -> None:
        assert windows_launcher._find_recovery_version(tmp_path) is None
        _make_win_release(tmp_path, "0.2.0", complete=False)
        assert windows_launcher._find_recovery_version(tmp_path) is None

    def test_supervise_recovers_then_spawns(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _make_win_release(tmp_path, "0.2.13")
        (tmp_path / "current_version").write_text("0.2.99\n", encoding="utf-8")
        spawn_log: list[Path] = []

        def fake_spawn(python_path: Path, prefix: Path) -> _StubProc:
            spawn_log.append(python_path)
            return _StubProc(returncode=0)

        monkeypatch.setattr(windows_launcher, "_spawn_release_launcher", fake_spawn)

        assert windows_launcher._supervise(tmp_path) == 0
        assert (tmp_path / "current_version").read_text().strip() == "0.2.13"
        assert len(spawn_log) == 1
        assert "0.2.13" in str(spawn_log[0])

    def test_supervise_exits_70_without_a_recovery_target(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        (tmp_path / "releases").mkdir()
        (tmp_path / "current_version").write_text("0.2.0\n", encoding="utf-8")

        def fake_spawn(python_path: Path, prefix: Path) -> _StubProc:
            raise AssertionError("spawn must not run")

        monkeypatch.setattr(windows_launcher, "_spawn_release_launcher", fake_spawn)

        assert windows_launcher._supervise(tmp_path) == 70

    def test_supervise_recovery_is_bounded(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Even with a fresh distinct target each time, recovery stops after
        ``_MAX_RECOVERY_ATTEMPTS`` instead of looping forever."""
        resolve_calls: list[Path] = []
        counter = iter(range(1, 1000))

        def always_missing(prefix: Path) -> Path:
            resolve_calls.append(prefix)
            raise FileNotFoundError("missing interpreter")

        monkeypatch.setattr(
            windows_launcher, "_resolve_release_python", always_missing
        )
        monkeypatch.setattr(
            windows_launcher,
            "_find_recovery_version",
            lambda prefix: f"9.9.{next(counter)}",
        )
        monkeypatch.setattr(
            windows_launcher, "_write_current_version", lambda p, v: None
        )

        assert windows_launcher._supervise(tmp_path) == 70
        assert len(resolve_calls) <= windows_launcher._MAX_RECOVERY_ATTEMPTS + 1
