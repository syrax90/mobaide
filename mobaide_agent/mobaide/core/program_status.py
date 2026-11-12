"""Attention from the program-status reports a CLI writes to its terminal.

Codex (0.163+), Antigravity ``agy`` (1.4+), GitHub Copilot (1.0.106+) and
Claude Code report what they are doing with OSC 7501 (the Program Status
Protocol)::

    ESC ] 7501 ; state=blocked:kind=permission:app=codex ST

``state`` is ``idle | working | done | blocked | error | clear``; ``kind``
(``permission | question | auth``) qualifies ``blocked``. The reports already
flow through the PTY reader, so a session gets "your move" notifications with no
hooks, no config file and no trust prompt — which is how every terminal session
without Claude Code hooks raises attention. Claude sessions keep their hooks as
the only source: a session never listens to both.

Codex and Copilot write reports unconditionally. ``agy`` and Claude Code first send the
support query ``ESC ] 7501 ; ? ST`` and report only when the terminal answers,
so :class:`ProgramStatusResponder` answers it for the sessions that listen.

Spec: https://www.superlogical.com/rex/docs/build/program-status (rev 0.4).
"""
from __future__ import annotations

import asyncio
import enum
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

# Longest report the spec allows, OSC through ST. A longer one is discarded.
MAX_REPORT_BYTES = 4096

_INTRO = b"\x1b]7501;"
_QUERY_RE = re.compile(rb"\x1b\]7501;\?(\x07|\x1b\\)")
# The proper prefixes of a support query, held across a read boundary.
_QUERY_PREFIXES = tuple(b"\x1b]7501;?\x1b"[:n] for n in range(1, 10))
_KEY_RE = re.compile(r"[a-z]{1,16}")
_VALUE_RE = re.compile(r"[A-Za-z0-9_.,+/=-]*")

# Trailing-edge window between ``done``/``error`` and the alert. A CLI that
# starts its next turn on its own (a queued message) reports ``working`` again
# within milliseconds, and that turn's end is the one worth announcing.
PROGRAM_STATUS_QUIET_SECONDS = 1.5

# Least time between two alerts of one session. A program that flips between
# states, or recorded output replayed in a terminal (``cat`` of a session log),
# must not turn every flip into a push. An alert due sooner waits out the gap
# instead of being dropped, and any state change meanwhile cancels the wait: a
# prompt still standing at the end alerts once, one answered meanwhile never.
PROGRAM_STATUS_MIN_ALERT_SPACING_SECONDS = 5.0

# The answer to the support query, in the spec's long form. It must not equal
# the query: a tty that echoes input back (``ECHO`` without ``ECHOCTL``) would
# otherwise hand every reply straight back as a new query, forever.
_QUERY_REPLY_BODY = (
    b"\x1b]7501;?:states=idle,working,done,blocked,error:kinds=permission,question,auth"
)


class ProgramState(enum.Enum):
    IDLE = "idle"
    WORKING = "working"
    DONE = "done"
    BLOCKED = "blocked"
    ERROR = "error"
    CLEAR = "clear"


_STATES = {s.value: s for s in ProgramState}
_BLOCKED_KINDS = frozenset({"permission", "question", "auth"})


@dataclass(frozen=True, slots=True)
class ProgramStatus:
    state: ProgramState
    kind: str | None = None
    app: str | None = None


def parse_program_status(payload: bytes) -> ProgramStatus | None:
    """Parse the body of one report (between ``7501;`` and ST).

    Follows the spec's tolerance rules: a malformed pair is skipped, a repeated
    key keeps its last value, and a report without a known ``state`` is
    ignored. Only the root record (no ``id``) describes the program itself; a
    report for a sub-record returns ``None``.
    """
    if len(payload) > MAX_REPORT_BYTES:
        return None
    pairs: dict[str, str] = {}
    for part in payload.decode("latin-1").split(":"):
        key, sep, value = part.partition("=")
        key = key.strip()
        if key == "id":
            # A sub-record, or a malformed id the spec says to ignore.
            return None
        value = value.strip()
        if sep and _KEY_RE.fullmatch(key) and _VALUE_RE.fullmatch(value):
            pairs[key] = value
    state = _STATES.get(pairs.get("state", ""))
    if state is None:
        return None
    kind = pairs.get("kind")
    if state is not ProgramState.BLOCKED or kind not in _BLOCKED_KINDS:
        kind = None
    return ProgramStatus(state, kind, pairs.get("app") or None)


class ProgramStatusTap:
    """Passive tap that pulls OSC 7501 reports out of a PTY byte stream.

    Like :class:`~mobaide.core.osc_parser.OscTitleExtractor`, it never changes
    the bytes. A report split across reads is reassembled from a bounded carry.
    """

    def __init__(self) -> None:
        self._carry = b""

    def feed(self, chunk: bytes) -> list[ProgramStatus]:
        if self._carry:
            data = self._carry + chunk
            self._carry = b""
        elif b"\x1b]75" not in chunk and b"\x1b" not in chunk[-6:]:
            return []
        else:
            data = chunk
        reports: list[ProgramStatus] = []
        pos = 0
        while True:
            start = data.find(_INTRO, pos)
            if start < 0:
                break
            body = start + len(_INTRO)
            end = _find_terminator(data, body)
            if end < 0:
                if len(data) - start <= MAX_REPORT_BYTES:
                    self._carry = data[start:]
                return reports
            report = parse_program_status(data[body:end])
            if report is not None:
                reports.append(report)
            pos = end + 1
        tail = data[-(len(_INTRO) - 1):]
        for size in range(min(len(tail), len(_INTRO) - 1), 0, -1):
            if _INTRO.startswith(tail[-size:]):
                self._carry = tail[-size:]
                break
        return reports


