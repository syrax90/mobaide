"""Tests for the one-alert rule when a chat and a terminal session show the
same Codex turn.

A Codex chat can live-attach to a thread a Codex TUI has open, and then both
sessions report every turn on it. :class:`SharedTurnDedupe` pairs them by
folder, so the matrix below is either a double notification or a lost one when
it answers wrongly. The clock is injected, so no test sleeps.
"""
from __future__ import annotations

import os

import pytest

from mobaide.core.attention_dedupe import (
    SHARED_TURN_WINDOW_SECONDS,
    SharedTurnDedupe,
    real_cwd,
)

CWD = "/work/repo"


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def dedupe(clock) -> SharedTurnDedupe:
    return SharedTurnDedupe(clock=clock)


def _admit(
    dedupe: SharedTurnDedupe,
    session_id: str,
    ui: str,
    *,
    app: str | None = "codex",
    cwd: str | None = CWD,
    reason: str = "turn_finished",
) -> bool:
    return dedupe.admit(session_id=session_id, ui=ui, app=app, cwd=cwd, reason=reason)


class TestSharedThread:
    @pytest.mark.parametrize("reason", ["turn_finished", "turn_failed", "awaiting_permission"])
    @pytest.mark.parametrize(("first", "second"), [("chat", "terminal"), ("terminal", "chat")])
    def test_the_other_ui_on_the_same_thread_is_suppressed(
        self, dedupe, clock, reason, first, second,
    ) -> None:
        assert _admit(dedupe, "s-1", first, reason=reason)
        clock.advance(1.0)
        assert not _admit(dedupe, "s-2", second, reason=reason)

    def test_two_terminals_in_one_folder_both_alert(self, dedupe) -> None:
        # Far more often two conversations than one thread opened twice.
        assert _admit(dedupe, "t-1", "terminal")
        assert _admit(dedupe, "t-2", "terminal")


    def test_a_session_never_suppresses_itself(self, dedupe) -> None:
        assert _admit(dedupe, "s-1", "chat")
        assert _admit(dedupe, "s-1", "chat")


    @pytest.mark.parametrize(
        ("first", "second"),
        [("turn_failed", "turn_finished"), ("turn_finished", "turn_failed")],
    )
    def test_a_failed_and_a_finished_turn_are_one_turn_end(
        self, dedupe, clock, first, second,
    ) -> None:
        # The chat sees the agent's error; the TUI may report the same turn
        # as plain ``done``.
        assert _admit(dedupe, "s-1", "chat", reason=first)
        clock.advance(1.0)
        assert not _admit(dedupe, "s-2", "terminal", reason=second)

    @pytest.mark.parametrize("turn_end", ["turn_finished", "turn_failed"])
    def test_a_permission_prompt_is_not_a_turn_end(self, dedupe, turn_end) -> None:
        assert _admit(dedupe, "c-1", "chat", reason="awaiting_permission")
        assert _admit(dedupe, "t-1", "terminal", reason=turn_end)
        assert _admit(dedupe, "c-2", "chat", reason=turn_end, cwd="/work/other")
        assert _admit(dedupe, "t-2", "terminal", reason="awaiting_permission", cwd="/work/other")


class TestWindow:
    def test_suppressed_just_inside_the_window(self, dedupe, clock) -> None:
        assert _admit(dedupe, "c-1", "chat")
        clock.advance(SHARED_TURN_WINDOW_SECONDS - 0.01)
        assert not _admit(dedupe, "t-1", "terminal")

    def test_admitted_once_the_window_has_passed(self, dedupe, clock) -> None:
        # The next turn on the thread is news again.
        assert _admit(dedupe, "c-1", "chat")
        clock.advance(SHARED_TURN_WINDOW_SECONDS)
        assert _admit(dedupe, "t-1", "terminal")

    def test_a_suppressed_alert_records_no_claim(self, dedupe, clock) -> None:
        # Had the suppressed terminal claimed the event, a chat a few seconds
        # later would be muted by an alert that never went out.
        assert _admit(dedupe, "c-1", "chat")
        clock.advance(5.0)
        assert not _admit(dedupe, "t-1", "terminal")
        clock.advance(1.0)
        assert _admit(dedupe, "c-2", "chat")
        assert not _admit(dedupe, "t-1", "terminal")


class TestOutOfScope:
    @pytest.mark.parametrize("app", ["claude", None])
    def test_apps_without_a_shared_thread_always_alert(self, dedupe, app) -> None:
        assert _admit(dedupe, "c-1", "chat", app=app)
        assert _admit(dedupe, "t-1", "terminal", app=app)

    @pytest.mark.parametrize("reason", ["ended", "awaiting_os_permission"])
    def test_reasons_only_one_side_reports_always_alert(self, dedupe, reason) -> None:
        assert _admit(dedupe, "c-1", "chat", reason=reason)
        assert _admit(dedupe, "t-1", "terminal", reason=reason)

    @pytest.mark.parametrize("cwd", [None, ""])
    def test_without_a_folder_nothing_pairs(self, dedupe, cwd) -> None:
        assert _admit(dedupe, "c-1", "chat", cwd=cwd)
        assert _admit(dedupe, "t-1", "terminal", cwd=cwd)


class TestRealCwd:
    def test_a_symlinked_folder_pairs_with_its_target(self, tmp_path) -> None:
        target = tmp_path / "repo"
        target.mkdir()
        link = tmp_path / "link"
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable")
        assert real_cwd(str(link)) == real_cwd(str(target))
