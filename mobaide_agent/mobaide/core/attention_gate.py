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

``stop_hook_active``
    Another hook already blocked this turn's end, so Claude will continue.
``background_tasks``
    In-flight background work registered in the session, each with a status in
    ``pending | running | completed | failed | killed | paused``. Upstream
    documents it as distinguishing "session is done" from "session is paused
    waiting for background work to wake it".
``session_crons``
    Scheduled wakeups (``CronCreate``, ``ScheduleWakeup``, ``/loop``) that will
    re-drive this session later.
``prompt_id``
    Correlates a user prompt with every event until the next prompt, which makes
    it a free per-turn dedupe key.

State machine, one instance per session:

* ``stop`` — cancel any pending window and drop when the payload says work
  continues or this turn already alerted; otherwise (re)arm the quiet window.
* window elapses — alert ``turn_finished``, unless the session died or another
  attention reason was raised meanwhile.
* ``idle`` (``Notification``/``idle_prompt``) — an independent backstop that
  alerts immediately. Claude Code raises it after 60 s of genuine idleness, so
  it covers what the payload-driven path suppresses wrongly: a task wedged in
  ``running``, or a CLI too old to send ``background_tasks``. Both paths share
  the turn dedupe, so neither can double-alert and neither can starve the other.
* ``permission`` — always alerts immediately. Being blocked on the user is never
  debounced.
* ``end`` — cancels the window *before* alerting, so a pending ``turn_finished``
  cannot fire immediately ahead of ``ended``. Turn state resets rather than
  latches: ``SessionEnd`` also fires with reason ``clear`` for ``/clear``, which
  leaves the PTY running.
* ``prompt_submit``, or any user keystroke via ``reset_turn`` — cancels the
  window and resets the turn so the next stop can alert again.
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


def _has_active_background_work(payload: Mapping[str, Any]) -> bool:
    """Whether the payload shows work that will wake the session on its own."""
    crons = payload.get("session_crons")
    if isinstance(crons, (list, tuple)) and crons:
        return True
    tasks = payload.get("background_tasks")
    if not isinstance(tasks, (list, tuple)):
        return False
    # ``status`` must be checked for str-ness before the membership test: set
    # lookup hashes the left operand, so a JSON array or object there would
    # raise ``TypeError`` out of a handler that must never 500 — this one
    # answers Claude Code's blocking ``Stop`` hook.
    return any(
        isinstance(task, Mapping)
        and isinstance(task.get("status"), str)
        and task["status"] in ATTENTION_ACTIVE_TASK_STATUSES
        for task in tasks
    )


def _prompt_id(payload: Mapping[str, Any]) -> str | None:
    value = payload.get("prompt_id")
    return value if isinstance(value, str) and value else None


class AttentionGate:
    """Decides whether a Claude Code hook becomes a user-visible attention.

    ``on_alert`` is called only when an alert should actually reach the user; a
    suppressed hook leaves the session's public attention untouched, which
    matters because clients re-post notifications for any session carrying an
    attention value in a full-state snapshot.

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
        quiet_seconds: float | None = None,
    ) -> None:
        self._on_alert = on_alert
        self._is_running = is_running
        self._current_attention = current_attention
        self._quiet_seconds = (
            ATTENTION_QUIET_SECONDS if quiet_seconds is None else quiet_seconds
        )
        self._timer: asyncio.TimerHandle | None = None
        self._armed_prompt_id: str | None = None
        self._alerted_prompt_id: str | None = None
        self._alerted_at: float | None = None

    def handle(self, kind: HookKind, payload: Mapping[str, Any]) -> None:
        """Apply one hook to the gate. Never raises on a malformed payload."""
        if kind is HookKind.PERMISSION:
            self.cancel()
            self._on_alert("awaiting_permission", None)
        elif kind is HookKind.IDLE:
            self.cancel()
            self._alert_turn(_prompt_id(payload))
        elif kind is HookKind.STOP:
            self._on_stop(payload)
        elif kind is HookKind.PROMPT_SUBMIT:
            self.reset_turn()
        elif kind is HookKind.END:
            self.reset_turn()
            self._on_alert("ended", str(payload.get("reason") or "other"))

    def cancel(self) -> None:
        """Drop a pending quiet window, keeping the turn's alerted state."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._armed_prompt_id = None

    def reset_turn(self) -> None:
        """Cancel the window and forget this turn, so the next stop can alert."""
        self.cancel()
        self._alerted_prompt_id = None
        self._alerted_at = None

    def _on_stop(self, payload: Mapping[str, Any]) -> None:
        if payload.get("stop_hook_active") is True:
            self.cancel()
            return
        if _has_active_background_work(payload):
            self.cancel()
            return
        prompt_id = _prompt_id(payload)
        if prompt_id is not None and prompt_id == self._alerted_prompt_id:
            self.cancel()
            return
        self._arm(prompt_id)

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
        self._armed_prompt_id = None
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
