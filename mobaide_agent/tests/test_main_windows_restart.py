"""Tests for the Windows restart dispatcher in ``mobaide.__main__``.

Covers four helpers introduced by the SCM-service -> Scheduled-Task
migration:

* ``_try_restart_windows_service`` — dispatcher that probes for the
  Scheduled Task first (post-migration shape) and falls back to the
  legacy SCM service during the upgrade window.
* ``_restart_windows_scheduled_task`` — End -> wait-ready -> Run flow.
* ``_restart_windows_scm_service`` — legacy stop -> wait-stopped -> start
  flow with the updated "migrate to Scheduled Task" hint.
* ``_wait_for_windows_scheduled_task_ready`` — polling helper that
  parses ``schtasks.exe /Query /FO LIST /V`` for ``Status: Ready``.

The dispatcher is reachable from three CLI verbs (``fetch-trust-key``,
``pair-qr --wait``, ``re-pair``) so a silent regression here strands
post-pair restarts for every Windows operator. The tests stub
``_run_with_timeout`` at the module level (the project's existing pattern
for testing subprocess-backed CLI helpers — see ``test_main.py`` and
``test_fetch_trust_key.py``) so the test suite runs on Linux / macOS
CI hosts that have no ``schtasks.exe`` or ``sc.exe`` on PATH.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

import pytest

import mobaide.__main__ as _main


class _FakeWindowsProcesses:
    """Stand-in for ``mobaide.agent.windows_processes``.

    Records calls into ``events`` so a test can share that list with the
    ``_run_with_timeout`` recorder and assert the interleaving.
    """

    def __init__(self) -> None:
        self.events: list = []
        self.inside = False
        self.leftover: list[int] = []
        self.remaining: list[int] = []
        self.list_error: OSError | None = None

    def list_processes(self) -> list:
        if self.list_error is not None:
            raise self.list_error
        return []

    def runs_inside_install(self, _processes, _pid: int, prefix: str) -> bool:
        self.events.append(("inside", prefix))
        return self.inside

    def install_process_ids(self, _processes, prefix: str, *, keep_pid: int) -> list[int]:
        self.events.append(("leftover", prefix, keep_pid))
        return list(self.leftover)

    def terminate(self, pids, *, timeout_s: float) -> list[int]:
        self.events.append(("terminate", list(pids)))
        return list(self.remaining)


@pytest.fixture(autouse=True)
def fake_processes(monkeypatch: pytest.MonkeyPatch) -> _FakeWindowsProcesses:
    """Keep every test here off the real process table: on a Windows host the
    real helpers would stop whatever runs from the default install prefix."""
    fake = _FakeWindowsProcesses()
    monkeypatch.setattr(_main, "_windows_processes", lambda: fake)
    return fake


def _install_run_with_timeout_recorder(
    monkeypatch: pytest.MonkeyPatch,
    responder: Callable[[list[str]], tuple[int, str, str]],
) -> list[list[str]]:
    """Wire a deterministic ``_run_with_timeout`` stub onto ``_main``.

    ``responder`` decides each call's ``(returncode, stdout, stderr)``
    triple given the argv that was about to be invoked. The full argv
    log is appended to the returned list so tests can assert on call
    ordering and the exact subprocess flags the dispatcher selected.

    Patching the helper rather than the underlying ``subprocess.run`` is
    deliberate: every Windows restart helper funnels through
    ``_run_with_timeout`` (so TimeoutExpired / OSError become a uniform
    ``(-1, "", reason)`` return), and the tests want to exercise the
    dispatcher's branching logic, not the subprocess-wrapping shim.
    """
    calls: list[list[str]] = []

    def fake_run_with_timeout(
        cmd: list[str], *, timeout: int = _main._AGENT_RESTART_TIMEOUT_S
    ) -> tuple[int, str, str]:
        calls.append(list(cmd))
        return responder(list(cmd))

    monkeypatch.setattr(_main, "_run_with_timeout", fake_run_with_timeout)
    return calls


def _silence_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the polling helpers with no-ops.

    Tests that target the dispatcher / restart functions do NOT also want
    to exercise the polling loop's deadline arithmetic; the poll helpers
    each have their own dedicated test class. Stubbing them out here
    keeps the dispatcher tests focused and avoids the call-log of the
    polling helper bleeding into ``calls``.
    """
    monkeypatch.setattr(
        _main, "_wait_for_windows_scheduled_task_ready", lambda _name: None
    )
    monkeypatch.setattr(
        _main, "_wait_for_windows_service_stopped", lambda _name: None
    )


