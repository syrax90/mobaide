from __future__ import annotations

import asyncio
import collections
import logging
import re
from dataclasses import dataclass, field
import sys
import time
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Any

from .attention_gate import AttentionGate, HookKind
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
# DECSET/DECRST, parsed as the parameter *list* it is: a TUI may batch modes
# into one sequence (`ESC[?1049;25h`), so matching a single number followed by
# `h`/`l` would miss the very transitions this tracks.
_PRIVATE_MODE_RE = re.compile(rb"\x1b\[\?([\d;]*)([hl])")
# Upper bound on the unfinished sequence carried between reads. A batched
# DECSET naming every mode this tracks is well under this; anything longer is
# not a private-mode sequence, and the cap is what stops a stream of `ESC[?`
# with no terminator from growing the carry without limit.
_PRIVATE_MODE_TAIL_MAX = 256
_PRIVATE_MODE_PARAM_BYTES = frozenset(b"0123456789;")

# Three aliases for one thing: which screen buffer is selected. A program may
# enter with one and leave with another, so they are tracked as a single state
# rather than three flags — independent flags would leave the buffer "on"
# after `ESC[?1049h` … `ESC[?47l` and put a fresh client back on the alternate
# screen. On the alternate screen the program owns the whole grid and paints
# absolute positions, so replaying bytes laid out at a stale grid desynchronizes
# it; off it the replay is line-oriented text the client's emulator re-wraps,
# and dropping it would delete usable scrollback.
_ALT_SCREEN_MODES = (1049, 1047, 47)
_DEFAULT_ALT_SCREEN_MODE = 1049

# Modes worth restoring at the head of a replay span. The sequences that
# established them may sit behind the span's start — cut at a grid change, or
# trimmed away as the ring rolled — and a client's replay wipe clears the
# display, not the emulator's mode state. Without them the span is parsed with
# the wrong screen buffer, the wrong input encoding, or the wrong key
# encoding. Curated rather than exhaustive: these are the modes whose absence
# changes how the retained bytes render or how the user's input is encoded.
_RESTORED_MODES = (
    1,     # DECCKM — application cursor keys (arrows as SS3, not CSI)
    7,     # DECAWM — autowrap; off, drawing the last column must not scroll
    25,    # cursor visibility
    2004,  # bracketed paste
)

# Mouse reporting is one selection, not a set of flags — the same shape as the
# screen aliases. Emulators hold a single active protocol: setting any of these
# replaces the previous one, and resetting *any* of them turns reporting off
# entirely. Tracked as flags, the restore prefix would emit them in numeric
# order and the highest number would win rather than the child's last action —
# putting a client back into any-event reporting after the child asked for
# drag, or back on after the child turned it off.
_MOUSE_PROTOCOL_MODES = (9, 1000, 1001, 1002, 1003)
_MOUSE_ENCODING_MODES = (1005, 1006, 1015)
_DEFAULT_MOUSE_PROTOCOL = 1000

# Home, erase display, erase scrollback — the same wipe every client feeds
# before a replay, re-issued after the prefix selects a buffer so the one that
# is actually about to be painted is the one that gets cleared.
_CLEAR_SELECTED_BUFFER = b"\x1b[H\x1b[2J\x1b[3J"


