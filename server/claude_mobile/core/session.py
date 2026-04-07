from __future__ import annotations

import asyncio
import collections
import logging
import time
from datetime import UTC, datetime

from .models import SessionConfig, SessionEvent, SessionInfo, SessionState
from .pty_backend import PtyBackend, create_pty_backend

logger = logging.getLogger(__name__)

# Bounded ring buffer of recent raw PTY output bytes, replayed to clients
# on (re)connect. ~256 KB is enough to capture the visible alt-screen of an
# Ink/React TUI plus a few seconds of scrollback.
RING_MAX_BYTES = 256 * 1024

# The session-internal event queue is bounded so the PTY reader naturally
# pauses (via ``await put``) when consumers are slow, providing backpressure
# end-to-end without dropping output bytes.
EVENT_QUEUE_MAXSIZE = 64


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
        self._event_queue: asyncio.Queue[SessionEvent] = asyncio.Queue(
            maxsize=EVENT_QUEUE_MAXSIZE,
        )
        self._reader_task: asyncio.Task[None] | None = None
        self._created_at = datetime.now(UTC)

    # -- Properties --

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

    # -- Lifecycle --

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
                self._scrollback.append(data)
                self._append_ring(data)
                # ``put`` (not ``put_nowait``) so a slow consumer applies
                # backpressure all the way down to the PTY read.
                await self._event_queue.put(
                    SessionEvent("output", self._config.session_id, data, time.time()),
                )
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

    def _append_ring(self, data: bytes) -> None:
        self._ring += data
        overflow = len(self._ring) - RING_MAX_BYTES
        if overflow > 0:
            del self._ring[:overflow]

    def get_ring(self) -> bytes:
        """Snapshot of the recent raw PTY bytes for binary-WS replay."""
        return bytes(self._ring)

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
        """Stop the session: terminate PTY, cancel reader, clean up."""
        if self._state in (SessionState.STOPPED, SessionState.STOPPING):
            return

        self._state = SessionState.STOPPING
        logger.info("Stopping session %s (force=%s)", self.session_id, force)

        # Terminate the PTY first — this unblocks the reader thread
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._backend.terminate, force)

        # Cancel the reader task
        if self._reader_task is not None and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await asyncio.wait_for(self._reader_task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        # Close the backend
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
        )
