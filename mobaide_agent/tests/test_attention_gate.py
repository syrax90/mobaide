"""Tests for the debounce between Claude Code's hooks and an attention alert.

``Stop`` fires once per model turn — including every re-invocation a completing
background task or subagent causes — so mapping it straight to ``turn_finished``
produced one notification per turn on every device. :class:`AttentionGate` is
the decision layer that fixes that, and every rule below is either a
notification storm or a missed "your move" when it answers wrongly.

The gate is synchronous but arms its quiet window with ``loop.call_later``, so
these tests are ``async`` only to have a real running loop to patch:
``_Scheduler`` stands in for ``call_later`` and elapses the window exactly when
a test says so, and ``_Clock`` replaces the module's ``time`` so the two-minute
fallback cooldown costs nothing. No test sleeps.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from mobaide.core import attention_gate
from mobaide.core.attention_gate import (
    ATTENTION_ACTIVE_TASK_STATUSES,
    ATTENTION_TURN_COOLDOWN_SECONDS,
    AttentionGate,
    HookKind,
    resolve_hook_kind,
    summarize_hook_payload,
)


class _Handle:
    """Stand-in for the ``asyncio.TimerHandle`` the gate holds and cancels."""

    def __init__(self, delay: float, callback: Callable[[], None]) -> None:
        self.delay = delay
        self.callback = callback
        self.cancelled = False
        self.fired = False

    def cancel(self) -> None:
        self.cancelled = True


class _Scheduler:
    """Manual ``loop.call_later``: a quiet window elapses only on ``elapse()``."""

    def __init__(self) -> None:
        self.handles: list[_Handle] = []

    def call_later(self, delay: float, callback: Callable[[], None]) -> _Handle:
        handle = _Handle(delay, callback)
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
            handle.callback()
        return len(due)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Session:
    """Stands in for the ``TerminalSession`` the gate drives.

    ``on_alert`` is the session's ``set_attention``, so what the gate alerted is
    also what it reads back as the current attention — the same coupling the
    real session has, and what makes the "already on screen" guard observable.
    """

    def __init__(self) -> None:
        self.running = True
        self.attention: str | None = None
        self.calls: list[tuple[str, str | None]] = []
        self.activity: list[str] = []

    def on_alert(self, attention: str, exit_reason: str | None = None) -> None:
        self.attention = attention
        self.calls.append((attention, exit_reason))

    def on_activity(self, activity: str) -> None:
        self.activity.append(activity)

    def user_responded(self) -> None:
        """Mirror ``clear_attention``: the badge drops, the turn is untouched."""
        self.attention = None

    @property
    def reasons(self) -> list[str]:
        return [attention for attention, _ in self.calls]


@pytest.fixture
async def scheduler(monkeypatch) -> _Scheduler:
    sched = _Scheduler()
    monkeypatch.setattr(asyncio.get_running_loop(), "call_later", sched.call_later)
    return sched


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(attention_gate, "time", fake)
    return fake


@pytest.fixture
def session() -> _Session:
    return _Session()


def _gate(session: _Session, quiet_seconds: float | None = None) -> AttentionGate:
    return AttentionGate(
        on_alert=session.on_alert,
        is_running=lambda: session.running,
        current_attention=lambda: session.attention,
        on_activity=session.on_activity,
        quiet_seconds=quiet_seconds,
    )


class TestStopClassification:
    async def test_clean_stop_alerts_after_the_quiet_window(
        self, session, scheduler,
    ) -> None:
        gate = _gate(session)
        gate.handle(HookKind.STOP, {"prompt_id": "p1", "background_tasks": []})

        assert session.calls == []
        assert scheduler.elapse() == 1
        assert session.calls == [("turn_finished", None)]


    @pytest.mark.parametrize("status", sorted(ATTENTION_ACTIVE_TASK_STATUSES))
    async def test_active_background_task_never_alerts(
        self, session, scheduler, status,
    ) -> None:
        gate = _gate(session)
        gate.handle(
            HookKind.STOP,
            {"prompt_id": "p1", "background_tasks": [{"status": status}]},
        )
        assert scheduler.elapse() == 0
        assert session.calls == []

    async def test_finished_background_tasks_do_not_suppress(
        self, session, scheduler,
    ) -> None:
        # Nothing prunes the list, so treating any entry as work-in-flight would
        # mute the session for the rest of its life.
        gate = _gate(session)
        gate.handle(
            HookKind.STOP,
            {
                "background_tasks": [
                    {"status": "completed"},
                    {"status": "failed"},
                    {"status": "killed"},
                ],
            },
        )
        assert scheduler.elapse() == 1
        assert session.reasons == ["turn_finished"]

    async def test_scheduled_cron_never_alerts(self, session, scheduler) -> None:
        gate = _gate(session)
        gate.handle(HookKind.STOP, {"session_crons": [{"id": "c1"}]})
        assert scheduler.elapse() == 0
        assert session.calls == []

    async def test_suppressed_stop_drops_an_armed_window(
        self, session, scheduler,
    ) -> None:
        # A clean Stop arms, then a background task's Stop lands inside the
        # window: the armed window must die with it, or the burst still
        # produces the alert it was meant to suppress.
        gate = _gate(session)
        gate.handle(HookKind.STOP, {"prompt_id": "p1"})
        gate.handle(
            HookKind.STOP,
            {"prompt_id": "p1", "background_tasks": [{"status": "running"}]},
        )
        assert scheduler.elapse() == 0
        assert session.calls == []


class TestTurnDedupe:
    async def test_stop_burst_within_one_turn_alerts_once(
        self, session, scheduler,
    ) -> None:
        # The reported bug's shape: N background agents, N+1 Stops, one turn.
        gate = _gate(session)
        for _ in range(3):
            gate.handle(HookKind.STOP, {"prompt_id": "p1"})

        assert len(scheduler.pending) == 1
        assert scheduler.elapse() == 1
        assert session.reasons == ["turn_finished"]

    async def test_stop_after_the_turn_alerted_is_dropped(
        self, session, scheduler,
    ) -> None:
        gate = _gate(session)
        gate.handle(HookKind.STOP, {"prompt_id": "p1"})
        scheduler.elapse()
        session.user_responded()

        gate.handle(HookKind.STOP, {"prompt_id": "p1"})

        # The window still arms and elapses — that is what turns the activity
        # ``idle`` — but the turn's dedupe key withholds the alert.
        assert scheduler.elapse() == 1
        assert session.reasons == ["turn_finished"]
        assert session.activity[-1] == "idle"

    async def test_the_next_prompt_alerts_again(self, session, scheduler) -> None:
        # The dedupe is keyed on the turn, not latched: a genuinely new prompt
        # still earns its own "your move".
        gate = _gate(session)
        gate.handle(HookKind.STOP, {"prompt_id": "p1"})
        scheduler.elapse()
        session.user_responded()

        gate.handle(HookKind.STOP, {"prompt_id": "p2"})
        scheduler.elapse()

        assert session.reasons == ["turn_finished", "turn_finished"]

    async def test_alert_is_withheld_while_the_badge_is_still_up(
        self, session, scheduler,
    ) -> None:
        # ``turn_finished`` from the previous turn is still on screen, so a
        # second one-shot event would re-notify for what the user already sees.
        gate = _gate(session)
        gate.handle(HookKind.STOP, {"prompt_id": "p1"})
        scheduler.elapse()

        gate.handle(HookKind.STOP, {"prompt_id": "p2"})

        assert scheduler.elapse() == 1
        assert session.reasons == ["turn_finished"]

    async def test_second_turn_inside_the_cooldown_is_dropped(
        self, session, scheduler, clock,
    ) -> None:
        # No ``prompt_id`` (older CLI), so time is the only dedupe key left.
        gate = _gate(session)
        gate.handle(HookKind.STOP, {})
        scheduler.elapse()
        session.user_responded()
        clock.advance(ATTENTION_TURN_COOLDOWN_SECONDS - 1)

        gate.handle(HookKind.STOP, {})
        scheduler.elapse()

        assert session.reasons == ["turn_finished"]


class TestIdleBackstop:
    async def test_idle_does_not_double_alert_an_alerted_turn(
        self, session, scheduler,
    ) -> None:
        gate = _gate(session)
        gate.handle(HookKind.STOP, {"prompt_id": "p1"})
        scheduler.elapse()
        session.user_responded()

        gate.handle(HookKind.IDLE, {"prompt_id": "p1"})

        assert session.reasons == ["turn_finished"]

    async def test_idle_with_a_rotated_prompt_id_does_not_alert_again(
        self, session, scheduler,
    ) -> None:
        # ``idle_prompt`` can carry a newer ``prompt_id`` than the ``Stop`` that
        # ended the turn, so the dedupe key alone lets it through.
        gate = _gate(session)
        gate.handle(HookKind.STOP, {"prompt_id": "p1"})
        scheduler.elapse()
        session.user_responded()

        gate.handle(HookKind.IDLE, {"prompt_id": "p2"})

        assert session.reasons == ["turn_finished"]

    async def test_idle_alerts_after_a_muted_window(self, session, scheduler) -> None:
        # A scroll or a focus report muted the window, so the user saw no
        # alert for this turn; the 60 s backstop must still deliver it.
        gate = _gate(session)
        gate.handle(HookKind.STOP, {"prompt_id": "p1"})
        gate.mute()
        scheduler.elapse()
        assert session.reasons == []

        gate.handle(HookKind.IDLE, {"prompt_id": "p2"})

        assert session.reasons == ["turn_finished"]

    async def test_idle_drops_the_pending_window(self, session, scheduler) -> None:
        gate = _gate(session)
        gate.handle(HookKind.STOP, {"prompt_id": "p1"})

        gate.handle(HookKind.IDLE, {"prompt_id": "p1"})

        assert scheduler.elapse() == 0
        assert session.reasons == ["turn_finished"]


class TestUngatedHooks:
    async def test_permission_alerts_immediately_and_drops_the_window(
        self, session, scheduler,
    ) -> None:
        gate = _gate(session)
        gate.handle(HookKind.STOP, {"prompt_id": "p1"})

        gate.handle(HookKind.PERMISSION, {})

        assert session.calls == [("awaiting_permission", None)]
        assert scheduler.elapse() == 0

    async def test_end_alerts_and_the_lapsed_window_cannot_follow(
        self, session, scheduler,
    ) -> None:
        # Ordering guard: a pending ``turn_finished`` firing after ``ended``
        # would badge a dead session "your move".
        gate = _gate(session)
        gate.handle(HookKind.STOP, {"prompt_id": "p1"})

        gate.handle(HookKind.END, {"reason": "clear"})

        assert scheduler.elapse() == 0
        assert session.calls == [("ended", "clear")]


    async def test_end_reopens_the_turn(self, session, scheduler) -> None:
        # ``SessionEnd`` also fires with reason ``clear`` for ``/clear``, which
        # leaves the PTY running — so the gate resets rather than latches.
        gate = _gate(session)
        gate.handle(HookKind.STOP, {"prompt_id": "p1"})
        scheduler.elapse()

        gate.handle(HookKind.END, {"reason": "clear"})
        session.user_responded()
        gate.handle(HookKind.STOP, {"prompt_id": "p1"})
        scheduler.elapse()

        assert session.reasons == ["turn_finished", "ended", "turn_finished"]


class TestTurnReset:
    async def test_prompt_submit_drops_the_pending_window(
        self, session, scheduler,
    ) -> None:
        gate = _gate(session)
        gate.handle(HookKind.STOP, {"prompt_id": "p1"})

        gate.handle(HookKind.PROMPT_SUBMIT, {})

        assert scheduler.elapse() == 0
        assert session.calls == []

    async def test_prompt_submit_reopens_an_alerted_turn(
        self, session, scheduler,
    ) -> None:
        gate = _gate(session)
        gate.handle(HookKind.STOP, {"prompt_id": "p1"})
        scheduler.elapse()
        session.user_responded()

        gate.handle(HookKind.PROMPT_SUBMIT, {})
        gate.handle(HookKind.STOP, {"prompt_id": "p1"})
        scheduler.elapse()

        assert session.reasons == ["turn_finished", "turn_finished"]

    async def test_cancel_keeps_the_turn_alerted(self, session, scheduler) -> None:
        # ``cancel`` is what session teardown calls; unlike ``reset_turn`` it
        # must not hand the turn back, or a late hook could re-alert.
        gate = _gate(session)
        gate.handle(HookKind.STOP, {"prompt_id": "p1"})
        scheduler.elapse()
        session.user_responded()

        gate.cancel()
        gate.handle(HookKind.STOP, {"prompt_id": "p1"})

        assert scheduler.elapse() == 1
        assert session.reasons == ["turn_finished"]


class TestActivity:
    """``on_activity`` drives the clients' "agent is working" indicator, so it
    must follow every turn boundary whether or not an alert fires: a row stuck
    on ``working`` is the bug this signal exists to fix."""


    async def test_clean_stop_turns_idle_only_when_the_window_elapses(
        self, session, scheduler,
    ) -> None:
        # A clean ``Stop`` can still be followed by a background agent's
        # re-invocation, so idle waits for the same quiet window as the alert.
        gate = _gate(session)
        gate.handle(HookKind.PROMPT_SUBMIT, {"prompt_id": "p1"})
        gate.handle(HookKind.STOP, {"prompt_id": "p1"})
        assert session.activity == ["working"]

        scheduler.elapse()

        assert session.activity == ["working", "idle"]

    async def test_a_withheld_alert_still_turns_idle(self, session, scheduler) -> None:
        # The badge from the previous turn is still up, so this turn's alert is
        # withheld — the indicator must stop anyway.
        gate = _gate(session)
        gate.handle(HookKind.STOP, {"prompt_id": "p1"})
        scheduler.elapse()
        gate.handle(HookKind.PROMPT_SUBMIT, {"prompt_id": "p2"})
        session.attention = "turn_finished"
        gate.handle(HookKind.STOP, {"prompt_id": "p2"})

        scheduler.elapse()

        assert session.reasons == ["turn_finished"]
        assert session.activity == ["idle", "working", "idle"]


class TestMute:
    """A keystroke mutes the pending window instead of dropping it: the user is
    at the terminal, so no "your move" — but the turn still ends, and the
    activity must not stay ``working`` because the user typed or scrolled."""

    async def test_muted_window_turns_idle_without_alerting(
        self, session, scheduler,
    ) -> None:
        gate = _gate(session)
        gate.handle(HookKind.PROMPT_SUBMIT, {"prompt_id": "p1"})
        gate.handle(HookKind.STOP, {"prompt_id": "p1"})

        gate.mute()

        assert scheduler.elapse() == 1
        assert session.calls == []
        assert session.activity == ["working", "idle"]

    async def test_mute_without_a_pending_window_does_not_mute_the_next_one(
        self, session, scheduler,
    ) -> None:
        # A keystroke while Claude is still mid-turn says nothing about the
        # ``Stop`` that comes later.
        gate = _gate(session)
        gate.mute()

        gate.handle(HookKind.STOP, {"prompt_id": "p1"})

        assert scheduler.elapse() == 1
        assert session.reasons == ["turn_finished"]

    async def test_a_muted_window_does_not_consume_the_turn(
        self, session, scheduler,
    ) -> None:
        # Muting is per window, like the cancel it replaced: a background agent
        # re-waking the session and stopping again still earns the alert.
        gate = _gate(session)
        gate.handle(HookKind.STOP, {"prompt_id": "p1"})
        gate.mute()
        scheduler.elapse()

        gate.handle(HookKind.STOP, {"prompt_id": "p1"})
        scheduler.elapse()

        assert session.reasons == ["turn_finished"]


class TestIdleWhileWorkInFlight:
    """An ``idle_prompt`` while the last ``Stop`` still saw background work is
    not an alert: the row keeps the activity that ``Stop`` gave it, and the
    ``Stop`` that really ends the turn still shows "Ready" once."""

    @staticmethod
    def _stop_with_background(gate: AttentionGate, prompt_id: str = "p1") -> None:
        gate.handle(
            HookKind.STOP,
            {"prompt_id": prompt_id, "background_tasks": [{"status": "running"}]},
        )

    async def test_the_final_stop_alerts_after_an_idle_that_waited(
        self, session, scheduler,
    ) -> None:
        gate = _gate(session)
        self._stop_with_background(gate)
        gate.handle(HookKind.IDLE, {"prompt_id": "p1"})
        assert session.reasons == []

        gate.handle(HookKind.STOP, {"prompt_id": "p1"})
        scheduler.elapse()

        assert session.reasons == ["turn_finished"]

    async def test_repeated_idles_with_work_in_flight_never_alert(
        self, session, scheduler,
    ) -> None:
        gate = _gate(session)
        self._stop_with_background(gate)
        gate.handle(HookKind.IDLE, {"prompt_id": "p1"})
        self._stop_with_background(gate)
        gate.handle(HookKind.IDLE, {"prompt_id": "p1"})

        assert session.reasons == []
        assert session.activity[-1] == "working"
        assert scheduler.pending == []

    async def test_idle_after_a_clean_stop_is_final(self, session, scheduler) -> None:
        # The background work finished before this ``Stop``, so the idle alert
        # is the turn's real end and a later ``Stop`` must not repeat it.
        gate = _gate(session)
        self._stop_with_background(gate)
        gate.handle(HookKind.STOP, {"prompt_id": "p1"})
        gate.handle(HookKind.IDLE, {"prompt_id": "p1"})
        session.user_responded()

        gate.handle(HookKind.STOP, {"prompt_id": "p1"})
        scheduler.elapse()

        assert session.reasons == ["turn_finished"]

    async def test_prompt_submit_forgets_the_background_work(
        self, session, scheduler,
    ) -> None:
        gate = _gate(session)
        self._stop_with_background(gate)
        gate.handle(HookKind.PROMPT_SUBMIT, {"prompt_id": "p2"})
        gate.handle(HookKind.IDLE, {"prompt_id": "p2"})
        session.user_responded()

        gate.handle(HookKind.STOP, {"prompt_id": "p2"})
        scheduler.elapse()

        assert session.reasons == ["turn_finished"]


class TestStopHookContinuation:
    """``stop_hook_active`` marks the ``Stop`` after a continuation a blocking
    ``Stop`` hook (or ``/goal``) forced. The CLI tells such hooks to let it
    through, so it is normally the turn's real end and must not mute "Ready"."""

    async def test_continuation_stop_alerts_after_the_quiet_window(
        self, session, scheduler,
    ) -> None:
        gate = _gate(session)
        gate.handle(HookKind.STOP, {"prompt_id": "p1", "stop_hook_active": True})

        assert session.calls == []
        assert scheduler.elapse() == 1
        assert session.reasons == ["turn_finished"]
        assert session.activity == ["idle"]

    async def test_blocking_hook_chain_alerts_once_and_ends_idle(
        self, session, scheduler,
    ) -> None:
        # The hook blocks the first ``Stop``; Claude continues and stops again
        # with ``stop_hook_active``. That second stop re-arms the one window.
        gate = _gate(session)
        gate.handle(HookKind.PROMPT_SUBMIT, {"prompt_id": "p1"})
        gate.handle(HookKind.STOP, {"prompt_id": "p1", "stop_hook_active": False})
        gate.handle(HookKind.STOP, {"prompt_id": "p1", "stop_hook_active": True})

        assert len(scheduler.pending) == 1
        assert scheduler.elapse() == 1
        assert session.reasons == ["turn_finished"]
        assert session.activity == ["working", "idle"]

    async def test_background_work_still_keeps_a_continuation_stop_working(
        self, session, scheduler,
    ) -> None:
        gate = _gate(session)
        gate.handle(
            HookKind.STOP,
            {
                "prompt_id": "p1",
                "stop_hook_active": True,
                "background_tasks": [{"status": "running"}],
            },
        )

        assert scheduler.elapse() == 0
        assert session.calls == []
        assert session.activity == ["working"]


