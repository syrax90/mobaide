"""Tests for the OSC 7501 program-status reports a CLI writes to its terminal.

Every terminal session without Claude Code hooks raises attention from these
reports, so each rule below is either a missed "your move" or a notification
nobody asked for when it answers wrongly. The parser and tap decide which bytes
are a report; the responder answers the support query the CLIs that ask first
send; the gate turns a stream of states into at most one alert per event.

The gate arms its quiet window with ``loop.call_later``. As in
``test_attention_gate.py``, ``_Scheduler`` replaces ``call_later`` so a window
elapses exactly when a test says so, and no test sleeps. ``_Clock`` stands in
for the monotonic clock the gate spaces its alerts by.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from mobaide.core.program_status import (
    MAX_REPORT_BYTES,
    PROGRAM_STATUS_MIN_ALERT_SPACING_SECONDS,
    PROGRAM_STATUS_QUIET_SECONDS,
    ProgramState,
    ProgramStatus,
    ProgramStatusGate,
    ProgramStatusResponder,
    ProgramStatusTap,
    parse_program_status,
)

BEL = b"\x07"
ST = b"\x1b\\"
QUERY_BEL = b"\x1b]7501;?\x07"
QUERY_ST = b"\x1b]7501;?\x1b\\"
# The spec's long-form answer. It is not the query, so a tty that echoes it
# back can never hand it in again as a new query.
REPLY_BODY = (
    b"\x1b]7501;?:states=idle,working,done,blocked,error:kinds=permission,question,auth"
)
REPLY_BEL = REPLY_BODY + BEL
REPLY_ST = REPLY_BODY + ST


def _report(body: bytes, terminator: bytes = BEL) -> bytes:
    return b"\x1b]7501;" + body + terminator


class TestParse:
    def test_reads_state_kind_and_app(self) -> None:
        assert parse_program_status(b"state=blocked:kind=permission:app=codex") == (
            ProgramStatus(ProgramState.BLOCKED, "permission", "codex")
        )

    @pytest.mark.parametrize("state", list(ProgramState))
    def test_every_state_parses(self, state) -> None:
        assert parse_program_status(f"state={state.value}".encode()) == (
            ProgramStatus(state)
        )

    @pytest.mark.parametrize(
        "payload",
        [b"", b"?", b"app=codex", b"state=", b"state=paused:app=codex"],
    )
    def test_a_report_without_a_known_state_is_ignored(self, payload) -> None:
        # ``?`` is the support query's body: it must never read as a report.
        assert parse_program_status(payload) is None

    def test_a_sub_record_is_ignored(self) -> None:
        # Only the root record describes the program; a sub-agent's ``done``
        # must not announce the whole turn.
        assert parse_program_status(b"state=done:id=task-1:app=codex") is None

    @pytest.mark.parametrize("kind", ["permission", "question", "auth"])
    def test_blocked_keeps_a_known_kind(self, kind) -> None:
        status = parse_program_status(f"state=blocked:kind={kind}".encode())
        assert status is not None and status.kind == kind

    def test_blocked_drops_an_unknown_kind(self) -> None:
        status = parse_program_status(b"state=blocked:kind=coffee")
        assert status == ProgramStatus(ProgramState.BLOCKED, None, None)

    def test_kind_only_qualifies_blocked(self) -> None:
        status = parse_program_status(b"state=done:kind=permission")
        assert status == ProgramStatus(ProgramState.DONE, None, None)

    @pytest.mark.parametrize(
        "payload",
        [
            b"state=done:garbage:app=codex",
            b"state=done:APP=claude:app=codex",
            b"state=done:app=co dex:app=codex",
            b"state=done:=x:app=codex",
        ],
    )
    def test_a_malformed_pair_is_skipped(self, payload) -> None:
        assert parse_program_status(payload) == (
            ProgramStatus(ProgramState.DONE, None, "codex")
        )

    def test_a_body_over_the_limit_is_discarded(self) -> None:
        body = b"state=done:msg=" + b"A" * MAX_REPORT_BYTES
        assert parse_program_status(body) is None

    def test_agy_base64_message_does_not_spoil_the_report(self) -> None:
        # agy appends ``msg=<base64>``; ``=`` padding and ``+``/``/`` must pass.
        status = parse_program_status(
            b"state=blocked:kind=auth:app=antigravity:msg=U2lnbiBpbg+/=="
        )
        assert status == ProgramStatus(ProgramState.BLOCKED, "auth", "antigravity")


class TestTap:
    def test_bel_and_st_terminated_reports(self) -> None:
        tap = ProgramStatusTap()
        assert tap.feed(_report(b"state=working:app=codex")) == [
            ProgramStatus(ProgramState.WORKING, None, "codex"),
        ]
        assert tap.feed(_report(b"state=done:app=codex", ST)) == [
            ProgramStatus(ProgramState.DONE, None, "codex"),
        ]

    def test_reports_between_output_in_order(self) -> None:
        tap = ProgramStatusTap()
        chunk = (
            b"\x1b[2Jhello"
            + _report(b"state=working")
            + b"\x1b]0;title\x07 middle "
            + _report(b"state=done", ST)
            + b"tail"
        )
        assert [s.state for s in tap.feed(chunk)] == [
            ProgramState.WORKING,
            ProgramState.DONE,
        ]

    def test_the_support_query_is_not_a_report(self) -> None:
        tap = ProgramStatusTap()
        assert tap.feed(QUERY_BEL + QUERY_ST) == []

    def test_an_echoed_query_reply_is_not_a_report(self) -> None:
        # A tty with ECHO on hands the agent's own answer back as output.
        tap = ProgramStatusTap()
        assert tap.feed(REPLY_BEL + REPLY_ST + _report(b"state=working")) == [
            ProgramStatus(ProgramState.WORKING),
        ]

    @pytest.mark.parametrize("terminator", [BEL, ST], ids=["bel", "st"])
    def test_a_report_split_at_every_byte_is_reassembled_once(self, terminator) -> None:
        stream = b"before" + _report(b"state=blocked:kind=permission:app=codex", terminator) + b"after"
        for cut in range(1, len(stream)):
            tap = ProgramStatusTap()
            reports = tap.feed(stream[:cut]) + tap.feed(stream[cut:])
            assert reports == [
                ProgramStatus(ProgramState.BLOCKED, "permission", "codex"),
            ], f"failed at cut={cut}"

    def test_a_consumed_report_is_not_reported_again(self) -> None:
        tap = ProgramStatusTap()
        tap.feed(_report(b"state=done"))
        assert tap.feed(b"more output") == []

    def test_an_unterminated_report_over_the_limit_is_dropped(self) -> None:
        # The carry is bounded: a runaway report must not grow it forever, and
        # the next well-formed report still parses.
        tap = ProgramStatusTap()
        assert tap.feed(b"\x1b]7501;state=done:msg=" + b"x" * MAX_REPORT_BYTES) == []
        assert tap.feed(BEL) == []
        assert tap.feed(_report(b"state=error")) == [ProgramStatus(ProgramState.ERROR)]

    def test_a_report_never_contains_escape(self) -> None:
        # An ESC that is not ``ESC \\`` still ends the body, so a broken report
        # cannot swallow the next one.
        tap = ProgramStatusTap()
        chunk = b"\x1b]7501;state=done\x1b[0m" + _report(b"state=working")
        assert [s.state for s in tap.feed(chunk)] == [
            ProgramState.DONE,
            ProgramState.WORKING,
        ]


class TestResponder:
    def test_bel_query_is_stripped_and_answered_with_bel(self) -> None:
        r = ProgramStatusResponder()
        assert r.process(b"hi" + QUERY_BEL + b"bye") == b"hibye"
        assert r.pending_replies() == REPLY_BEL
        # Replies are drained on read.
        assert r.pending_replies() == b""

    def test_every_query_is_answered_with_its_own_terminator(self) -> None:
        r = ProgramStatusResponder()
        assert r.process(QUERY_BEL + b"x" + QUERY_ST) == b"x"
        assert r.pending_replies() == REPLY_BEL + REPLY_ST

    @pytest.mark.parametrize("reply", [REPLY_BEL, REPLY_ST], ids=["bel", "st"])
    def test_an_echoed_reply_is_not_a_new_query(self, reply) -> None:
        # A tty with ECHO on hands the reply straight back; answering it again
        # would loop forever, and stripping it would eat real output.
        r = ProgramStatusResponder()
        assert r.process(b"a" + reply + b"b") == b"a" + reply + b"b"
        assert r.pending_replies() == b""

    @pytest.mark.parametrize("reply", [REPLY_BEL, REPLY_ST], ids=["bel", "st"])
    def test_an_echoed_reply_split_at_every_byte_passes_through(self, reply) -> None:
        # Its first bytes are a query prefix the responder holds across reads.
        for cut in range(1, len(reply)):
            r = ProgramStatusResponder()
            out = r.process(reply[:cut]) + r.process(reply[cut:])
            assert out == reply, f"failed at cut={cut}"
            assert r.pending_replies() == b"", f"failed at cut={cut}"

    @pytest.mark.parametrize(
        "payload",
        [
            _report(b"state=done:app=codex"),
            _report(b"state=blocked:kind=permission", ST),
            b"\x1b]0;title\x07",
            b"\x1b[31mred\x1b[0m",
            b"plain text",
        ],
    )
    def test_everything_else_passes_through_unanswered(self, payload) -> None:
        r = ProgramStatusResponder()
        assert r.process(payload) == payload
        assert r.pending_replies() == b""

    @pytest.mark.parametrize(
        ("query", "reply"), [(QUERY_BEL, REPLY_BEL), (QUERY_ST, REPLY_ST)], ids=["bel", "st"],
    )
    def test_a_query_split_at_every_byte(self, query, reply) -> None:
        stream = b"\x1b[0mA" + query + b"B\x1b[1m"
        for cut in range(1, len(stream)):
            r = ProgramStatusResponder()
            out = r.process(stream[:cut]) + r.process(stream[cut:])
            assert out == b"\x1b[0mAB\x1b[1m", f"failed at cut={cut}"
            assert r.pending_replies() == reply, f"failed at cut={cut}"

    def test_a_held_prefix_that_is_not_a_query_is_released_intact(self) -> None:
        r = ProgramStatusResponder()
        first = r.process(b"x\x1b]7501;")
        second = r.process(b"state=done\x07")
        assert first + second == b"x\x1b]7501;state=done\x07"
        assert r.pending_replies() == b""


class _Handle:
    """Stand-in for the ``asyncio.TimerHandle`` the gate holds and cancels."""

    def __init__(self, delay: float, callback: Callable[..., None], args: tuple) -> None:
        self.delay = delay
        self.callback = callback
        self.args = args
        self.cancelled = False
        self.fired = False

    def cancel(self) -> None:
        self.cancelled = True


class _Scheduler:
    """Manual ``loop.call_later``: a quiet window elapses only on ``elapse()``."""

    def __init__(self) -> None:
        self.handles: list[_Handle] = []

    def call_later(self, delay: float, callback: Callable[..., None], *args) -> _Handle:
        handle = _Handle(delay, callback, args)
        self.handles.append(handle)
        return handle

    @property
    def pending(self) -> list[_Handle]:
        return [h for h in self.handles if not (h.cancelled or h.fired)]

    def elapse(self) -> int:
        """Run every armed window; return how many were still armed."""
        due = self.pending
        for handle in due:
            handle.fired = True
            handle.callback(*handle.args)
        return len(due)


class _Session:
    """Stands in for the ``TerminalSession`` the gate drives: what the gate
    alerted is also what it reads back as the current attention."""

    def __init__(self) -> None:
        self.running = True
        self.attention: str | None = None
        self.alerts: list[str] = []
        self.permission_clears = 0

    def on_alert(self, reason: str) -> None:
        self.attention = reason
        self.alerts.append(reason)

    def on_clear_permission(self) -> None:
        self.permission_clears += 1
        self.attention = None


class _Clock:
    """Manual monotonic clock: time moves only when a test sets ``now``."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
