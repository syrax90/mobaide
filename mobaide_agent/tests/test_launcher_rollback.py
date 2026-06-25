"""Cover the launcher's crash-loop rollback heuristic.

The launcher is intentionally self-contained and import-light (it runs
across release directories and must survive a partially-staged release),
so the tests poke at its private helpers and at ``main()`` with
``_run_release`` monkeypatched into a capture function.

``_run_release`` is the single seam that hands control to the release
venv's Python — POSIX delegates to ``os.execv``, Windows to
``_spawn_and_wait_windows``. Monkeypatching ``_run_release`` covers
both code paths from a single test with no need to fake the actual
exec/subprocess machinery.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from mobaide.launcher import __main__ as launcher
from tests.conftest import make_release, release_names


def _set_current_version(prefix: Path, version: str) -> None:
    (prefix / "current_version").write_text(version + "\n", encoding="utf-8")


def _seed_journal(prefix: Path, entries: list[tuple[float, str]]) -> None:
    journal = prefix / launcher.JOURNAL_NAME
    journal.write_text(
        "".join(f"{ts:.3f} {v}\n" for ts, v in entries),
        encoding="utf-8",
    )


def test_crash_count_only_counts_matching_version_in_window():
    now = 1000.0
    entries = [
        (now - 200, "0.2.0"),  # outside window
        (now - 30, "0.1.0"),   # different version
        (now - 20, "0.2.0"),   # in window
        (now - 5, "0.2.0"),    # in window
    ]
    assert launcher._crash_count(entries, "0.2.0", now) == 2
    assert launcher._crash_count(entries, "0.1.0", now) == 1


def test_find_rollback_target_picks_highest_sibling(tmp_path):
    prefix = tmp_path
    make_release(prefix, "0.1.0")
    make_release(prefix, "0.1.5")
    make_release(prefix, "0.2.0")  # the failing one
    target = launcher._find_rollback_target(prefix, "0.2.0")
    assert target == "0.1.5"


def test_find_rollback_target_skips_release_without_python(tmp_path):
    prefix = tmp_path
    make_release(prefix, "0.1.0")
    # 0.1.5 directory exists but has no python — must be skipped.
    (prefix / "releases" / "0.1.5").mkdir(parents=True)
    target = launcher._find_rollback_target(prefix, "0.2.0")
    assert target == "0.1.0"


def test_find_rollback_target_skips_incomplete_release(tmp_path):
    """A sibling swapped into place but not yet finalized (no ``.installed``
    sentinel, no console script) has a runnable interpreter but must still be
    skipped — the launcher must never roll back into a half-built release."""
    prefix = tmp_path
    make_release(prefix, "0.1.0")
    make_release(prefix, "0.1.5", complete=False)  # higher, but unfinalized
    target = launcher._find_rollback_target(prefix, "0.2.0")
    assert target == "0.1.0"


def test_find_rollback_target_accepts_legacy_release_without_sentinel(tmp_path):
    """Upgrade compatibility: a release installed before the sentinel existed
    has no ``.installed`` but does have the agent console script (pip writes it
    only after a successful wheel install), so it reads as complete and remains
    a valid rollback target — otherwise an upgrade would strand it."""
    prefix = tmp_path
    legacy = make_release(prefix, "0.1.0", complete=False)
    bin_dir = legacy / "venv" / ("Scripts" if sys.platform == "win32" else "bin")
    script = bin_dir / (
        "mobaide-agent.exe" if sys.platform == "win32" else "mobaide-agent"
    )
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    assert launcher._find_rollback_target(prefix, "0.2.0") == "0.1.0"


def test_find_rollback_target_returns_none_when_alone(tmp_path):
    prefix = tmp_path
    make_release(prefix, "0.2.0")
    assert launcher._find_rollback_target(prefix, "0.2.0") is None


def test_find_rollback_target_skips_candidate_in_crash_loop(tmp_path):
    """A sibling release that itself has crossed the crash threshold
    in the same window must NOT be picked as a rollback target — that
    is the anti-oscillation guard."""
    prefix = tmp_path
    make_release(prefix, "0.1.0")
    make_release(prefix, "0.1.5")
    make_release(prefix, "0.2.0")
    now = 1000.0
    # 0.1.5 is also crash-looping in the window — must be excluded even
    # though it is the highest sibling. Falls through to 0.1.0.
    entries = [
        (now - 50, "0.1.5"),
        (now - 30, "0.1.5"),
        (now - 10, "0.1.5"),
    ]
    target = launcher._find_rollback_target(prefix, "0.2.0", entries, now)
    assert target == "0.1.0"


def test_find_rollback_target_returns_none_when_all_candidates_loop(tmp_path):
    prefix = tmp_path
    make_release(prefix, "0.1.0")
    make_release(prefix, "0.2.0")
    now = 1000.0
    # Both sibling releases are crash-looping — no viable rollback exists.
    entries = [
        (now - 50, "0.1.0"),
        (now - 30, "0.1.0"),
        (now - 10, "0.1.0"),
    ]
    target = launcher._find_rollback_target(prefix, "0.2.0", entries, now)
    assert target is None


def _install_fake_run_release(monkeypatch, captured: dict[str, list]) -> None:
    """Replace ``_run_release`` with a capture-and-exit stub.

    The fixture captures ``(python, args)`` exactly like the
    pre-refactor ``os.execv`` capture did. Tests assert on
    ``captured["argv"][-1]`` and read ``captured`` as before, so the
    refactor is transparent to test assertions.
    """

    def fake_run_release(python: Path, args: list[str]) -> None:
        captured["argv"].append((str(python), list(args)))
        raise SystemExit(0)

    monkeypatch.setattr(launcher, "_run_release", fake_run_release)


def test_main_rolls_back_after_three_crashes(tmp_path, monkeypatch):
    prefix = tmp_path
    make_release(prefix, "0.1.0")
    failing_release = make_release(prefix, "0.2.0")
    _set_current_version(prefix, "0.2.0")
    # Two stale journal entries — main() will write the third and trip
    # the threshold.
    import time as _time

    now = _time.time()
    _seed_journal(prefix, [(now - 30, "0.2.0"), (now - 10, "0.2.0")])

    monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(prefix))

    captured: dict[str, list] = {"argv": []}
    _install_fake_run_release(monkeypatch, captured)
    monkeypatch.setattr(sys, "argv", [str(failing_release / "venv" / "bin" / "python")])

    with pytest.raises(SystemExit) as excinfo:
        launcher.main()
    assert excinfo.value.code == 0

    assert (prefix / "current_version").read_text(encoding="utf-8").strip() == "0.1.0"
    assert captured["argv"], "launcher must exec into the rolled-back release"
    exec_path, _ = captured["argv"][-1]
    assert "0.1.0" in exec_path


def test_main_passes_through_when_no_crash_loop(tmp_path, monkeypatch):
    prefix = tmp_path
    make_release(prefix, "0.1.0")
    make_release(prefix, "0.2.0")
    _set_current_version(prefix, "0.2.0")

    monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(prefix))
    captured: dict[str, list] = {"argv": []}
    _install_fake_run_release(monkeypatch, captured)
    monkeypatch.setattr(sys, "argv", ["launcher"])

    with pytest.raises(SystemExit):
        launcher.main()

    assert (prefix / "current_version").read_text(encoding="utf-8").strip() == "0.2.0"
    exec_path, _ = captured["argv"][-1]
    assert "0.2.0" in exec_path


def test_main_prunes_old_releases_on_normal_boot(tmp_path, monkeypatch):
    """A normal boot execs the current release AND garbage-collects releases
    older than the retained set (current + rollback target)."""
    prefix = tmp_path
    make_release(prefix, "0.1.0")
    make_release(prefix, "0.1.5")
    make_release(prefix, "0.2.0")
    _set_current_version(prefix, "0.2.0")

    monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(prefix))
    captured: dict[str, list] = {"argv": []}
    _install_fake_run_release(monkeypatch, captured)
    monkeypatch.setattr(sys, "argv", ["launcher"])

    with pytest.raises(SystemExit):
        launcher.main()

    exec_path, _ = captured["argv"][-1]
    assert "0.2.0" in exec_path
    names = release_names(prefix)
    assert names == {"0.2.0", "0.1.5"}


def test_main_prune_keeps_rolled_back_release(tmp_path, monkeypatch):
    """After a crash-loop rollback, prune runs against the post-rollback
    version: it must keep the release just rolled back to (now current) plus
    the rollback target, and drop only the stale older release."""
    prefix = tmp_path
    make_release(prefix, "0.1.0")
    make_release(prefix, "0.1.5")
    make_release(prefix, "0.2.0")
    _set_current_version(prefix, "0.2.0")
    import time as _time

    now = _time.time()
    _seed_journal(prefix, [(now - 30, "0.2.0"), (now - 10, "0.2.0")])

    monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(prefix))
    captured: dict[str, list] = {"argv": []}
    _install_fake_run_release(monkeypatch, captured)
    monkeypatch.setattr(sys, "argv", ["launcher"])

    with pytest.raises(SystemExit):
        launcher.main()

    assert (prefix / "current_version").read_text(encoding="utf-8").strip() == "0.1.5"
    exec_path, _ = captured["argv"][-1]
    assert "0.1.5" in exec_path
    names = release_names(prefix)
    assert names == {"0.1.5", "0.2.0"}


def test_main_no_rollback_target_falls_through(tmp_path, monkeypatch):
    """Only one release exists; rollback is impossible — main() still
    execs the failing release and lets the supervisor's hard limit take
    over."""
    prefix = tmp_path
    failing_release = make_release(prefix, "0.2.0")
    _set_current_version(prefix, "0.2.0")
    import time as _time

    now = _time.time()
    _seed_journal(prefix, [(now - 30, "0.2.0"), (now - 10, "0.2.0")])

    monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(prefix))
    captured: dict[str, list] = {"argv": []}
    _install_fake_run_release(monkeypatch, captured)
    monkeypatch.setattr(sys, "argv", ["launcher"])

    with pytest.raises(SystemExit):
        launcher.main()

    # current_version unchanged because no rollback target was found.
    assert (prefix / "current_version").read_text(encoding="utf-8").strip() == "0.2.0"
    exec_path, _ = captured["argv"][-1]
    assert str(failing_release) in exec_path


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only code path")
def test_run_release_routes_through_spawn_and_wait_on_windows(monkeypatch):
    """``_run_release`` must hand off to ``_spawn_and_wait_windows`` on
    Windows rather than calling ``os.execv``.

    The launcher's pre-fix ``os.execv`` did not preserve PID on
    Windows (Python issue #9148): the service host's
    ``Popen.poll()`` would see the launcher exit and mark the
    service STOPPED while the agent ran orphaned. The Windows
    branch must therefore NOT call ``os.execv``.
    """
    captured: dict[str, list] = {"argv": []}

    def fake_spawn(args: list[str]) -> None:
        captured["argv"].append(("spawn", list(args)))
        raise SystemExit(0)

    def fake_execv(path: str, args: list[str]) -> None:
        captured["argv"].append(("execv", path, list(args)))

    monkeypatch.setattr(launcher, "_spawn_and_wait_windows", fake_spawn)
    monkeypatch.setattr(launcher.os, "execv", fake_execv)

    with pytest.raises(SystemExit):
        launcher._run_release(Path(r"C:\fake\python.exe"), ["arg0", "arg1"])

    assert captured["argv"], "expected the Windows branch to fire"
    assert captured["argv"][-1][0] == "spawn", (
        "Windows must route through _spawn_and_wait_windows, "
        "not os.execv — that's the whole point of the refactor "
        "(see launcher/__main__.py module docstring)"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only code path")
def test_spawn_and_wait_windows_attaches_job_object(monkeypatch):
    """Happy path: ``_spawn_and_wait_windows`` creates a Job Object
    with ``KILL_ON_JOB_CLOSE``, ``Popen``s the agent with
    ``close_fds=True``, assigns the agent to the job, then waits.

    Order matters: the job must be set up BEFORE the wait so the
    KILL_ON_JOB_CLOSE invariant is in place if the launcher gets
    terminated during ``proc.wait()``.
    """
    import subprocess as _subprocess

    calls: list[tuple[str, tuple]] = []

    class _FakeKernel32:
        CreateJobObjectW = staticmethod(
            lambda *a: (calls.append(("CreateJobObjectW", a)), 0xDEADBEEF)[1]
        )
        SetInformationJobObject = staticmethod(
            lambda *a: (calls.append(("SetInformationJobObject", a)), 1)[1]
        )
        OpenProcess = staticmethod(
            lambda *a: (calls.append(("OpenProcess", a)), 0xCAFEBABE)[1]
        )
        AssignProcessToJobObject = staticmethod(
            lambda *a: (calls.append(("AssignProcessToJobObject", a)), 1)[1]
        )
        CloseHandle = staticmethod(
            lambda *a: (calls.append(("CloseHandle", a)), 1)[1]
        )

    fake = _FakeKernel32()
    # Restype assignments need to no-op (lambdas above don't have
    # .restype); attach a dummy attribute the production code can set.
    for name in (
        "CreateJobObjectW",
        "SetInformationJobObject",
        "OpenProcess",
        "AssignProcessToJobObject",
        "CloseHandle",
    ):
        setattr(getattr(fake, name), "restype", None)  # type: ignore[attr-defined]

    import ctypes as _ctypes

    monkeypatch.setattr(_ctypes, "WinDLL", lambda *a, **k: fake)

    class _FakePopen:
        def __init__(self, args, **kwargs):
            calls.append(("Popen", (tuple(args), kwargs)))
            self.pid = 4242
            self._wait_rc = 0

        def wait(self):
            calls.append(("wait", ()))
            return self._wait_rc

    monkeypatch.setattr(_subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(launcher.subprocess, "Popen", _FakePopen)

    with pytest.raises(SystemExit) as excinfo:
        launcher._spawn_and_wait_windows(["python.exe", "-m", "mobaide", "run"])
    assert excinfo.value.code == 0

    op_sequence = [c[0] for c in calls]
    assert op_sequence[0] == "CreateJobObjectW"
    assert op_sequence[1] == "SetInformationJobObject"
    assert "Popen" in op_sequence
    assert "AssignProcessToJobObject" in op_sequence
    assert op_sequence.index("Popen") < op_sequence.index(
        "AssignProcessToJobObject"
    ), "agent must be spawned before assigning to job"
    assert op_sequence.index("AssignProcessToJobObject") < op_sequence.index(
        "wait"
    ), "job assignment must complete before wait (KILL_ON_JOB_CLOSE invariant)"

    popen_call = next(c for c in calls if c[0] == "Popen")
    popen_kwargs = popen_call[1][1]
    assert popen_kwargs.get("close_fds") is True
    flags = popen_kwargs.get("creationflags", 0)
    assert not (flags & _subprocess.CREATE_NEW_PROCESS_GROUP), (
        "CREATE_NEW_PROCESS_GROUP is incompatible with the "
        "KILL_ON_JOB_CLOSE teardown semantics — must not be set"
    )
    assert flags & _subprocess.CREATE_NO_WINDOW, (
        "CREATE_NO_WINDOW must be set so the agent process does not "
        "allocate a console window when the supervisor runs under "
        "pythonw.exe via the per-user Scheduled Task"
    )

    set_info_call = next(c for c in calls if c[0] == "SetInformationJobObject")
    job_info_class = set_info_call[1][1]
    assert job_info_class == launcher._JOB_INFO_CLASS_EXTENDED_LIMIT, (
        "must use JobObjectExtendedLimitInformation (=9) — the basic "
        "class lacks the kill-on-close limit flag"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only code path")
def test_spawn_and_wait_windows_falls_back_when_job_create_fails(monkeypatch):
    """If ``CreateJobObjectW`` returns 0 (failure), the function
    must still ``Popen`` + ``wait`` and propagate the exit code —
    the primary PID-preservation fix takes priority over the
    secondary clean-teardown guarantee.

    Production assigns ``.restype`` on all five kernel32 functions
    before the ``if job_handle`` guard, so the fake exposes every
    one even though only ``CreateJobObjectW`` is logically invoked
    in this branch (see ``_install_full_kernel32_fake`` docstring).
    """
    calls: list[tuple[str, tuple]] = []
    _install_full_kernel32_fake(monkeypatch, calls, create_job_handle=0)
    _install_fake_popen(monkeypatch, calls, rc=7)

    with pytest.raises(SystemExit) as excinfo:
        launcher._spawn_and_wait_windows(["python.exe", "-m", "mobaide", "run"])
    assert excinfo.value.code == 7
    op_sequence = [c[0] for c in calls]
    assert "Popen" in op_sequence and "wait" in op_sequence
    assert "AssignProcessToJobObject" not in op_sequence


def _install_full_kernel32_fake(
    monkeypatch,
    calls: list[tuple[str, tuple]],
    *,
    create_job_handle: int = 0xDEADBEEF,
    set_info_rc: int = 1,
    open_process_handle: int = 0xCAFEBABE,
    assign_rc: int = 1,
):
    """Install a kernel32 fake exposing all five Win32 functions the
    production code touches.

    The production ``_spawn_and_wait_windows`` body assigns ``.restype``
    on five functions BEFORE any conditional branching, so every
    function name must exist on the fake (as an attribute that supports
    ``.restype`` assignment) regardless of which call path the test
    targets. A fake that only stubs ``CreateJobObjectW`` crashes on
    the next ``.restype`` line — every failure-mode test in this
    file shares this fixture to avoid that layout trap.
    """
    import ctypes as _ctypes

    class _FakeKernel32:
        CreateJobObjectW = staticmethod(
            lambda *a: (
                calls.append(("CreateJobObjectW", a)),
                create_job_handle,
            )[1]
        )
        SetInformationJobObject = staticmethod(
            lambda *a: (
                calls.append(("SetInformationJobObject", a)),
                set_info_rc,
            )[1]
        )
        OpenProcess = staticmethod(
            lambda *a: (
                calls.append(("OpenProcess", a)),
                open_process_handle,
            )[1]
        )
        AssignProcessToJobObject = staticmethod(
            lambda *a: (
                calls.append(("AssignProcessToJobObject", a)),
                assign_rc,
            )[1]
        )
        CloseHandle = staticmethod(
            lambda *a: (calls.append(("CloseHandle", a)), 1)[1]
        )

    fake = _FakeKernel32()
    for name in (
        "CreateJobObjectW",
        "SetInformationJobObject",
        "OpenProcess",
        "AssignProcessToJobObject",
        "CloseHandle",
    ):
        setattr(getattr(fake, name), "restype", None)  # type: ignore[attr-defined]

    monkeypatch.setattr(_ctypes, "WinDLL", lambda *a, **k: fake)
    return fake


def _install_fake_popen(monkeypatch, calls: list, *, rc: int = 0):
    """Install a minimal ``subprocess.Popen`` fake on both the local
    ``subprocess`` reference and the launcher's bound reference.

    Two patches are required because the production module did
    ``import subprocess`` (binding the module to ``launcher.subprocess``)
    AND uses it through that binding inside the function — patching only
    the global ``subprocess`` module's ``Popen`` attribute misses the
    second site on some import orderings.
    """
    import subprocess as _subprocess

    class _FakePopen:
        def __init__(self, args, **kwargs):
            calls.append(("Popen", (tuple(args), kwargs)))
            self.pid = 4242
            self._rc = rc

        def wait(self):
            calls.append(("wait", ()))
            return self._rc

        def terminate(self):
            calls.append(("terminate", ()))

    monkeypatch.setattr(_subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(launcher.subprocess, "Popen", _FakePopen)
    return _FakePopen


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only code path")
def test_spawn_and_wait_windows_sets_kill_on_job_close_limit_flag(monkeypatch):
    """The job-object configuration must include
    ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` (``0x2000``) in
    ``LimitFlags``.

    This is the load-bearing flag of the entire Windows refactor:
    without it the agent process survives the launcher's
    ``TerminateProcess`` and lingers as an orphan holding port 8000
    on the next SCM restart attempt (the user-visible symptom of
    the original install timeout). The existing
    ``test_spawn_and_wait_windows_attaches_job_object`` only checks
    the ``JobObjectExtendedLimitInformation`` *class* arg (=9), not
    the actual flag value — so a future regression that swaps the
    constant for ``JOB_OBJECT_LIMIT_BREAKAWAY_OK`` (0x800) or any
    other value would slip through. This test reads the
    ``LimitFlags`` field out of the byref'd struct at the call site
    via ``ctypes.cast`` to a mirror layout.
    """
    import ctypes as _ctypes
    from ctypes import wintypes

    class _BasicLimitMirror(_ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", _ctypes.c_int64),
            ("PerJobUserTimeLimit", _ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
        ]

    captured_flags: dict[str, int] = {}

    def _capture_set_info(*args):
        if len(args) >= 3:
            byref_arg = args[2]
            try:
                addr = _ctypes.cast(byref_arg, _ctypes.c_void_p).value
                if addr is not None:
                    mirror = _ctypes.cast(
                        addr, _ctypes.POINTER(_BasicLimitMirror)
                    )
                    captured_flags["LimitFlags"] = int(mirror.contents.LimitFlags)
            except (ValueError, _ctypes.ArgumentError):
                pass
        return 1

    calls: list[tuple[str, tuple]] = []
    fake = _install_full_kernel32_fake(monkeypatch, calls)
    fake.SetInformationJobObject = staticmethod(_capture_set_info)
    setattr(
        fake.SetInformationJobObject, "restype", None
    )  # type: ignore[attr-defined]

    _install_fake_popen(monkeypatch, calls, rc=0)

    with pytest.raises(SystemExit):
        launcher._spawn_and_wait_windows(["python.exe", "-m", "mobaide", "run"])

    assert "LimitFlags" in captured_flags, (
        "test could not read LimitFlags from the byref'd struct — the "
        "production code did not call SetInformationJobObject with the "
        "expected (handle, class, byref, size) signature"
    )
    flags = captured_flags["LimitFlags"]
    assert flags & 0x2000, (
        f"LimitFlags=0x{flags:08x} missing JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE "
        f"(0x2000) — without this flag the agent will survive launcher "
        f"termination and orphan-bind port 8000 on the next restart "
        f"(see mobaide/launcher/__main__.py:_spawn_and_wait_windows "
        f"docstring for the exact failure chain)"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only code path")
def test_spawn_and_wait_windows_skips_assign_when_set_information_fails(
    monkeypatch,
):
    """``SetInformationJobObject`` returning 0 (failure) must trigger
    the fail-soft path: close the job handle, skip job assignment,
    still ``Popen`` + ``wait`` and propagate the exit code.

    Pins the docstring's fail-soft contract — "if any ctypes call
    fails …, skip the job assignment and still ``Popen``-and-``wait``
    so the primary PID-preservation fix holds even in degraded-teardown
    mode". A regression that hard-aborted on SetInfo failure would
    leave a Windows-only configuration (restrictive job policy, older
    Windows with reduced job-object support) unable to start the
    agent at all, defeating the primary PID-preservation purpose of
    the refactor.
    """
    calls: list[tuple[str, tuple]] = []
    _install_full_kernel32_fake(monkeypatch, calls, set_info_rc=0)
    _install_fake_popen(monkeypatch, calls, rc=11)

    with pytest.raises(SystemExit) as excinfo:
        launcher._spawn_and_wait_windows(["python.exe", "-m", "mobaide", "run"])
    assert excinfo.value.code == 11

    op_sequence = [c[0] for c in calls]
    assert "CreateJobObjectW" in op_sequence
    assert "SetInformationJobObject" in op_sequence
    assert "CloseHandle" in op_sequence, (
        "failed SetInformationJobObject must be followed by "
        "CloseHandle(job) — otherwise the launcher leaks the kernel "
        "handle for its lifetime"
    )
    assert "Popen" in op_sequence and "wait" in op_sequence
    assert "AssignProcessToJobObject" not in op_sequence, (
        "after SetInformationJobObject failure the job handle is "
        "torn down; assigning the agent to a destroyed job is "
        "undefined behaviour and must not be attempted"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only code path")
def test_spawn_and_wait_windows_skips_assign_when_open_process_fails(
    monkeypatch,
):
    """``OpenProcess`` returning 0 (failure) — the agent PID is alive
    but unreachable for the SET_QUOTA|TERMINATE handle the job
    assignment requires — must NOT crash the launcher. ``Popen`` +
    ``wait`` still run, with the job assignment skipped.

    A Windows policy that strips PROCESS_SET_QUOTA from the launcher's
    primary token (some hardened SCM contexts) would land here.
    Without this guard the agent would never come up under those
    conditions.
    """
    calls: list[tuple[str, tuple]] = []
    _install_full_kernel32_fake(monkeypatch, calls, open_process_handle=0)
    _install_fake_popen(monkeypatch, calls, rc=0)

    with pytest.raises(SystemExit) as excinfo:
        launcher._spawn_and_wait_windows(["python.exe", "-m", "mobaide", "run"])
    assert excinfo.value.code == 0

    op_sequence = [c[0] for c in calls]
    assert "OpenProcess" in op_sequence
    assert "Popen" in op_sequence and "wait" in op_sequence
    assert "AssignProcessToJobObject" not in op_sequence, (
        "OpenProcess(0) means no valid child handle is available; "
        "AssignProcessToJobObject(job, 0) would corrupt the job's "
        "state"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only code path")
def test_spawn_and_wait_windows_propagates_nonzero_exit_code(monkeypatch):
    """On the happy path with a non-zero ``proc.wait()`` return code,
    ``_spawn_and_wait_windows`` must ``sys.exit`` with that exact code.

    The Scheduled-Task supervisor (``deploy/windows_launcher.py``)
    reads ``proc.returncode`` after the wait and uses it both to
    decide whether to respawn (exit 75 → in-process respawn) and to
    propagate the exit code to Task Scheduler (everything else).
    Task Scheduler's restart-on-failure policy keys off non-zero
    exits. Swallowing or re-mapping the code (e.g. to 1 or to 0)
    would mask the underlying crash from both the operator and Task
    Scheduler.
    """
    calls: list[tuple[str, tuple]] = []
    _install_full_kernel32_fake(monkeypatch, calls)
    _install_fake_popen(monkeypatch, calls, rc=42)

    with pytest.raises(SystemExit) as excinfo:
        launcher._spawn_and_wait_windows(["python.exe", "-m", "mobaide", "run"])
    assert excinfo.value.code == 42, (
        "agent exit code must be propagated verbatim so Task "
        "Scheduler's restart-on-failure policy and the supervisor's "
        "exit-75 vs. other-code branching see the real failure "
        "code, not a synthesised one"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only code path")
def test_spawn_and_wait_windows_handles_keyboard_interrupt_during_wait(
    monkeypatch,
):
    """If the launcher receives ``KeyboardInterrupt`` while waiting on
    the agent (e.g. console-attached debug session), it must
    ``terminate()`` the agent and re-``wait()`` to reap before
    exiting — never leave the agent running as an orphan.

    Without the second wait, ``terminate()`` returns immediately but
    the agent process is still in transit (its handle hasn't fully
    closed), so the job's KILL_ON_JOB_CLOSE wouldn't fire reliably and
    the launcher would exit before the agent dies. The re-wait
    enforces synchronous teardown.
    """
    import subprocess as _subprocess

    calls: list[tuple[str, tuple]] = []
    _install_full_kernel32_fake(monkeypatch, calls)

    class _InterruptThenExit:
        def __init__(self, args, **kwargs):
            calls.append(("Popen", (tuple(args), kwargs)))
            self.pid = 4242
            self._wait_count = 0

        def wait(self):
            self._wait_count += 1
            calls.append(("wait", (self._wait_count,)))
            if self._wait_count == 1:
                raise KeyboardInterrupt
            return 130

        def terminate(self):
            calls.append(("terminate", ()))

    monkeypatch.setattr(_subprocess, "Popen", _InterruptThenExit)
    monkeypatch.setattr(launcher.subprocess, "Popen", _InterruptThenExit)

    with pytest.raises(SystemExit) as excinfo:
        launcher._spawn_and_wait_windows(["python.exe", "-m", "mobaide", "run"])
    assert excinfo.value.code == 130

    op_sequence = [c[0] for c in calls]
    wait_idx = op_sequence.index("wait")
    term_idx = op_sequence.index("terminate")
    second_wait_idx = op_sequence.index("wait", wait_idx + 1)
    assert wait_idx < term_idx < second_wait_idx, (
        "KeyboardInterrupt handling must be wait -> terminate -> wait, "
        f"got call order {op_sequence}"
    )