class TestLiveness:
    async def test_window_does_not_alert_a_session_that_died(
        self, session, scheduler,
    ) -> None:
        # The liveness poller reaps crashed sessions without calling ``stop()``,
        # so cancellation on teardown cannot be relied on here.
        gate = _gate(session)
        gate.handle(HookKind.STOP, {"prompt_id": "p1"})
        session.running = False

        assert scheduler.elapse() == 1
        assert session.calls == []


class TestMalformedPayloads:
    @pytest.mark.parametrize(
        "payload",
        [
            {"background_tasks": None},
            {"background_tasks": "running"},
            {"background_tasks": [None, "running"]},
            {"background_tasks": [{}]},
            # Unhashable statuses: the active-set membership test hashes its
            # left operand, so these raise ``TypeError`` unless ``status`` is
            # confirmed to be a string first.
            {"background_tasks": [{"status": ["running"]}]},
            {"background_tasks": [{"status": {}}]},
            {"session_crons": None},
            {"session_crons": "c1"},
            {"stop_hook_active": "true"},
            {"prompt_id": 42},
            {"prompt_id": ""},
        ],
    )
    async def test_unreadable_field_counts_as_absent(
        self, session, scheduler, payload,
    ) -> None:
        # The hook endpoint must never 500 back into Claude, and a field the
        # gate cannot read must not be able to mute the session either.
        gate = _gate(session)
        gate.handle(HookKind.STOP, payload)

        assert scheduler.elapse() == 1
        assert session.reasons == ["turn_finished"]


