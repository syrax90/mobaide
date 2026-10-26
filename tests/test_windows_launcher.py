"""Tests for ``deploy/windows_launcher.py``.

The module hosts the per-user Scheduled Task supervisor that replaces
the previous LocalSystem SCM service (``deploy/windows_service.py``).
The supervisor lives at ``<prefix>/svc/mobaide_launcher.py`` outside
any release directory so the registered task ``Execute`` path stays
stable across upgrades; it is stdlib-only by design (no pywin32, no
``mobaide`` imports) so any host Python 3.x can run these tests
regardless of platform.

Lives under ``tests/`` (the repo-root test dir, NOT
``mobaide_agent/tests/``) because the supervisor is deploy
infrastructure -- nothing inside the ``mobaide`` package imports it.
``install.ps1`` fetches the file from get.mobaide.com (gh-pages)
alongside ``install.ps1`` itself; ``tests/test_publish_gh_pages_workflow.py``
pins that co-versioning contract. This module pins the supervisor's
own behavioural surface.

Mirrors the parts of the deleted ``tests/test_windows_service.py``
that survive the rewrite: ``_default_prefix`` precedence,
``_resolve_release_python`` happy + error paths, ``_open_log`` rotation
+ logs-dir creation, ``_spawn_release_launcher`` stdio redirection +
parent-handle close, ``_terminate_subprocess`` grace-period contract,
and the new supervisor-loop coverage that subsumes the deleted
``SvcDoRun`` tests: exit-75 respawn, exit-code propagation, current_version
re-resolution between iterations, KeyboardInterrupt teardown, and the
resolve/spawn error -> exit 70 paths.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "deploy"))

import windows_launcher  # noqa: E402

_REAL_BIND_DESCENDANTS = windows_launcher._bind_descendants_to_supervisor


@pytest.fixture(autouse=True)
def _no_kill_on_close_job(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep ``main`` from putting the pytest process in a kill-on-close job:
    joining one cannot be undone, and pytest's children would die with it."""
    monkeypatch.setattr(
        windows_launcher, "_bind_descendants_to_supervisor", lambda: None
    )


class TestDefaultPrefix:
    """Precedence: ``--prefix`` argv > ``MOBAIDE_AGENT_PREFIX`` env >
    ``%ProgramFiles%\\MobaideAgent`` default.

    The Scheduled Task action passes ``--prefix <prefix>`` explicitly
    (the task's environment does NOT get ``MOBAIDE_AGENT_PREFIX`` set
    automatically), so argv wins. The env var still works for
    interactive smoke-tests where an operator launches the supervisor
    by hand.
    """

    def test_cli_prefix_takes_precedence_over_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", r"D:\from\env")
        assert windows_launcher._default_prefix(r"D:\from\argv") == Path(
            r"D:\from\argv"
        )

    def test_env_used_when_cli_prefix_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``MOBAIDE_AGENT_PREFIX`` overrides the ProgramFiles default
        when no ``--prefix`` is given on the command line.
        """
        monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", r"D:\custom\mobaide")
        assert windows_launcher._default_prefix(None) == Path(
            r"D:\custom\mobaide"
        )

    def test_env_used_when_cli_prefix_empty_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty-string ``cli_prefix`` (defensive, in case argparse hands
        in an empty value) falls through to the env-var branch rather
        than being treated as a real prefix. ``Path("")`` is the cwd —
        a silently wrong supervisor home that would resolve
        ``current_version`` from the install user's working directory.
        """
        monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", r"D:\custom\mobaide")
        assert windows_launcher._default_prefix("") == Path(
            r"D:\custom\mobaide"
        )

    def test_program_files_default_when_env_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without ``--prefix`` and without ``MOBAIDE_AGENT_PREFIX``,
        fall back to ``%ProgramFiles%\\MobaideAgent``.

        Mirrors the resolution in install.ps1's ``$Script:Prefix``
        default and ``mobaide.launcher._default_prefix``. Drift between
        the three silently breaks the supervisor — it would read the
        wrong ``current_version`` file.
        """
        monkeypatch.delenv("MOBAIDE_AGENT_PREFIX", raising=False)
        monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
        assert (
            windows_launcher._default_prefix(None)
            == Path(r"C:\Program Files") / "MobaideAgent"
        )

    def test_program_files_default_when_program_files_env_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When ``%ProgramFiles%`` itself is unset (e.g. running the
        supervisor under a stripped environment), fall back to the
        hardcoded ``C:\\Program Files`` constant baked into
        ``_DEFAULT_PROGRAM_FILES``. Documented defaults that keep the
        supervisor functional in degraded environments.
        """
        monkeypatch.delenv("MOBAIDE_AGENT_PREFIX", raising=False)
        monkeypatch.delenv("ProgramFiles", raising=False)
        assert (
            windows_launcher._default_prefix(None)
            == Path(r"C:\Program Files") / "MobaideAgent"
        )


class TestResolveReleasePython:
    """Read ``current_version`` and resolve the active release's
    ``python.exe``. Three error paths plus the happy path; each error
    path raises ``FileNotFoundError`` with an operator-readable message
    that ``_supervise`` writes to ``stderr`` (which is itself redirected
    to the rotated ``agent.err.log`` so operators can recover the
    string after the fact).
    """

    def test_returns_release_interpreter_on_happy_path(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "current_version").write_text("1.2.3\n", encoding="utf-8")
        release_scripts = tmp_path / "releases" / "1.2.3" / "venv" / "Scripts"
        release_scripts.mkdir(parents=True)
        (release_scripts / "python.exe").write_bytes(b"\x4d\x5a")  # MZ
        # A release current_version points at is, by definition, finalized;
        # the post-fix gate requires the completion sentinel (or console
        # script) before resolving the interpreter.
        (tmp_path / "releases" / "1.2.3" / ".installed").write_text(
            "1.2.3", encoding="utf-8"
        )
        resolved = windows_launcher._resolve_release_python(tmp_path)
        assert resolved == release_scripts / "python.exe"

    def test_strips_whitespace_from_current_version(
        self, tmp_path: Path
    ) -> None:
        """``current_version`` may end with ``\\r\\n`` on Windows-written
        files. Strip both ends so the release directory resolves
        correctly. Without this, ``releases/1.2.3\\r/venv/...`` would
        be looked up and the happy path would silently degrade to the
        ``missing release interpreter`` error branch.
        """
        (tmp_path / "current_version").write_text(
            "  1.2.3  \r\n", encoding="utf-8"
        )
        release_scripts = tmp_path / "releases" / "1.2.3" / "venv" / "Scripts"
        release_scripts.mkdir(parents=True)
        (release_scripts / "python.exe").write_bytes(b"\x4d\x5a")
        # Finalized release: the post-fix completeness gate needs the
        # sentinel before _resolve_release_python returns the interpreter.
        (tmp_path / "releases" / "1.2.3" / ".installed").write_text(
            "1.2.3", encoding="utf-8"
        )
        resolved = windows_launcher._resolve_release_python(tmp_path)
        assert resolved == release_scripts / "python.exe"

    def test_raises_on_missing_current_version(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="cannot read"):
            windows_launcher._resolve_release_python(tmp_path)

    def test_raises_on_empty_current_version(self, tmp_path: Path) -> None:
        """A whitespace-only ``current_version`` is treated as empty.

        The post-install gap (file written but flush incomplete on a
        sudden reboot) and the failed-staging gap (updater rewrote
        ``current_version`` to an empty file) both surface here as
        ``empty`` rather than the more confusing ``missing release
        interpreter <empty>/venv/...``.
        """
        (tmp_path / "current_version").write_text("   \n", encoding="utf-8")
        with pytest.raises(FileNotFoundError, match="empty"):
            windows_launcher._resolve_release_python(tmp_path)

    def test_raises_on_missing_interpreter(self, tmp_path: Path) -> None:
        """A ``current_version`` pointing at a non-existent release
        surfaces as ``missing release interpreter`` with the resolved
        path AND the requested version embedded in the message so the
        operator can correlate it with the staged-update state.
        """
        (tmp_path / "current_version").write_text("9.9.9", encoding="utf-8")
        with pytest.raises(
            FileNotFoundError, match="missing release interpreter"
        ) as excinfo:
            windows_launcher._resolve_release_python(tmp_path)
        # The error message must mention the version string so the
        # operator can correlate it with the staged-update state.
        assert "9.9.9" in str(excinfo.value)


