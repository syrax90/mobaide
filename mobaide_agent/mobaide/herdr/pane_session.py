"""A herdr pane shown as a Mobaide terminal session.

The session speaks the normal terminal frames, so every client shows it with
its terminal screen. It differs from a PTY session in what it owns: nothing.
The pane and its program belong to herdr, so

* stopping the session only detaches (it never closes the pane),
* it is not in the session list, does not count for the session limit and
  does not hold back an agent update (``external``),
* its stream runs only while a client is attached.

Two modes. **Watch** shows the pane at its real size and never resizes it;
the client fits that grid to its screen, and input goes to the pane without
taking it over. **Control** gives the pane the client's size and its input
directly, until control is released or the last client leaves.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

from mobaide.core.models import SessionEvent, SessionInfo, SessionKind, SessionState

# The timing of a message is the PTY session's: a controlled pane is a
# terminal like any other, and a tuning there must reach it.
from mobaide.core.session import (
    _FOCUS_REPORTS,
    BRACKETED_PASTE_START,
    EXECUTE_CLEAR_DELAY_SECONDS,
    PASTE_PREFIX_DELAY_SECONDS,
    REPLAY_CHUNK_BYTES,
    SUBMIT_CR_DELAY_SECONDS,
)

from . import errors
from .backend import CONTROL, WATCH, PaneStream, ServerLink

logger = logging.getLogger(__name__)

# The replay kept for a client that attaches: the last full repaint and the
# bytes after it. Past this size the stream is opened again, which starts a
# new repaint.
RING_MAX_BYTES = 2 * 1024 * 1024
EVENT_QUEUE_MAXSIZE = 2048
DEFAULT_GRID = (120, 40)
RETRY_SECONDS = (1.0, 2.0, 5.0, 15.0, 30.0)
# Messages that wait for herdr to take them, per pane.
MAX_PENDING_SUBMITS = 32
# A stream that lived this long worked: the next failure starts the delays again.
STREAM_HEALTHY_SECONDS = 30.0

# Session-socket event that carries no bytes: the ``pane`` block of the state
# frame changed (mode or grid).
PANE_EVENT = "pane"

_PASTE_MARKERS = (BRACKETED_PASTE_START, b"\x1b[201~")


def submit_text(data: bytes) -> str:
    """The message inside a client's submit frame. Clients put line-clear keys
    in front and wrap a multi-line message in bracketed-paste markers; herdr
    adds its own paste markers, so only the text goes on."""
    for marker in _PASTE_MARKERS:
        data = data.replace(marker, b"")
    text = data.decode("utf-8", errors="replace")
    return text.lstrip("\x01\x0b\x15").replace("\r\n", "\n").replace("\r", "\n")


class HerdrPaneSession:
    # Read by ``SessionManager``: a view of a process this agent does not own.
    external = True

    def __init__(
        self,
        session_id: str,
        link: ServerLink,
        pane_key: str,
        *,
        on_input: Callable[[], None] | None = None,
    ) -> None:
        self._session_id = session_id
        self._link = link
        self._pane_key = pane_key
        self._on_input = on_input
        self._state = SessionState.RUNNING
        self._created_at = datetime.now(UTC)
        self._event_queue: asyncio.Queue[SessionEvent] = asyncio.Queue(
            maxsize=EVENT_QUEUE_MAXSIZE
        )
        self._ring = bytearray()
        self._mode = WATCH
        # The client that took control. Its id is in the state frame, so every
        # other client keeps the dictated grid and sends no size.
        self._controller: str | None = None
        self._grid: tuple[int, int] | None = None
        # The size the controller asked for, in this control period.
        self._client_size: tuple[int, int] | None = None
        self._viewers = 0
        self._relay_viewers = 0
        self._idle_since: float | None = None
        self._stream: PaneStream | None = None
        self._stream_mode: str | None = None
        self._restart = False
        self._dirty = False
        self._worker: asyncio.Future[None] | None = None
        self._retry: asyncio.TimerHandle | None = None
        self._failures = 0
        self._stream_opened_at: float | None = None
        self._closing: set[asyncio.Future[None]] = set()
        self._submits: set[asyncio.Future[None]] = set()
        self._submit_lock = asyncio.Lock()
        self._grid_revision: object = None

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def pid(self) -> int | None:
        return None

    @property
    def event_queue(self) -> asyncio.Queue[SessionEvent]:
        return self._event_queue

    @property
    def server_id(self) -> str:
        return self._link.server_id

    @property
    def pane_key(self) -> str:
        return self._pane_key

    @property
    def viewers(self) -> int:
        return self._viewers

    @property
    def idle_since(self) -> float | None:
        """Loop time since which no client is attached; ``None`` while one is."""
        return self._idle_since

    @property
    def mode(self) -> str:
        return self._mode

    def is_alive(self) -> bool:
        return self._state is SessionState.RUNNING

    def iter_ring_chunks(self, chunk_size: int = REPLAY_CHUNK_BYTES) -> Iterator[bytes]:
        snapshot = bytes(self._ring)
        for start in range(0, len(snapshot), chunk_size):
            yield snapshot[start : start + chunk_size]

    def get_scrollback(self) -> list[str]:
        return []

    def wire_state_extras(self) -> dict[str, Any]:
        """Extra keys of this session's state frame. A client draws the given
        grid and sends no size of its own, except the one named ``controller``."""
        pane: dict[str, Any] = {"mode": self._mode}
        grid = self._grid
        if self._mode == CONTROL:
            pane["controller"] = self._controller or ""
            grid = self._client_size or self._grid
        if grid is not None:
            pane["cols"], pane["rows"] = grid
        return {"pane": pane}

    def to_info(self) -> SessionInfo:
        pane_id = self._pane_id()
        title = cwd = None
        server = self._link.state()
        if pane_id is not None and server is not None:
            for workspace in server.workspaces:
                for tab in workspace.tabs:
                    for pane in tab.panes:
                        if pane.pane_id == pane_id:
                            title, cwd = pane.title, pane.cwd
        return SessionInfo(
            session_id=self._session_id,
            state=self._state,
            created_at=self._created_at,
            pid=None,
            command=[],
            cwd=cwd,
            title=title,
            kind=SessionKind.SHELL,
        )

    async def start(self) -> None:
        self._idle_since = asyncio.get_running_loop().time()

    async def stop(self, force: bool = False) -> None:
        """Detach from the pane. The pane and its program keep running."""
        if self._state is SessionState.STOPPED:
            return
        self._state = SessionState.STOPPED
        self._cancel_retry()
        self._cancel_submits()
        # Before the wait below: the caller removes the session right after
        # this returns, and the readers of the queue must see the end first.
        self._emit(SessionEvent(event_type="stopped", session_id=self._session_id))
        self._kick()
        worker = self._worker
        if worker is not None:
            try:
                await asyncio.wait_for(asyncio.shield(worker), timeout=3.0)
            except (asyncio.TimeoutError, Exception):
                pass

    def viewer_attached(self, count: int = 1, *, relay: bool = False) -> None:
        """``count`` clients began to watch. Relay viewers are counted apart
        from direct ones: a tunnel drop says nothing about a direct socket."""
        count = max(1, count)
        if relay:
            self._relay_viewers += count
        self._viewers += count
        self._idle_since = None
        self._kick()

    def viewer_detached(self, *, relay: bool = False) -> None:
        if relay:
            if self._relay_viewers == 0:
                return
            self._relay_viewers -= 1
        self._viewers = max(0, self._viewers - 1)
        if self._viewers == 0:
            self._no_viewers()

    def set_relay_viewers(self, count: int) -> None:
        """The relay says how many of its clients watch now. A number, not a
        step: a detach of the view that ended can arrive after the attach of
        the one that replaced it under the same id, and steps would then
        count one client too few."""
        count = max(0, count)
        delta = count - self._relay_viewers
        if delta == 0:
            return
        self._relay_viewers = count
        self._viewers = max(0, self._viewers + delta)
        if self._viewers == 0:
            self._no_viewers()
        elif delta > 0:
            self._idle_since = None
            self._kick()

    def reset_relay_viewers(self) -> None:
        """The relay tunnel dropped: no relay client is known to watch. When
        the tunnel is back, the relay says how many clients each session has."""
        gone, self._relay_viewers = self._relay_viewers, 0
        if gone:
            self._viewers = max(0, self._viewers - gone)
            if self._viewers == 0:
                self._no_viewers()

    def _no_viewers(self) -> None:
        self._idle_since = asyncio.get_running_loop().time()
        # Control ends with the last viewer, so a phone that went to sleep
        # does not keep a desktop pane at phone size.
        if self._mode == CONTROL:
            self._set_mode(WATCH)
        self._kick()

    async def set_control(self, control: bool, client_id: str = "") -> bool:
        """Give control to ``client_id`` or take it back; returns whether
        that changed anything. One client controls a pane; a second "take"
        moves control to that client. Only the controller can give control
        back, so a watcher that leaves does not end another device's control."""
        if self._state is not SessionState.RUNNING:
            raise errors.pane_not_found()
        if not control:
            if self._mode == CONTROL and client_id == (self._controller or ""):
                self._set_mode(WATCH)
                self._kick()
                return True
            return False
        server = self._link.state()
        if server is None or not server.features.control:
            raise errors.not_supported("give control of a pane")
        if self._viewers == 0:
            # Control with no viewer would wait for the next client to attach.
            raise errors.HerdrError(409, "pane_not_open", "Open the pane before you take control.")
        if self._mode == CONTROL and self._controller == client_id:
            return False
        self._controller = client_id
        if self._mode != CONTROL:
            # Control starts at the pane's own size, until the controller
            # sends one. A size from an earlier control period does not apply.
            self._client_size = None
        # A take from another controller keeps the size: it is what the pane
        # has now, and the watchers draw it until the new controller's arrives.
        self._set_mode(CONTROL)
        self._kick()
        return True

    def server_changed(self) -> None:
        """The server mirror changed: the pane may be gone or have a new size."""
        if self._state is not SessionState.RUNNING:
            return
        if self._pane_id() is None:
            self._end()
            return
        revision = getattr(self._link, "grid_revision", None)
        if revision != self._grid_revision and self._stream_mode == WATCH:
            self._grid_revision = revision
            asyncio.ensure_future(self._check_grid())

    async def _check_grid(self) -> None:
        pane_id = self._pane_id()
        if pane_id is None or self._stream_mode != WATCH:
            return
        try:
            grid = await self._link.pane_grid(pane_id)
        except errors.HerdrError:
            return
        if grid is not None and grid != self._grid and self._stream_mode == WATCH:
            self._restart = True
            self._kick()

    def _pane_id(self) -> str | None:
        return self._link.pane_id_for_key(self._pane_key)

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        if mode != CONTROL:
            self._controller = None
        self._emit(SessionEvent(event_type=PANE_EVENT, session_id=self._session_id))

    def _set_grid(self, grid: tuple[int, int] | None) -> None:
        if grid != self._grid:
            self._grid = grid
            self._emit(SessionEvent(event_type=PANE_EVENT, session_id=self._session_id))

    def _emit(self, event: SessionEvent) -> None:
        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            # A dropped diff would leave a wrong screen: start a new repaint.
            self._restart = True
            self._kick()

    def _end(self) -> None:
        """The pane is closed or its server is gone."""
        if self._state is SessionState.STOPPED:
            return
        self._state = SessionState.STOPPED
        self._cancel_retry()
        self._cancel_submits()
        self._kick()
        self._emit(SessionEvent(event_type="stopped", session_id=self._session_id))

    def end(self) -> None:
        self._end()

    def _cancel_submits(self) -> None:
        for task in list(self._submits):
            task.cancel()

    def _cancel_retry(self) -> None:
        if self._retry is not None:
            self._retry.cancel()
            self._retry = None

    def _kick(self) -> None:
        """Make the stream match what is wanted now. Calls collapse into one
        worker, so opens and closes never overlap."""
        self._dirty = True
        if self._worker is None or self._worker.done():
            self._worker = asyncio.ensure_future(self._work())

    async def _work(self) -> None:
        while self._dirty:
            self._dirty = False
            try:
                await self._reconcile()
            except Exception:
                logger.exception("herdr.pane_session.reconcile_failed session=%s", self._session_id)

    async def _reconcile(self) -> None:
        wanted = self._viewers > 0 and self._state is SessionState.RUNNING
        if (
            wanted
            and self._stream is not None
            and self._stream_mode == self._mode
            and not self._restart
        ):
            return
        self._restart = False
        await self._close_stream()
        if not wanted:
            return
        pane_id = self._pane_id()
        if pane_id is None:
            self._end()
            return
        mode = self._mode
        try:
            if mode == CONTROL:
                size = self._client_size or self._grid or DEFAULT_GRID
            else:
                self._grid_revision = getattr(self._link, "grid_revision", None)
                size = await self._link.pane_grid(pane_id) or self._grid or DEFAULT_GRID
                self._set_grid(size)
            self._ring.clear()
            stream = await self._link.open_stream(pane_id, mode, size[0], size[1], self)
        except errors.HerdrError as exc:
            self._open_failed(mode, exc)
            return
        # Set before the start: a stream can report its first frames, a new
        # size or its own end from inside ``start``, and each of these is read
        # against the stream and the mode that stand here.
        self._stream = stream
        self._stream_mode = mode
        self._stream_opened_at = asyncio.get_running_loop().time()
        try:
            await stream.start()
        except Exception as exc:
            # Any failure: a stream that never started must not stand as the
            # current one, or no later kick would open another.
            if self._stream is stream:
                self._stream = None
                self._stream_mode = None
            if not isinstance(exc, errors.HerdrError):
                logger.exception("herdr.pane_session.start_failed session=%s", self._session_id)
                exc = errors.upstream("The pane view did not start.")
            self._open_failed(mode, exc)
            return
        if self._stream is not stream:
            return  # it ended inside ``start``; ``on_closed`` took over
        # A size that arrived while the stream opened was only stored.
        wanted_size = self._client_size
        if mode == CONTROL and wanted_size is not None and wanted_size != size:
            await stream.resize(*wanted_size)

    def _open_failed(self, mode: str, exc: errors.HerdrError) -> None:
        logger.info(
            "herdr.pane_session.open_failed session=%s mode=%s code=%s",
            self._session_id, mode, exc.code,
        )
        if exc.code in ("pane_not_found", "server_not_found"):
            self._end()
            return
        if mode == CONTROL:
            self._notice(f"Control is not available: {exc.message}")
            self._set_mode(WATCH)
            self._dirty = True
            return
        self._notice(exc.message)
        self._schedule_retry()

    def _schedule_retry(self) -> None:
        self._cancel_retry()
        delay = RETRY_SECONDS[min(self._failures, len(RETRY_SECONDS) - 1)]
        self._failures += 1
        self._retry = asyncio.get_running_loop().call_later(delay, self._kick)

    async def _close_stream(self) -> None:
        stream, self._stream, self._stream_mode = self._stream, None, None
        if stream is not None:
            await self._close_ended(stream)

    @staticmethod
    async def _close_ended(stream: PaneStream) -> None:
        try:
            await stream.aclose()
        except Exception:
            logger.debug("herdr.pane_session.close_failed", exc_info=True)

    def _notice(self, message: str) -> None:
        text = f"\x1b[0m\x1b[H\x1b[2J[Mobaide] {message}\r\n".encode("utf-8", errors="replace")
        self._publish(text, repaint=True)

    def on_output(self, data: bytes, *, repaint: bool) -> None:
        self._publish(data, repaint=repaint)

    def _publish(self, data: bytes, *, repaint: bool) -> None:
        if not data:
            return
        if repaint:
            self._ring = bytearray(data)
        else:
            self._ring += data
            if len(self._ring) > RING_MAX_BYTES:
                self._restart = True
                self._kick()
        for start in range(0, len(data), REPLAY_CHUNK_BYTES):
            self._emit(
                SessionEvent(
                    event_type="output",
                    session_id=self._session_id,
                    data=data[start : start + REPLAY_CHUNK_BYTES],
                )
            )

    def on_grid(self, cols: int, rows: int) -> None:
        if self._stream_mode == WATCH:
            self._set_grid((cols, rows))

    def on_closed(self, reason: str | None) -> None:
        """The stream ended on its own."""
        if self._stream is None:
            return
        closed_mode = self._stream_mode
        ended, self._stream = self._stream, None
        self._stream_mode = None
        # An ended stream still has its process or its channel: close it, in a
        # task that is held, or the bridge process is never reaped.
        task = asyncio.ensure_future(self._close_ended(ended))
        self._closing.add(task)
        task.add_done_callback(self._closing.discard)
        # A stream that lived counts as a success. One that paints a frame and
        # ends is a failure like any other, so its restarts slow down.
        loop_time = asyncio.get_running_loop().time()
        if self._stream_opened_at is not None and (
            loop_time - self._stream_opened_at >= STREAM_HEALTHY_SECONDS
        ):
            self._failures = 0
        if self._state is not SessionState.RUNNING:
            return
        if self._pane_id() is None:
            self._end()
            return
        logger.info(
            "herdr.pane_session.stream_closed session=%s mode=%s reason=%s",
            self._session_id, closed_mode, reason,
        )
        if closed_mode == CONTROL:
            # Another controller took the pane, or herdr ended the control.
            self._set_mode(WATCH)
            self._kick()
            return
        self._schedule_retry()

    def _note_input(self) -> None:
        if self._on_input is not None:
            self._on_input()

    async def _type(self, data: bytes) -> None:
        if self._stream is not None and self._stream_mode == CONTROL:
            await self._stream.write(data)
            return
        pane_id = self._pane_id()
        if pane_id is None:
            return
        try:
            await self._link.send_bytes(pane_id, data)
        except errors.HerdrError as exc:
            logger.debug("herdr.pane_session.input_refused code=%s", exc.code)

    async def write(self, data: bytes) -> None:
        if self._state is not SessionState.RUNNING or data in _FOCUS_REPORTS:
            return
        self._note_input()
        await self._type(data)

    async def write_submit(self, data: bytes) -> None:
        if self._state is not SessionState.RUNNING:
            return
        self._note_input()
        if self._stream is not None and self._stream_mode == CONTROL:
            # The sequence of ``TerminalSession.write_submit``: the keys ahead
            # of a paste go out on their own, or Claude CLI drops the Enter.
            # Each part goes through ``_type``: control can end during a wait,
            # and the rest must then still reach the pane, typed without it.
            paste_at = data.find(BRACKETED_PASTE_START)
            if paste_at > 0:
                await self._type(data[:paste_at])
                await asyncio.sleep(PASTE_PREFIX_DELAY_SECONDS)
                data = data[paste_at:]
            await self._type(data)
            await asyncio.sleep(SUBMIT_CR_DELAY_SECONDS)
            await self._type(b"\r")
            return
        pane_id = self._pane_id()
        text = submit_text(data)
        if pane_id is None or not text:
            return
        if len(self._submits) >= MAX_PENDING_SUBMITS:
            # Far more than a person queues: a client that sends faster than
            # herdr takes must not grow this without a limit.
            logger.info("herdr.pane_session.submit_refused code=busy")
            return
        # Not awaited here: input is serial for a session, and herdr-core
        # holds a message until the program takes it. Esc and Ctrl-C must not
        # wait behind that.
        task = asyncio.ensure_future(self._submit(pane_id, text))
        self._submits.add(task)
        task.add_done_callback(self._submits.discard)

    async def _submit(self, pane_id: str, text: str) -> None:
        # One at a time, so two messages arrive in the order they were sent.
        async with self._submit_lock:
            if self._state is not SessionState.RUNNING:
                return
            try:
                await self._link.submit(pane_id, text)
            except errors.HerdrError as exc:
                logger.info("herdr.pane_session.submit_refused code=%s", exc.code)

    async def write_execute(self, data: bytes) -> None:
        await self.write(b"\x03")
        await asyncio.sleep(EXECUTE_CLEAR_DELAY_SECONDS)
        await self.write_submit(data)

    async def resize(self, rows: int, cols: int) -> None:
        if rows <= 0 or cols <= 0:
            return
        if self._mode != CONTROL:
            # A watcher has no say in the size of the pane.
            return
        changed = self._client_size != (cols, rows)
        self._client_size = (cols, rows)
        if self._stream is not None and self._stream_mode == CONTROL:
            await self._stream.resize(cols, rows)
        if changed:
            # The watchers draw the controller's grid.
            self._emit(SessionEvent(event_type=PANE_EVENT, session_id=self._session_id))