async def scheduler(monkeypatch) -> _Scheduler:
    sched = _Scheduler()
    monkeypatch.setattr(asyncio.get_running_loop(), "call_later", sched.call_later)
    return sched


@pytest.fixture
def session() -> _Session:
    return _Session()


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


def _gate(
    session: _Session,
    quiet_seconds: float | None = None,
    *,
    min_alert_spacing_seconds: float | None = None,
    clock: _Clock | None = None,
) -> ProgramStatusGate:
    return ProgramStatusGate(
        on_alert=session.on_alert,
        on_clear_permission=session.on_clear_permission,
        is_running=lambda: session.running,
        current_attention=lambda: session.attention,
        quiet_seconds=quiet_seconds,
        min_alert_spacing_seconds=min_alert_spacing_seconds,
        clock=clock or _Clock(),
    )


def _status(state: ProgramState, kind: str | None = None, app: str | None = None) -> ProgramStatus:
    return ProgramStatus(state, kind, app)


def _client_write(session: _Session, gate: ProgramStatusGate) -> None:
    """What a keystroke or a scroll from a client does to a terminal session."""
    gate.cancel()
    session.attention = None


class TestGateBlocked:
    @pytest.mark.parametrize("kind", ["permission", "question", None])
    async def test_blocked_alerts_at_once(self, session, scheduler, kind) -> None:
        gate = _gate(session)
        gate.handle(_status(ProgramState.BLOCKED, kind))
        assert session.alerts == ["awaiting_permission"]
        assert scheduler.handles == []

    async def test_blocked_for_auth_is_ignored(self, session, scheduler) -> None:
        # agy reports it for a moment at every start.
        gate = _gate(session)
        gate.handle(_status(ProgramState.BLOCKED, "auth"))
        assert session.alerts == []

    async def test_blocked_drops_a_pending_done_window(self, session, scheduler) -> None:
        gate = _gate(session)
        gate.handle(_status(ProgramState.DONE))
        gate.handle(_status(ProgramState.BLOCKED, "permission"))
        assert scheduler.elapse() == 0
        assert session.alerts == ["awaiting_permission"]