class TestOpenLog:
    """``_open_log`` opens the per-stream log in append mode with
    boot-time rotation at 5 MB (keep one backup). Used by
    ``_spawn_release_launcher`` for both stdout and stderr — symmetric
    coverage on the rotation branch matters because a regression on
    one stream silently kills the other operator-visible diagnostic.
    """

    def test_creates_logs_dir_when_missing(self, tmp_path: Path) -> None:
        """The MobaideAgent install flow only guarantees ``<prefix>``
        and ``<prefix>/releases/<v>``; the ``logs/`` directory is
        allocated lazily on first supervisor boot. Without
        ``mkdir(parents=True, exist_ok=True)`` here, the first boot
        would crash with ``FileNotFoundError`` opening the log, which
        the operator would never see because stderr is going to that
        very missing file.
        """
        assert not (tmp_path / "logs").exists()

        fh = windows_launcher._open_log(
            tmp_path,
            windows_launcher._AGENT_ERR_LOG_FILE_NAME,
            windows_launcher._AGENT_ERR_LOG_BACKUP_NAME,
        )
        try:
            assert (tmp_path / "logs").is_dir()
            assert (tmp_path / "logs" / "agent.err.log").exists()
        finally:
            fh.close()

    def test_appends_to_existing_log(self, tmp_path: Path) -> None:
        """An under-5MB existing log must be opened for append, not
        truncated. Truncation would lose the recent crash-loop history
        that the next-boot ``Wait-AgentHealthy`` tail relies on.
        """
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_path = log_dir / "agent.err.log"
        log_path.write_bytes(b"prior content\n")

        fh = windows_launcher._open_log(
            tmp_path,
            windows_launcher._AGENT_ERR_LOG_FILE_NAME,
            windows_launcher._AGENT_ERR_LOG_BACKUP_NAME,
        )
        try:
            fh.write(b"new content\n")
        finally:
            fh.close()

        assert log_path.read_bytes() == b"prior content\nnew content\n"
        assert not (log_dir / "agent.err.log.1").exists(), (
            "small existing log must NOT be rotated; backup creation "
            "indicates the size threshold check is broken"
        )

    def test_rotates_at_5mb_threshold(self, tmp_path: Path) -> None:
        """A pre-existing log of >= 5 MB must be rotated to the .1
        backup so a crash-loop with verbose tracebacks can't fill the
        disk.
        """
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_path = log_dir / "agent.err.log"
        # Write just over the rotation threshold.
        log_path.write_bytes(b"\x00" * (windows_launcher._AGENT_LOG_ROTATE_BYTES + 1))

        fh = windows_launcher._open_log(
            tmp_path,
            windows_launcher._AGENT_ERR_LOG_FILE_NAME,
            windows_launcher._AGENT_ERR_LOG_BACKUP_NAME,
        )
        try:
            pass
        finally:
            fh.close()

        assert (log_dir / "agent.err.log.1").exists(), (
            "oversized log must be rotated to .1 backup"
        )
        # The live log must exist (recreated by the append-mode open)
        # and be smaller than the pre-rotation size.
        assert log_path.exists()
        assert log_path.stat().st_size < windows_launcher._AGENT_LOG_ROTATE_BYTES

    def test_overwrites_stale_backup_on_rotation(self, tmp_path: Path) -> None:
        """When ``agent.err.log`` is oversized AND ``agent.err.log.1``
        already exists, rotation must unlink the stale backup first,
        then replace the live log onto the backup name.

        Without the unlink, ``log_path.replace(backup)`` on Windows
        raises ``WinError 183: file already exists`` and the rotation
        aborts. The stale-backup case is the steady state on any host
        that has rolled over once.
        """
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_path = log_dir / "agent.err.log"
        backup_path = log_dir / "agent.err.log.1"
        log_path.write_bytes(b"NEW" * (2 * 1024 * 1024))
        backup_path.write_bytes(b"OLD")
        assert log_path.stat().st_size >= windows_launcher._AGENT_LOG_ROTATE_BYTES

        fh = windows_launcher._open_log(
            tmp_path,
            windows_launcher._AGENT_ERR_LOG_FILE_NAME,
            windows_launcher._AGENT_ERR_LOG_BACKUP_NAME,
        )
        try:
            pass
        finally:
            fh.close()

        assert backup_path.exists()
        backup_content = backup_path.read_bytes()
        assert backup_content.startswith(b"NEW"), (
            "rotation must overwrite the stale backup with the just-"
            "rotated live log content -- found bytes "
            f"{backup_content[:8]!r} (looks like the original 'OLD' "
            "backup, which means the rotation was aborted by the "
            "FileExistsError that ``replace()`` raises on Windows when "
            "the destination already exists)"
        )

    def test_returns_binary_append_mode_handle(self, tmp_path: Path) -> None:
        """The handle must be in binary append mode (``ab``).

        ``Popen`` calls ``fileno()`` on the handle and dups the OS file
        descriptor into the child; text-mode wrappers would impose
        Python-side newline translation on writes coming from the
        child's C-level ``fwrite``, garbling structlog's JSON output.
        Append mode (vs ``wb``) is the no-truncate guarantee that the
        ``test_appends_to_existing_log`` test pins from the other
        direction.
        """
        fh = windows_launcher._open_log(
            tmp_path,
            windows_launcher._AGENT_OUT_LOG_FILE_NAME,
            windows_launcher._AGENT_OUT_LOG_BACKUP_NAME,
        )
        try:
            # ``mode`` is the documented attribute on file objects from
            # ``open()``; it contains ``b`` for binary and ``a`` for
            # append. The order of flag characters is implementation-
            # defined so check membership rather than equality.
            mode = getattr(fh, "mode", "")
            assert "a" in mode, f"expected append mode, got {mode!r}"
            assert "b" in mode, f"expected binary mode, got {mode!r}"
        finally:
            fh.close()


