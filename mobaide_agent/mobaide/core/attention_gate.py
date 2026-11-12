"""Debounce between Claude Code's hooks and a user-visible attention alert.

``Stop`` fires every time the main agent finishes responding, which includes
every re-invocation caused by a background task or subagent completing. Mapping
it straight to ``turn_finished`` produced one notification per model turn: a
session that fans out N background agents alerted N+1 times, at most once
usefully. Upstream has no "the session is really idle" event
(anthropics/claude-code#45781), and ``Stop`` can fire several times inside one
turn when system reminders interleave (anthropics/claude-code#54360).

The ``Stop`` payload does carry everything needed to decide. Every field below
is optional — an older CLI simply omits it, and the gate degrades to the quiet
window plus a time cooldown:

``background_tasks``
    In-flight background work registered in the session, each with a status in
    ``pending | running | completed | failed | killed | paused``. Upstream
    documents it as distinguishing "session is done" from "session is paused
    waiting for background work to wake it".
``session_crons``
    Scheduled wakeups (``CronCreate``, ``ScheduleWakeup``, ``/loop``) that will
    re-drive this session later.
``prompt_id``
    Rotates on every ``UserPromptSubmit`` — a typed prompt, but also the
    ``<task-notification>`` or sub-agent hand-back that wakes the session — so
    it is a free dedupe key for one model turn.

``stop_hook_active`` is deliberately ignored. It marks the stop that follows a
continuation an earlier ``Stop`` hook forced, and the CLI advises hooks to let
such a stop through — so for a well-behaved hook it is the real end of the
turn, not a sign that work continues. A hook that blocks again (a ``/goal``
loop) just yields another ``Stop``, which re-arms the window; a continuation
longer than the window still alerts early and dedupes the real end.

State machine, one instance per session. Besides alerts, the gate reports the
session's ``activity``, which the clients render as a persistent indicator
independent of whether an alert fired:

``working``
    The main agent is mid-turn, or a background agent is running.
``waiting``
    The main agent is parked and only passive background work is in flight: a
    shell, a monitor, a scheduled wakeup. The session will normally resume by
    itself, so this is neither "working" nor "your move".
``idle``
    Nothing is in flight. Only this state may alert ``turn_finished``.

The last ``Stop`` payload is the authority on which of the three applies:

* ``prompt_submit`` — resets the turn so the next stop can alert again;
  activity ``working``. A prompt the user typed also marks every background
  task parked at that moment as long-lived (see below). A task notification,
  a sub-agent hand-back and a scheduled wakeup are not typed.
* ``tool`` (``PreToolUse``) — a heartbeat. A main-agent call proves a turn is
  live even when no ``UserPromptSubmit`` announced it (a ``!`` command the
  model answers), so it cancels a pending window; activity ``working``. A
  sub-agent call counts only while the last ``Stop`` listed in-flight work: a
  sub-agent that resumes after its own background shell ends raises no
  main-level hook until it hands back. A heartbeat never forgets the turn's
  dedupe key: a ``/goal`` loop stops and continues many times in one turn, and
  each continuation would otherwise alert again.
* ``stop`` — a running background agent keeps the session ``working``;
  passive work alone makes it ``waiting``. Either cancels any pending window.
  With nothing in flight, (re)arm the quiet window.
* window elapses — activity ``idle``, then alert ``turn_finished`` unless the
  session died, another attention reason was raised meanwhile, the turn already
  alerted, or a keystroke muted the window.
* ``idle`` (``Notification``/``idle_prompt``) — Claude Code raises it 60 s
  after the main agent stops, and a running shell or monitor does not delay it.
  So it means "done" only when the last ``Stop`` listed nothing in flight:
  then it alerts at once, which covers a turn that ended without a ``Stop``
  (an interrupt) and a CLI too old to send ``background_tasks``. Otherwise
  the session keeps the activity that ``Stop`` gave it and never turns
  ``idle``: a sub-agent parked on its own background shell is not listed as
  running, and alerting here told the user the session was done minutes before
  it resumed. It also stays silent once the quiet window has alerted for the
  turn. Its ``prompt_id`` can differ from the ``Stop``'s, so the dedupe key
  would not stop a second alert for a turn the user has already seen and
  dismissed. A muted window does not count: it showed the user nothing.
* ``permission`` — always alerts immediately. Being blocked on the user is never
  debounced. Activity is left alone: the turn resumes once the user answers.
* ``end`` — cancels the window *before* alerting, so a pending ``turn_finished``
  cannot fire immediately ahead of ``ended``. Turn state resets rather than
  latches: ``SessionEnd`` also fires with reason ``clear`` for ``/clear``, which
  leaves the PTY running at an idle prompt.
* a user keystroke (``mute``) — the pending window still elapses into ``idle``,
  but raises no alert: the user is plainly present.

Long-lived tasks. A dev server never completes, so every ``Stop`` after it
starts lists it, and a session that ran one would never alert again. A passive
task still in flight when the user types the next prompt is therefore treated
as long-lived and stops counting as in-flight work. The turn that started it
still ends ``waiting``: nothing in the payload tells a dev server from a build
the session will resume after.
"""
from __future__ import annotations

