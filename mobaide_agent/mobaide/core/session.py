from __future__ import annotations

import asyncio
import collections
import logging
import re
import sys
import time
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Any

from .attention_dedupe import SHARED_THREAD_APPS, SharedTurnDedupe, real_cwd
from .attention_gate import AttentionGate, HookKind
from .da_responder import DaResponder
from .macos_access import full_disk_access
from .models import SessionConfig, SessionEvent, SessionInfo, SessionKind, SessionState
from .osc_color_responder import OscColorResponder
from .osc_parser import OscTitleExtractor
from .program_status import ProgramStatusGate, ProgramStatusResponder, ProgramStatusTap
from .pty_backend import PtyBackend, create_pty_backend

logger = logging.getLogger(__name__)

# Belt-and-braces scrub of terminal query/response bytes that would otherwise
# leak into the ring (e.g. from sessions started before the agent-side
# interceptors were deployed, or a reply echoed by the child). Applied per-write
# inside ``_append_ring`` so the ring is always clean — the replay senders
# (``agent/ws/session_io.py`` and ``relay/client.py``) need no second sweep.
#
# Because it runs only on the ring path, this is also where CPR/DSR queries are
# neutralized: they are left intact on the *live* stream (a live client still
# answers correctly inside the child's query window) and removed only from the
# replayed history, so a reconnect can never re-trigger the client emulator into
# answering a stale query. The primary fixes remain the stateful interceptors in
# ``core/da_responder.py`` and ``core/osc_color_responder.py``, which strip
# queries before the child writes a reply at all.
#
# Matches:
#   * DA1/DA2 replies (CSI ? … c / CSI > … c) and DA3 (DCS ! | … ST)
#   * OSC 10/11/12 dynamic-color *queries*, single or compound (… ;? repeated,
#     BEL/ST). Only the query form
#     is scrubbed, never ``… ;rgb:…`` — a color *reply* is byte-identical to a
#     legitimate color *set*, and the reply never reaches the output ring anyway
#     (it returns via the input channel). The query is what makes a replayed
#     stream re-trigger the client emulator.
#   * DSR / CPR / DECDSR queries (CSI 5 n / CSI 6 n / CSI ? 6 n)
#
# This scrub is stateless, yet it never has to rejoin a query split across PTY
# reads: ``DaResponder.process`` runs first in the reader loop and carries any
# unfinished trailing CSI (up to its 8-byte carry window) into the next read, so
# a CPR/DSR query straddling a chunk boundary (e.g. ``ESC[`` then ``6n``) is
# already reassembled before ``_append_ring`` applies this regex — and the
# longest sequence matched here (``ESC[?6n``, 5 bytes) fits well inside that
# window. OSC color queries are likewise removed upstream by OscColorResponder,
# so the OSC alternation here is belt-and-braces.
_RING_SCRUB_RE = re.compile(
    rb"\x1b\[\?[\d;]*c"
    rb"|\x1b\[>[\d;]*c"
    rb"|\x1bP![|][^\x1b]*\x1b\\"
    rb"|\x1b\]1[012](?:;\?)+(?:\x07|\x1b\\)"
    rb"|\x1b\[(?:[56]|\?6)n",
)

# Default sliding-window cap on the raw PTY ring buffer replayed to clients
# on (re)connect. Per-session overrides flow through ``SessionConfig
# .ring_buffer_bytes``; this constant just supplies the field's default and
# is referenced by tests that exercise the default. ~4 MB covers tens of
# minutes of dense Ink/React TUI output without disk persistence.
RING_MAX_BYTES = 4 * 1024 * 1024

# Bytes per chunk when replaying the ring on reconnect. Small enough that
# each ``emulator.append()`` on Android's main thread finishes well under a
# UI frame, so the user keeps seeing the "Replaying buffer…" overlay
# progress smoothly instead of a multi-second freeze.
REPLAY_CHUNK_BYTES = 64 * 1024

# The session-internal event queue is bounded.  When it fills, the oldest
# output event is discarded (drop-oldest policy) so the PTY reader never
# stalls.  The ring buffer retains recent bytes for reconnect replay.
EVENT_QUEUE_MAXSIZE = 64