class TestResolveHookKind:

    def test_url_param_wins_over_the_event_name(self) -> None:
        # Both ``Notification`` matchers share one event name, and upstream
        # skips the matcher filter entirely when an event carries no match
        # query — only the baked param can tell permission from idle.
        assert resolve_hook_kind("idle", "Notification") is HookKind.IDLE

    def test_unknown_url_param_is_unroutable(self) -> None:
        assert resolve_hook_kind("subagent_stop", "Stop") is None

    @pytest.mark.parametrize(
        ("event_name", "kind"),
        [
            ("Notification", HookKind.PERMISSION),
            ("Stop", HookKind.STOP),
            ("UserPromptSubmit", HookKind.PROMPT_SUBMIT),
            ("SessionEnd", HookKind.END),
        ],
    )
    def test_pre_upgrade_settings_fall_back_to_the_event_name(
        self, event_name, kind,
    ) -> None:
        # A session spawned by the previous agent has no ``hook`` param in its
        # URLs and must keep working for the rest of its life.
        assert resolve_hook_kind("", event_name) is kind


def _task(task_id: str, task_type: str, status: str = "running") -> dict[str, str]:
    return {"id": task_id, "type": task_type, "status": status}


def _stop(prompt_id: str, *tasks: dict[str, str]) -> dict[str, object]:
    return {"prompt_id": prompt_id, "background_tasks": list(tasks)}