class TestGateTurnEnd:
    @pytest.mark.parametrize(
        ("state", "reason"),
        [(ProgramState.DONE, "turn_finished"), (ProgramState.ERROR, "turn_failed")],
    )
    async def test_alerts_after_the_quiet_window(self, session, scheduler, state, reason) -> None:
        gate = _gate(session)
        gate.handle(_status(state))

        assert session.alerts == []
        assert [h.delay for h in scheduler.pending] == [PROGRAM_STATUS_QUIET_SECONDS]
        assert scheduler.elapse() == 1
        assert session.alerts == [reason]

    @pytest.mark.parametrize(
        "state", [ProgramState.WORKING, ProgramState.IDLE, ProgramState.CLEAR]
    )
    async def test_a_new_state_cancels_the_window(self, session, scheduler, state) -> None:
        # A queued message starts the next turn within milliseconds; that
        # turn's end is the one worth announcing.
        gate = _gate(session)
        gate.handle(_status(ProgramState.DONE))
        gate.handle(_status(state))
        assert scheduler.elapse() == 0
        assert session.alerts == []


class TestGateRepeats:
    """Only a change of report alerts. The spec lets a CLI repeat a report, and
    a client keystroke or scroll clears the badge, so a repeat would otherwise
    announce the same prompt or turn twice."""

    async def test_a_repeated_blocked_after_a_keystroke_stays_quiet(
        self, session, scheduler,
    ) -> None:
        gate = _gate(session, min_alert_spacing_seconds=0)
        gate.handle(_status(ProgramState.BLOCKED, "permission"))
        _client_write(session, gate)
        gate.handle(_status(ProgramState.BLOCKED, "permission"))
        assert session.alerts == ["awaiting_permission"]

    async def test_blocked_again_after_working_is_a_new_prompt(self, session, scheduler) -> None:
        gate = _gate(session, min_alert_spacing_seconds=0)
        gate.handle(_status(ProgramState.BLOCKED, "permission"))
        gate.handle(_status(ProgramState.WORKING))
        gate.handle(_status(ProgramState.BLOCKED, "permission"))
        assert session.alerts == ["awaiting_permission", "awaiting_permission"]

    async def test_blocked_of_another_kind_is_a_new_prompt(self, session, scheduler) -> None:
        gate = _gate(session, min_alert_spacing_seconds=0)
        gate.handle(_status(ProgramState.BLOCKED, "permission"))
        _client_write(session, gate)
        gate.handle(_status(ProgramState.BLOCKED, "question"))
        assert session.alerts == ["awaiting_permission", "awaiting_permission"]

    @pytest.mark.parametrize(
        ("state", "reason"),
        [(ProgramState.DONE, "turn_finished"), (ProgramState.ERROR, "turn_failed")],
    )
    async def test_a_repeat_inside_the_window_keeps_the_first_window(
        self, session, scheduler, state, reason,
    ) -> None:
        gate = _gate(session)
        gate.handle(_status(state))
        gate.handle(_status(state))
        # Neither re-armed nor extended: one handle, still armed.
        assert len(scheduler.handles) == 1
        assert scheduler.elapse() == 1
        assert session.alerts == [reason]

    @pytest.mark.parametrize(
        ("state", "reason"),
        [(ProgramState.DONE, "turn_finished"), (ProgramState.ERROR, "turn_failed")],
    )
    async def test_a_repeated_turn_end_after_a_keystroke_stays_quiet(
        self, session, scheduler, state, reason,
    ) -> None:
        gate = _gate(session)
        gate.handle(_status(state))
        scheduler.elapse()
        _client_write(session, gate)
        gate.handle(_status(state))
        assert scheduler.elapse() == 0
        assert session.alerts == [reason]

    async def test_working_between_two_dones_inside_the_window_alerts_the_last_turn(
        self, session, scheduler,
    ) -> None:
        # ``working`` cancelled the first window, so the second ``done`` is
        # the only one left to announce the turn.
        gate = _gate(session)
        gate.handle(_status(ProgramState.DONE))
        gate.handle(_status(ProgramState.WORKING))
        gate.handle(_status(ProgramState.DONE))
        assert scheduler.elapse() == 1
        assert session.alerts == ["turn_finished"]

    async def test_done_after_working_is_a_new_turn(self, session, scheduler) -> None:
        gate = _gate(session)
        gate.handle(_status(ProgramState.DONE))
        scheduler.elapse()
        _client_write(session, gate)
        gate.handle(_status(ProgramState.WORKING))
        gate.handle(_status(ProgramState.DONE))
        assert scheduler.elapse() == 1
        assert session.alerts == ["turn_finished", "turn_finished"]

    async def test_clear_forgets_the_last_report(self, session, scheduler) -> None:
        gate = _gate(session, min_alert_spacing_seconds=0)
        gate.handle(_status(ProgramState.BLOCKED, "permission"))
        _client_write(session, gate)
        gate.handle(_status(ProgramState.CLEAR))
        gate.handle(_status(ProgramState.BLOCKED, "permission"))
        assert session.alerts == ["awaiting_permission", "awaiting_permission"]