def _find_terminator(data: bytes, start: int) -> int:
    """Index of the BEL or of the ESC that opens ``ESC \\``, else -1.

    A report body never contains ESC, so the first ESC ends it either way; a
    bare ESC at the very end of ``data`` may still become ``ESC \\``.
    """
    bel = data.find(b"\x07", start)
    esc = data.find(b"\x1b", start)
    if esc >= 0 and (bel < 0 or esc < bel):
        return esc if esc + 1 < len(data) else -1
    return bel


class ProgramStatusResponder:
    """Answers the OSC 7501 support query on the session's behalf.

    The query is stripped from the stream (as :mod:`osc_color_responder` does
    for color queries), so a replayed ring can never make a client emulator
    answer it a second time. Use only on sessions that listen to the reports.
    """

    def __init__(self) -> None:
        self._carry = b""
        self._pending = bytearray()

    def process(self, chunk: bytes) -> bytes:
        data = self._carry + chunk if self._carry else chunk
        self._carry = b""
        if b"\x1b]7501;?" in data:
            data = _QUERY_RE.sub(self._answer, data)
        if b"\x1b" in data[-9:]:
            for prefix in reversed(_QUERY_PREFIXES):
                if data.endswith(prefix):
                    self._carry = prefix
                    return data[: -len(prefix)]
        return data

    def _answer(self, match: re.Match[bytes]) -> bytes:
        self._pending += _QUERY_REPLY_BODY + match.group(1)
        return b""

    def pending_replies(self) -> bytes:
        if not self._pending:
            return b""
        replies = bytes(self._pending)
        self._pending.clear()
        return replies


class ProgramStatusGate:
    """Turns one session's program-status transitions into attention.

    * ``blocked`` (permission or question) alerts ``awaiting_permission`` at
      once. ``blocked`` for ``auth`` is ignored: ``agy`` reports it for a moment
      at every start, and a sign-in prompt is answered on the PC anyway.
    * ``done`` alerts ``turn_finished`` and ``error`` alerts ``turn_failed``,
      both after a short quiet window that a new ``working`` cancels. A failed
      turn never also alerts as finished.
    * ``working``, ``idle`` and ``clear`` cancel any pending alert and drop a
      standing ``awaiting_permission``: the prompt was answered or dismissed,
      possibly by another client on a shared thread, or the program exited.
    * Alerts are at least ``min_alert_spacing_seconds`` apart (see
      :data:`PROGRAM_STATUS_MIN_ALERT_SPACING_SECONDS`).

    A finished turn is otherwise cleared only by client input, as for Claude
    sessions, so a turn another client started on a shared thread cannot alert
    again while its predecessor's "your move" is still standing.

    Only a change of state alerts. The spec lets a program repeat a report, and
    a client keystroke clears the badge, so a repeated ``done`` or ``blocked``
    after a scroll would otherwise announce the same turn or prompt twice.
    """

    def __init__(
        self,
        *,
        on_alert: Callable[[str], None],
        on_clear_permission: Callable[[], None],
        is_running: Callable[[], bool],
        current_attention: Callable[[], str | None],
        quiet_seconds: float | None = None,
        min_alert_spacing_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._on_alert = on_alert
        self._on_clear_permission = on_clear_permission
        self._is_running = is_running
        self._current_attention = current_attention
        self._quiet_seconds = (
            PROGRAM_STATUS_QUIET_SECONDS if quiet_seconds is None else quiet_seconds
        )
        self._min_alert_spacing = (
            PROGRAM_STATUS_MIN_ALERT_SPACING_SECONDS
            if min_alert_spacing_seconds is None
            else min_alert_spacing_seconds
        )
        self._clock = clock
        self._timer: asyncio.TimerHandle | None = None
        self._armed_reason: str | None = None
        self._last: tuple[ProgramState, str | None] | None = None
        self._last_alert_at: float | None = None
        self.last_app: str | None = None

    def handle(self, status: ProgramStatus) -> None:
        if status.app:
            self.last_app = status.app
        state = status.state
        report = (state, status.kind)
        previous = self._last
        repeated = report == previous
        self._last = None if state is ProgramState.CLEAR else report
        if repeated and state in (ProgramState.BLOCKED, ProgramState.DONE, ProgramState.ERROR):
            return
        if state is ProgramState.BLOCKED:
            if status.kind == "auth":
                return
            self._schedule("awaiting_permission", 0.0)
        elif state is ProgramState.DONE:
            # A ``done`` straight after ``error`` closes the same failed turn:
            # the failure is its outcome, whether it alerted yet or not.
            failed = previous is not None and previous[0] is ProgramState.ERROR
            if not failed and self._armed_reason != "turn_failed":
                self._schedule("turn_finished", self._quiet_seconds)
        elif state is ProgramState.ERROR:
            self._schedule("turn_failed", self._quiet_seconds)
        else:
            self.cancel()
            if self._current_attention() == "awaiting_permission":
                self._on_clear_permission()

    def cancel(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._armed_reason = None

    def _schedule(self, reason: str, delay: float) -> None:
        self.cancel()
        if self._last_alert_at is not None:
            gap = self._last_alert_at + self._min_alert_spacing - self._clock()
            delay = max(delay, gap)
        if delay <= 0:
            self._alert(reason)
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._alert(reason)
            return
        self._timer = loop.call_later(delay, self._fire, reason)
        self._armed_reason = reason

    def _fire(self, reason: str) -> None:
        self._timer = None
        self._armed_reason = None
        self._alert(reason)

    def _alert(self, reason: str) -> None:
        if not self._is_running():
            return
        current = self._current_attention()
        if current == "ended" or current == reason:
            return
        if reason == "turn_finished" and current == "turn_failed":
            return
        self._last_alert_at = self._clock()
        self._on_alert(reason)