class TestParkedOnBackgroundWork:
    """A ``Stop`` that lists in-flight work is not the end of the turn, and what
    it lists decides the indicator: an agent at work keeps the session
    ``working``, a shell, monitor or wakeup the session merely sits on makes it
    ``waiting``. Neither arms the window that leads to "Ready"."""

    @pytest.mark.parametrize(
        ("payload", "activity"),
        [
            (_stop("p1", _task("t1", "shell")), "waiting"),
            (_stop("p1", _task("t1", "monitor")), "waiting"),
            (_stop("p1", _task("t1", "subagent", "paused")), "waiting"),
            ({"prompt_id": "p1", "session_crons": [{"id": "c1"}]}, "waiting"),
            (_stop("p1", _task("t1", "subagent")), "working"),
            (_stop("p1", _task("t1", "shell"), _task("t2", "subagent")), "working"),
            # A type this agent does not know yet, or none at all: calling an
            # agent at work "parked" is the worse mistake.
            (_stop("p1", _task("t1", "not_invented_yet")), "working"),
            ({"background_tasks": [{"id": "t1", "status": "running"}]}, "working"),
        ],
    )
    async def test_stop_reports_what_is_still_in_flight(
        self, session, scheduler, payload, activity,
    ) -> None:
        gate = _gate(session)
        gate.handle(HookKind.STOP, payload)

        assert scheduler.pending == []
        assert session.activity == [activity]
        assert session.calls == []