class TestTryRestartWindowsServiceDispatcher:
    """Dispatcher precedence: Scheduled Task -> legacy SCM -> nothing.

    The post-migration shape is the Scheduled Task; the SCM probe is the
    transitional upgrade path that lets an operator who installed the
    pre-migration build still get an automatic restart after they re-run
    the new installer. Without the dispatcher, the legacy SCM service
    would be silently abandoned and operators would see ``need admin?``
    on every restart attempt during their first post-upgrade pair.
    """

    def test_dispatches_to_scheduled_task_when_schtasks_query_succeeds(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # schtasks query returns 0 -> Scheduled Task is registered ->
        # dispatcher must take the Scheduled Task path and never touch sc.exe.
        def responder(cmd: list[str]) -> tuple[int, str, str]:
            if cmd[0] == "schtasks.exe" and "/Query" in cmd:
                return 0, "", ""
            if cmd[0] == "schtasks.exe" and "/End" in cmd:
                return 0, "", ""
            if cmd[0] == "schtasks.exe" and "/Run" in cmd:
                return 0, "", ""
            # Hard-fail if the dispatcher accidentally drops down to the
            # SCM branch; sc.exe must NEVER be invoked once the Scheduled
            # Task is known to exist.
            raise AssertionError(
                f"unexpected sc.exe call after schtasks /Query hit: {cmd!r}"
            )

        _silence_waits(monkeypatch)
        calls = _install_run_with_timeout_recorder(monkeypatch, responder)

        rc = _main._try_restart_windows_service()

        assert rc == 0
        # The dispatcher MUST have invoked schtasks.exe /Query first.
        # A regression that reverses the probe order (sc.exe first)
        # would silently take the legacy path on a fresh Scheduled-Task
        # install and emit the "migrate to the Scheduled Task" hint
        # against a system that already migrated.
        assert calls[0] == ["schtasks.exe", "/Query", "/TN", "MobaideAgent"], (
            f"first probe must be schtasks.exe /Query /TN MobaideAgent; "
            f"got {calls[0]!r}"
        )
        # Confirm we reached the Scheduled Task restart path (End + Run).
        invoked_commands = [c[0] + " " + c[1] for c in calls if len(c) >= 2]
        assert "schtasks.exe /End" in invoked_commands, (
            f"expected schtasks.exe /End in calls; got {invoked_commands!r}"
        )
        assert "schtasks.exe /Run" in invoked_commands, (
            f"expected schtasks.exe /Run in calls; got {invoked_commands!r}"
        )
        # Defence in depth: sc.exe must never appear.
        assert not any(c[0] == "sc.exe" for c in calls), (
            f"sc.exe must NOT be invoked when the Scheduled Task probe "
            f"hits; got calls: {calls!r}"
        )
        out = capsys.readouterr().out
        assert "Restarted MobaideAgent Scheduled Task" in out

    def test_falls_back_to_scm_when_schtasks_misses_and_sc_hits(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # schtasks query returns non-zero (task not registered) ->
        # dispatcher must probe sc.exe; if that succeeds it must take the
        # legacy SCM restart path. This is the upgrade-window contract.
        def responder(cmd: list[str]) -> tuple[int, str, str]:
            if cmd[0] == "schtasks.exe" and "/Query" in cmd:
                # 1 is what schtasks emits for ``cannot find the file
                # specified`` on a missing task.
                return 1, "", "ERROR: The system cannot find the file specified."
            if cmd[0] == "sc.exe" and cmd[1] == "query":
                return 0, "", ""
            if cmd[0] == "sc.exe" and cmd[1] == "stop":
                return 0, "", ""
            if cmd[0] == "sc.exe" and cmd[1] == "start":
                return 0, "", ""
            raise AssertionError(f"unexpected call: {cmd!r}")

        _silence_waits(monkeypatch)
        calls = _install_run_with_timeout_recorder(monkeypatch, responder)

        rc = _main._try_restart_windows_service()

        assert rc == 0
        # Probe order: schtasks /Query -> sc.exe query -> sc.exe stop ->
        # sc.exe start.
        assert calls[0] == ["schtasks.exe", "/Query", "/TN", "MobaideAgent"]
        assert calls[1] == ["sc.exe", "query", "MobaideAgent"]
        out = capsys.readouterr().out
        # The legacy-service success message MUST carry the migration
        # hint -- it's the only operator-facing cue that a re-install
        # will move them off the deprecated path.
        assert "Restarted legacy MobaideAgent service" in out, (
            f"legacy-service success line must identify itself as legacy; "
            f"got stdout: {out!r}"
        )
        assert "migrate to the Scheduled Task" in out, (
            f"legacy-service success line must include the migration "
            f"hint so operators upgrade off the deprecated path; got "
            f"stdout: {out!r}"
        )

    def test_prints_skip_message_when_neither_registered(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Both probes miss -> dispatcher must NOT attempt any restart and
        # MUST emit a single skip-restart line so operators understand
        # why the post-pair restart silently no-op'd. Returns 0 so the
        # outer ``_try_restart_service`` does not surface a fake failure
        # to the pair / fetch-trust-key flow.
        def responder(cmd: list[str]) -> tuple[int, str, str]:
            if cmd[0] == "schtasks.exe" and "/Query" in cmd:
                return 1, "", "ERROR: The system cannot find the file specified."
            if cmd[0] == "sc.exe" and cmd[1] == "query":
                return 1060, "", "[SC] EnumQueryServicesStatus:OpenService FAILED 1060"
            raise AssertionError(
                f"dispatcher must not invoke restart commands when both "
                f"probes miss; got unexpected call {cmd!r}"
            )

        _silence_waits(monkeypatch)
        calls = _install_run_with_timeout_recorder(monkeypatch, responder)

        rc = _main._try_restart_windows_service()

        assert rc == 0
        # Exactly two probes; nothing else.
        assert len(calls) == 2, (
            f"both-miss path must invoke exactly the two probes "
            f"(schtasks /Query, sc.exe query); got {len(calls)} calls: "
            f"{calls!r}"
        )
        out = capsys.readouterr().out
        # Single skip-restart line referencing both candidates.
        assert "No MobaideAgent Scheduled Task or service" in out
        assert "skipping restart" in out


class TestRestartWindowsScheduledTask:
    """End -> wait-ready -> Run is the canonical Scheduled-Task restart.

    ``schtasks.exe /End`` is best-effort (it terminates any running
    instance; rc 1 means "nothing was running"). The wait-ready poll
    closes the race between End and Run -- without it, ``/Run`` issued
    against a still-tearing-down instance returns rc 1 ("the task is
    currently running") and the operator gets the misleading
    ``need admin?`` hint.
    """

    def test_invokes_end_then_run_in_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Both commands succeed. The test verifies argv ordering at the
        # call-log level so a regression that flips End / Run (which
        # would skip the actual restart and just start a second
        # parallel instance) is caught here.
        def responder(cmd: list[str]) -> tuple[int, str, str]:
            return 0, "", ""

        _silence_waits(monkeypatch)
        calls = _install_run_with_timeout_recorder(monkeypatch, responder)

        rc = _main._restart_windows_scheduled_task("MobaideAgent")

        assert rc == 0
        # /End must be issued BEFORE /Run; the wait-ready poll between
        # them is stubbed in ``_silence_waits`` so it doesn't add calls.
        assert calls == [
            ["schtasks.exe", "/End", "/TN", "MobaideAgent"],
            ["schtasks.exe", "/Run", "/TN", "MobaideAgent"],
        ], (
            "expected exactly two schtasks.exe calls (/End then /Run) "
            f"in that order; got {calls!r}"
        )

    def test_end_uses_stop_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # /End must run with the longer stop timeout (30 s by default),
        # not the 15 s restart timeout. A 15 s ceiling on /End would
        # cap how long the supervisor waits for a stuck launcher to
        # release its Job Object children before the wait-ready poll
        # starts looping fruitlessly.
        seen_timeouts: list[tuple[str, int]] = []

        def fake_run_with_timeout(
            cmd: list[str], *, timeout: int = _main._AGENT_RESTART_TIMEOUT_S
        ) -> tuple[int, str, str]:
            # Tag each call with the verb in cmd[1] so the assertion can
            # zero in on the End-vs-Run timeout difference.
            verb = cmd[1] if len(cmd) > 1 else ""
            seen_timeouts.append((verb, timeout))
            return 0, "", ""

        monkeypatch.setattr(_main, "_run_with_timeout", fake_run_with_timeout)
        _silence_waits(monkeypatch)

        _main._restart_windows_scheduled_task("MobaideAgent")

        end_timeout = next(t for verb, t in seen_timeouts if verb == "/End")
        assert end_timeout == _main._AGENT_STOP_TIMEOUT_S, (
            f"/End must use _AGENT_STOP_TIMEOUT_S "
            f"({_main._AGENT_STOP_TIMEOUT_S}s) to allow the launcher's "
            f"25s graceful-stop window plus margin; got {end_timeout}s"
        )

    def test_emits_admin_hint_on_run_failure(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # /Run fails (rc 1 -- common when the calling shell is not
        # elevated and the task RunLevel is Highest). Function MUST
        # return 0 (never fail the pair operation) and MUST emit a
        # PowerShell-equivalent command pair so the operator has a
        # copy-pasteable manual fallback.
        def responder(cmd: list[str]) -> tuple[int, str, str]:
            if "/Run" in cmd:
                return 1, "", "ERROR: Access is denied."
            return 0, "", ""

        _silence_waits(monkeypatch)
        _install_run_with_timeout_recorder(monkeypatch, responder)

        rc = _main._restart_windows_scheduled_task("MobaideAgent")

        # Best-effort contract: never fail the pair operation.
        assert rc == 0
        out = capsys.readouterr().out
        assert "Could not restart automatically" in out
        # Both halves of the PowerShell fallback MUST appear; a partial
        # hint (only Stop, only Start) would leave the operator unable
        # to complete the manual restart cycle.
        assert "Stop-ScheduledTask" in out, (
            f"failure hint must mention Stop-ScheduledTask -- the "
            f"PowerShell equivalent of schtasks /End; got: {out!r}"
        )
        assert "Start-ScheduledTask" in out, (
            f"failure hint must mention Start-ScheduledTask -- the "
            f"PowerShell equivalent of schtasks /Run; got: {out!r}"
        )
        # Crucially: NO ``Restart-Service`` (the legacy hint) -- a
        # regression that copy-pasted the SCM-service failure hint into
        # the Scheduled-Task path would mis-direct operators.
        assert "Restart-Service" not in out, (
            f"Scheduled-Task failure hint must NOT mention Restart-Service "
            f"(that's the legacy SCM hint); got: {out!r}"
        )

    def test_invokes_wait_ready_between_end_and_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The wait-ready poll exists to close the race where /End has
        # been submitted but the task is still in "running" state -- a
        # /Run issued against that state gets rejected. Pin the relative
        # ordering: /End -> wait-ready -> /Run.
        ordering: list[str] = []

        def fake_run_with_timeout(
            cmd: list[str], *, timeout: int = _main._AGENT_RESTART_TIMEOUT_S
        ) -> tuple[int, str, str]:
            if cmd[0] == "schtasks.exe" and "/End" in cmd:
                ordering.append("end")
            elif cmd[0] == "schtasks.exe" and "/Run" in cmd:
                ordering.append("run")
            return 0, "", ""

        def fake_wait(_name: str) -> None:
            ordering.append("wait")

        monkeypatch.setattr(_main, "_run_with_timeout", fake_run_with_timeout)
        monkeypatch.setattr(
            _main, "_wait_for_windows_scheduled_task_ready", fake_wait
        )

        _main._restart_windows_scheduled_task("MobaideAgent")

        assert ordering == ["end", "wait", "run"], (
            f"_restart_windows_scheduled_task must run /End, then "
            f"_wait_for_windows_scheduled_task_ready, then /Run -- the "
            f"wait closes the schtasks tear-down race; got ordering "
            f"{ordering!r}"
        )


class TestRestartStopsLeftoverAgent:
    """``/End`` kills only the supervisor. With an older supervisor the
    launcher and agent survive it, keep the agent port and the credentials
    they booted with, and the instance ``/Run`` starts exits on the port
    clash -- after ``re-pair`` that left a QR no agent was waiting on.
    """

    def test_stops_leftover_processes_between_end_and_run(
        self, monkeypatch: pytest.MonkeyPatch, fake_processes: _FakeWindowsProcesses
    ) -> None:
        fake_processes.leftover = [10, 13]
        _silence_waits(monkeypatch)
        calls = _install_run_with_timeout_recorder(monkeypatch, lambda _cmd: (0, "", ""))
        fake_processes.events = calls

        _main._restart_windows_scheduled_task("MobaideAgent", Path(r"D:\agent"))

        install = os.path.abspath(Path(r"D:\agent"))
        assert calls == [
            ("inside", install),
            ["schtasks.exe", "/End", "/TN", "MobaideAgent"],
            ("leftover", install, os.getpid()),
            ("terminate", [10, 13]),
            ["schtasks.exe", "/Run", "/TN", "MobaideAgent"],
        ]

    def test_reports_how_many_processes_it_stopped(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        fake_processes: _FakeWindowsProcesses,
    ) -> None:
        fake_processes.leftover = [10, 13]
        _silence_waits(monkeypatch)
        _install_run_with_timeout_recorder(monkeypatch, lambda _cmd: (0, "", ""))

        _main._restart_windows_scheduled_task("MobaideAgent", Path(r"D:\agent"))

        assert "Stopped 2 leftover agent process(es)" in capsys.readouterr().out

    def test_nothing_left_over_prints_nothing_extra(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        fake_processes: _FakeWindowsProcesses,
    ) -> None:
        _silence_waits(monkeypatch)
        _install_run_with_timeout_recorder(monkeypatch, lambda _cmd: (0, "", ""))

        _main._restart_windows_scheduled_task("MobaideAgent")

        assert not any(e[0] == "terminate" for e in fake_processes.events)
        assert "leftover" not in capsys.readouterr().out

    def test_names_processes_that_would_not_stop_and_still_runs(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        fake_processes: _FakeWindowsProcesses,
    ) -> None:
        fake_processes.leftover = [10, 13]
        fake_processes.remaining = [13]
        _silence_waits(monkeypatch)
        calls = _install_run_with_timeout_recorder(monkeypatch, lambda _cmd: (0, "", ""))

        rc = _main._restart_windows_scheduled_task("MobaideAgent")

        assert rc == 0
        assert ["schtasks.exe", "/Run", "/TN", "MobaideAgent"] in calls
        out = capsys.readouterr().out
        assert "Could not stop leftover agent process(es) 13" in out
        assert "Stop-Process -Force -Id 13" in out

    def test_process_table_failure_does_not_block_the_restart(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        fake_processes: _FakeWindowsProcesses,
    ) -> None:
        fake_processes.list_error = OSError("snapshot failed")
        _silence_waits(monkeypatch)
        calls = _install_run_with_timeout_recorder(monkeypatch, lambda _cmd: (0, "", ""))

        rc = _main._restart_windows_scheduled_task("MobaideAgent")

        assert rc == 0
        assert calls == [
            ["schtasks.exe", "/End", "/TN", "MobaideAgent"],
            ["schtasks.exe", "/Run", "/TN", "MobaideAgent"],
        ]
        assert "snapshot failed" in capsys.readouterr().out

    def test_skips_restart_from_inside_an_agent_session(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        fake_processes: _FakeWindowsProcesses,
    ) -> None:
        # The supervisor's job takes the session -- and this command -- down
        # with the agent, so /End would leave nothing alive to issue /Run.
        fake_processes.inside = True
        _silence_waits(monkeypatch)
        calls = _install_run_with_timeout_recorder(monkeypatch, lambda _cmd: (0, "", ""))

        rc = _main._restart_windows_scheduled_task("MobaideAgent")

        assert rc == 0
        assert calls == []
        out = capsys.readouterr().out
        assert "Not restarting MobaideAgent" in out
        assert "outside Mobaide" in out

    def test_dispatcher_threads_the_install_prefix_through(
        self, monkeypatch: pytest.MonkeyPatch, fake_processes: _FakeWindowsProcesses
    ) -> None:
        _silence_waits(monkeypatch)
        _install_run_with_timeout_recorder(monkeypatch, lambda _cmd: (0, "", ""))
        monkeypatch.setattr(_main.sys, "platform", "win32")

        _main._try_restart_service(Path(r"D:\custom"))

        assert ("inside", os.path.abspath(Path(r"D:\custom"))) in fake_processes.events


class TestRestartWindowsScmService:
    """Legacy SCM service path used during the SCM -> Scheduled-Task
    upgrade window. Verifies the updated operator-facing strings: the
    success line must hint at re-running the installer to migrate, and
    the failure line must keep the legacy ``Restart-Service`` fallback
    so the operator has a copy-pasteable command even when the migration
    pathway is unavailable.
    """

    def test_failure_hint_preserves_restart_service_command(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Stop fails non-acceptably; failure path must still emit the
        # ``Restart-Service`` PowerShell command so operators stuck on
        # the legacy path (e.g. inside a corporate image that blocks
        # ``Register-ScheduledTask``) have a working manual fallback.
        def responder(cmd: list[str]) -> tuple[int, str, str]:
            if cmd[1] == "stop":
                return 5, "", "[SC] OpenService FAILED 5: Access is denied."
            return 0, "", ""

        _silence_waits(monkeypatch)
        _install_run_with_timeout_recorder(monkeypatch, responder)

        rc = _main._restart_windows_scm_service("MobaideAgent")

        assert rc == 0
        out = capsys.readouterr().out
        assert "Could not restart legacy service automatically" in out
        assert "Restart-Service MobaideAgent" in out, (
            f"legacy-failure hint must keep ``Restart-Service "
            f"MobaideAgent`` as the copy-paste fallback so operators on "
            f"a no-migrate image still have a working command; got: {out!r}"
        )

    def test_accepts_1062_stop_returncode_as_already_stopped(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # ``sc.exe stop`` on an already-stopped service returns 1062
        # (``the service has not been started``). The restart function
        # MUST treat that as success and proceed to start, otherwise the
        # restart of a manually-stopped service silently degrades to
        # "could not restart" instead of bringing the service back up.
        # 1062 is the only non-zero rc explicitly whitelisted by the
        # ``stop_acceptable`` predicate.
        def responder(cmd: list[str]) -> tuple[int, str, str]:
            if cmd[1] == "stop":
                return 1062, "", "[SC] ControlService FAILED 1062"
            return 0, "", ""

        _silence_waits(monkeypatch)
        _install_run_with_timeout_recorder(monkeypatch, responder)

        rc = _main._restart_windows_scm_service("MobaideAgent")

        assert rc == 0
        out = capsys.readouterr().out
        assert "Restarted legacy MobaideAgent service" in out, (
            f"stop rc=1062 (already stopped) must follow the success "
            f"path so an idempotent restart still brings the service "
            f"back up; got: {out!r}"
        )


class TestWaitForWindowsScheduledTaskReady:
    """The poll loop returns on the FIRST ``Status: Ready`` line found,
    returns silently if the task vanishes between checks (Query rc != 0),
    and respects the 10 s deadline so the wider restart flow cannot
    block indefinitely on a stuck Task Scheduler.
    """

    def test_returns_when_status_ready_in_output(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Smoking-gun positive: first Query call returns a ``Status:
        # Ready`` line; loop returns immediately without sleeping.
        sleep_calls: list[float] = []
        monkeypatch.setattr(_main.time, "sleep", sleep_calls.append)

        def fake_run_with_timeout(
            cmd: list[str], *, timeout: int = _main._AGENT_RESTART_TIMEOUT_S
        ) -> tuple[int, str, str]:
            stdout = (
                "TaskName: \\MobaideAgent\n"
                "Next Run Time: N/A\n"
                "Status: Ready\n"
                "Logon Mode: Interactive only\n"
            )
            return 0, stdout, ""

        monkeypatch.setattr(_main, "_run_with_timeout", fake_run_with_timeout)

        _main._wait_for_windows_scheduled_task_ready("MobaideAgent")

        # ``Status: Ready`` was hit on the first iteration so the loop
        # MUST NOT have slept. A regression that sleeps unconditionally
        # before checking the output would add ~0.5 s to every restart.
        assert sleep_calls == [], (
            f"_wait_for_windows_scheduled_task_ready must not sleep "
            f"after seeing ``Status: Ready`` on the first Query; got "
            f"sleep_calls={sleep_calls!r}"
        )

    def test_uses_query_with_verbose_list_format(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The Status line only appears in the /FO LIST /V output; the
        # default /FO TABLE format would emit a different header set
        # ("TaskName Next Run Time Status") that the line-by-line parser
        # would silently fail to match, never returning early and
        # bottoming out on the 10 s timeout.
        seen_argv: list[list[str]] = []

        def fake_run_with_timeout(
            cmd: list[str], *, timeout: int = _main._AGENT_RESTART_TIMEOUT_S
        ) -> tuple[int, str, str]:
            seen_argv.append(list(cmd))
            return 0, "Status: Ready\n", ""

        monkeypatch.setattr(_main, "_run_with_timeout", fake_run_with_timeout)
        monkeypatch.setattr(_main.time, "sleep", lambda _s: None)

        _main._wait_for_windows_scheduled_task_ready("MobaideAgent")

        assert seen_argv, "Query was never invoked"
        # The argv MUST include /FO LIST /V (in that order if present)
        # so the verbose output carrying the Status line is produced.
        cmd = seen_argv[0]
        assert "/FO" in cmd, (
            f"Query must specify /FO so the parser can rely on a known "
            f"output format; got {cmd!r}"
        )
        fo_idx = cmd.index("/FO")
        assert cmd[fo_idx + 1] == "LIST", (
            f"Query must use /FO LIST (the only format that emits "
            f"``Status: Ready`` line by line); got {cmd!r}"
        )
        assert "/V" in cmd, (
            f"Query must request verbose output (/V); ``Status:`` is a "
            f"verbose-only column. Got argv: {cmd!r}"
        )

    def test_returns_silently_when_query_returns_nonzero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # If the Query rc != 0 the task has vanished between the
        # dispatcher's probe and this poll (e.g. an operator
        # ``Unregister-ScheduledTask`` from another shell). The poll
        # MUST return immediately and silently rather than loop until
        # timeout -- the follow-up /Run will then fail and the caller's
        # admin-hint branch handles it.
        sleep_calls: list[float] = []
        query_count = [0]

        def fake_run_with_timeout(
            cmd: list[str], *, timeout: int = _main._AGENT_RESTART_TIMEOUT_S
        ) -> tuple[int, str, str]:
            query_count[0] += 1
            return 1, "", "ERROR: The system cannot find the file specified."

        monkeypatch.setattr(_main, "_run_with_timeout", fake_run_with_timeout)
        monkeypatch.setattr(_main.time, "sleep", sleep_calls.append)

        _main._wait_for_windows_scheduled_task_ready("MobaideAgent")

        assert query_count[0] == 1, (
            f"task-vanished path must exit after exactly one Query call; "
            f"a regression that retried on rc != 0 would block the "
            f"restart flow for the full 10s timeout. Got "
            f"{query_count[0]} Query calls"
        )
        assert sleep_calls == [], (
            f"task-vanished path must not sleep; got {sleep_calls!r}"
        )

    def test_returns_after_timeout_when_never_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Smoking-gun timeout: Query keeps returning rc 0 with no
        # ``Status: Ready`` line (e.g. task stuck in Running). The poll
        # MUST honour the 10 s ``_WINDOWS_STOP_POLL_TIMEOUT_S`` deadline
        # and return rather than spin forever -- otherwise a wedged
        # task would strand the entire pair-restart flow.
        #
        # Strategy: feed a monotonic clock that advances by 6s per call
        # so the deadline check trips on the second iteration. Two
        # iterations is enough to prove the function honours its
        # deadline without relying on real wall time.
        fake_now = [0.0]

        def fake_monotonic() -> float:
            return fake_now[0]

        def fake_sleep(_s: float) -> None:
            # Each sleep advances the fake clock by the poll interval
            # plus a fudge factor; that guarantees the next
            # ``time.monotonic() < deadline`` check fails by iteration 2.
            fake_now[0] += 6.0

        query_count = [0]

        def fake_run_with_timeout(
            cmd: list[str], *, timeout: int = _main._AGENT_RESTART_TIMEOUT_S
        ) -> tuple[int, str, str]:
            query_count[0] += 1
            # Never emit ``Status: Ready`` -- simulate a wedged task.
            return 0, "TaskName: \\MobaideAgent\nStatus: Running\n", ""

        monkeypatch.setattr(_main.time, "monotonic", fake_monotonic)
        monkeypatch.setattr(_main.time, "sleep", fake_sleep)
        monkeypatch.setattr(_main, "_run_with_timeout", fake_run_with_timeout)

        _main._wait_for_windows_scheduled_task_ready("MobaideAgent")

        # The exact iteration count is implementation-defined (depends
        # on the deadline arithmetic and the interval constant), but it
        # MUST be bounded -- 1 to ~20 iterations is the acceptable
        # range. A regression that ignored the deadline check entirely
        # would either hang forever (real wall clock) or loop a very
        # large number of times under this fake clock.
        assert 1 <= query_count[0] <= 20, (
            f"expected bounded number of Query iterations before the "
            f"_WINDOWS_STOP_POLL_TIMEOUT_S (10s) deadline trips; got "
            f"{query_count[0]} iterations -- either the deadline check "
            f"is missing (would loop forever) or the interval got "
            f"clamped to a near-zero value"
        )

    def test_ignores_running_status_lines_keeps_polling(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Realistic transition: first poll sees ``Status: Running``,
        # second poll sees ``Status: Ready``. The function MUST sleep
        # between them (not return early on the Running line). The
        # parser's ``startswith("Status:") and "Ready" in stripped``
        # contract is exactly this: it must not match
        # ``Status: Running`` even though the line starts with "Status:".
        statuses = ["Status: Running\n", "Status: Ready\n"]
        sleep_calls: list[float] = []

        def fake_run_with_timeout(
            cmd: list[str], *, timeout: int = _main._AGENT_RESTART_TIMEOUT_S
        ) -> tuple[int, str, str]:
            return 0, statuses.pop(0), ""

        monkeypatch.setattr(_main, "_run_with_timeout", fake_run_with_timeout)
        monkeypatch.setattr(_main.time, "sleep", sleep_calls.append)
        monkeypatch.setattr(_main.time, "monotonic", lambda: 0.0)

        _main._wait_for_windows_scheduled_task_ready("MobaideAgent")

        # Two calls (Running, then Ready) -> exactly one intervening
        # sleep before the Ready iteration returned.
        assert len(sleep_calls) == 1, (
            f"expected exactly 1 sleep between Running and Ready polls; "
            f"got sleep_calls={sleep_calls!r}. A regression that "
            f"matched on ``Status:`` line alone (without the ``Ready in "
            f"stripped`` second clause) would have returned on the "
            f"first Running line and skipped the sleep."
        )
        # Pin the interval at the documented poll constant so a drift
        # between the interval and the timeout is caught here.
        assert sleep_calls[0] == _main._WINDOWS_STOP_POLL_INTERVAL_S, (
            f"intra-poll sleep must equal _WINDOWS_STOP_POLL_INTERVAL_S "
            f"({_main._WINDOWS_STOP_POLL_INTERVAL_S}s); got "
            f"{sleep_calls[0]}s"
        )


class TestRunWithTimeoutHidesConsoleWindow:
    """Pin ``creationflags=_WINDOWS_NO_WINDOW`` on ``_run_with_timeout``.

    ``_run_with_timeout`` is the shared subprocess shim that every
    Windows restart helper funnels through (``schtasks.exe``,
    ``sc.exe``). The agent runs under ``pythonw.exe`` via the
    per-user Scheduled Task (no parent console). Without
    ``CREATE_NO_WINDOW``, each schtasks / sc invocation pops a
    transient console window into the user's session on every
    post-pair restart, ``fetch-trust-key``, and ``re-pair``
    invocation. The flag is OS-conditional via the module-level
    ``_WINDOWS_NO_WINDOW`` constant (``CREATE_NO_WINDOW`` on win32,
    ``0`` elsewhere).
    """

    def test_keeps_capture_output_alongside_creationflags(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The flag must be additive: ``capture_output=True`` MUST also
        remain in the kwargs.

        Switching to ``DETACHED_PROCESS`` instead of
        ``CREATE_NO_WINDOW`` would suppress the console but also sever
        stdio inheritance, breaking ``capture_output=True`` -- the
        callers (dispatcher branches) parse the stdout / stderr for
        ``Status: Ready`` / ``Status: Running`` lines, so losing
        capture_output silently produces empty strings and the polling
        helpers fall through to their timeout branch.
        """
        captured: dict[str, object] = {}

        def fake_run(cmd, **kwargs):
            captured["kwargs"] = dict(kwargs)
            return _DummyCompleted(stdout="ok\n")

        monkeypatch.setattr(_main.subprocess, "run", fake_run)

        _main._run_with_timeout(["sc.exe", "query", "MobaideAgent"])

        kwargs = captured["kwargs"]
        assert kwargs.get("capture_output") is True, (
            "_run_with_timeout must keep capture_output=True alongside "
            "the creationflags kwarg; dropping it would silently empty "
            f"the returned stdout/stderr. Got kwargs={kwargs!r}"
        )
        # text=True is the documented contract: the callers strip and
        # match string literals against the output.
        assert kwargs.get("text") is True, (
            "_run_with_timeout must keep text=True so the returned "
            "stdout/stderr are decoded strings (callers do `.strip()` "
            "and substring search on them); got kwargs={!r}".format(kwargs)
        )


class _DummyCompleted:
    """Minimal stand-in for ``subprocess.CompletedProcess`` for the
    ``_run_with_timeout`` shim's happy-path branch.

    The shim reads ``returncode``, ``stdout``, ``stderr`` off the
    returned object; ``SimpleNamespace`` would do too, but a tiny
    class is clearer about intent and lets the test surface a
    ``MissingAttribute`` error if the shim's contract drifts (e.g.
    starts reading ``args``).
    """

    def __init__(
        self,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