import asyncio
import enum
import time
from collections.abc import Callable, Mapping
from typing import Any

# Trailing-edge debounce applied to ``Stop`` before it becomes a notification.
# ``prompt_id`` and ``background_tasks`` carry the real suppression load, so this
# only has to absorb a burst of ``Stop``s inside one turn — sub-second in the
# interleaved-system-reminder case. Kept short because the product value of
# "your move" is that it arrives promptly.
ATTENTION_QUIET_SECONDS = 8.0

# Minimum spacing between ``turn_finished`` alerts when the CLI sends no
# ``prompt_id`` to dedupe on. Only reachable on an older Claude Code; a longer
# window would swallow legitimate back-to-back short turns and trade a spam bug
# for a missed-notification bug.
ATTENTION_TURN_COOLDOWN_SECONDS = 120.0

# ``background_tasks[].status`` values that mean work is still in flight. We
# filter on the active set rather than trusting the producer to have pruned the
# list, so a task left ``failed``/``killed`` cannot suppress alerts forever.
ATTENTION_ACTIVE_TASK_STATUSES = frozenset({"pending", "running", "paused"})

# ``background_tasks[].type`` values that compute nothing by themselves: the
# session is parked on them. Every other type (``subagent``, ``workflow``,
# ``teammate``, a type this agent does not know yet) is an agent at work.
ATTENTION_PASSIVE_TASK_TYPES = frozenset({"shell", "monitor"})

# CLI-written ``UserPromptSubmit`` prompts start with a tag (``<task-notification>``,
# ``<agent-message …>``). Matching any tag errs safe: a typed ``<`` prompt only
# delays long-lived marking by one prompt. Used only when ``source`` is absent.
_CLI_PROMPT_PREFIX = "<"

# The one tag that marks the user's own input: the CLI wraps a collapsed paste
# in it, and the apps send every multi-line composer prompt as a paste.
_PASTE_PROMPT_PREFIX = "<pasted_content"

# Bounds for the per-hook log summary: enough to diagnose a wrong state from
# the log, never an unbounded echo of a payload.
_SUMMARY_MAX_TASKS = 8
_SUMMARY_MAX_FIELD_CHARS = 24

# Attention reasons a "your move" alert must not overwrite: it is either already
# on screen or terminal. The prompt-blocking reasons are deliberately absent — a
# ``Stop`` or ``idle_prompt`` arriving proves the prompt was answered (on the
# host, if not from the app), so a stale ``awaiting_permission`` /
# ``awaiting_os_permission`` badge should give way rather than mask the session's
# real state.
_TURN_ALERT_BLOCKED_BY = frozenset({"turn_finished", "ended"})


class HookKind(enum.Enum):
    """Gate-facing classification of an incoming Claude Code hook.

    Baked into each hook URL at spawn (``hook_settings``) rather than derived
    from the payload: Claude Code skips its matcher filter entirely when an
    event carries no match query, in which case every registered ``Notification``
    matcher fires and the body alone cannot tell ``permission_prompt`` from
    ``idle_prompt``.
    """

    PERMISSION = "permission"
    IDLE = "idle"
    STOP = "stop"
    PROMPT_SUBMIT = "prompt_submit"
    TOOL = "tool"
    END = "end"


# Fallback for a settings file written before the agent was upgraded: its hook
# URLs carry no ``hook`` param, so the event name has to classify them. Matches
# the pre-gate behaviour, where ``Notification`` was registered for permission
# prompts only.
LEGACY_EVENT_TO_KIND = {
    "Notification": HookKind.PERMISSION,
    "Stop": HookKind.STOP,
    "UserPromptSubmit": HookKind.PROMPT_SUBMIT,
    "SessionEnd": HookKind.END,
}


def resolve_hook_kind(hook: str, hook_event_name: str) -> HookKind | None:
    """Classify a hook POST, preferring the URL param over the event name."""
    if hook:
        try:
            return HookKind(hook)
        except ValueError:
            return None
    return LEGACY_EVENT_TO_KIND.get(hook_event_name)


