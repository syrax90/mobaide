from __future__ import annotations

import asyncio
import collections
import logging
import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime

from .da_responder import DaResponder
from .models import SessionConfig, SessionEvent, SessionInfo, SessionState
from .osc_parser import OscTitleExtractor
from .pty_backend import PtyBackend, create_pty_backend

logger = logging.getLogger(__name__)

# Belt-and-braces strip for DA capability-query *replies* that may leak into
# the ring (e.g. from sessions started before the agent-side DaResponder was
# deployed). Applied per-write inside ``_append_ring`` so the ring is
# always DA-clean — the replay senders (``agent/ws/session_io.py`` and
# ``relay/client.py``) no longer need a second sweep. Matches DA1/DA2
# (CSI ? ... c / CSI > ... c) and DA3 (DCS ! | ... ST). The primary fix
# remains ``core/da_responder.py``, which intercepts queries before the
# child writes a reply at all.
_DA_REPLY_STRIP = re.compile(
    rb"\x1b\[\?[\d;]*c|\x1b\[>[\d;]*c|\x1bP![|][^\x1b]*\x1b\\",
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
        # Passive OSC 0/1/2 tap. Observes bytes flowing through the reader
        # loop; never mutates them. The latest committed title is mirrored
        # to ``_title`` after the coalescer flushes.
        self._osc = OscTitleExtractor()
        self._title: str | None = None
        self._pending_title: str | None = None
        self._title_event = asyncio.Event()
        self._title_task: asyncio.Task[None] | None = None

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
                # Intercept DA queries before they hit the ring buffer.
                # Replies are written back to the PTY master so the child
                # sees a normal terminal response.
                data = self._da.process(data)
                replies = self._da.pending_replies()
                if replies:
                    fut = loop.run_in_executor(
                        None, self._backend.write, replies,
                    )
                    fut.add_done_callback(self._log_da_write_failure)
                if not data:
                    continue
                new_title = self._osc.feed(data)
                if new_title is not None and new_title != self._pending_title:
                    self._pending_title = new_title
                    self._title_event.set()
                self._scrollback.append(data)
                self._append_ring(data)
                # Drop-oldest policy: never stall the PTY reader due to slow
                # consumers.  Stale output frames are acceptable to lose —
                # the ring buffer holds recent bytes for reconnect replay.
                event = SessionEvent("output", self._config.session_id, data, time.time())
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
        except asyncio.CancelledError:
            raise

    def _log_da_write_failure(self, fut: asyncio.Future) -> None:
        """Surface exceptions from the fire-and-forget DA reply write."""
        exc = fut.exception()
        if exc is not None:
            logger.warning(
                "Session %s: DA reply write failed: %r",
                self.session_id,
                exc,
            )

    def _append_ring(self, data: bytes) -> None:
        # Strip DA replies in-line so the ring is always DA-clean. Replay
        # senders rely on this — without the per-write strip they'd have to
        # snapshot-then-strip the entire (up to 4 MB) ring on every
        # reconnect, doubling peak heap during the handshake.
        cleaned = _DA_REPLY_STRIP.sub(b"", data)
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

    async def write(self, data: bytes) -> None:
        """Write raw bytes to the PTY stdin."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._backend.write, data)

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

        # Terminate the PTY first — this unblocks the reader thread.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._backend.terminate, force)

        if self._reader_task is not None and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await asyncio.wait_for(self._reader_task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        if self._title_task is not None and not self._title_task.done():
            self._title_task.cancel()
            try:
                await asyncio.wait_for(self._title_task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        await loop.run_in_executor(None, self._backend.close)

        self._state = SessionState.STOPPED
        try:
            self._event_queue.put_nowait(
                SessionEvent("stopped", self._config.session_id, timestamp=time.time()),
            )
        except asyncio.QueueFull:
            pass
        logger.info("Session %s stopped", self.session_id)

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
        )