class TestToolHeartbeat:
    """``PreToolUse`` proves work is going on. From the main agent that is
    always true; from a sub-agent only while the last ``Stop`` still listed
    something in flight."""

    async def test_main_agent_call_cancels_an_armed_window(
        self, session, scheduler,
    ) -> None:
        # A ``!`` command the model answers starts a turn that no
        # ``UserPromptSubmit`` announces. Without the heartbeat the window the
        # previous ``Stop`` armed says "Ready" while the agent works.
        gate = _gate(session)
        gate.handle(HookKind.STOP, {"prompt_id": "p1"})

        gate.handle(HookKind.TOOL, {"prompt_id": "p1"})

        assert scheduler.elapse() == 0
        assert session.calls == []
        assert session.activity == ["working"]

        gate.handle(HookKind.STOP, {"prompt_id": "p1"})

        assert scheduler.elapse() == 1
        assert session.reasons == ["turn_finished"]

    async def test_sub_agent_call_with_nothing_in_flight_is_ignored(
        self, session, scheduler,
    ) -> None:
        # One of the CLI's own helper agents, running after the turn. No
        # ``Stop`` follows it, so a session it pulled back to ``working`` would
        # stay there, and a window it cancelled would never say "Ready".
        gate = _gate(session)
        gate.handle(HookKind.STOP, {"prompt_id": "p1"})

        gate.handle(HookKind.TOOL, {"prompt_id": "p1", "agent_id": "a1"})

        assert session.activity == []
        assert scheduler.elapse() == 1
        assert session.reasons == ["turn_finished"]

        gate.handle(HookKind.TOOL, {"prompt_id": "p1", "agent_id": "a1"})

        assert session.activity == ["idle"]