# Trailing-edge debounce window for OSC-title updates. Ink/React redraws
# can flip the title several times per render tick; coalescing to ~2 Hz
# keeps the WebSocket fanout cheap without lagging human-visible label
# changes.
TITLE_COALESCE_SECONDS = 0.5

# Pause between writing submitted text and the CR that executes it. Ink TUIs
# (Claude CLI) batch stdin per render tick and treat a CR arriving in the
# same chunk as pasted content — only a CR landing as its own later read is
# interpreted as the Enter key. 0.2s is 2x the proven post-Ctrl+C redraw
# pause, with margin for ConPTY input coalescing on Windows hosts.
SUBMIT_CR_DELAY_SECONDS = 0.2

# Pause between the line-clear keys clients prepend to a submit (Ctrl+A,
# Ctrl+K) and a bracketed paste that follows them. Claude CLI mishandles a
# paste it collapses to "[Pasted text #N]" when those keys share its stdin
# chunk: the following CR is swallowed and the prompt is never submitted.
PASTE_PREFIX_DELAY_SECONDS = 0.2

BRACKETED_PASTE_START = b"\x1b[200~"

# Pause after Ctrl+C so the TUI cancels stale input and redraws a fresh
# prompt before replacement text arrives.
EXECUTE_CLEAR_DELAY_SECONDS = 0.1

# A freshly-spawned Claude renders its full TUI (kilobytes) within a second or
# two; a child blocked on an invisible macOS file-access (TCC) modal emits at
# most a few terminal-init bytes then stalls. If a session is still RUNNING,
# has had no user input, and has produced fewer than STARTUP_STALL_MIN_BYTES
# this many seconds after spawn, the agent raises an ``awaiting_os_permission``
# attention so a remote client sees an actionable "approve on your Mac" state
# instead of a silent freeze. macOS-only by default (see
# ``SessionConfig.startup_stall_seconds``).
STARTUP_STALL_SECONDS = 8.0

# Output-byte floor separating "rendered its UI and is idly waiting" (a normal
# ready session — already well past this) from "never got going" (blocked).
# Kept low: only the handful of init bytes a stalled child emits fall under it,
# while a healthy Claude banner is several KB — so an idle-but-ready session is
# never misflagged.
STARTUP_STALL_MIN_BYTES = 256

# Grace period after the graceful signal (SIGTERM) before escalating to a
# forceful kill (SIGKILL) during teardown. Interactive login shells and many
# TUIs ignore SIGTERM and would otherwise keep the PTY slave open, stranding
# the reader's blocking os.read() and hanging stop() forever. force=True stops
# skip this grace and kill immediately.
GRACEFUL_TERM_SECONDS = 2.0

# Poll cadence while waiting for the child to exit after a signal. poll() is a
# non-blocking waitpid(WNOHANG), so this is cheap; 50 ms keeps a well-behaved
# child's shutdown latency near-instant.
CHILD_EXIT_POLL_SECONDS = 0.05

# Sessions whose attention comes from Claude Code hooks (``AttentionGate``).
# Every other terminal session listens to OSC 7501 program-status reports
# instead; never both, so one event can never alert twice.
HOOK_DRIVEN_KINDS = frozenset({SessionKind.CLAUDE.value, SessionKind.AUTO_SCAN.value})

# DEC 1004 focus-in / focus-out reports a client emulator writes on its own.
_FOCUS_REPORTS = frozenset({b"\x1b[I", b"\x1b[O"})

# Upper bound on joining each background task (reader, title, stall) during
# teardown. The reader may be parked in an uncancellable os.read() inside the
# thread pool; we cancel and wait at most this long, then abandon it rather
# than letting teardown block forever.
TASK_JOIN_TIMEOUT_SECONDS = 2.0


def _kind_value(kind: SessionKind | str) -> str:
    return kind.value if isinstance(kind, SessionKind) else kind


