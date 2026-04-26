"""Cover the launcher's crash-loop rollback heuristic.

The launcher is intentionally self-contained and import-light (it runs
across release directories and must survive a partially-staged release),
so the tests poke at its private helpers and at ``main()`` with
``os.execv`` monkeypatched into a capture function.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from claude_mobile.launcher import __main__ as launcher


def _make_release(prefix: Path, version: str) -> Path:
    release = prefix / "releases" / version
    if sys.platform == "win32":
        bin_dir = release / "venv" / "Scripts"
        python = bin_dir / "python.exe"
    else:
        bin_dir = release / "venv" / "bin"
        python = bin_dir / "python"
    bin_dir.mkdir(parents=True, exist_ok=True)
    python.write_text("#!/bin/sh\nexec /bin/true\n", encoding="utf-8")
    return release


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
    _make_release(prefix, "0.1.0")
    _make_release(prefix, "0.1.5")
    _make_release(prefix, "0.2.0")  # the failing one
    target = launcher._find_rollback_target(prefix, "0.2.0")
    assert target == "0.1.5"


def test_find_rollback_target_skips_release_without_python(tmp_path):
    prefix = tmp_path
    _make_release(prefix, "0.1.0")
    # 0.1.5 directory exists but has no python — must be skipped.
    (prefix / "releases" / "0.1.5").mkdir(parents=True)
    target = launcher._find_rollback_target(prefix, "0.2.0")
    assert target == "0.1.0"


def test_find_rollback_target_returns_none_when_alone(tmp_path):
    prefix = tmp_path
    _make_release(prefix, "0.2.0")
    assert launcher._find_rollback_target(prefix, "0.2.0") is None


def test_find_rollback_target_skips_candidate_in_crash_loop(tmp_path):
    """A sibling release that itself has crossed the crash threshold
    in the same window must NOT be picked as a rollback target — that
    is the anti-oscillation guard."""
    prefix = tmp_path
    _make_release(prefix, "0.1.0")
    _make_release(prefix, "0.1.5")
    _make_release(prefix, "0.2.0")
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
    _make_release(prefix, "0.1.0")
    _make_release(prefix, "0.2.0")
    now = 1000.0
    # Both sibling releases are crash-looping — no viable rollback exists.
    entries = [
        (now - 50, "0.1.0"),
        (now - 30, "0.1.0"),
        (now - 10, "0.1.0"),
    ]
    target = launcher._find_rollback_target(prefix, "0.2.0", entries, now)
    assert target is None


def test_main_rolls_back_after_three_crashes(tmp_path, monkeypatch):
    prefix = tmp_path
    _make_release(prefix, "0.1.0")
    failing_release = _make_release(prefix, "0.2.0")
    _set_current_version(prefix, "0.2.0")
    # Two stale journal entries — main() will write the third and trip
    # the threshold.
    import time as _time

    now = _time.time()
    _seed_journal(prefix, [(now - 30, "0.2.0"), (now - 10, "0.2.0")])

    monkeypatch.setenv("CLAUDE_AGENT_PREFIX", str(prefix))

    captured: dict[str, list] = {"argv": []}

    def fake_execv(path: str, args: list[str]) -> None:
        captured["argv"].append((path, list(args)))
        raise SystemExit(0)

    monkeypatch.setattr(launcher.os, "execv", fake_execv)
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
    _make_release(prefix, "0.1.0")
    _make_release(prefix, "0.2.0")
    _set_current_version(prefix, "0.2.0")

    monkeypatch.setenv("CLAUDE_AGENT_PREFIX", str(prefix))
    captured: dict[str, list] = {"argv": []}

    def fake_execv(path: str, args: list[str]) -> None:
        captured["argv"].append((path, list(args)))
        raise SystemExit(0)

    monkeypatch.setattr(launcher.os, "execv", fake_execv)
    monkeypatch.setattr(sys, "argv", ["launcher"])

    with pytest.raises(SystemExit):
        launcher.main()

    assert (prefix / "current_version").read_text(encoding="utf-8").strip() == "0.2.0"
    exec_path, _ = captured["argv"][-1]
    assert "0.2.0" in exec_path


def test_main_no_rollback_target_falls_through(tmp_path, monkeypatch):
    """Only one release exists; rollback is impossible — main() still
    execs the failing release and lets the supervisor's hard limit take
    over."""
    prefix = tmp_path
    failing_release = _make_release(prefix, "0.2.0")
    _set_current_version(prefix, "0.2.0")
    import time as _time

    now = _time.time()
    _seed_journal(prefix, [(now - 30, "0.2.0"), (now - 10, "0.2.0")])

    monkeypatch.setenv("CLAUDE_AGENT_PREFIX", str(prefix))
    captured: dict[str, list] = {"argv": []}

    def fake_execv(path: str, args: list[str]) -> None:
        captured["argv"].append((path, list(args)))
        raise SystemExit(0)

    monkeypatch.setattr(launcher.os, "execv", fake_execv)
    monkeypatch.setattr(sys, "argv", ["launcher"])

    with pytest.raises(SystemExit):
        launcher.main()

    # current_version unchanged because no rollback target was found.
    assert (prefix / "current_version").read_text(encoding="utf-8").strip() == "0.2.0"
    exec_path, _ = captured["argv"][-1]
    assert str(failing_release) in exec_path