class TestLongLivedTasks:
    """A dev server never completes, so every ``Stop`` after it starts lists it.
    The turn that started it ends ``waiting``; once the user types the next
    prompt it no longer counts, or the session would never say "Ready" again."""

    @staticmethod
    def _park_on_a_shell(gate: AttentionGate) -> None:
        gate.handle(HookKind.PROMPT_SUBMIT, {"prompt_id": "p1", "prompt": "serve"})
        gate.handle(HookKind.STOP, _stop("p1", _task("t1", "shell")))

    @pytest.mark.parametrize(
        ("prompt", "ready"),
        [
            ({"prompt": "now fix the bug"}, True),
            # An older CLI that sends no prompt text.
            ({}, True),
            # Written by the CLI itself: the shell reporting, a sub-agent
            # handing back. The session is still inside the turn that parked.
            ({"prompt": "<task-notification>\n"}, False),
            ({"prompt": "<agent-message from="}, False),
            # A collapsed paste is the user's own input: the apps send every
            # multi-line composer prompt as one.
            ({"prompt": '<pasted_content id="1">\nfix\n</pasted_content id="1">'}, True),
            # ``source`` outranks the look of the text, in both directions.
            ({"source": "user", "prompt": "<task-notification>\n"}, True),
            ({"source": "task", "prompt": "now fix the bug"}, False),
        ],
    )
    async def test_only_a_typed_prompt_makes_a_parked_shell_long_lived(
        self, session, scheduler, prompt, ready,
    ) -> None:
        gate = _gate(session)
        self._park_on_a_shell(gate)
        assert session.activity[-1] == "waiting"

        gate.handle(HookKind.PROMPT_SUBMIT, {"prompt_id": "p2", **prompt})
        gate.handle(HookKind.STOP, _stop("p2", _task("t1", "shell")))

        assert scheduler.elapse() == (1 if ready else 0)
        assert session.reasons == (["turn_finished"] if ready else [])
        assert session.activity[-1] == ("idle" if ready else "waiting")

    async def test_a_scheduled_wakeup_is_not_a_typed_prompt(
        self, session, scheduler,
    ) -> None:
        # A wakeup submits plain text, exactly like a typed prompt. Read as
        # typed, it marked the running shell long-lived and showed "Ready"
        # while the session was still parked on it.
        gate = _gate(session)
        gate.handle(HookKind.PROMPT_SUBMIT, {"prompt_id": "p1", "prompt": "serve"})
        gate.handle(
            HookKind.STOP,
            {**_stop("p1", _task("t1", "shell")), "session_crons": [{"id": "c1"}]},
        )

        gate.handle(HookKind.PROMPT_SUBMIT, {"prompt_id": "p2", "prompt": "check"})
        gate.handle(HookKind.STOP, _stop("p2", _task("t1", "shell")))

        assert scheduler.elapse() == 0
        assert session.calls == []
        assert session.activity[-1] == "waiting"

    async def test_a_shell_started_after_the_baseline_still_parks_the_session(
        self, session, scheduler,
    ) -> None:
        gate = _gate(session)
        self._park_on_a_shell(gate)

        gate.handle(HookKind.PROMPT_SUBMIT, {"prompt_id": "p2", "prompt": "build"})
        gate.handle(
            HookKind.STOP, _stop("p2", _task("t1", "shell"), _task("t2", "shell")),
        )

        assert scheduler.elapse() == 0
        assert session.calls == []
        assert session.activity[-1] == "waiting"


class TestMalformedActivityFields:
    @pytest.mark.parametrize(
        ("kind", "payload", "activity"),
        [
            # Unhashable where a set lookup would hash it: ``type`` against the
            # passive types, ``id`` against the long-lived ids.
            (
                HookKind.STOP,
                {"background_tasks": [{"status": "running", "type": ["shell"]}]},
                "working",
            ),
            (
                HookKind.STOP,
                {
                    "background_tasks": [
                        {"status": "running", "type": "shell", "id": ["t1"]},
                    ],
                },
                "waiting",
            ),
            (
                HookKind.STOP,
                {"background_tasks": [{"status": "paused", "type": {}, "id": 7}]},
                "waiting",
            ),
            (HookKind.PROMPT_SUBMIT, {"prompt": 42, "source": ["user"]}, "working"),
            # An ``agent_id`` the gate cannot read is not a sub-agent.
            (HookKind.TOOL, {"agent_id": ["a1"]}, "working"),
            (HookKind.TOOL, {"agent_id": ""}, "working"),
        ],
    )
    async def test_unreadable_field_never_raises(
        self, session, scheduler, kind, payload, activity,
    ) -> None:
        # ``Stop`` and ``PreToolUse`` both hold Claude until the hook answers.
        gate = _gate(session)
        gate.handle(kind, payload)

        assert session.activity == [activity]
        assert session.calls == []


_PROMPT = HookKind.PROMPT_SUBMIT
_TOOL = HookKind.TOOL
_STOP = HookKind.STOP
_IDLE = HookKind.IDLE
_END = HookKind.END

_SUBAGENT_1 = _task("t1", "subagent")
_EXIT = {"reason": "prompt_input_exit"}


def _typed(prompt_id: str) -> dict[str, str]:
    return {"prompt_id": prompt_id, "prompt": "do the task"}


def _task_notification(prompt_id: str) -> dict[str, str]:
    return {"prompt_id": prompt_id, "prompt": "<task-notification>\n"}


def _agent_message(prompt_id: str) -> dict[str, str]:
    return {"prompt_id": prompt_id, "prompt": "<agent-message from="}


def _tool(prompt_id: str, agent_id: str | None = None) -> dict[str, str]:
    payload = {"prompt_id": prompt_id}
    if agent_id is not None:
        payload["agent_id"] = agent_id
    return payload


# Hook sequences recorded from Claude Code 2.1.322, cut to the fields the gate
# reads. Row: seconds since start, hook, payload, then the resulting activity
# and every alert so far. ``SubagentStart``/``SubagentStop``/``PostToolUse`` are omitted.