class TestGateClearsPermission:
    @pytest.mark.parametrize(
        "state",
        [ProgramState.WORKING, ProgramState.IDLE, ProgramState.CLEAR],
        ids=["answered", "dismissed", "program-exited"],
    )
    async def test_a_prompt_no_longer_standing_drops_the_badge(
        self, session, scheduler, state,
    ) -> None:
        # Answered elsewhere, dismissed, or gone with the program: the spec
        # says a terminal must drop ``blocked`` on exit, and Claude Code
        # reports ``clear`` when it exits.
        gate = _gate(session)
        gate.handle(_status(ProgramState.BLOCKED, "permission"))
        gate.handle(_status(state))
        assert session.permission_clears == 1
        assert session.attention is None

    async def test_working_keeps_a_finished_turn_badge(self, session, scheduler) -> None:
        # A turn another client starts on a shared thread must not wipe the
        # "your move" the user has not seen yet.
        gate = _gate(session, quiet_seconds=0)
        gate.handle(_status(ProgramState.DONE))
        gate.handle(_status(ProgramState.WORKING))
        assert session.permission_clears == 0
        assert session.attention == "turn_finished"


class TestGateGuards:
    async def test_a_window_does_not_alert_a_session_that_died(self, session, scheduler) -> None:
        gate = _gate(session)
        gate.handle(_status(ProgramState.DONE))
        session.running = False
        assert scheduler.elapse() == 1
        assert session.alerts == []

    async def test_an_ended_session_stays_ended(self, session, scheduler) -> None:
        session.attention = "ended"
        gate = _gate(session, quiet_seconds=0)
        gate.handle(_status(ProgramState.BLOCKED, "permission"))
        gate.handle(_status(ProgramState.DONE))
        assert session.alerts == []

    async def test_the_same_reason_on_screen_is_not_raised_again(self, session, scheduler) -> None:
        gate = _gate(session, quiet_seconds=0, min_alert_spacing_seconds=0)
        gate.handle(_status(ProgramState.DONE))
        gate.handle(_status(ProgramState.WORKING))
        gate.handle(_status(ProgramState.DONE))
        assert session.alerts == ["turn_finished"]

    async def test_a_failed_turn_is_not_downgraded_to_finished(self, session, scheduler) -> None:
        gate = _gate(session, quiet_seconds=0, min_alert_spacing_seconds=0)
        gate.handle(_status(ProgramState.ERROR))
        gate.handle(_status(ProgramState.DONE))
        assert session.alerts == ["turn_failed"]

    async def test_a_failure_after_a_finished_turn_still_alerts(self, session, scheduler) -> None:
        gate = _gate(session, quiet_seconds=0, min_alert_spacing_seconds=0)
        gate.handle(_status(ProgramState.DONE))
        gate.handle(_status(ProgramState.ERROR))
        assert session.alerts == ["turn_finished", "turn_failed"]

    async def test_a_permission_prompt_after_a_failure_alerts(self, session, scheduler) -> None:
        gate = _gate(session, quiet_seconds=0, min_alert_spacing_seconds=0)
        gate.handle(_status(ProgramState.ERROR))
        gate.handle(_status(ProgramState.BLOCKED, "permission"))
        assert session.alerts == ["turn_failed", "awaiting_permission"]