class TerminalSession:
    """Owns a single PTY session with async I/O, scrollback, ring buffer,
    and bounded event queue."""

    def __init__(
        self,
        config: SessionConfig,
        backend: PtyBackend | None = None,
        attention_dedupe: SharedTurnDedupe | None = None,
    ) -> None:
        self._config = config
        self._backend = backend or create_pty_backend()
        self._attention_dedupe = attention_dedupe
        self._state = SessionState.STARTING
        self._scrollback: collections.deque[bytes] = collections.deque(
            maxlen=config.scrollback_size,
        )
        # Raw byte ring for binary-WS replay on reconnect.
        self._ring = bytearray()
        self._ring_cap = config.ring_buffer_bytes
        self._event_queue: asyncio.Queue[SessionEvent] = asyncio.Queue(
            maxsize=EVENT_QUEUE_MAXSIZE,
        )
        self._reader_task: asyncio.Task[None] | None = None
        self._created_at = datetime.now(UTC)
        # Intercepts DA1/2/3 queries from the child so they don't end up in
        # the ring buffer leaking into the client input field.
        self._da = DaResponder()
        # Intercepts OSC 10/11/12 color queries the same way, answering with the
        # Termux default palette so the reply never has to come from the client
        # emulator — whose reply would otherwise loop back as input on replay.
        self._osc_color = OscColorResponder()
        # Passive OSC 0/1/2 tap. Observes bytes flowing through the reader
        # loop; never mutates them. The latest committed title is mirrored
        # to ``_title`` after the coalescer flushes.
        self._osc = OscTitleExtractor()
        self._title: str | None = None
        self._pending_title: str | None = None
        self._title_event = asyncio.Event()
        self._title_task: asyncio.Task[None] | None = None
        # Attention state, set from Claude Code hooks (the agent's
        # /internal/hook endpoint) or, for every other session, from OSC 7501
        # program-status reports. Mirrored onto the wire DTO via
        # ``to_info`` so a reconnecting client resyncs the "awaiting you"
        # badge even if it missed the one-shot ``session.attention`` event.
        self._attention: str | None = None
        self._exit_reason: str | None = None
        # ``working`` / ``waiting`` / ``idle`` as Claude Code's hooks report it;
        # ``None`` until the first hook, and for good on a session without hooks
        # (Codex, Antigravity, OpenCode, Copilot, Cursor, Terminal), whose
        # activity the agent cannot see.
        self._activity: str | None = None
        # Decides which Claude Code hooks actually become an alert. ``Stop``
        # fires once per model turn — including every re-invocation a completing
        # background task causes — so it is debounced and deduped here rather
        # than raised straight as attention.
        self._attention_gate = AttentionGate(
            on_alert=self.set_attention,
            on_activity=self.set_activity,
            is_running=lambda: self._state == SessionState.RUNNING,
            current_attention=lambda: self._attention,
            quiet_seconds=config.attention_quiet_seconds,
        )
        # The hookless sessions' source: OSC 7501 reports from the CLI. The
        # responder answers the support query so CLIs that ask first report.
        self._status_tap: ProgramStatusTap | None = None
        self._status_responder: ProgramStatusResponder | None = None
        self._status_gate: ProgramStatusGate | None = None
        if _kind_value(config.kind) not in HOOK_DRIVEN_KINDS:
            self._status_tap = ProgramStatusTap()
            self._status_responder = ProgramStatusResponder()
            self._status_gate = ProgramStatusGate(
                on_alert=self._raise_program_attention,
                on_clear_permission=self.clear_attention,
                is_running=lambda: self._state == SessionState.RUNNING,
                current_attention=lambda: self._attention,
                quiet_seconds=config.attention_quiet_seconds,
            )
        self._real_cwd: str | None = None
        # A Codex TUI started with ``-c`` overrides (Mobaide's managed launch
        # sets the reasoning effort that way) runs its own app server, so no
        # chat can join its thread and there is nothing to pair it with.
        self._may_share_thread = not (
            _kind_value(config.kind) == SessionKind.CODEX.value and "-c" in config.command
        )
        # Startup-stall watchdog state (see STARTUP_STALL_SECONDS). The reader
        # loop tallies output bytes; the watchdog raises an
        # ``awaiting_os_permission`` attention if the child stays silent, which
        # on macOS almost always means a TCC modal is blocking it on the host.
        self._startup_output_bytes = 0
        self._user_input_seen = False
        self._stall_task: asyncio.Task[None] | None = None

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def session_id(self) -> str:
        return self._config.session_id

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def pid(self) -> int | None:
        return self._backend.get_pid()

    @property
    def event_queue(self) -> asyncio.Queue[SessionEvent]:
        return self._event_queue

    def is_alive(self) -> bool:
        return self._backend.is_alive()

    async def start(self) -> None:
        """Spawn the PTY process and start the async reader loop."""
        self._backend.spawn(
            cmd=self._config.command,
            cwd=self._config.cwd,
            env=self._config.env,
            rows=self._config.rows,
            cols=self._config.cols,
        )
        self._state = SessionState.RUNNING
        await self._event_queue.put(
            SessionEvent("started", self._config.session_id, timestamp=time.time()),
        )
        self._reader_task = asyncio.create_task(
            self._reader_loop(),
            name=f"pty-reader-{self._config.session_id}",
        )
        self._title_task = asyncio.create_task(
            self._title_coalescer(),
            name=f"title-coalescer-{self._config.session_id}",
        )
        stall = self._config.startup_stall_seconds
        if stall is None:
            stall = STARTUP_STALL_SECONDS if sys.platform == "darwin" else 0.0
        if stall > 0:
            self._stall_task = asyncio.create_task(
                self._startup_stall_watchdog(stall),
                name=f"stall-watchdog-{self._config.session_id}",
            )
        logger.info("Session %s started (pid=%s)", self.session_id, self.pid)

    async def _reader_loop(self) -> None:
        """Read PTY output in a thread executor and feed the event queue."""
        loop = asyncio.get_running_loop()
        while self._state == SessionState.RUNNING:
            try:
                data: bytes = await loop.run_in_executor(
                    None, self._backend.read, 4096,
                )
                if not data:
                    break
                # Intercept terminal capability/color queries before they hit
                # the ring buffer: a replayed query would make the client
                # emulator answer it and loop the reply back into the input
                # field. DA (CSI) and OSC 10/11/12 (color) queries are stripped
                # here and answered on the child's behalf; the combined replies
                # go back to the PTY master in one write so the child sees a
                # normal terminal response. (CPR/DSR is handled ring-only in
                # ``_append_ring`` because the agent has no cursor position to
                # report.)
                # The OSC 7501 query goes first: a CLI sends it right before
                # DA1 and treats a DA1 reply that arrives first as "unsupported".
                replies = b""
                if self._status_responder is not None:
                    data = self._status_responder.process(data)
                    replies = self._status_responder.pending_replies()
                data = self._da.process(data)
                data = self._osc_color.process(data)
                replies += self._da.pending_replies() + self._osc_color.pending_replies()
                if replies:
                    fut = loop.run_in_executor(
                        None, self._backend.write, replies,
                    )
                    fut.add_done_callback(self._log_reply_write_failure)
                if not data:
                    continue
                new_title = self._osc.feed(data)
                if new_title is not None and new_title != self._pending_title:
                    self._pending_title = new_title
                    self._title_event.set()
                if self._status_tap is not None and self._status_gate is not None:
                    for report in self._status_tap.feed(data):
                        self._status_gate.handle(report)
                self._scrollback.append(data)
                self._append_ring(data)
                self._startup_output_bytes += len(data)
                if (
                    self._attention == "awaiting_os_permission"
                    and self._startup_output_bytes >= STARTUP_STALL_MIN_BYTES
                ):
                    # Output is flowing again — the user approved the macOS
                    # modal (or it was a false positive). Drop the badge.
                    self.clear_attention()
                # Drop-oldest policy: never stall the PTY reader due to slow
                # consumers.  Stale output frames are acceptable to lose —
                # the ring buffer holds recent bytes for reconnect replay.
                event = SessionEvent("output", self._config.session_id, data, time.time())
                self._enqueue_drop_oldest(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug(
                    "Reader loop exception for session %s",
                    self.session_id,
                    exc_info=True,
                )
                break

        if self._state == SessionState.RUNNING and self._config.exits_when_done:
            self._state = SessionState.STOPPED
            await self._event_queue.put(
                SessionEvent("stopped", self._config.session_id, timestamp=time.time()),
            )
            logger.info("Session %s finished", self.session_id)
            # ``stop()`` returns early on a stopped session: free here what it
            # would have freed.
            self.cancel_attention_window()
            for task in (self._title_task, self._stall_task):
                if task is not None:
                    task.cancel()
            await loop.run_in_executor(None, self._backend.close)
        elif self._state == SessionState.RUNNING:
            self._state = SessionState.CRASHED
            await self._event_queue.put(
                SessionEvent(
                    "error",
                    self._config.session_id,
                    None,
                    time.time(),
                ),
            )
            logger.warning("Session %s crashed", self.session_id)

    async def _title_coalescer(self) -> None:
        """Trailing-edge debounce loop that publishes title updates.

        Wakes on ``_title_event``, sleeps for ``TITLE_COALESCE_SECONDS`` so
        intra-window changes collapse to the latest value, then enqueues
        a ``"title"`` SessionEvent if the value actually changed. The drop-
        oldest fallback mirrors the output path: title events must never
        block the coalescer.
        """
        try:
            while True:
                await self._title_event.wait()
                await asyncio.sleep(TITLE_COALESCE_SECONDS)
                self._title_event.clear()
                snapshot = self._pending_title
                if snapshot == self._title:
                    continue
                self._title = snapshot
                event = SessionEvent(
                    "title",
                    self._config.session_id,
                    None,
                    time.time(),
                )
                self._enqueue_drop_oldest(event)
        except asyncio.CancelledError:
            raise

    async def _startup_stall_watchdog(self, timeout: float) -> None:
        """Flag a session that never produced output as awaiting OS permission.

        Sleeps ``timeout`` seconds, then — if the session is still RUNNING, the
        user has not typed, the child has emitted fewer than
        ``STARTUP_STALL_MIN_BYTES``, and no other attention is already pending —
        raises ``awaiting_os_permission``. On macOS that almost always means a
        TCC file-access modal is blocking the child on the host's desktop, which
        a remote client can neither see nor answer. The ``_attention is None``
        guard keeps a genuine Claude ``awaiting_permission`` hook from being
        clobbered; ``full_disk_access`` is probed only to enrich the log.
        """
        try:
            await asyncio.sleep(timeout)
        except asyncio.CancelledError:
            raise
        if (
            self._state == SessionState.RUNNING
            and not self._user_input_seen
            and self._startup_output_bytes < STARTUP_STALL_MIN_BYTES
            and self._attention is None
        ):
            # Offload the probe's blocking open() off the event loop, matching
            # this module's run_in_executor discipline for all other I/O.
            fda = await asyncio.to_thread(full_disk_access)
            logger.info(
                "Session %s stalled at startup (%d bytes in %.1fs); raising "
                "awaiting_os_permission (full_disk_access=%s)",
                self.session_id,
                self._startup_output_bytes,
                timeout,
                fda,
            )
            self.set_attention("awaiting_os_permission")

    def _log_reply_write_failure(self, fut: asyncio.Future) -> None:
        """Surface exceptions from the fire-and-forget query-reply write
        (DA + OSC color)."""
        exc = fut.exception()
        if exc is not None:
            logger.warning(
                "Session %s: terminal-query reply write failed: %r",
                self.session_id,
                exc,
            )

    def _append_ring(self, data: bytes) -> None:
        # Scrub terminal query/response bytes in-line so the ring is always
        # clean. Replay senders rely on this — without the per-write strip
        # they'd have to snapshot-then-strip the entire (up to 4 MB) ring on
        # every reconnect, doubling peak heap during the handshake. This is also
        # the ring-only neutralization point for CPR/DSR (see _RING_SCRUB_RE).
        cleaned = _RING_SCRUB_RE.sub(b"", data)
        if not cleaned:
            return
        self._ring += cleaned
        overflow = len(self._ring) - self._ring_cap
        if overflow > 0:
            del self._ring[:overflow]

    def get_ring(self) -> bytes:
        """Snapshot of the recent raw PTY bytes for binary-WS replay."""
        return bytes(self._ring)

    def iter_ring_chunks(
        self, chunk_size: int = REPLAY_CHUNK_BYTES,
    ) -> Iterator[bytes]:
        """Yield the ring as ``bytes`` chunks of ``chunk_size``.

        Snapshots ``self._ring`` once at the start of iteration so chunks
        stay consistent across ``await`` suspension points in the caller
        (between chunks the PTY reader may append more bytes or trim the
        head — without the snapshot, slice indices would drift). One full
        copy upfront is strictly cheaper than the prior
        ``get_ring()`` + ``regex.sub`` double-materialisation, because the
        ring is already DA-clean (see ``_append_ring``).

        An empty ring yields nothing — callers that need a
        "handshake-always-has-an-OUTPUT-frame" invariant must emit one
        explicitly.
        """
        if chunk_size <= 0:
            raise ValueError(f"chunk_size must be positive: {chunk_size}")
        snapshot = bytes(self._ring)
        for start in range(0, len(snapshot), chunk_size):
            yield snapshot[start : start + chunk_size]

    def on_claude_hook(self, kind: HookKind, payload: Mapping[str, Any]) -> None:
        """Feed one Claude Code hook to the attention gate.

        The gate decides whether it becomes a user-visible attention; a
        suppressed hook leaves the public ``attention`` field untouched, so a
        client resyncing from a full-state snapshot cannot resurrect an alert
        this session deliberately withheld.

        ``UserPromptSubmit`` additionally drops the public badge, and that is
        what keeps a session alerting past its first turn. ``attention`` is
        otherwise cleared only by a client PTY write, so a user who answers on
        the host — or an unattended Auto Scan session — would leave
        ``turn_finished`` latched, and the gate's own "already on screen" guard
        would then mute every later turn. A prompt submission proves the
        composer is live, so any badge still standing is stale by construction.
        """
        self._attention_gate.handle(kind, payload)
        if kind is HookKind.PROMPT_SUBMIT:
            self.clear_attention()

    def mute_attention_window(self) -> None:
        """Mute a pending debounce without forgetting the turn.

        What a keystroke warrants: the user is plainly present, so a queued
        "your move" should not fire — but client input is not proof of a *new*
        turn. Both apps push terminal scrolling through the same PTY-write path
        as typing, so treating any input as a turn boundary would let a scroll
        gesture discard the dedupe key and re-open the alert storm while
        background work is still draining. The turn boundary is
        ``UserPromptSubmit`` alone; the reader loop's ``awaiting_os_permission``
        auto-clear is not one either, since resumed output is not the user
        acting. The window still runs out, so the session's activity turns
        ``idle`` on time even while the user scrolls.

        Hookless sessions get the same treatment for their program-status
        window: a keystroke cancels a pending "turn finished".
        """
        self._attention_gate.mute()
        if self._status_gate is not None:
            self._status_gate.on_input()

    def cancel_attention_window(self) -> None:
        """Drop every pending alert window outright: the session is stopping."""
        self._attention_gate.cancel()
        if self._status_gate is not None:
            self._status_gate.cancel()

    def set_activity(self, activity: str) -> None:
        """Record ``working`` / ``waiting`` / ``idle`` and refresh the session row.

        Only a change emits: the gate reports every transition its hooks imply,
        and ``Stop`` alone can fire several times per turn.
        """
        if activity == self._activity:
            return
        self._activity = activity
        self._enqueue_drop_oldest(
            SessionEvent("activity", self._config.session_id, None, time.time()),
        )

    def _raise_program_attention(self, reason: str) -> None:
        """Raise an attention from an OSC 7501 report, unless the same turn
        already alerted from a chat session on a shared thread."""
        app = (
            self._status_gate.last_app if self._status_gate else None
        ) or _kind_value(self._config.kind)
        if (
            self._attention_dedupe is not None
            and self._may_share_thread
            and app in SHARED_THREAD_APPS
        ):
            if self._real_cwd is None:
                self._real_cwd = real_cwd(self._config.cwd)
            if not self._attention_dedupe.admit(
                session_id=self.session_id,
                ui="terminal",
                app=app,
                cwd=self._real_cwd,
                reason=reason,
            ):
                logger.info(
                    "Session %s: %s already alerted by a chat on the same thread",
                    self.session_id,
                    reason,
                )
                return
        self.set_attention(reason)

    def set_attention(self, attention: str, exit_reason: str | None = None) -> None:
        """Record an attention trigger and emit an attention event.

        ``attention`` is one of ``awaiting_permission`` | ``turn_finished`` |
        ``turn_failed`` | ``ended`` (Claude Code hooks via the agent's
        ``/internal/hook`` endpoint, or OSC 7501 reports for hookless sessions)
        or ``awaiting_os_permission`` (the startup-stall watchdog).
        ``exit_reason`` is supplied only for ``ended``. Always called on the
        event loop, so the ``put_nowait`` enqueue is safe.

        Re-raising the reason already pending refreshes the stored state without
        re-emitting: the event is a one-shot notification trigger, and the
        clients collapse per session, so a repeat would re-alert for something
        the user is already being shown. A *changed* ``exit_reason`` still
        emits — ``SessionEnd`` fires with reason ``clear`` for ``/clear`` while
        the PTY keeps running, so the session can legitimately end again later
        and that second ending must still reach the user.
        """
        changed = attention != self._attention or (
            exit_reason is not None and exit_reason != self._exit_reason
        )
        self._attention = attention
        if exit_reason is not None:
            self._exit_reason = exit_reason
        if changed:
            self._emit_attention(attention)

    def clear_attention(self) -> None:
        """Clear attention once the user has responded.

        No-op when nothing is pending, so ordinary keystrokes don't spam the
        fanout. Emits an attention event carrying ``None`` so connected clients
        drop the "awaiting you" badge via the ``session.updated`` snapshot.
        """
        if self._attention is None:
            return
        self._attention = None
        self._emit_attention(None)

    def _emit_attention(self, attention: str | None) -> None:
        """Enqueue an ``attention`` SessionEvent (drop-oldest, never blocks)."""
        self._enqueue_drop_oldest(
            SessionEvent(
                "attention",
                self._config.session_id,
                None,
                time.time(),
                attention=attention,
            )
        )

    def _enqueue_drop_oldest(self, event: SessionEvent) -> None:
        """Put ``event`` on the bounded event queue, evicting the oldest entry
        if it is full so the producer never blocks. Shared by every queue
        producer in this class (PTY reader, title coalescer, attention hooks)."""
        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                self._event_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._event_queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def write(self, data: bytes) -> None:
        """Write raw bytes to the PTY stdin."""
        # User input means the user responded — drop any pending attention so
        # the badge clears for both "typed next instruction" and "approved a
        # permission prompt". Also suppress the startup-stall watchdog: a user
        # who is typing is plainly not staring at a frozen screen.
        self._user_input_seen = True
        if data in _FOCUS_REPORTS:
            # The client emulator's focus report (a tab switch, a dismissed
            # keyboard), not the user acting on the prompt: an alert still
            # waiting out its spacing gap must survive it.
            self._attention_gate.mute()
        else:
            self.mute_attention_window()
        self.clear_attention()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._backend.write, data)

    async def write_submit(self, data: bytes) -> None:
        """Write ``data`` to the PTY, pause, then write CR as a separate write.

        The CR must NOT travel in the same write as the text: Ink TUIs
        (Claude CLI) batch each stdin chunk and treat an embedded CR as
        pasted content, so ``text + "\\r"`` in one chunk fills the composer
        without submitting. A bare CR arriving as its own read after
        ``SUBMIT_CR_DELAY_SECONDS`` is parsed as a discrete Enter keypress.

        Bytes ahead of a bracketed paste go out as their own write too, after
        which ``PASTE_PREFIX_DELAY_SECONDS`` passes before the paste — see
        that constant for why.

        Awaited inline by the frame dispatchers, so frames for this session
        queue behind the delay — that ordering is intentional: bytes typed
        right after a submit logically follow the Enter.

        Callers guard empty ``data``; an empty submit is dropped upstream so
        a buggy client cannot fire a bare Enter on a stale composer.
        """
        paste_at = data.find(BRACKETED_PASTE_START)
        if paste_at > 0:
            await self.write(data[:paste_at])
            await asyncio.sleep(PASTE_PREFIX_DELAY_SECONDS)
            data = data[paste_at:]
        await self.write(data)
        await asyncio.sleep(SUBMIT_CR_DELAY_SECONDS)
        await self.write(b"\r")

    async def write_execute(self, data: bytes) -> None:
        """Ctrl+C to cancel stale input, pause for the redraw, then submit ``data``."""
        await self.write(b"\x03")
        await asyncio.sleep(EXECUTE_CLEAR_DELAY_SECONDS)
        await self.write_submit(data)

    async def resize(self, rows: int, cols: int) -> None:
        """Resize the PTY terminal."""
        self._backend.resize(rows, cols)

    def get_scrollback(self) -> list[str]:
        """Return the current scrollback buffer as decoded strings.

        The realtime WS path uses ``get_ring()`` for byte-exact replay; this
        helper exists for the REST ``/scrollback`` endpoint, which is JSON
        and therefore must hand back strings. Invalid UTF-8 is replaced.
        """
        return [chunk.decode("utf-8", errors="replace") for chunk in self._scrollback]

    async def stop(self, force: bool = False) -> None:
        """Stop the session, terminating the PTY process and cleaning up resources."""
        if self._state in (SessionState.STOPPED, SessionState.STOPPING):
            return

        self._state = SessionState.STOPPING
        logger.info("Stopping session %s (force=%s)", self.session_id, force)

        self.cancel_attention_window()

        loop = asyncio.get_running_loop()
        await self._terminate_child(loop, force)

        # Killing the child closes the PTY slave, so the reader's blocking
        # os.read returns EOF and the task ends on its own. _join_task still
        # caps the wait defensively — see its docstring.
        await self._join_task(self._reader_task, TASK_JOIN_TIMEOUT_SECONDS)
        await self._join_task(self._title_task, TASK_JOIN_TIMEOUT_SECONDS)
        await self._join_task(self._stall_task, TASK_JOIN_TIMEOUT_SECONDS)

        await loop.run_in_executor(None, self._backend.close)

        self._state = SessionState.STOPPED
        try:
            self._event_queue.put_nowait(
                SessionEvent("stopped", self._config.session_id, timestamp=time.time()),
            )
        except asyncio.QueueFull:
            pass
        logger.info("Session %s stopped", self.session_id)

    async def _terminate_child(
        self, loop: asyncio.AbstractEventLoop, force: bool
    ) -> None:
        """Reliably kill the child so the PTY slave closes and the reader's
        blocking ``os.read`` returns EOF.

        A single SIGTERM is not enough: interactive login shells (``bash -l``)
        and many TUIs ignore it and keep the PTY slave open, which would strand
        the reader thread and hang teardown forever. So we send the graceful
        signal first, give the child a short grace window to exit, then escalate
        to SIGKILL if it is still alive. ``force=True`` skips the grace and kills
        immediately.
        """
        await loop.run_in_executor(None, self._backend.terminate, force)
        if not force and self._backend.is_alive():
            await self._await_child_exit(GRACEFUL_TERM_SECONDS)
        if self._backend.is_alive():
            await loop.run_in_executor(None, self._backend.terminate, True)

    async def _await_child_exit(self, timeout: float) -> bool:
        """Poll the child until it exits or ``timeout`` elapses; return whether
        it is gone.

        ``is_alive`` wraps a non-blocking ``poll()`` (``waitpid(WNOHANG)``), so
        polling on the event loop is cheap and never blocks.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._backend.is_alive():
                return True
            await asyncio.sleep(CHILD_EXIT_POLL_SECONDS)
        return not self._backend.is_alive()

    @staticmethod
    async def _join_task(task: asyncio.Task[None] | None, timeout: float) -> None:
        """Cancel ``task`` and wait at most ``timeout`` for it to finish.

        Deliberately uses ``asyncio.wait`` rather than ``asyncio.wait_for``:
        ``wait_for`` cancels then *awaits* the task until cancellation completes,
        which never happens if the task is parked in an uncancellable executor
        call (the PTY reader's blocking ``os.read``, the stall watchdog's
        ``asyncio.to_thread``) — so it would block forever despite its own
        timeout. ``asyncio.wait`` returns once ``timeout`` elapses regardless of
        task state, so a still-stuck task is abandoned instead of wedging
        teardown. An abandoned reader ends harmlessly once its read returns,
        since the session is no longer RUNNING.
        """
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.wait({task}, timeout=timeout)

    def to_info(self) -> SessionInfo:
        """Build a lightweight DTO for this session."""
        return SessionInfo(
            session_id=self._config.session_id,
            state=self._state,
            created_at=self._created_at,
            pid=self.pid,
            command=self._config.command,
            cwd=self._config.cwd,
            title=self._title,
            project_name=self._config.project_name,
            kind=self._config.kind,
            kind_label=self._config.kind_label,
            attention=self._attention,
            exit_reason=self._exit_reason,
            activity=self._activity if self._state == SessionState.RUNNING else None,
        )