# The main agent waits on a sub-agent, which waits on its own background shell.
# From 7.9 s the only task listed is that shell, and the sub-agent is not listed
# as running, so the ``idle_prompt`` at 67.9 s arrives 28 s before work resumes.
_WAITS_ON_A_SUB_AGENT_THAT_WAITS_ON_A_SHELL = [
    (0.0, _PROMPT, _typed("p1"), "working", []),
    (2.1, _TOOL, _tool("p1"), "working", []),
    (3.1, _STOP, _stop("p1", _SUBAGENT_1), "working", []),
    (3.8, _TOOL, _tool("p1", "a1"), "working", []),
    (6.9, _PROMPT, _task_notification("p2"), "working", []),
    (7.9, _STOP, _stop("p2", _task("t2", "shell")), "waiting", []),
    (67.9, _IDLE, {"prompt_id": "p2"}, "waiting", []),
    (95.7, _TOOL, _tool("p2", "a1"), "working", []),
    (96.3, _PROMPT, _agent_message("p3"), "working", []),
    (97.4, _STOP, _stop("p3"), "working", []),
    (157.4, _IDLE, {"prompt_id": "p4"}, "idle", ["turn_finished"]),
    (217.2, _END, _EXIT, "idle", ["turn_finished", "ended"]),
]

# The main agent waits on its own background shell for 99 s.
_WAITS_ON_ITS_OWN_SHELL = [
    (0.0, _PROMPT, _typed("p1"), "working", []),
    (1.5, _TOOL, _tool("p1"), "working", []),
    (2.9, _STOP, _stop("p1", _task("t1", "shell")), "waiting", []),
    (62.9, _IDLE, {"prompt_id": "p1"}, "waiting", []),
    (102.1, _PROMPT, _task_notification("p2"), "working", []),
    (103.7, _TOOL, _tool("p2"), "working", []),
    (124.9, _STOP, _stop("p2"), "working", []),
    (184.9, _IDLE, {"prompt_id": "p2"}, "idle", ["turn_finished"]),
    (192.0, _END, _EXIT, "idle", ["turn_finished", "ended"]),
]

# A sub-agent starts a shell and hands back, the shell's notification wakes the
# session twice, and the sub-agent finishes 42 s later.
_SUB_AGENT_THEN_ITS_SHELL = [
    (0.0, _PROMPT, _typed("p1"), "working", []),
    (1.8, _TOOL, _tool("p1"), "working", []),
    (2.9, _STOP, _stop("p1", _SUBAGENT_1), "working", []),
    (3.5, _TOOL, _tool("p1", "a1"), "working", []),
    (5.4, _TOOL, _tool("p1", "a1"), "working", []),
    (5.7, _PROMPT, _agent_message("p2"), "working", []),
    (6.8, _STOP, _stop("p2", _task("t2", "shell")), "waiting", []),
    (6.8, _PROMPT, _task_notification("p3"), "working", []),
    (7.9, _STOP, _stop("p3", _task("t2", "shell")), "waiting", []),
    (49.9, _TOOL, _tool("p3", "a1"), "working", []),
    (52.0, _PROMPT, _task_notification("p4"), "working", []),
    (53.0, _STOP, _stop("p4"), "working", []),
    (113.0, _IDLE, {"prompt_id": "p4"}, "idle", ["turn_finished"]),
    (122.1, _END, _EXIT, "idle", ["turn_finished", "ended"]),
]

# The same shape in a session that was already at "Ready" from an earlier turn
# when the user typed.
_SUB_AGENT_THEN_ITS_SHELL_AFTER_AN_EARLIER_TURN = [
    (0.0, _STOP, _stop("p1"), None, []),
    (25.5, _PROMPT, _typed("p2"), "working", ["turn_finished"]),
    (27.2, _TOOL, _tool("p2"), "working", ["turn_finished"]),
    (29.7, _STOP, _stop("p2", _SUBAGENT_1), "working", ["turn_finished"]),
    (30.5, _TOOL, _tool("p2", "a1"), "working", ["turn_finished"]),
    (31.7, _TOOL, _tool("p2", "a1"), "working", ["turn_finished"]),
    (36.0, _PROMPT, _task_notification("p3"), "working", ["turn_finished"]),
    (37.3, _STOP, _stop("p3", _task("t2", "shell")), "waiting", ["turn_finished"]),
    (57.3, _TOOL, _tool("p3", "a1"), "working", ["turn_finished"]),
    (58.1, _PROMPT, _agent_message("p4"), "working", ["turn_finished"]),
    (59.4, _STOP, _stop("p4"), "working", ["turn_finished"]),
    (119.5, _IDLE, {"prompt_id": "p5"}, "idle", ["turn_finished"] * 2),
    (127.7, _END, _EXIT, "idle", ["turn_finished", "turn_finished", "ended"]),
]