class TestGatePendingFailure:
    """A CLI may report ``done`` right after ``error`` for the same turn. That
    ``done`` never alerts: not while the failure still waits out its quiet
    window or the alert gap, nor after it alerted or client input dropped it.
    The user would hear "finished" for a turn that failed."""

    async def test_done_inside_the_window_keeps_the_failure(self, session, scheduler) -> None:
        gate = _gate(session)
        gate.handle(_status(ProgramState.ERROR))
        gate.handle(_status(ProgramState.DONE))

        assert len(scheduler.handles) == 1
        assert [h.delay for h in scheduler.pending] == [PROGRAM_STATUS_QUIET_SECONDS]
        assert scheduler.elapse() == 1
        assert session.alerts == ["turn_failed"]

    async def test_done_while_the_failure_waits_out_the_gap_keeps_it(
        self, session, scheduler, clock,
    ) -> None:
        gate = _gate(session, min_alert_spacing_seconds=5.0, clock=clock)
        gate.handle(_status(ProgramState.BLOCKED, "permission"))
        gate.handle(_status(ProgramState.WORKING))
        clock.now = 1.0
        gate.handle(_status(ProgramState.ERROR))
        # Past the quiet window, still inside the gap the prompt opened.
        clock.now = 3.0
        gate.handle(_status(ProgramState.DONE))

        assert [h.delay for h in scheduler.pending] == [4.0]
        clock.now = 5.0
        assert scheduler.elapse() == 1
        assert session.alerts == ["awaiting_permission", "turn_failed"]

    @pytest.mark.parametrize(
        "state", [ProgramState.WORKING, ProgramState.IDLE, ProgramState.CLEAR],
    )
    async def test_a_new_state_drops_the_failure_and_the_next_done_finishes(
        self, session, scheduler, state,
    ) -> None:
        gate = _gate(session)
        gate.handle(_status(ProgramState.ERROR))
        gate.handle(_status(state))
        gate.handle(_status(ProgramState.DONE))

        assert scheduler.elapse() == 1
        assert session.alerts == ["turn_finished"]

    async def test_client_input_drops_the_failure_and_the_done_after_it_stays_quiet(
        self, session, scheduler,
    ) -> None:
        # The user was present, and the ``done`` only ends the failed turn.
        gate = _gate(session)
        gate.handle(_status(ProgramState.ERROR))
        _client_write(session, gate)
        gate.handle(_status(ProgramState.DONE))

        assert scheduler.elapse() == 0
        assert session.alerts == []

    async def test_done_after_an_alerted_failure_and_a_focus_report_stays_quiet(
        self, session, scheduler,
    ) -> None:
        gate = _gate(session)
        gate.handle(_status(ProgramState.ERROR))
        assert scheduler.elapse() == 1
        # A client emulator's focus report clears the badge but not the gate.
        session.attention = None
        gate.handle(_status(ProgramState.DONE))

        assert scheduler.elapse() == 0
        assert session.alerts == ["turn_failed"]

    async def test_a_sign_in_report_between_error_and_done_keeps_the_failure(
        self, session, scheduler,
    ) -> None:
        gate = _gate(session)
        gate.handle(_status(ProgramState.ERROR))
        gate.handle(_status(ProgramState.BLOCKED, "auth"))
        gate.handle(_status(ProgramState.DONE))

        assert len(scheduler.handles) == 1
        assert scheduler.elapse() == 1
        assert session.alerts == ["turn_failed"]

    async def test_a_failure_after_done_replaces_the_pending_finish(
        self, session, scheduler,
    ) -> None:
        gate = _gate(session)
        gate.handle(_status(ProgramState.DONE))
        gate.handle(_status(ProgramState.ERROR))

        assert scheduler.elapse() == 1
        assert session.alerts == ["turn_failed"]

    async def test_done_still_replaces_a_deferred_prompt(self, session, scheduler, clock) -> None:
        # Only a pending failure outranks ``done``.
        gate = _gate(session, min_alert_spacing_seconds=5.0, clock=clock)
        gate.handle(_status(ProgramState.BLOCKED, "permission"))
        gate.handle(_status(ProgramState.WORKING))
        clock.now = 1.0
        gate.handle(_status(ProgramState.BLOCKED, "question"))
        clock.now = 2.0
        gate.handle(_status(ProgramState.DONE))

        assert [h.delay for h in scheduler.pending] == [3.0]
        clock.now = 5.0
        assert scheduler.elapse() == 1
        assert session.alerts == ["awaiting_permission", "turn_finished"]