def _active_tasks(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The payload's background tasks that are still in flight."""
    tasks = payload.get("background_tasks")
    if not isinstance(tasks, (list, tuple)):
        return []
    # ``status`` must be checked for str-ness before the membership test: set
    # lookup hashes the left operand, so a JSON array or object there would
    # raise ``TypeError`` out of a handler that must never 500 — this one
    # answers Claude Code's blocking ``Stop`` hook.
    return [
        task
        for task in tasks
        if isinstance(task, Mapping)
        and isinstance(task.get("status"), str)
        and task["status"] in ATTENTION_ACTIVE_TASK_STATUSES
    ]


def _is_passive(task: Mapping[str, Any]) -> bool:
    """Whether an in-flight task is something the session is parked on."""
    if task["status"] == "paused":
        return True
    task_type = task.get("type")
    return isinstance(task_type, str) and task_type in ATTENTION_PASSIVE_TASK_TYPES


def _passive_task_ids(payload: Mapping[str, Any]) -> frozenset[str]:
    return frozenset(
        task["id"]
        for task in _active_tasks(payload)
        if _is_passive(task) and isinstance(task.get("id"), str)
    )


def _in_flight_activity(
    payload: Mapping[str, Any], long_lived_ids: frozenset[str],
) -> str | None:
    """The activity a ``Stop`` payload implies, or ``None`` with nothing in flight."""
    waiting = _has_crons(payload)
    for task in _active_tasks(payload):
        if not _is_passive(task):
            return "working"
        task_id = task.get("id")
        if not isinstance(task_id, str) or task_id not in long_lived_ids:
            waiting = True
    return "waiting" if waiting else None


def _has_crons(payload: Mapping[str, Any]) -> bool:
    crons = payload.get("session_crons")
    return isinstance(crons, (list, tuple)) and bool(crons)


def _is_cli_prompt(payload: Mapping[str, Any]) -> bool:
    prompt = payload.get("prompt")
    if not isinstance(prompt, str):
        return False
    text = prompt.lstrip()
    return text.startswith(_CLI_PROMPT_PREFIX) and not text.startswith(
        _PASTE_PROMPT_PREFIX,
    )


def _is_user_prompt(payload: Mapping[str, Any], *, wakeup_pending: bool) -> bool:
    """Whether the user typed this ``UserPromptSubmit``, not the CLI itself.

    A scheduled wakeup submits its prompt as plain text, exactly like a typed
    one, so while the last ``Stop`` listed a wakeup no prompt counts as typed.
    """
    source = payload.get("source")
    if isinstance(source, str) and source:
        return source == "user"
    return not wakeup_pending and not _is_cli_prompt(payload)


def _is_subagent_call(payload: Mapping[str, Any]) -> bool:
    agent_id = payload.get("agent_id")
    return isinstance(agent_id, str) and bool(agent_id)


def _clip(value: Any) -> str:
    return value[:_SUMMARY_MAX_FIELD_CHARS] if isinstance(value, str) else "?"


def summarize_hook_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The payload fields the gate decides on, bounded and safe to log.

    Task types and statuses only: a task's ``command`` and ``description``, and
    the prompt text, are the user's work and stay out of the log.
    """
    summary: dict[str, Any] = {}
    prompt_id = _prompt_id(payload)
    if prompt_id is not None:
        summary["prompt_id"] = prompt_id[:8]
    if _is_subagent_call(payload):
        summary["subagent"] = True
    if _is_cli_prompt(payload):
        summary["cli_prompt"] = True
    source = payload.get("source")
    if isinstance(source, str) and source:
        summary["source"] = _clip(source)
    tasks = payload.get("background_tasks")
    if isinstance(tasks, (list, tuple)):
        summary["tasks"] = ",".join(
            f"{_clip(task.get('type'))}:{_clip(task.get('status'))}"
            for task in tasks[:_SUMMARY_MAX_TASKS]
            if isinstance(task, Mapping)
        )
        if len(tasks) > _SUMMARY_MAX_TASKS:
            summary["tasks_total"] = len(tasks)
    crons = payload.get("session_crons")
    if isinstance(crons, (list, tuple)) and crons:
        summary["crons"] = len(crons)
    return summary


def _prompt_id(payload: Mapping[str, Any]) -> str | None:
    value = payload.get("prompt_id")
    return value if isinstance(value, str) and value else None


def _ignore_activity(_activity: str) -> None:
    return None


class AttentionGate:
    """Decides whether a Claude Code hook becomes a user-visible attention.

    ``on_alert`` is called only when an alert should actually reach the user; a
    suppressed hook leaves the session's public attention untouched, which
    matters because clients re-post notifications for any session carrying an
    attention value in a full-state snapshot. ``on_activity`` receives every
    ``working`` / ``waiting`` / ``idle`` transition the hooks imply, alerted or
    not; repeats are expected and the receiver dedupes them.

    Timing uses ``loop.call_later`` rather than a sleeping task: cancelling a
    task after its sleep has already resumed still runs the body, since there is
    no further await point at which ``CancelledError`` could be delivered.
    """

    def __init__(
        self,
        *,
        on_alert: Callable[[str, str | None], None],
        is_running: Callable[[], bool],
        current_attention: Callable[[], str | None],
        on_activity: Callable[[str], None] | None = None,
        quiet_seconds: float | None = None,
    ) -> None:
        self._on_alert = on_alert
        self._on_activity = on_activity or _ignore_activity
        self._is_running = is_running
        self._current_attention = current_attention
        self._quiet_seconds = (
            ATTENTION_QUIET_SECONDS if quiet_seconds is None else quiet_seconds
        )
        self._timer: asyncio.TimerHandle | None = None
        self._armed_prompt_id: str | None = None
        self._muted = False
        self._alerted_prompt_id: str | None = None
        self._alerted_at: float | None = None
        self._parked_activity: str | None = None
        self._settled = False
        self._wakeup_pending = False
        self._passive_ids: frozenset[str] = frozenset()
        self._long_lived_ids: frozenset[str] = frozenset()

    def handle(self, kind: HookKind, payload: Mapping[str, Any]) -> None:
        """Apply one hook to the gate. Never raises on a malformed payload."""
        if kind is HookKind.PERMISSION:
            self.cancel()
            self._on_alert("awaiting_permission", None)
        elif kind is HookKind.IDLE:
            self.cancel()
            if self._parked_activity is not None:
                self._on_activity(self._parked_activity)
                return
            self._on_activity("idle")
            if not self._settled:
                self._alert_turn(_prompt_id(payload))
        elif kind is HookKind.STOP:
            self._on_stop(payload)
        elif kind is HookKind.PROMPT_SUBMIT:
            if _is_user_prompt(payload, wakeup_pending=self._wakeup_pending):
                self._long_lived_ids = self._passive_ids
            self.reset_turn()
            self._on_activity("working")
        elif kind is HookKind.TOOL:
            self._on_tool(payload)
        elif kind is HookKind.END:
            self.reset_turn()
            self._on_activity("idle")
            self._on_alert("ended", str(payload.get("reason") or "other"))

    def cancel(self) -> None:
        """Drop a pending quiet window, keeping the turn's alerted state."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._armed_prompt_id = None
        self._muted = False

    def mute(self) -> None:
        """Let a pending window elapse into ``idle`` without alerting.

        What a keystroke warrants: the user is plainly present, so a queued
        "your move" should not fire — but the turn still ends, and the activity
        indicator must not stay ``working`` because the user typed or scrolled.
        """
        if self._timer is not None:
            self._muted = True

    def reset_turn(self) -> None:
        """Cancel the window and forget this turn, so the next stop can alert."""
        self.cancel()
        self._alerted_prompt_id = None
        self._alerted_at = None
        self._parked_activity = None
        self._settled = False

    def _on_stop(self, payload: Mapping[str, Any]) -> None:
        self._settled = False
        self._wakeup_pending = _has_crons(payload)
        self._passive_ids = _passive_task_ids(payload)
        self._parked_activity = _in_flight_activity(payload, self._long_lived_ids)
        if self._parked_activity is not None:
            self.cancel()
            self._on_activity(self._parked_activity)
            return
        self._arm(_prompt_id(payload))

    def _on_tool(self, payload: Mapping[str, Any]) -> None:
        if _is_subagent_call(payload):
            # With nothing in flight this is one of the CLI's own helper agents
            # running after the turn; it must not pull a finished session back
            # to ``working``, because no ``Stop`` would follow to end it.
            if self._parked_activity is not None:
                self._on_activity("working")
            return
        self.cancel()
        self._settled = False
        self._on_activity("working")

    def _arm(self, prompt_id: str | None) -> None:
        self.cancel()
        self._armed_prompt_id = prompt_id
        if self._quiet_seconds <= 0:
            self._fire()
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No loop to schedule on (only reachable outside the agent's
            # runtime). Alerting straight away beats losing the signal.
            self._fire()
            return
        self._timer = loop.call_later(self._quiet_seconds, self._fire)

    def _fire(self) -> None:
        self._timer = None
        prompt_id = self._armed_prompt_id
        muted = self._muted
        self._armed_prompt_id = None
        self._muted = False
        self._settled = not muted
        self._on_activity("idle")
        if not muted:
            self._alert_turn(prompt_id)

    def _alert_turn(self, prompt_id: str | None) -> None:
        # The session-state check is what covers a session reaped without
        # ``stop()`` (the liveness poller removes crashed sessions from the
        # registry directly), so cancellation alone cannot be relied on.
        if not self._is_running():
            return
        if self._current_attention() in _TURN_ALERT_BLOCKED_BY:
            return
        if prompt_id is not None:
            if prompt_id == self._alerted_prompt_id:
                return
            self._alerted_prompt_id = prompt_id
        elif (
            self._alerted_at is not None
            and time.monotonic() - self._alerted_at < ATTENTION_TURN_COOLDOWN_SECONDS
        ):
            return
        self._alerted_at = time.monotonic()
        self._on_alert("turn_finished", None)