@dataclass
class _TerminalModes:
    """The private-mode state a replay span has to start in.

    Two instances are kept per session: one following the child live, and one
    pinned to the start of the current replay span. The span's copy is what a
    reconnecting client needs, because the sequences that established it may
    sit behind the span's start — cut at a grid change, or trimmed away as the
    ring rolled.
    """

    alt_screen: bool = False
    alt_screen_mode: int = _DEFAULT_ALT_SCREEN_MODE
    # The protocol/encoding in force, or None when reporting is off.
    mouse_protocol: int | None = None
    mouse_encoding: int | None = None
    modes: dict[int, bool] = field(default_factory=dict)

    def copy(self) -> "_TerminalModes":
        return _TerminalModes(
            alt_screen=self.alt_screen,
            alt_screen_mode=self.alt_screen_mode,
            mouse_protocol=self.mouse_protocol,
            mouse_encoding=self.mouse_encoding,
            modes=dict(self.modes),
        )

    def restore_prefix(self) -> bytes:
        """DECSET/DECRST sequences re-establishing this state."""
        parts = [
            b"\x1b[?%dh" % mode if enabled else b"\x1b[?%dl" % mode
            for mode, enabled in sorted(self.modes.items())
        ]
        # Stated either way, and last so no alias ordering can undo it. A
        # client's emulator is retained across reconnects and its replay wipe
        # clears the display, not the selected buffer — so a span that starts
        # on the primary screen has to say so, or an emulator left on the
        # alternate buffer parses primary-screen history there.
        # One sequence per selection, reproducing the child's last action
        # rather than the numeric order of every mode it ever touched.
        if self.mouse_protocol is not None:
            parts.append(b"\x1b[?%dh" % self.mouse_protocol)
        else:
            parts.append(b"\x1b[?%dl" % _DEFAULT_MOUSE_PROTOCOL)
        if self.mouse_encoding is not None:
            parts.append(b"\x1b[?%dh" % self.mouse_encoding)
        parts.append(
            b"\x1b[?%d%s" % (self.alt_screen_mode, b"h" if self.alt_screen else b"l")
        )
        # And the buffer this just selected is not the one the client wiped:
        # its clear ran before this prefix, against whichever buffer it happened
        # to be on. Without this, a partial span paints over stale content.
        parts.append(_CLEAR_SELECTED_BUFFER)
        return b"".join(parts)


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
        # Bytes in the ring carry no width of their own, and a replay is
        # rendered at whatever grid the client has now. Replaying output that
        # was laid out at a different column count desynchronizes a full-screen
        # TUI's cursor arithmetic for the whole rest of the span, and nothing
        # repairs it: a same-size TIOCSWINSZ raises no SIGWINCH, so the program
        # is never asked to repaint. So track where the current width began and
        # replay only from there. Rows are excluded deliberately — they do not
        # affect wrapping.
        # The PTY's current grid.
        self._pty_cols = config.cols
        self._pty_rows = config.rows
        # The grid the alternate screen's current content was painted at: set
        # when the child enters that screen, and again whenever a cut lands.
        # This is what a resize is compared against — not the PTY's latest
        # grid, so an excursion that returns needs no cut; and not the span's
        # start, which for a span covering a primary-screen stretch says
        # nothing about the grid the alternate-screen paint inside it used.
        self._alt_cols = config.cols
        self._alt_rows = config.rows
        self._ring_width_start = 0
        # Private-mode state: one copy following the child, one pinned to the
        # start of the replay span (see `_track_modes` and `_span`).
        self._live = _TerminalModes()
        self._span = _TerminalModes()
        self._mode_tail = b""
        # A grid change waits for the output that replaces the old span: the
        # ring offset to cut at, the modes in force when it was armed, and the
        # last read that must not land it.
        # Applied on a later append, so a child that dies before repainting
        # keeps the screen it died showing.
        self._pending_cut: tuple[int, _TerminalModes, int] | None = None
        # Bumped as each PTY read is issued, alongside the future it is waiting
        # on. A chunk the executor had *already returned* when the resize
        # arrived carries pre-resize output and cannot be the replacement the
        # cut waits for — but a read still parked in `read()` will return the
        # repaint, and on an idle session that is where the reader always is.
        # `Future.done()` is what separates the two; the sequence number alone
        # cannot, since it is bumped before the await.
        self._read_seq = 0
        self._read_fut: asyncio.Future[bytes] | None = None
        # True once the span stops being self-establishing — a cut landed, or
        # the head trimmed. Only then does it need a state prefix: a span that
        # still starts at the child's first byte carries every transition it
        # needs, and prefixing it would put bytes on the wire that the session
        # never produced.
        self._span_partial = False
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
        # Decides which Claude Code hooks actually become an alert. ``Stop``
        # fires once per model turn — including every re-invocation a completing
        # background task causes — so it is debounced and deduped here rather
        # than raised straight as attention.
        self._attention_gate = AttentionGate(
            on_alert=self.set_attention,
            is_running=lambda: self._state == SessionState.RUNNING,
            current_attention=lambda: self._attention,
            quiet_seconds=config.attention_quiet_seconds,
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
                self._read_seq += 1
                read_seq = self._read_seq
                self._read_fut = loop.run_in_executor(
                    None, self._backend.read, 4096,
                )
                data: bytes = await self._read_fut
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
                self._track_modes(data)
                new_title = self._osc.feed(data)
                if new_title is not None and new_title != self._pending_title:
                    self._pending_title = new_title
                    self._title_event.set()
                self._scrollback.append(data)
                self._append_ring(data, read_seq=read_seq)
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

    def _track_modes(self, data: bytes) -> None:
        """Follow the private modes a replay span would otherwise start without.

        Stateful across reads, like the DA and OSC processors on the same
        bytes: a `?1049h` split across two 4 KiB reads still counts.
        """
        was_alt = self._live.alt_screen
        window = self._mode_tail + data
        self._apply_modes(window, self._live)
        if self._live.alt_screen and not was_alt:
            # Everything the child paints from here is laid out at the PTY's
            # grid as it stands now — which is not the grid the span started
            # at if the PTY was resized while the child was on the primary
            # screen, where no cut is ever made.
            self._alt_rows, self._alt_cols = self._pty_rows, self._pty_cols
        self._mode_tail = self._unfinished_mode_tail(window)

    @staticmethod
    def _unfinished_mode_tail(window: bytes) -> bytes:
        """The trailing bytes that could still become a private-mode sequence.

        The sequence itself, not a fixed-size suffix: a batched DECSET naming
        several modes runs well past any arbitrary window, so slicing the last
        N bytes can drop the opening `ESC[?` and lose the whole sequence — and
        with it an alternate-screen transition the replay cut depends on.
        """
        start = window.rfind(b"\x1b[?")
        if start != -1:
            candidate = window[start:]
            params = candidate[3:]
            if (
                len(candidate) <= _PRIVATE_MODE_TAIL_MAX
                and all(byte in _PRIVATE_MODE_PARAM_BYTES for byte in params)
            ):
                # Still unterminated, so it cannot have matched yet — and
                # cannot match twice once the terminator arrives.
                return candidate
        # The split can also fall before the `?`.
        for partial in (b"\x1b[", b"\x1b"):
            if window.endswith(partial):
                return partial
        return b""

    @staticmethod
    def _apply_modes(
        data: bytes, state: _TerminalModes, starts_before: int | None = None
    ) -> None:
        """Fold every DECSET/DECRST in ``data`` into ``state``.

        ``starts_before`` bounds which matches count by where they *begin*, so
        a caller can append lookahead bytes purely to complete a sequence that
        straddles the end of the region it cares about.
        """
        for match in _PRIVATE_MODE_RE.finditer(data):
            if starts_before is not None and match.start() >= starts_before:
                break
            params, action = match.group(1), match.group(2)
            enabled = action == b"h"
            for raw in params.split(b";"):
                if not raw:
                    continue
                try:
                    mode = int(raw)
                except ValueError:  # pragma: no cover — regex admits digits only
                    continue
                if mode in _MOUSE_PROTOCOL_MODES:
                    # Any reset clears reporting; a set replaces whatever was
                    # selected before.
                    state.mouse_protocol = mode if enabled else None
                elif mode in _MOUSE_ENCODING_MODES:
                    state.mouse_encoding = mode if enabled else None
                elif mode in _ALT_SCREEN_MODES:
                    # One state, whichever alias set it: a program can enter
                    # with 1049 and leave with 47, and they select the same
                    # buffer. The alias is remembered so the restore prefix
                    # speaks the dialect the program itself used.
                    state.alt_screen = enabled
                    state.alt_screen_mode = mode
                elif mode in _RESTORED_MODES:
                    state.modes[mode] = enabled

    @property
    def _alt_screen(self) -> bool:
        return self._live.alt_screen

    def _cut_offset(self, *, extra: int = 0) -> int:
        """Where to cut, rolled back over any half-written mode sequence.

        The cut falls on a read boundary, which is exactly where a private-mode
        sequence can be split — its head already in the ring, its terminator
        still to come. Cutting between them would drop the mode from the span's
        state (the terminator has not reached `_live` yet) and leave the span
        opening on an orphaned fragment. The carried tail is the ring's trailing
        bytes verbatim, since an unterminated `ESC[?…` matches no scrub
        alternative, so rolling back by its length is exact.
        """
        return max(0, len(self._ring) + extra - len(self._mode_tail))

    def _append_ring(self, data: bytes, *, read_seq: int | None = None) -> None:
        # Scrub terminal query/response bytes in-line so the ring is always
        # clean. Replay senders rely on this — without the per-write strip
        # they'd have to snapshot-then-strip the entire (up to 4 MB) ring on
        # every reconnect, doubling peak heap during the handshake. This is also
        # the ring-only neutralization point for CPR/DSR (see _RING_SCRUB_RE).
        cleaned = _RING_SCRUB_RE.sub(b"", data)
        if not cleaned:
            return
        if self._pending_cut is not None:
            offset, modes, armed_after = self._pending_cut
            # A chunk whose read was already in flight when the resize arrived
            # holds output the child produced at the old grid, so landing on it
            # would start the span grid-mixed — the very thing the cut exists to
            # prevent. Wait for a read issued after the resize instead.
            if read_seq is None or read_seq > armed_after:
                # The span starts in the modes captured when the cut was armed,
                # not the ones live now: this very chunk may already have
                # changed them (a repaint ending in `ESC[?1049l`), and the
                # span's bytes were produced under the state that preceded it.
                self._ring_width_start = min(offset, len(self._ring))
                self._span = modes
                # The PTY's grid as it stands now, not the target recorded when
                # the cut was armed: a resize made off the alternate screen
                # neither re-arms nor cancels, so that target can be stale, and
                # writing it here would leave `_alt_*` describing a grid nothing
                # was painted at — after which every same-grid re-assert arms a
                # cut and the next output truncates the replay.
                self._alt_rows, self._alt_cols = self._pty_rows, self._pty_cols
                self._span_partial = True
                self._pending_cut = None
            else:
                # Push the cut past the stale chunk. `_live` already includes
                # this chunk's own mode changes, which is exactly the state the
                # span would start in were it to begin after it.
                self._pending_cut = (
                    self._cut_offset(extra=len(cleaned)),
                    self._live.copy(),
                    armed_after,
                )
        self._ring += cleaned
        overflow = len(self._ring) - self._ring_cap
        if overflow > 0:
            dropped = bytes(self._ring[:overflow])
            del self._ring[:overflow]
            self._span_partial = True
            # Whatever the trim ate *inside* the span is state the span no
            # longer establishes for itself, so fold it into the span's copy.
            # Without this a long-running session silently loses the modes its
            # replay depends on, exactly as a cut does.
            eaten = dropped[self._ring_width_start :]
            if eaten:
                # Lookahead into what survived: the trim boundary is byte-
                # granular, so it can bisect a sequence — `ESC[?104` dropped,
                # `9h` retained. Parsing the dropped side alone would miss the
                # mode entirely, since neither half establishes it. Bounded by
                # where the match starts so the retained bytes' own sequences
                # are left to the replay.
                span_start = len(eaten)
                self._apply_modes(
                    eaten + bytes(self._ring[:_PRIVATE_MODE_TAIL_MAX]),
                    self._span,
                    starts_before=span_start,
                )
            self._ring_width_start = max(0, self._ring_width_start - overflow)

    def get_ring(self) -> bytes:
        """Whole-ring snapshot, for tests and diagnostics.

        Binary-WS replay goes through ``iter_ring_chunks()``, which — unlike
        this — starts at ``_ring_width_start`` so the span it yields is all one
        column count.
        """
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

        Starts at the last column change, not at the head: output laid out at
        a stale width cannot be re-wrapped and would mis-render for the rest of
        the span. History older than that change is dropped from the replay —
        it is unrenderable at the current grid, which is the whole point.

        A cut span is prefixed with the private modes in force when it was made
        (`_mode_restore_prefix`), because the sequences that established them
        are behind the cut and a client's replay wipe does not restore them.

        An empty ring yields nothing — callers that need a
        "handshake-always-has-an-OUTPUT-frame" invariant must emit one
        explicitly.
        """
        if chunk_size <= 0:
            raise ValueError(f"chunk_size must be positive: {chunk_size}")
        snapshot = bytes(self._ring[self._ring_width_start :])
        if self._span_partial:
            snapshot = self._span.restore_prefix() + snapshot
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

    def cancel_attention_window(self) -> None:
        """Drop a pending debounce without forgetting the turn.

        What a keystroke warrants: the user is plainly present, so a queued
        "your move" should not fire — but client input is not proof of a *new*
        turn. Both apps push terminal scrolling through the same PTY-write path
        as typing, so treating any input as a turn boundary would let a scroll
        gesture discard the dedupe key and re-open the alert storm while
        background work is still draining. The turn boundary is
        ``UserPromptSubmit`` alone; the reader loop's ``awaiting_os_permission``
        auto-clear is not one either, since resumed output is not the user
        acting.
        """
        self._attention_gate.cancel()

    def set_attention(self, attention: str, exit_reason: str | None = None) -> None:
        """Record an attention trigger and emit an attention event.

        ``attention`` is one of ``awaiting_permission`` | ``turn_finished`` |
        ``ended`` (Claude Code hooks, via the agent's ``/internal/hook``
        endpoint) or ``awaiting_os_permission`` (the startup-stall watchdog).
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
        self.cancel_attention_window()
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
        """Resize the PTY terminal, and schedule the replay cut it implies.

        Three conditions, each load-bearing:

        * **The grid differs from the replay span's.** Either axis: on the
          alternate screen a program paints absolute positions, so rows drive
          cursor addresses, scrolling regions and bottom-anchored UI exactly as
          columns drive wrapping. Rows change routinely right after create,
          where every client's row estimate includes chrome the real terminal
          subtracts. Compared against the *span's* grid rather than the last
          resize, so an excursion that returns before any output — a transient
          layout, or two clients asserting in turn — cancels its own cut
          instead of discarding history the span never stopped matching.
        * **The child is on the alternate screen.** There, stale-grid bytes
          desynchronize its arithmetic for the rest of the span and no repaint
          after the replay repairs it. On the normal screen the replay is
          line-oriented text the client's emulator re-wraps, so it stays
          readable and dropping it would delete usable scrollback for no gain.
        * **The session is running.** A stopped or crashed child will never
          repaint, so cutting its history just blanks its last output. The
          backend can still accept a resize after the child dies — the master
          fd outlives it until the liveness sweep — so this has to be explicit.

        The cut is *scheduled*, not applied: it takes effect when the output
        that replaces the old span arrives. A child that dies between the
        resize and its repaint therefore keeps the screen it died showing,
        which the state check alone cannot cover — death can follow the resize
        as easily as precede it. The schedule carries the modes in force now,
        because the chunk that lands it may itself change them.
        """
        self._backend.resize(rows, cols)
        self._pty_rows, self._pty_cols = rows, cols
        if (rows, cols) == (self._alt_rows, self._alt_cols):
            # Back to the grid the span was produced at — whatever excursion
            # armed a cut, the span is homogeneous with the PTY again and
            # cutting would discard history for nothing.
            self._pending_cut = None
        elif self._alt_screen and self._state == SessionState.RUNNING:
            # A read still blocked will deliver the repaint, so the cut must
            # land on it; one already returned holds old-grid output and must
            # be stepped over.
            in_flight = self._read_fut is not None and not self._read_fut.done()
            self._pending_cut = (
                self._cut_offset(),
                self._live.copy(),
                self._read_seq - 1 if in_flight else self._read_seq,
            )

    def get_scrollback(self) -> list[str]:
        """Return the current scrollback buffer as decoded strings.

        The realtime WS path uses ``iter_ring_chunks()`` for byte-exact replay
        (which starts at the last width change, unlike this); this
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

        self._attention_gate.cancel()

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
        )