class TestGateAlertSpacing:
    """Alerts of one session are at least ``min_alert_spacing_seconds`` apart. A
    program that flips between states, or a session log replayed with
    ``cat``, must not turn every flip into a push. An alert due sooner waits
    out the gap; any state change or client input meanwhile drops it."""

    async def test_a_flood_of_prompt_flips_alerts_once(self, session, scheduler, clock) -> None:
        gate = _gate(session, clock=clock)
        for _ in range(1000):
            gate.handle(_status(ProgramState.BLOCKED, "permission"))
            gate.handle(_status(ProgramState.WORKING))

        clock.now = 60.0
        assert scheduler.elapse() == 0
        assert session.alerts == ["awaiting_permission"]

    async def test_a_prompt_left_standing_after_a_flood_alerts_once_after_the_gap(
        self, session, scheduler, clock,
    ) -> None:
        gate = _gate(session, clock=clock)
        for _ in range(1000):
            gate.handle(_status(ProgramState.BLOCKED, "permission"))
            gate.handle(_status(ProgramState.WORKING))
        gate.handle(_status(ProgramState.BLOCKED, "permission"))

        assert session.alerts == ["awaiting_permission"]
        assert [h.delay for h in scheduler.pending] == [
            PROGRAM_STATUS_MIN_ALERT_SPACING_SECONDS,
        ]
        clock.now = PROGRAM_STATUS_MIN_ALERT_SPACING_SECONDS
        assert scheduler.elapse() == 1
        assert session.alerts == ["awaiting_permission", "awaiting_permission"]

    async def test_a_prompt_inside_the_gap_waits_out_the_rest_of_it(
        self, session, scheduler, clock,
    ) -> None:
        gate = _gate(session, quiet_seconds=0, min_alert_spacing_seconds=5.0, clock=clock)
        gate.handle(_status(ProgramState.DONE))
        clock.now = 2.0
        gate.handle(_status(ProgramState.BLOCKED, "permission"))

        assert session.alerts == ["turn_finished"]
        assert [h.delay for h in scheduler.pending] == [3.0]
        clock.now = 5.0
        assert scheduler.elapse() == 1
        assert session.alerts == ["turn_finished", "awaiting_permission"]

    @pytest.mark.parametrize(
        "state", [ProgramState.WORKING, ProgramState.IDLE, ProgramState.CLEAR],
    )
    async def test_a_prompt_answered_inside_the_gap_never_alerts(
        self, session, scheduler, clock, state,
    ) -> None:
        gate = _gate(session, min_alert_spacing_seconds=5.0, clock=clock)
        gate.handle(_status(ProgramState.BLOCKED, "permission"))
        gate.handle(_status(ProgramState.WORKING))
        clock.now = 1.0
        gate.handle(_status(ProgramState.BLOCKED, "permission"))
        clock.now = 2.0
        gate.handle(_status(state))

        clock.now = 60.0
        assert scheduler.elapse() == 0
        assert session.alerts == ["awaiting_permission"]

    async def test_client_input_inside_the_gap_drops_the_deferred_alert(
        self, session, scheduler, clock,
    ) -> None:
        gate = _gate(session, quiet_seconds=0, min_alert_spacing_seconds=5.0, clock=clock)
        gate.handle(_status(ProgramState.DONE))
        clock.now = 1.0
        gate.handle(_status(ProgramState.BLOCKED, "permission"))
        _client_write(session, gate)

        clock.now = 60.0
        assert scheduler.elapse() == 0
        assert session.alerts == ["turn_finished"]

    @pytest.mark.parametrize(
        ("since_last_alert", "delay"),
        [
            (10.0, PROGRAM_STATUS_QUIET_SECONDS),
            (4.0, PROGRAM_STATUS_QUIET_SECONDS),
            (1.0, 4.0),
        ],
        ids=["after-the-gap", "gap-ends-inside-the-window", "gap-outlasts-the-window"],
    )
    async def test_a_turn_end_waits_for_the_later_of_its_window_and_the_gap(
        self, session, scheduler, clock, since_last_alert, delay,
    ) -> None:
        gate = _gate(session, min_alert_spacing_seconds=5.0, clock=clock)
        gate.handle(_status(ProgramState.BLOCKED, "permission"))
        gate.handle(_status(ProgramState.WORKING))
        clock.now = since_last_alert
        gate.handle(_status(ProgramState.DONE))

        assert [h.delay for h in scheduler.pending] == [delay]
        assert scheduler.elapse() == 1
        assert session.alerts == ["awaiting_permission", "turn_finished"]

    async def test_an_alert_that_was_not_raised_does_not_restart_the_gap(
        self, session, scheduler, clock,
    ) -> None:
        # The second turn end finds "your move" still on screen and stays
        # quiet; the prompt after it is the first alert in 10 s and must not
        # wait for a gap that quiet turn end never opened.
        gate = _gate(session, quiet_seconds=0, min_alert_spacing_seconds=5.0, clock=clock)
        gate.handle(_status(ProgramState.DONE))
        clock.now = 10.0
        gate.handle(_status(ProgramState.WORKING))
        gate.handle(_status(ProgramState.DONE))
        gate.handle(_status(ProgramState.WORKING))
        gate.handle(_status(ProgramState.BLOCKED, "permission"))

        assert session.alerts == ["turn_finished", "awaiting_permission"]
        assert scheduler.handles == []


class TestGateLastApp:
    async def test_tracks_the_last_reported_app(self, session, scheduler) -> None:
        gate = _gate(session)
        assert gate.last_app is None
        gate.handle(_status(ProgramState.WORKING, app="codex"))
        gate.handle(_status(ProgramState.WORKING))
        assert gate.last_app == "codex"
        gate.handle(_status(ProgramState.IDLE, app="antigravity"))
        assert gate.last_app == "antigravity"