# Two sub-agents; one leaves a monitor behind that outlives the recording. The
# session never reaches "Ready": it is still parked on that monitor at the end.
_ORPHAN_MONITOR_LEFT_BY_A_SUB_AGENT = [
    (0.0, _PROMPT, _typed("p1"), "working", []),
    (1.6, _TOOL, _tool("p1"), "working", []),
    (2.2, _TOOL, _tool("p1"), "working", []),
    (3.4, _STOP, _stop("p1", _SUBAGENT_1, _task("t2", "subagent")), "working", []),
    (3.7, _TOOL, _tool("p1", "a1"), "working", []),
    (5.8, _TOOL, _tool("p1", "a2"), "working", []),
    (7.2, _TOOL, _tool("p1", "a2"), "working", []),
    (9.4, _TOOL, _tool("p1", "a2"), "working", []),
    (10.5, _TOOL, _tool("p1", "a2"), "working", []),
    (11.5, _PROMPT, _agent_message("p2"), "working", []),
    (
        12.5,
        _STOP,
        _stop("p2", _SUBAGENT_1, _task("t3", "shell"), _task("t4", "shell")),
        "working",
        [],
    ),
    (12.5, _PROMPT, _task_notification("p3"), "working", []),
    (
        13.6,
        _STOP,
        _stop("p3", _SUBAGENT_1, _task("t3", "shell"), _task("t4", "shell")),
        "working",
        [],
    ),
    (25.2, _TOOL, _tool("p3", "a1"), "working", []),
    (25.7, _PROMPT, _agent_message("p4"), "working", []),
    (
        26.9,
        _STOP,
        _stop("p4", _task("t3", "shell"), _task("t4", "shell")),
        "waiting",
        [],
    ),
    (57.2, _PROMPT, _task_notification("p5"), "working", []),
    (58.2, _STOP, _stop("p5", _task("t4", "shell")), "waiting", []),
    (112.2, _END, _EXIT, "idle", ["ended"]),
]


class TestRecordedSequences:
    """The bug these pin: a session showing "Ready", and pushing it, while a
    sub-agent or a background shell was still going to resume it."""

    @staticmethod
    def _replay(
        session: _Session, scheduler: _Scheduler, hooks: list[tuple],
    ) -> list[tuple[str | None, list[str]]]:
        """Feed ``(seconds, kind, payload)`` rows to a gate on their own clock.

        A quiet window runs out when the recorded gap after it was armed is at
        least as long as the window. A ``UserPromptSubmit`` also drops the
        badge, as ``TerminalSession.on_claude_hook`` does.
        """
        gate = _gate(session)
        armed_at: list[float] = []
        seen = []
        for at, kind, payload in hooks:
            for handle, armed in zip(scheduler.handles, armed_at, strict=True):
                if handle in scheduler.pending and armed + handle.delay <= at:
                    handle.fired = True
                    handle.callback()
            gate.handle(kind, payload)
            if kind is HookKind.PROMPT_SUBMIT:
                session.user_responded()
            armed_at += [at] * (len(scheduler.handles) - len(armed_at))
            seen.append(
                (session.activity[-1] if session.activity else None, session.reasons),
            )
        return seen

    @pytest.mark.parametrize(
        "rows",
        [
            pytest.param(_WAITS_ON_A_SUB_AGENT_THAT_WAITS_ON_A_SHELL, id="s5"),
            pytest.param(_WAITS_ON_ITS_OWN_SHELL, id="s3"),
            pytest.param(_SUB_AGENT_THEN_ITS_SHELL, id="s2"),
            pytest.param(_SUB_AGENT_THEN_ITS_SHELL_AFTER_AN_EARLIER_TURN, id="s4"),
            pytest.param(_ORPHAN_MONITOR_LEFT_BY_A_SUB_AGENT, id="s1"),
        ],
    )
    async def test_activity_and_alerts_follow_the_recording(
        self, session, scheduler, rows,
    ) -> None:
        seen = self._replay(
            session, scheduler, [(at, kind, payload) for at, kind, payload, *_ in rows],
        )

        assert seen == [(activity, alerts) for *_, activity, alerts in rows]


class TestSummarizeHookPayload:
    """The summary goes to the agent log on every hook. A task's command and
    description, and the prompt, are the user's work and must stay out of it."""

    def test_summary_keeps_only_what_the_gate_decides_on(self) -> None:
        payload = {
            "prompt_id": "0123456789abcdef",
            "prompt": "<task-notification>PRIVATE-PROMPT",
            "agent_id": "a1",
            "last_assistant_message": "PRIVATE-REPLY",
            "background_tasks": [
                {
                    "id": "t1",
                    "type": "shell",
                    "status": "running",
                    "command": "PRIVATE-COMMAND",
                    "description": "PRIVATE-DESCRIPTION",
                },
                {"id": "t2", "type": "subagent", "status": "completed"},
            ],
            "session_crons": [{"id": "c1", "prompt": "PRIVATE-CRON"}],
        }

        assert summarize_hook_payload(payload) == {
            "prompt_id": "01234567",
            "subagent": True,
            "cli_prompt": True,
            "tasks": "shell:running,subagent:completed",
            "crons": 1,
        }

    @pytest.mark.parametrize(
        ("payload", "summary"),
        [
            (
                {"background_tasks": [{"type": "x" * 500, "status": {"a": 1}}] * 20},
                {"tasks": ",".join(["x" * 24 + ":?"] * 8), "tasks_total": 20},
            ),
            ({"background_tasks": [None, 7, {"type": None}]}, {"tasks": "?:?"}),
            ({"prompt_id": 42, "background_tasks": "running", "session_crons": 5}, {}),
        ],
    )
    def test_summary_is_bounded_and_survives_any_payload(
        self, payload, summary,
    ) -> None:
        # It runs inside the handler that answers a blocking hook, on a body
        # the agent does not control.
        assert summarize_hook_payload(payload) == summary
