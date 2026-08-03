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
    ATTENTION_QUIET_SECONDS,
    ATTENTION_TURN_COOLDOWN_SECONDS,
    AttentionGate,
    HookKind,
    resolve_hook_kind,
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

    def on_alert(self, attention: str, exit_reason: str | None = None) -> None:
        self.attention = attention
        self.calls.append((attention, exit_reason))

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

    async def test_window_defaults_to_the_module_constant(
        self, session, scheduler,
    ) -> None:
        gate = _gate(session)
        gate.handle(HookKind.STOP, {})
        assert [h.delay for h in scheduler.pending] == [ATTENTION_QUIET_SECONDS]

    async def test_zero_window_alerts_without_arming(self, session, scheduler) -> None:
        # ``SessionConfig.attention_quiet_seconds = 0`` means "no debounce".
        gate = _gate(session, quiet_seconds=0)
        gate.handle(HookKind.STOP, {})
        assert session.reasons == ["turn_finished"]
        assert scheduler.handles == []

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

    async def test_stop_hook_active_never_alerts(self, session, scheduler) -> None:
        gate = _gate(session)
        gate.handle(HookKind.STOP, {"stop_hook_active": True})
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

        assert scheduler.elapse() == 0
        assert session.reasons == ["turn_finished"]

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

    async def test_second_turn_after_the_cooldown_alerts(
        self, session, scheduler, clock,
    ) -> None:
        gate = _gate(session)
        gate.handle(HookKind.STOP, {})
        scheduler.elapse()
        session.user_responded()
        clock.advance(ATTENTION_TURN_COOLDOWN_SECONDS)

        gate.handle(HookKind.STOP, {})
        scheduler.elapse()

        assert session.reasons == ["turn_finished", "turn_finished"]


class TestIdleBackstop:
    async def test_idle_alerts_after_a_suppressed_stop(
        self, session, scheduler,
    ) -> None:
        # Claude Code raises ``idle_prompt`` after 60 s of real idleness, the
        # only signal left when a task is wedged in ``running`` or the CLI is
        # too old to report ``background_tasks`` at all.
        gate = _gate(session)
        gate.handle(
            HookKind.STOP,
            {"prompt_id": "p1", "background_tasks": [{"status": "running"}]},
        )

        gate.handle(HookKind.IDLE, {})

        assert session.reasons == ["turn_finished"]
        assert scheduler.pending == []

    async def test_idle_does_not_double_alert_an_alerted_turn(
        self, session, scheduler,
    ) -> None:
        gate = _gate(session)
        gate.handle(HookKind.STOP, {"prompt_id": "p1"})
        scheduler.elapse()
        session.user_responded()

        gate.handle(HookKind.IDLE, {"prompt_id": "p1"})

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

    @pytest.mark.parametrize("payload", [{}, {"reason": ""}, {"reason": None}])
    async def test_end_without_a_reason_falls_back_to_other(
        self, session, scheduler, payload,
    ) -> None:
        gate = _gate(session)
        gate.handle(HookKind.END, payload)
        assert session.calls == [("ended", "other")]

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

        assert scheduler.elapse() == 0
        assert session.reasons == ["turn_finished"]


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
    @pytest.mark.parametrize("kind", list(HookKind))
    def test_url_param_resolves_every_kind(self, kind) -> None:
        assert resolve_hook_kind(kind.value, "") is kind

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

    def test_unknown_event_name_without_a_param_is_unroutable(self) -> None:
        assert resolve_hook_kind("", "PreToolUse") is None
