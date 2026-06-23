from __future__ import annotations

import asyncio
import collections
import logging
import re
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime

from .da_responder import DaResponder
from .macos_access import full_disk_access
from .models import SessionConfig, SessionEvent, SessionInfo, SessionState
from .osc_color_responder import OscColorResponder
from .osc_parser import OscTitleExtractor
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
#   * OSC 10/11/12 dynamic-color *queries* (… ;? , BEL/ST). Only the query form
#     is scrubbed, never ``… ;rgb:…`` — a color *reply* is byte-identical to a
#     legitimate color *set*, and the reply never reaches the output ring anyway
#     (it returns via the input channel). The query is what makes a replayed
#     stream re-trigger the client emulator.
#   * DSR / CPR / DECDSR queries (CSI 5 n / CSI 6 n / CSI ? 6 n)
_RING_SCRUB_RE = re.compile(
    rb"\x1b\[\?[\d;]*c"
    rb"|\x1b\[>[\d;]*c"
    rb"|\x1bP![|][^\x1b]*\x1b\\"
    rb"|\x1b\]1[012];\?(?:\x07|\x1b\\)"
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

# Upper bound on joining each background task (reader, title, stall) during
# teardown. The reader may be parked in an uncancellable os.read() inside the
# thread pool; we cancel and wait at most this long, then abandon it rather
# than letting teardown block forever.
TASK_JOIN_TIMEOUT_SECONDS = 2.0


class TerminalSession:
    """Owns a single PTY session with async I/O, scrollback, ring buffer,
    and bounded event queue."""

    def __init__(
        self,
        config: SessionConfig,
        backend: PtyBackend | None = None,
    ) -> None:
        self._config = config
        self._backend = backend or create_pty_backend()
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
        # Hook-driven attention state (set by the agent's /internal/hook
        # endpoint from Claude Code hooks). Mirrored onto the wire DTO via
        # ``to_info`` so a reconnecting client resyncs the "awaiting you"
        # badge even if it missed the one-shot ``session.attention`` event.
        self._attention: str | None = None
        self._exit_reason: str | None = None
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
                data = self._da.process(data)
                data = self._osc_color.process(data)
                replies = self._da.pending_replies() + self._osc_color.pending_replies()
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

        if self._state == SessionState.RUNNING:
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

    def set_attention(self, attention: str, exit_reason: str | None = None) -> None:
        """Record an attention trigger and emit an attention event.

        ``attention`` is one of ``awaiting_permission`` | ``turn_finished`` |
        ``ended`` (Claude Code hooks, via the agent's ``/internal/hook``
        endpoint) or ``awaiting_os_permission`` (the startup-stall watchdog).
        ``exit_reason`` is supplied only for ``ended``. Always called on the
        event loop, so the ``put_nowait`` enqueue is safe.
        """
        self._attention = attention
        if exit_reason is not None:
            self._exit_reason = exit_reason
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

        Awaited inline by the frame dispatchers, so frames for this session
        queue behind the delay — that ordering is intentional: bytes typed
        right after a submit logically follow the Enter.

        Callers guard empty ``data``; an empty submit is dropped upstream so
        a buggy client cannot fire a bare Enter on a stale composer.
        """
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
            attention=self._attention,
            exit_reason=self._exit_reason,
        )