class _FakePopen:
    """Captures the constructor kwargs without actually spawning a child.

    The supervisor passes file-object kwargs (``stdout`` / ``stderr``)
    that ``_FakePopen`` stores so tests can inspect both the redirect
    target and the close-state after the ``Popen`` call completes (the
    supervisor must release its parent-side wrapper once the OS-level
    handle has been duplicated into the child).
    """

    captured_kwargs: list[dict[str, object]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        _FakePopen.captured_kwargs.append(dict(kwargs))


@pytest.fixture(autouse=True)
def _reset_fake_popen() -> None:
    """Tests share the class-level ``captured_kwargs`` list; reset it
    between tests so cross-test contamination cannot mask a regression
    (e.g. a test asserting on ``captured_kwargs[-1]`` accidentally
    seeing a prior test's value when the current test's spawn was
    skipped).
    """
    _FakePopen.captured_kwargs = []


class TestSpawnReleaseLauncher:
    """``_spawn_release_launcher`` redirects stdout + stderr to rotated
    log files, NOT DEVNULL. structlog's queue listener writes to
    ``sys.stdout``; routing stdout to DEVNULL silently discarded every
    ``pairing_monitor.*`` / ``relay_client.*`` / ``http.request.*`` log
    line. That gap was the systemic blocker on every prior debugging
    attempt of the empty-.env pairing failure mode and is the reason
    the supervisor opens TWO file handles (stdout + stderr) rather
    than redirecting both streams to the same file or to DEVNULL.
    """

    def test_invokes_release_python_with_mobaide_launcher_module(
        self, tmp_path: Path
    ) -> None:
        """The supervisor must spawn ``<release_python> -m mobaide.launcher``.

        The ``-m`` form runs the package's ``__main__`` entry point;
        the release-pinned launcher is responsible for the crash-loop
        rollback + Job-Object teardown. Hardcoding a different module
        name silently breaks the chain: the supervisor would Popen
        successfully, the wrong module would print "no module" to the
        captured stderr, exit non-zero, and Task Scheduler would
        restart the supervisor in an inescapable crash loop.
        """
        captured_args: list[tuple[object, ...]] = []

        class _Capture:
            def __init__(self, *args: object, **kwargs: object) -> None:
                captured_args.append(args)

        with mock.patch.object(windows_launcher.subprocess, "Popen", _Capture):
            windows_launcher._spawn_release_launcher(
                Path(r"C:\fake\python.exe"), tmp_path
            )

        assert captured_args, "Popen was not invoked"
        argv = captured_args[0][0]
        assert isinstance(argv, list)
        assert argv == [r"C:\fake\python.exe", "-m", "mobaide.launcher"], (
            "Popen argv must be [<release_python>, '-m', 'mobaide.launcher']; "
            f"got {argv!r}"
        )

    def test_redirects_stdout_to_agent_out_log(self, tmp_path: Path) -> None:
        """``_spawn_release_launcher`` must redirect stdout to
        ``<prefix>/logs/agent.out.log`` (not DEVNULL).

        structlog's queue listener writes its records to
        ``sys.stdout``; routing stdout to DEVNULL would re-introduce
        the empty-.env pairing-failure debugging blocker.
        """
        with mock.patch.object(
            windows_launcher.subprocess, "Popen", _FakePopen
        ):
            windows_launcher._spawn_release_launcher(
                Path(r"C:\fake\python.exe"), tmp_path
            )

        kwargs = _FakePopen.captured_kwargs[-1]
        stdout = kwargs.get("stdout")
        assert stdout is not subprocess.DEVNULL, (
            "stdout must NOT be DEVNULL -- every structlog line going to "
            "/dev/null was the systemic blocker on every prior debugging "
            "attempt of the empty-.env pairing failure mode"
        )
        assert (tmp_path / "logs" / "agent.out.log").exists()

    def test_redirects_stderr_to_agent_err_log(self, tmp_path: Path) -> None:
        """``_spawn_release_launcher`` must redirect stderr to
        ``<prefix>/logs/agent.err.log`` (not DEVNULL).

        Before the redirect, every Python traceback from the launcher
        or the agent (config error, missing dep, port conflict) was
        discarded -- operators only saw the eventvwr line, never the
        real error. The fix routes stderr to a rotated log file that
        install.ps1's ``Wait-AgentHealthy`` can point at.
        """
        with mock.patch.object(
            windows_launcher.subprocess, "Popen", _FakePopen
        ):
            windows_launcher._spawn_release_launcher(
                Path(r"C:\fake\python.exe"), tmp_path
            )

        kwargs = _FakePopen.captured_kwargs[-1]
        stderr = kwargs.get("stderr")
        assert stderr is not subprocess.DEVNULL, (
            "stderr must NOT be DEVNULL -- a silent launcher crash is the "
            "exact diagnostic gap install.ps1's Wait-AgentHealthy was "
            "added to expose"
        )
        assert (tmp_path / "logs" / "agent.err.log").exists()

    def test_uses_devnull_for_stdin(self, tmp_path: Path) -> None:
        """``stdin`` must be DEVNULL -- the agent is a daemon and must
        not inherit the supervisor's stdin (which is detached under
        Task Scheduler anyway, but explicit DEVNULL is defence-in-depth
        for the interactive smoke-test case where an operator runs the
        supervisor by hand from a console).
        """
        with mock.patch.object(
            windows_launcher.subprocess, "Popen", _FakePopen
        ):
            windows_launcher._spawn_release_launcher(
                Path(r"C:\fake\python.exe"), tmp_path
            )

        kwargs = _FakePopen.captured_kwargs[-1]
        assert kwargs.get("stdin") is subprocess.DEVNULL, (
            "stdin must be DEVNULL so the agent inherits nothing the "
            f"supervisor itself was reading; got {kwargs.get('stdin')!r}"
        )

    def test_closes_parent_stdout_handle_after_popen(
        self, tmp_path: Path
    ) -> None:
        """The parent's stdout wrapper opened for ``agent.out.log``
        must be closed once ``subprocess.Popen`` has dup'd the OS handle
        into the child.

        Leaving the wrapper open pins the file on Windows: the next
        boot's rotation would see oversized ``agent.out.log``, call
        ``log_path.replace(backup)``, and fail with
        ``WinError 32: file is being used by another process``. The
        fix lives in ``_spawn_release_launcher``'s
        ``finally: out_log.close()``.
        """
        with mock.patch.object(
            windows_launcher.subprocess, "Popen", _FakePopen
        ):
            windows_launcher._spawn_release_launcher(
                Path(r"C:\fake\python.exe"), tmp_path
            )

        kwargs = _FakePopen.captured_kwargs[-1]
        fh = kwargs.get("stdout")
        assert hasattr(fh, "closed"), (
            f"expected stdout to be a file-like object, got {type(fh).__name__}"
        )
        assert getattr(fh, "closed", None) is True, (
            "parent's stdout wrapper for agent.out.log must be closed "
            "after Popen returns -- leaving it open pins the out log "
            "on Windows and blocks the next boot's rotation rename"
        )

    def test_closes_parent_stderr_handle_after_popen(
        self, tmp_path: Path
    ) -> None:
        """Symmetric companion to the stdout-close test: the parent's
        stderr wrapper opened for ``agent.err.log`` must also be closed
        once ``Popen`` returns.
        """
        with mock.patch.object(
            windows_launcher.subprocess, "Popen", _FakePopen
        ):
            windows_launcher._spawn_release_launcher(
                Path(r"C:\fake\python.exe"), tmp_path
            )

        kwargs = _FakePopen.captured_kwargs[-1]
        fh = kwargs.get("stderr")
        assert hasattr(fh, "closed"), (
            f"expected stderr to be a file-like object, got {type(fh).__name__}"
        )
        assert getattr(fh, "closed", None) is True, (
            "parent's stderr wrapper must be closed after Popen returns "
            "-- subprocess has already dup'd the OS fd into the child"
        )

    def test_rotates_oversized_stdout_log(self, tmp_path: Path) -> None:
        """Pre-existing ``agent.out.log`` larger than 5 MB must be
        rotated to ``agent.out.log.1`` so structlog's per-request lines
        can't fill the disk on a long-running agent.

        Distinct from the stderr-rotation pin because the stdout
        redirect was added later and its rotation symmetry has burned
        twice before: a regression that reverts to a single shared log
        file or drops the second ``_open_log`` call breaks this without
        breaking the stderr-rotation test.
        """
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        out_log_path = log_dir / "agent.out.log"
        out_log_path.write_bytes(
            b"\x00" * (windows_launcher._AGENT_LOG_ROTATE_BYTES + 1)
        )

        with mock.patch.object(
            windows_launcher.subprocess, "Popen", _FakePopen
        ):
            windows_launcher._spawn_release_launcher(
                Path(r"C:\fake\python.exe"), tmp_path
            )

        assert (log_dir / "agent.out.log.1").exists()
        assert out_log_path.stat().st_size < windows_launcher._AGENT_LOG_ROTATE_BYTES

    def test_rotates_oversized_stderr_log(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        err_log_path = log_dir / "agent.err.log"
        err_log_path.write_bytes(
            b"\x00" * (windows_launcher._AGENT_LOG_ROTATE_BYTES + 1)
        )

        with mock.patch.object(
            windows_launcher.subprocess, "Popen", _FakePopen
        ):
            windows_launcher._spawn_release_launcher(
                Path(r"C:\fake\python.exe"), tmp_path
            )

        assert (log_dir / "agent.err.log.1").exists()
        assert err_log_path.stat().st_size < windows_launcher._AGENT_LOG_ROTATE_BYTES

    def test_passes_create_no_window_creationflag(self, tmp_path: Path) -> None:
        """``Popen`` must be called with ``creationflags=CREATE_NO_WINDOW``.

        The Scheduled Task action invokes the supervisor under
        ``pythonw.exe`` (GUI subsystem, no console allocated). Without
        ``CREATE_NO_WINDOW`` on the child ``Popen``, the spawned
        release-pinned launcher's ``python.exe`` is a console-subsystem
        binary -- Windows would allocate a fresh ``conhost.exe`` window
        for it, defeating the whole hidden-process design. The literal
        ``0x08000000`` is the documented ``CREATE_NO_WINDOW`` value
        (matches ``subprocess.CREATE_NO_WINDOW``); duplicated in the
        supervisor module because the stdlib-only contract forbids
        importing ``mobaide`` or its constants.
        """
        with mock.patch.object(
            windows_launcher.subprocess, "Popen", _FakePopen
        ):
            windows_launcher._spawn_release_launcher(
                Path(r"C:\fake\python.exe"), tmp_path
            )

        kwargs = _FakePopen.captured_kwargs[-1]
        flags = kwargs.get("creationflags")
        assert flags is not None, (
            "Popen must receive a ``creationflags`` kwarg so the spawned "
            "launcher does not allocate a console window under "
            "pythonw.exe; got kwargs without the key"
        )
        assert flags & 0x08000000, (
            "creationflags must include CREATE_NO_WINDOW (0x08000000) so "
            "the release-pinned ``python.exe`` child does not pop a "
            "console window when the supervisor runs under pythonw.exe. "
            f"Got creationflags={flags!r}"
        )

    def test_create_no_window_does_not_set_breakaway_from_job(
        self, tmp_path: Path
    ) -> None:
        """``creationflags`` must not include ``CREATE_BREAKAWAY_FROM_JOB``.

        The release-pinned launcher (``mobaide.launcher``) wraps the
        agent in a Windows Job Object with
        ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``; setting
        ``CREATE_BREAKAWAY_FROM_JOB`` (``0x01000000``) on the supervisor's
        spawn of the launcher would let a future descendant escape the
        job, defeating the clean-teardown guarantee. A regression that
        OR's in unrelated flags must surface here before it ships.
        """
        with mock.patch.object(
            windows_launcher.subprocess, "Popen", _FakePopen
        ):
            windows_launcher._spawn_release_launcher(
                Path(r"C:\fake\python.exe"), tmp_path
            )

        kwargs = _FakePopen.captured_kwargs[-1]
        flags = kwargs.get("creationflags", 0)
        assert not (flags & 0x01000000), (
            "creationflags must NOT include CREATE_BREAKAWAY_FROM_JOB "
            "(0x01000000) -- the launcher's KILL_ON_JOB_CLOSE teardown "
            "relies on the supervisor->launcher->agent process tree "
            f"staying inside the job. Got creationflags={flags!r}"
        )


class TestSpawnReleaseLauncherErrorPath:
    """``_spawn_release_launcher`` opens ``agent.out.log`` before
    ``agent.err.log``. If the err_log open fails (disk full, permission
    denied, mid-supervise rotation race), the already-open out_log
    wrapper MUST be closed before the exception propagates -- otherwise
    the supervisor leaks a file handle every time a transient err_log
    open fails, which pins the out_log on Windows and prevents the next
    boot's rotation rename. The fix lives in
    ``_spawn_release_launcher``'s ``try / except BaseException:
    out_log.close(); raise`` guard.
    """

    def test_closes_out_log_when_err_log_open_raises(
        self, tmp_path: Path
    ) -> None:
        # Stub ``_open_log`` to succeed on the first call (out_log) and
        # raise ``OSError`` on the second (err_log). The first call's
        # handle is captured so the test can assert .close() ran before
        # the exception escaped ``_spawn_release_launcher``.
        opened_handles: list[object] = []
        call_count = [0]

        real_open_log = windows_launcher._open_log

        def fake_open_log(prefix: Path, file_name: str, backup_name: str):
            call_count[0] += 1
            if call_count[0] == 1:
                fh = real_open_log(prefix, file_name, backup_name)
                opened_handles.append(fh)
                return fh
            raise OSError(28, "simulated disk-full on err_log open")

        with mock.patch.object(windows_launcher, "_open_log", fake_open_log):
            # The error must propagate as an OSError -- callers (the
            # _supervise loop) catch OSError and return exit 70 with a
            # stderr diagnostic. Swallowing the error here would mean
            # the supervisor silently Popen-ed nothing and Task
            # Scheduler would see a clean exit on what is actually a
            # configuration failure.
            with pytest.raises(OSError, match="simulated disk-full"):
                windows_launcher._spawn_release_launcher(
                    Path(r"C:\fake\python.exe"), tmp_path
                )

        # Exactly one handle was opened (out_log). err_log never got a
        # successful open, so the test fixture only holds the out_log
        # wrapper.
        assert len(opened_handles) == 1, (
            f"expected exactly one _open_log call to succeed (out_log) "
            f"before the err_log call raises; got "
            f"{len(opened_handles)} handles"
        )
        out_log = opened_handles[0]
        # The smoking-gun assertion: the out_log wrapper must be closed
        # by the time the exception propagates. Without the
        # ``try / except BaseException: out_log.close(); raise`` guard
        # around the err_log open, this would be False and the
        # supervisor would leak a file handle every time the err_log
        # open transient-fails.
        assert getattr(out_log, "closed", None) is True, (
            "out_log wrapper must be closed before the err_log "
            "OSError propagates -- the leak guard lives at "
            "_spawn_release_launcher's ``try / except BaseException: "
            "out_log.close(); raise`` block. Without it, the supervisor "
            "would pin the out_log on Windows and the next boot's "
            "rotation rename would fail with WinError 32"
        )

    def test_closes_out_log_when_err_log_open_raises_keyboard_interrupt(
        self, tmp_path: Path
    ) -> None:
        # The leak guard uses ``except BaseException`` (not just
        # ``except Exception``). That distinction matters: a
        # KeyboardInterrupt arriving in the narrow window between the
        # two _open_log calls (e.g. the operator hit Ctrl+C during a
        # supervisor smoke-test) must still close out_log on the way
        # out. ``except Exception`` would miss
        # KeyboardInterrupt / SystemExit and re-leak.
        opened_handles: list[object] = []
        call_count = [0]

        real_open_log = windows_launcher._open_log

        def fake_open_log(prefix: Path, file_name: str, backup_name: str):
            call_count[0] += 1
            if call_count[0] == 1:
                fh = real_open_log(prefix, file_name, backup_name)
                opened_handles.append(fh)
                return fh
            raise KeyboardInterrupt

        with mock.patch.object(windows_launcher, "_open_log", fake_open_log):
            with pytest.raises(KeyboardInterrupt):
                windows_launcher._spawn_release_launcher(
                    Path(r"C:\fake\python.exe"), tmp_path
                )

        assert len(opened_handles) == 1
        out_log = opened_handles[0]
        assert getattr(out_log, "closed", None) is True, (
            "out_log must be closed even when the err_log open is "
            "interrupted by KeyboardInterrupt -- the guard uses "
            "``except BaseException`` precisely so a Ctrl+C during the "
            "supervisor smoke-test cannot leak the out_log handle"
        )


_UNSET = object()


class _StubProc:
    """Minimal stand-in for ``subprocess.Popen`` used by supervisor tests.

    ``poll`` returns ``returncode`` so ``wait()`` doesn't block;
    ``returncode`` mirrors what the launcher subprocess would have
    exited with. Each call to a method is recorded in ``calls`` for
    assertions.

    ``poll_returncode`` defaults to a sentinel (``_UNSET``) that maps
    onto ``returncode`` -- the common "process has already exited"
    shape used by ``_supervise``'s test stubs. Tests exercising the
    "process still running" branch pass ``poll_returncode=None``
    explicitly so ``poll()`` returns the literal None.
    """

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
        self._poll_returncode: object = (
            returncode if poll_returncode is _UNSET else poll_returncode
        )
        self._wait_raises = wait_raises
        self._terminate_raises = terminate_raises

    def poll(self) -> int | None:
        self.calls.append("poll")
        if isinstance(self._poll_returncode, BaseException):
            raise self._poll_returncode
        return self._poll_returncode  # type: ignore[return-value]

    def terminate(self) -> None:
        self.calls.append("terminate")
        if self._terminate_raises is not None:
            raise self._terminate_raises

    def wait(self, timeout: float | None = None) -> int:
        self.calls.append(f"wait(timeout={timeout})")
        if self._wait_raises is not None:
            raise self._wait_raises
        return self.returncode

    def kill(self) -> None:
        self.calls.append("kill")


class TestTerminateSubprocess:
    """``_terminate_subprocess`` is the supervisor's graceful-stop
    primitive. Called from the ``KeyboardInterrupt`` branch of
    ``_supervise`` when ``Stop-ScheduledTask`` delivers a polite stop;
    25-second grace via ``terminate`` then a hard ``kill`` fallback so
    a wedged agent cannot pin the supervisor indefinitely.
    """

    def test_no_op_when_proc_is_none(self) -> None:
        """Passing ``None`` must be a no-op. The supervisor sets
        ``proc = None`` after ``wait()`` returns; calling
        ``_terminate_subprocess(None)`` from the
        ``KeyboardInterrupt`` branch must not raise.
        """
        windows_launcher._terminate_subprocess(None)

    def test_no_op_when_proc_already_exited(self) -> None:
        """If ``poll()`` returns a non-None value, the process has
        already exited and there is nothing to terminate. The function
        must NOT call ``terminate`` or ``kill`` in that case -- doing
        so on Windows would log a meaningless ``OSError`` (handle
        already closed) into the captured stderr.
        """
        proc = _StubProc(returncode=0, poll_returncode=0)
        windows_launcher._terminate_subprocess(proc)
        assert proc.calls == ["poll"]

    def test_calls_terminate_then_wait(self) -> None:
        """Running process: ``poll()`` returns ``None``, then
        ``terminate()`` is invoked, then ``wait(timeout=25)`` collects
        the exit.
        """
        proc = _StubProc(returncode=0, poll_returncode=None)
        windows_launcher._terminate_subprocess(proc)
        assert proc.calls[0] == "poll"
        assert "terminate" in proc.calls
        assert any(c.startswith("wait(") for c in proc.calls)
        # Kill fallback must NOT fire when wait() returns within grace.
        assert "kill" not in proc.calls

    def test_falls_back_to_kill_on_wait_timeout(self) -> None:
        """If ``terminate`` followed by ``wait`` exceeds the 25-second
        grace, the function escalates to ``kill``. This guarantees the
        supervisor unblocks even if the agent has hung on a stuck
        atexit handler / final flush.
        """
        proc = _StubProc(
            returncode=0,
            poll_returncode=None,
            wait_raises=subprocess.TimeoutExpired(cmd="x", timeout=25),
        )
        windows_launcher._terminate_subprocess(proc)
        assert "terminate" in proc.calls
        assert "kill" in proc.calls

    def test_swallows_oserror_from_terminate(self) -> None:
        """``terminate`` may race the process's own teardown -- the
        process exits between the supervisor's ``poll`` and its
        ``terminate``, and ``terminate`` raises ``OSError`` (Windows
        handle-already-closed). The function must swallow this and
        proceed to ``wait`` rather than propagate -- otherwise the
        supervisor would print a confusing OSError traceback on every
        polite stop that races a clean exit.
        """
        proc = _StubProc(
            returncode=0,
            poll_returncode=None,
            terminate_raises=OSError("simulated already-closed handle"),
        )
        windows_launcher._terminate_subprocess(proc)
        assert "terminate" in proc.calls
        # The function continues to ``wait`` after the swallowed
        # OSError; ``kill`` is not reached because wait returns normally
        # in this stub.
        assert any(c.startswith("wait(") for c in proc.calls)


def _install_supervise_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    exit_codes: list[int],
    resolve_raises: BaseException | None = None,
    spawn_raises: BaseException | None = None,
) -> tuple[list[Path], list[Path]]:
    """Wire the supervisor loop's collaborators with deterministic stubs.

    Returns ``(spawn_log, resolve_log)`` for test assertions.
    ``exit_codes`` is consumed one entry per spawned launcher; if
    exhausted, the test fails (indicates an unexpected re-spawn).
    """
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
        if not exit_codes:
            raise AssertionError(
                "unexpected re-spawn: exit_codes list exhausted; _supervise "
                "looped more times than the test expected"
            )
        spawn_log.append(python_path)
        rc = exit_codes.pop(0)
        return _StubProc(returncode=rc, poll_returncode=rc)

    monkeypatch.setattr(
        windows_launcher, "_resolve_release_python", fake_resolve
    )
    monkeypatch.setattr(
        windows_launcher, "_spawn_release_launcher", fake_spawn
    )
    return spawn_log, resolve_log


class TestSupervise:
    """The supervisor loop is the Windows counterpart to systemd's
    ``RestartForceExitStatus=75``. Agent exits 75 after a successful
    pair (or after a staged auto-update); the supervisor must re-spawn
    the launcher so the next boot reads the just-written
    ``RELAY_AGENT_ID`` / ``RELAY_AGENT_TOKEN`` and brings up the relay
    client. Without this re-spawn loop, ``_supervise`` would return on
    first exit, Task Scheduler would report task-complete, and the
    user would be forced to ``Start-ScheduledTask MobaideAgent``
    manually -- exactly the "no additional actions" gap the loop was
    added to close.
    """

    def test_respawns_on_exit_restart_requested(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        spawn_log, _ = _install_supervise_stubs(
            monkeypatch,
            exit_codes=[
                windows_launcher._EXIT_RESTART_REQUESTED,
                0,
            ],
        )
        rc = windows_launcher._supervise(tmp_path)
        assert rc == 0
        assert len(spawn_log) == 2, (
            "expected exactly 2 spawns (initial + re-spawn after exit 75), "
            f"got {len(spawn_log)}"
        )

    def test_does_not_respawn_on_clean_exit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Exit code 0 (clean stop) must NOT trigger a re-spawn.

        Only ``_EXIT_RESTART_REQUESTED`` (75) is the restart signal;
        other codes mean the launcher decided to stop and the
        supervisor should propagate that to Task Scheduler unchanged.
        """
        spawn_log, _ = _install_supervise_stubs(
            monkeypatch, exit_codes=[0]
        )
        rc = windows_launcher._supervise(tmp_path)
        assert rc == 0
        assert len(spawn_log) == 1

    def test_does_not_respawn_on_crash_exit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Crash exit codes (e.g. 70 from the launcher's missing-
        interpreter path) must NOT trigger a re-spawn -- those are
        real failures the operator needs to see, and Task Scheduler's
        outer ``-RestartCount`` / ``-RestartInterval`` handles repeated
        failures.
        """
        spawn_log, _ = _install_supervise_stubs(
            monkeypatch, exit_codes=[70]
        )
        rc = windows_launcher._supervise(tmp_path)
        assert rc == 70
        assert len(spawn_log) == 1

    def test_propagates_arbitrary_exit_code(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The supervisor must propagate the launcher's exit code
        verbatim (except for 75). A regression that silently maps any
        non-zero exit to a canonical value would hide the launcher's
        signal to Task Scheduler about WHY it failed (e.g. exit 137 on
        OOM kill, exit 130 on SIGINT, exit 70 on resolve error).
        """
        _install_supervise_stubs(monkeypatch, exit_codes=[137])
        rc = windows_launcher._supervise(tmp_path)
        assert rc == 137

    def test_resolves_release_python_each_iteration(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Between re-spawns, ``_resolve_release_python`` is called
        again so a ``current_version`` rewritten by the auto-updater
        between iterations is picked up on the next boot.

        Without this, a staged auto-update + ``exit 75`` would
        re-spawn the launcher pinned to the OLD release, defeating the
        staged rollout -- the operator would see the agent restart but
        stay on the previous version until the SUPERVISOR itself was
        restarted (i.e. a full ``Stop-ScheduledTask`` + ``Start-
        ScheduledTask`` cycle).
        """
        _, resolve_log = _install_supervise_stubs(
            monkeypatch,
            exit_codes=[
                windows_launcher._EXIT_RESTART_REQUESTED,
                windows_launcher._EXIT_RESTART_REQUESTED,
                0,
            ],
        )
        rc = windows_launcher._supervise(tmp_path)
        assert rc == 0
        assert len(resolve_log) == 3, (
            "_resolve_release_python must be called once per spawn "
            "(3 spawns -> 3 resolves so a between-restart "
            "``current_version`` rewrite is picked up); got "
            f"{len(resolve_log)} calls"
        )

    def test_resolve_error_returns_70(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``FileNotFoundError`` from ``_resolve_release_python``
        (empty / missing / bad ``current_version``) maps to exit code
        70. The constant matches systemd's ``EX_SOFTWARE``-style
        "internal software error" code and is the documented signal to
        Task Scheduler that "this is a configuration problem, not a
        crash -- restarting will not help".
        """
        _install_supervise_stubs(
            monkeypatch,
            exit_codes=[],
            resolve_raises=FileNotFoundError("empty current_version"),
        )
        rc = windows_launcher._supervise(tmp_path)
        assert rc == 70

    def test_resolve_error_writes_to_stderr(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The resolve-error branch must emit an operator-readable
        diagnostic to stderr. The supervisor's stderr is itself
        redirected to ``<prefix>/logs/agent.err.log`` (by Task
        Scheduler's action redirection or by the smoke-test invocation
        env) so the message lands somewhere the operator can recover.
        Silent exit 70 was the original SCM-service complaint: agent
        gone, no signal.
        """
        _install_supervise_stubs(
            monkeypatch,
            exit_codes=[],
            resolve_raises=FileNotFoundError(
                "cannot read current_version: missing"
            ),
        )
        rc = windows_launcher._supervise(tmp_path)
        assert rc == 70
        err = capsys.readouterr().err
        assert "cannot resolve release interpreter" in err, (
            "resolve error must produce a stderr line with the prefix "
            "``mobaide_launcher: cannot resolve release interpreter``; "
            f"got stderr: {err!r}"
        )
        assert "current_version" in err

    def test_spawn_oserror_returns_70(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``OSError`` from ``Popen`` (e.g. permission denied opening
        the release ``python.exe`` after a partial AV quarantine) also
        maps to exit 70 with a stderr diagnostic. Mirrors the
        resolve-error path -- both are infrastructure problems that
        Task Scheduler restarts cannot fix.
        """
        _install_supervise_stubs(
            monkeypatch,
            exit_codes=[],
            spawn_raises=OSError(13, "permission denied"),
        )
        rc = windows_launcher._supervise(tmp_path)
        assert rc == 70

    def test_keyboard_interrupt_returns_zero(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``KeyboardInterrupt`` (CTRL_C / CTRL_BREAK from
        ``Stop-ScheduledTask``) terminates the launcher subprocess and
        exits with code 0.

        Returning 0 is the documented "polite stop" signal: Task
        Scheduler must see a clean exit and not restart the task
        immediately under its ``RestartCount`` setting (which would
        defeat ``Stop-ScheduledTask`` from the operator's
        perspective).
        """
        terminate_log: list[object] = []

        def fake_resolve(prefix: Path) -> Path:
            return prefix / "fake_python.exe"

        def fake_spawn(python_path: Path, prefix: Path) -> _StubProc:
            return _StubProc(returncode=0, poll_returncode=None)

        def fake_terminate(proc: object) -> None:
            terminate_log.append(proc)

        def raise_kbd_interrupt(self_proc: _StubProc) -> int:
            raise KeyboardInterrupt

        monkeypatch.setattr(
            windows_launcher, "_resolve_release_python", fake_resolve
        )
        monkeypatch.setattr(
            windows_launcher, "_spawn_release_launcher", fake_spawn
        )
        monkeypatch.setattr(
            windows_launcher, "_terminate_subprocess", fake_terminate
        )
        # Make _StubProc.wait raise KeyboardInterrupt the first time
        # it is called.
        monkeypatch.setattr(_StubProc, "wait", raise_kbd_interrupt)

        rc = windows_launcher._supervise(tmp_path)
        assert rc == 0
        # The supervisor must terminate the in-flight subprocess via
        # the centralised helper -- skipping it would orphan the
        # launcher (and via the Job Object, the agent).
        assert len(terminate_log) == 1, (
            "_terminate_subprocess must be called exactly once when "
            "KeyboardInterrupt unwinds the supervisor loop; got "
            f"{len(terminate_log)} calls"
        )

    def test_logs_restart_diagnostic_to_stderr(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """An exit-75 respawn must emit an operator-readable diagnostic
        to stderr so logs/agent.err.log carries a "restart requested"
        line. Operators correlating "agent disappeared then reappeared"
        with "I just paired" rely on this string; a silent re-spawn
        looks like a crash.
        """
        _install_supervise_stubs(
            monkeypatch,
            exit_codes=[windows_launcher._EXIT_RESTART_REQUESTED, 0],
        )
        windows_launcher._supervise(tmp_path)
        err = capsys.readouterr().err
        assert "restart requested" in err, (
            "exit-75 respawn must produce a stderr line containing the "
            "phrase 'restart requested' so operators can correlate the "
            "restart with the agent's pairing-complete event; got "
            f"stderr: {err!r}"
        )


class TestRedirectSupervisorStderr:
    """``_redirect_supervisor_stderr`` rebinds ``sys.stderr`` to a writer
    pointed at ``<prefix>/logs/launcher.err.log``.

    Under ``pythonw.exe`` (GUI subsystem, no console allocated) the
    interpreter's default ``sys.stderr`` is a no-op writer that silently
    drops every byte. The supervisor's own emergency diagnostics ("cannot
    resolve release interpreter", "failed to spawn launcher") are
    operator-facing and silently discarding them turns "agent never
    started" into an opaque failure mode. The redirect routes those
    writes onto a dedicated rotated log file (distinct from
    ``agent.err.log`` so supervisor-level failures are not interleaved
    with the spawned launcher's stderr).

    Each test monkeypatches ``sys.stderr`` so the global handle is
    restored on teardown; ``capsys`` is not used here because the
    helper assigns directly to ``sys.stderr`` and pytest's own
    redirection would interfere with that assignment.
    """

    def test_writes_land_in_launcher_err_log(
        self,
        tmp_path: Path,
    ) -> None:
        original_stderr = sys.stderr
        try:
            windows_launcher._redirect_supervisor_stderr(tmp_path)
            sys.stderr.write("supervisor: cannot resolve release interpreter\n")
            sys.stderr.flush()
        finally:
            # Restore stderr BEFORE asserting so a failing assertion is
            # surfaced through pytest's normal stderr -- not redirected
            # into the temp log where it would vanish.
            redirected = sys.stderr
            sys.stderr = original_stderr
            try:
                redirected.close()
            except OSError:
                pass

        log_path = tmp_path / "logs" / "launcher.err.log"
        assert log_path.exists(), (
            "_redirect_supervisor_stderr must create "
            "<prefix>/logs/launcher.err.log on first call -- without it, "
            "the supervisor's own diagnostics under pythonw.exe go to the "
            "interpreter's no-op stderr and operators see no signal"
        )
        contents = log_path.read_text(encoding="utf-8")
        assert "cannot resolve release interpreter" in contents, (
            "writes to ``sys.stderr`` after _redirect_supervisor_stderr "
            "must reach <prefix>/logs/launcher.err.log; got "
            f"contents={contents!r}"
        )

    def test_creates_log_file_with_distinct_filename_from_agent_err_log(
        self, tmp_path: Path
    ) -> None:
        """``_redirect_supervisor_stderr`` opens ``launcher.err.log``,
        not ``agent.err.log``.

        ``agent.err.log`` is reserved for the spawned launcher's stderr
        (redirected by ``_spawn_release_launcher`` via the ``stderr=``
        Popen kwarg). Sharing one filename would interleave supervisor-
        level "cannot spawn launcher" messages with release-pinned
        launcher / agent failures and (worse) introduce a concurrent-
        write race between the parent supervisor and the dup'd child
        handle. The two log files MUST be distinct on disk.

        We assert the file-system effect of the call, NOT subsequent
        ``sys.stderr`` writes -- the writes are pinned by the companion
        ``test_writes_land_in_launcher_err_log`` test.
        """
        original_stderr = sys.stderr
        try:
            windows_launcher._redirect_supervisor_stderr(tmp_path)
        finally:
            # Restore stderr immediately -- this test does not depend
            # on the redirect surviving; it only checks the file-creation
            # side-effect.
            redirected = sys.stderr
            sys.stderr = original_stderr
            try:
                redirected.close()
            except (OSError, AttributeError):
                pass

        log_dir = tmp_path / "logs"
        assert (log_dir / "launcher.err.log").exists(), (
            "the supervisor's stderr log MUST be named launcher.err.log "
            "so it is distinct from agent.err.log (which captures the "
            "spawned launcher subprocess stderr)"
        )
        # The agent err log file must NOT have been created by the
        # supervisor's stderr redirect -- only ``_spawn_release_launcher``
        # writes there.
        assert not (log_dir / "agent.err.log").exists(), (
            "_redirect_supervisor_stderr must NOT touch agent.err.log -- "
            "that filename is reserved for the spawned launcher's stderr "
            "redirect via Popen(stderr=...). Creating it here would race "
            "the child handle"
        )

    def test_uses_documented_filename_constant(self) -> None:
        """The on-disk filename comes from
        ``_LAUNCHER_ERR_LOG_FILE_NAME`` -- a module-level constant so
        operators tailing ``launcher.err.log`` and docs referencing the
        same path cannot drift out of sync.
        """
        assert windows_launcher._LAUNCHER_ERR_LOG_FILE_NAME == "launcher.err.log"
        assert (
            windows_launcher._LAUNCHER_ERR_LOG_BACKUP_NAME
            == "launcher.err.log.1"
        ), (
            "rotation backup constant must shadow the live-log name with "
            "the ``.1`` suffix used by every other supervisor log -- a "
            "regression here would put the backup at an undocumented "
            "name and break ``Get-ChildItem <prefix>/logs`` cleanup"
        )

class TestMainWiresStderrRedirect:
    """``main`` calls ``_redirect_supervisor_stderr`` BEFORE
    ``_supervise`` so the supervisor's own diagnostics during resolve /
    spawn errors land in ``launcher.err.log``.

    Order matters: if ``_supervise`` runs first and hits a resolve
    error, the stderr write at ``_supervise``'s exit-70 branch would
    silently disappear under pythonw.exe. The fix is to install the
    redirect at ``main`` top so every subsequent stderr write lands in
    the file.
    """

    def test_main_calls_redirect_before_supervise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        order: list[str] = []

        def fake_redirect(_prefix: Path) -> None:
            order.append("redirect")

        def fake_supervise(_prefix: Path) -> int:
            order.append("supervise")
            return 0

        monkeypatch.setattr(
            windows_launcher, "_install_signal_handlers", lambda: None
        )
        monkeypatch.setattr(
            windows_launcher, "_redirect_supervisor_stderr", fake_redirect
        )
        monkeypatch.setattr(windows_launcher, "_supervise", fake_supervise)
        monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", r"D:\foo")

        rc = windows_launcher.main([])

        assert rc == 0
        assert order == ["redirect", "supervise"], (
            "_redirect_supervisor_stderr must be called BEFORE "
            "_supervise so resolve/spawn-error diagnostics land in "
            "<prefix>/logs/launcher.err.log instead of pythonw.exe's "
            f"no-op stderr; got call order {order!r}"
        )

    def test_main_passes_resolved_prefix_to_redirect(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The same prefix used by ``_supervise`` MUST be threaded into
        ``_redirect_supervisor_stderr`` -- otherwise the stderr log
        could land under one install prefix while the supervisor reads
        ``current_version`` from a different one (e.g. ProgramFiles
        default vs. a ``--prefix D:\\custom`` override).
        """
        prefix_calls: dict[str, Path] = {}

        def fake_redirect(prefix: Path) -> None:
            prefix_calls["redirect"] = prefix

        def fake_supervise(prefix: Path) -> int:
            prefix_calls["supervise"] = prefix
            return 0

        monkeypatch.setattr(
            windows_launcher, "_install_signal_handlers", lambda: None
        )
        monkeypatch.setattr(
            windows_launcher, "_redirect_supervisor_stderr", fake_redirect
        )
        monkeypatch.setattr(windows_launcher, "_supervise", fake_supervise)

        windows_launcher.main(["--prefix", r"D:\custom\install"])

        assert prefix_calls.get("redirect") == Path(r"D:\custom\install")
        assert prefix_calls["redirect"] == prefix_calls["supervise"], (
            "_redirect_supervisor_stderr and _supervise must receive "
            "the same resolved Path -- a divergence would put the "
            "stderr log under one prefix and current_version reads "
            f"under another. Got {prefix_calls!r}"
        )


class TestBindDescendantsToSupervisor:
    """``schtasks /End`` terminates only the supervisor. Without the job the
    launcher and agent it started kept running -- holding the agent port and
    the relay credentials they booted with -- so the task's next start could
    not bind and exited, and a re-pair never completed.
    """

    def test_main_binds_after_redirect_and_before_supervise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        order: list[str] = []
        monkeypatch.setattr(windows_launcher, "_install_signal_handlers", lambda: None)
        monkeypatch.setattr(
            windows_launcher, "_redirect_supervisor_stderr",
            lambda _prefix: order.append("redirect"),
        )
        monkeypatch.setattr(
            windows_launcher, "_bind_descendants_to_supervisor",
            lambda: order.append("bind"),
        )

        def fake_supervise(_prefix: Path) -> int:
            order.append("supervise")
            return 0

        monkeypatch.setattr(windows_launcher, "_supervise", fake_supervise)

        windows_launcher.main(["--prefix", r"D:\foo"])

        # After the redirect so a failure is logged; before the first spawn
        # so the launcher is born inside the job.
        assert order == ["redirect", "bind", "supervise"]

    def test_no_op_on_non_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(windows_launcher, "_descendant_job", None)

        _REAL_BIND_DESCENDANTS()

        assert windows_launcher._descendant_job is None

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


class TestParseArgs:
    def test_prefix_argv(self) -> None:
        ns = windows_launcher._parse_args(["--prefix", r"D:\custom"])
        assert ns.prefix == r"D:\custom"

    def test_prefix_defaults_to_none(self) -> None:
        """Without ``--prefix``, the namespace's ``prefix`` attribute
        is None so ``_default_prefix(None)`` falls through to the env-
        var / ProgramFiles chain. A regression that defaulted ``prefix``
        to an empty string would break the smoke-test invocation
        ``python deploy/windows_launcher.py`` (cwd != install prefix).
        """
        ns = windows_launcher._parse_args([])
        assert ns.prefix is None


class TestMain:
    """``main`` is the top-level entry point invoked by the Scheduled
    Task action. Wires argparse + signal handlers + supervisor loop.
    Tests verify the wiring without entering the actual loop.
    """

    def test_main_invokes_supervise_with_resolved_prefix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``main(['--prefix', 'D:\\foo'])`` must call
        ``_supervise(Path('D:\\foo'))`` and propagate its return code.
        """
        calls: list[Path] = []

        def fake_supervise(prefix: Path) -> int:
            calls.append(prefix)
            return 42

        monkeypatch.setattr(windows_launcher, "_supervise", fake_supervise)
        # Stub out signal-handler installation so this test does not
        # leave a SIGBREAK handler in the test process on Windows.
        monkeypatch.setattr(
            windows_launcher, "_install_signal_handlers", lambda: None
        )

        rc = windows_launcher.main(["--prefix", r"D:\foo"])
        assert rc == 42
        assert calls == [Path(r"D:\foo")]

    def test_main_falls_through_to_env_var_when_argv_omits_prefix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``main([])`` must resolve the prefix from
        ``MOBAIDE_AGENT_PREFIX`` when ``--prefix`` is not in argv. This
        is the smoke-test path; the Scheduled Task always passes
        ``--prefix``.
        """
        calls: list[Path] = []

        def fake_supervise(prefix: Path) -> int:
            calls.append(prefix)
            return 0

        monkeypatch.setattr(windows_launcher, "_supervise", fake_supervise)
        monkeypatch.setattr(
            windows_launcher, "_install_signal_handlers", lambda: None
        )
        monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", r"D:\env\prefix")

        rc = windows_launcher.main([])
        assert rc == 0
        assert calls == [Path(r"D:\env\prefix")]

    def test_main_installs_signal_handlers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``main`` must call ``_install_signal_handlers`` before
        entering ``_supervise``. Skipping the handler installation
        leaves the default SIGBREAK behaviour (terminate without
        Python finalisers running) which prevents the
        ``KeyboardInterrupt`` branch of ``_supervise`` from firing on
        ``Stop-ScheduledTask``.
        """
        handler_installed = []

        def fake_install() -> None:
            handler_installed.append(True)

        def fake_supervise(prefix: Path) -> int:
            # Pin the relative order: handlers must be installed BEFORE
            # the supervisor loop starts (otherwise a stop signal
            # arriving during the first iteration's wait would be
            # silently dropped).
            assert handler_installed, (
                "_install_signal_handlers must be called BEFORE "
                "_supervise enters its wait loop; otherwise a stop "
                "signal arriving during the first iteration is dropped"
            )
            return 0

        monkeypatch.setattr(
            windows_launcher, "_install_signal_handlers", fake_install
        )
        monkeypatch.setattr(windows_launcher, "_supervise", fake_supervise)
        monkeypatch.setattr(os, "environ", os.environ.copy())
        monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", r"D:\foo")

        rc = windows_launcher.main([])
        assert rc == 0
        assert handler_installed == [True]


class TestInstallSignalHandlers:
    def test_no_op_on_non_windows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On Linux / macOS CI runners, ``_install_signal_handlers``
        must be a silent no-op. ``signal.SIGBREAK`` only exists on
        Windows; touching it elsewhere would raise ``AttributeError``
        and abort the supervisor before it even reached the loop.
        """
        monkeypatch.setattr(sys, "platform", "linux")
        # Must not raise.
        windows_launcher._install_signal_handlers()


def test_exit_restart_requested_constant_matches_agent_updater() -> None:
    """The supervisor's local ``_EXIT_RESTART_REQUESTED`` constant MUST
    equal ``mobaide.agent.updater.EXIT_RESTART_REQUESTED``.

    The constant is intentionally duplicated because the supervisor
    is stdlib-only -- importing ``mobaide`` would defeat the
    "survives a half-staged or rolled-back release" guarantee (the
    install on disk may not have ``mobaide`` yet, or may have a
    broken ``mobaide`` mid-rollback). A drift between the two breaks
    the post-pairing restart silently: the agent exits with the
    canonical value, the supervisor sees a different number, falls
    through to "stop normally", and the task stays stopped until the
    user re-runs it manually. This test imports the canonical
    definition from the in-tree source and asserts equality.
    """
    sys.path.insert(0, str(_REPO_ROOT / "mobaide_agent"))
    try:
        from mobaide.agent.updater import (  # type: ignore[import-not-found]
            EXIT_RESTART_REQUESTED,
        )
    finally:
        sys.path.pop(0)
    assert windows_launcher._EXIT_RESTART_REQUESTED == EXIT_RESTART_REQUESTED, (
        f"windows_launcher._EXIT_RESTART_REQUESTED="
        f"{windows_launcher._EXIT_RESTART_REQUESTED!r} must equal "
        f"mobaide.agent.updater.EXIT_RESTART_REQUESTED="
        f"{EXIT_RESTART_REQUESTED!r} -- see the comment on the duplicated "
        "constant in deploy/windows_launcher.py for why it is duplicated "
        "and why drift is a silent break"
    )


def test_log_rotate_threshold_is_5mb() -> None:
    """Pin the 5 MB rotation threshold. The value is referenced in
    docs/deployment.md's troubleshooting section ("tail the last 5 MB
    of agent.err.log") and a regression that drops the threshold
    silently would invalidate that operator-facing guidance.
    """
    assert windows_launcher._AGENT_LOG_ROTATE_BYTES == 5 * 1024 * 1024


def _make_win_release(
    prefix: Path, version: str, *, complete: bool = True
) -> None:
    """Create ``releases/<version>/venv/Scripts/python.exe`` (a stub file).

    A real release is only selectable once it is *complete* — the venv is
    built AND the wheel is installed — which the supervisor now enforces
    via ``_is_release_complete`` (sentinel OR ``mobaide-agent.exe`` console
    script). ``complete=True`` (the default, matching the steady state of
    every installed release) drops the ``.installed`` sentinel so the
    fixture models a finalized release; ``complete=False`` models a venv
    swapped into place but not yet pip-installed (no sentinel, no console
    script), which recovery/resolve must skip.
    """
    scripts = prefix / "releases" / version / "venv" / "Scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "python.exe").write_text("stub", encoding="utf-8")
    if complete:
        (prefix / "releases" / version / ".installed").write_text(
            version, encoding="utf-8"
        )


class TestSuperviseRecovery:
    """Self-heal: when ``current_version`` points at a release whose
    ``python.exe`` is gone (half-staged / corrupt), the supervisor rolls
    forward to the newest sibling with a runnable venv and respawns, rather
    than exiting straight to Task Scheduler and crash-looping. The in-venv
    ``mobaide.launcher`` cannot do this — it needs its own interpreter to run.
    """

    def test_find_recovery_version_picks_newest_numeric(self, tmp_path: Path) -> None:
        for v in ("0.2.0", "0.10.0", "0.9.0"):
            _make_win_release(tmp_path, v)
        assert windows_launcher._find_recovery_version(tmp_path) == "0.10.0"

    def test_find_recovery_version_ignores_scratch_and_venvless(
        self, tmp_path: Path
    ) -> None:
        # Scratch dir with a venv must be ignored (name does not parse).
        _make_win_release(tmp_path, ".staging-9.9.9-1")
        # Version dir without a venv must be ignored.
        (tmp_path / "releases" / "0.3.0").mkdir(parents=True)
        _make_win_release(tmp_path, "0.2.0")
        assert windows_launcher._find_recovery_version(tmp_path) == "0.2.0"

    def test_find_recovery_version_none_when_no_runnable(self, tmp_path: Path) -> None:
        (tmp_path / "releases" / "0.2.0").mkdir(parents=True)  # no venv
        assert windows_launcher._find_recovery_version(tmp_path) is None

    def test_find_recovery_version_none_when_no_releases_dir(
        self, tmp_path: Path
    ) -> None:
        assert windows_launcher._find_recovery_version(tmp_path) is None

    def test_write_current_version_atomic(self, tmp_path: Path) -> None:
        windows_launcher._write_current_version(tmp_path, "1.2.3")
        assert (tmp_path / "current_version").read_text().strip() == "1.2.3"

    def test_supervise_recovers_then_spawns(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _make_win_release(tmp_path, "0.2.13")
        # current_version points at a missing release → triggers recovery.
        (tmp_path / "current_version").write_text("0.2.99\n", encoding="utf-8")

        spawn_log: list[Path] = []

        def fake_spawn(python_path: Path, prefix: Path) -> _StubProc:
            spawn_log.append(python_path)
            return _StubProc(returncode=0, poll_returncode=0)

        monkeypatch.setattr(windows_launcher, "_spawn_release_launcher", fake_spawn)

        rc = windows_launcher._supervise(tmp_path)
        assert rc == 0
        assert (tmp_path / "current_version").read_text().strip() == "0.2.13"
        assert len(spawn_log) == 1
        assert "0.2.13" in str(spawn_log[0])

    def test_supervise_returns_70_when_no_recovery_target(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        (tmp_path / "releases").mkdir(parents=True)  # empty: nothing runnable
        (tmp_path / "current_version").write_text("0.2.0\n", encoding="utf-8")

        def fake_spawn(python_path: Path, prefix: Path) -> _StubProc:
            raise AssertionError("spawn must not run when no recovery target")

        monkeypatch.setattr(windows_launcher, "_spawn_release_launcher", fake_spawn)

        rc = windows_launcher._supervise(tmp_path)
        assert rc == 70

    def test_supervise_recovery_is_bounded(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Recovery cannot loop forever: if every recovery target still fails to
        resolve, the supervisor exits 70 after at most _MAX_RECOVERY_ATTEMPTS
        recoveries (bounded by the cap, even when each target is distinct so the
        per-version `recovered` set never short-circuits)."""
        (tmp_path / "current_version").write_text("0.0.0\n", encoding="utf-8")

        resolve_calls: list[Path] = []

        def always_missing(prefix: Path) -> Path:
            resolve_calls.append(prefix)
            raise FileNotFoundError("missing interpreter")

        counter = {"n": 0}

        def fake_find(prefix: Path) -> str:
            counter["n"] += 1
            return f"9.9.{counter['n']}"  # a fresh distinct target each time

        monkeypatch.setattr(
            windows_launcher, "_resolve_release_python", always_missing
        )
        monkeypatch.setattr(windows_launcher, "_find_recovery_version", fake_find)
        monkeypatch.setattr(
            windows_launcher, "_write_current_version", lambda p, v: None
        )

        def fake_spawn(python_path: Path, prefix: Path) -> _StubProc:
            raise AssertionError("must not spawn when resolve always fails")

        monkeypatch.setattr(windows_launcher, "_spawn_release_launcher", fake_spawn)

        rc = windows_launcher._supervise(tmp_path)
        assert rc == 70
        # Initial resolve per loop iteration; bounded by the cap + 1.
        assert len(resolve_calls) <= windows_launcher._MAX_RECOVERY_ATTEMPTS + 1


class TestReleaseCompletenessGate:
    """The venv "bad interpreter" fix made the supervisor treat a release
    as runnable only when it is *complete* — the pip-less venv has been
    swapped into place AND the wheel installed. Completeness is signalled
    by the ``.installed`` sentinel OR (for releases predating it) the
    ``mobaide-agent.exe`` console script pip writes last.

    ``_is_release_complete`` is the inlined Windows copy of
    ``mobaide.agent.install_paths.is_release_complete``; both
    ``_find_recovery_version`` and ``_resolve_release_python`` gate on it
    so the supervisor never exec's a half-built venv whose ``pip`` /
    ``mobaide-agent`` shebangs point at a swept staging dir. These tests
    pin the new gate from both directions: a sentinel-less / console-
    script-less release is excluded; a sentinel-bearing release is
    included; a legacy console-script-only release is still accepted.
    """

    def test_is_release_complete_true_with_sentinel(self, tmp_path: Path) -> None:
        """A release carrying ``.installed`` is complete even without the
        console script — the sentinel is the canonical post-fix signal the
        updater and installers write as their final step.
        """
        release = tmp_path / "releases" / "0.3.0"
        (release / "venv" / "Scripts").mkdir(parents=True)
        (release / ".installed").write_text("0.3.0", encoding="utf-8")
        assert windows_launcher._is_release_complete(release) is True

    def test_is_release_complete_true_with_legacy_console_script(
        self, tmp_path: Path
    ) -> None:
        """A release with no sentinel but a ``mobaide-agent.exe`` console
        script is accepted — pip writes that script only after a successful
        wheel install, so it proves completeness for releases made by a
        prior installer/updater that predates the sentinel. Without this
        legacy fallback, every pre-fix release would be judged incomplete
        and upgrades would brick.
        """
        release = tmp_path / "releases" / "0.3.0"
        scripts = release / "venv" / "Scripts"
        scripts.mkdir(parents=True)
        (scripts / "mobaide-agent.exe").write_bytes(b"\x4d\x5a")  # MZ
        assert not (release / ".installed").exists()
        assert windows_launcher._is_release_complete(release) is True

    def test_is_release_complete_false_when_unfinalized(
        self, tmp_path: Path
    ) -> None:
        """A venv swapped into place but not yet pip-installed — no
        sentinel AND no console script — is incomplete. This is the exact
        half-built post-swap state the fix exists to exclude; treating it
        as complete is what produced the "bad interpreter" crash when the
        supervisor exec'd a release whose wheel had never been installed.
        """
        release = tmp_path / "releases" / "0.3.0"
        (release / "venv" / "Scripts").mkdir(parents=True)
        # python.exe exists (venv was built) but neither completeness
        # signal is present.
        (release / "venv" / "Scripts" / "python.exe").write_text(
            "stub", encoding="utf-8"
        )
        assert windows_launcher._is_release_complete(release) is False

    def test_find_recovery_version_skips_incomplete_release(
        self, tmp_path: Path
    ) -> None:
        """``_find_recovery_version`` must skip a sentinel-less incomplete
        release even though its ``python.exe`` exists.

        The newest sibling (0.10.0) is swapped-into-place-but-unfinalized;
        recovery must fall through to the older complete 0.2.0 rather than
        roll forward onto a release whose wheel was never installed. Before
        the fix, ``_find_recovery_version`` keyed only on ``python.exe``
        existence and would have selected the broken 0.10.0 — the supervisor
        would then crash-loop on its missing ``mobaide-agent`` entry point.
        """
        _make_win_release(tmp_path, "0.2.0", complete=True)
        _make_win_release(tmp_path, "0.10.0", complete=False)
        assert windows_launcher._find_recovery_version(tmp_path) == "0.2.0", (
            "recovery must skip the newer incomplete release (no .installed "
            "sentinel, no console script) and pick the older complete one; "
            "selecting the half-built release re-opens the bad-interpreter "
            "crash"
        )

    def test_find_recovery_version_accepts_legacy_release_without_sentinel(
        self, tmp_path: Path
    ) -> None:
        """A legacy release (console script present, no ``.installed``) must
        still be recoverable.

        Guards the upgrade path: a host whose only sibling is a pre-sentinel
        release must not be stranded with "no recovery target". The
        console-script fallback in ``_is_release_complete`` is what keeps
        that release selectable.
        """
        release = tmp_path / "releases" / "0.9.0"
        scripts = release / "venv" / "Scripts"
        scripts.mkdir(parents=True)
        (scripts / "python.exe").write_text("stub", encoding="utf-8")
        (scripts / "mobaide-agent.exe").write_bytes(b"\x4d\x5a")
        assert not (release / ".installed").exists()
        assert windows_launcher._find_recovery_version(tmp_path) == "0.9.0", (
            "a legacy console-script release (no sentinel) must remain a "
            "valid recovery target, otherwise upgrades from a pre-sentinel "
            "install strand the supervisor with no rollback sibling"
        )

    def test_find_recovery_version_none_when_only_incomplete(
        self, tmp_path: Path
    ) -> None:
        """When every sibling is incomplete, ``_find_recovery_version``
        returns ``None`` so ``_supervise`` exits 70 rather than exec a
        half-built venv. The interpreter existing is not enough — only a
        finalized release is a valid recovery target.
        """
        _make_win_release(tmp_path, "0.2.0", complete=False)
        _make_win_release(tmp_path, "0.3.0", complete=False)
        assert windows_launcher._find_recovery_version(tmp_path) is None

    def test_resolve_release_python_rejects_incomplete_current_release(
        self, tmp_path: Path
    ) -> None:
        """``_resolve_release_python`` must reject a ``current_version``
        that points at an unfinalized release, surfacing it as a
        ``FileNotFoundError`` so ``_supervise`` recovers to a complete
        sibling instead of exec-ing the half-built venv.

        This covers Decision 4's forced-restage-crash case: the updater
        flipped (or a prior installer left) ``current_version`` at a
        release whose venv was swapped in but never pip-installed. The
        interpreter file exists, so the pre-fix code returned it happily;
        the post-fix gate rejects it with a "not finalized" message.
        """
        (tmp_path / "current_version").write_text("0.5.0\n", encoding="utf-8")
        scripts = tmp_path / "releases" / "0.5.0" / "venv" / "Scripts"
        scripts.mkdir(parents=True)
        (scripts / "python.exe").write_bytes(b"\x4d\x5a")  # interpreter present
        # No .installed sentinel and no mobaide-agent.exe → unfinalized.
        with pytest.raises(FileNotFoundError, match="not finalized") as excinfo:
            windows_launcher._resolve_release_python(tmp_path)
        # The version must be named so the operator can correlate it with
        # the staged-update state (mirrors the missing-interpreter branch).
        assert "0.5.0" in str(excinfo.value)

    def test_resolve_release_python_accepts_complete_current_release(
        self, tmp_path: Path
    ) -> None:
        """Positive control: a ``current_version`` pointing at a complete
        release (sentinel present) resolves to its ``python.exe`` — the
        gate must not over-reject a finalized release.
        """
        (tmp_path / "current_version").write_text("0.5.0\n", encoding="utf-8")
        release = tmp_path / "releases" / "0.5.0"
        scripts = release / "venv" / "Scripts"
        scripts.mkdir(parents=True)
        (scripts / "python.exe").write_bytes(b"\x4d\x5a")
        (release / ".installed").write_text("0.5.0", encoding="utf-8")
        resolved = windows_launcher._resolve_release_python(tmp_path)
        assert resolved == scripts / "python.exe"
