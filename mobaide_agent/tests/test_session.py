from __future__ import annotations

import asyncio
import sys
import threading
from typing import cast
from unittest.mock import AsyncMock, call, patch

import pytest

from mobaide.core.models import SessionConfig, SessionEvent, SessionState
from mobaide.core.pty_backend import PtyBackend
from mobaide.core.session import (
    EXECUTE_CLEAR_DELAY_SECONDS,
    GRACEFUL_TERM_SECONDS,
    STARTUP_STALL_MIN_BYTES,
    SUBMIT_CR_DELAY_SECONDS,
    TerminalSession,
)
from tests.conftest import IS_WINDOWS, skip_if_not_linux


@pytest.mark.timeout(15)
class TestTerminalSessionLifecycle:
    async def test_start_and_stop(self, session_config: SessionConfig) -> None:
        session = TerminalSession(session_config)
        await session.start()

        assert session.state == SessionState.RUNNING
        assert session.pid is not None
        assert session.session_id == "test-session"

        await session.stop()
        assert session.state == SessionState.STOPPED

    @skip_if_not_linux
    async def test_stop_terminates_sigterm_ignoring_child(self) -> None:
        """A child that ignores SIGTERM must still be stopped promptly.

        Regression for the agent-wide freeze: ``stop(force=False)`` used to send
        only SIGTERM, which interactive login shells and many TUIs ignore. The
        PTY reader's blocking ``os.read`` then never saw EOF, and
        ``asyncio.wait_for(reader_task, 5.0)`` could not honour its timeout
        against an uncancellable executor read — so teardown hung forever and,
        because the relay reader awaits each handler inline, froze the whole
        agent. ``stop`` now escalates to SIGKILL, so this completes.
        """
        config = SessionConfig(
            session_id="sigterm-ignorer",
            command=[
                sys.executable,
                "-c",
                "import signal, sys, time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "sys.stdout.write('ready\\n'); sys.stdout.flush(); "
                "time.sleep(300)",
            ],
            rows=24,
            cols=80,
            scrollback_size=100,
        )
        session = TerminalSession(config)
        await session.start()
        assert session.state == SessionState.RUNNING

        # Wait for "ready", which the child prints only after installing the
        # SIGTERM-ignoring handler — so by now SIGTERM provably won't kill it.
        seen_ready = False
        deadline = asyncio.get_event_loop().time() + 5.0
        while asyncio.get_event_loop().time() < deadline and not seen_ready:
            try:
                event = await asyncio.wait_for(session.event_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if event.event_type == "output" and event.data and b"ready" in event.data:
                seen_ready = True
        assert seen_ready, "child never signalled readiness"

        # Before the fix this never returns; the outer timeout turns the
        # regression into a clear failure instead of a frozen test run.
        await asyncio.wait_for(session.stop(force=False), timeout=10.0)
        assert session.state == SessionState.STOPPED

    async def test_write_and_read_output(self, session_config: SessionConfig) -> None:
        session = TerminalSession(session_config)
        await session.start()

        try:
            # Wait for shell to initialize
            await asyncio.sleep(0.5)

            if IS_WINDOWS:
                await session.write(b"echo SESSIONTEST456\r\n")
            else:
                await session.write(b"echo SESSIONTEST456\n")

            # Drain events until we see our marker
            output = b""
            deadline = asyncio.get_event_loop().time() + 5.0
            while asyncio.get_event_loop().time() < deadline:
                try:
                    event: SessionEvent = await asyncio.wait_for(
                        session.event_queue.get(),
                        timeout=1.0,
                    )
                    if event.event_type == "output" and event.data:
                        output += event.data
                    if b"SESSIONTEST456" in output:
                        break
                except asyncio.TimeoutError:
                    continue

            assert b"SESSIONTEST456" in output
        finally:
            await session.stop()

    async def test_scrollback_accumulates(
        self, session_config: SessionConfig
    ) -> None:
        session = TerminalSession(session_config)
        await session.start()

        try:
            await asyncio.sleep(0.5)

            for i in range(5):
                if IS_WINDOWS:
                    await session.write(f"echo line{i}\r\n".encode())
                else:
                    await session.write(f"echo line{i}\n".encode())

            # Give time for output to be read
            await asyncio.sleep(1.0)

            scrollback = session.get_scrollback()
            assert len(scrollback) > 0
        finally:
            await session.stop()

    async def test_scrollback_max_size(self) -> None:
        config = SessionConfig(
            session_id="small-buffer",
            command=["cmd.exe"] if IS_WINDOWS else ["/bin/bash"],
            scrollback_size=3,
        )
        session = TerminalSession(config)
        await session.start()

        try:
            await asyncio.sleep(0.5)

            for i in range(10):
                if IS_WINDOWS:
                    await session.write(f"echo overflow{i}\r\n".encode())
                else:
                    await session.write(f"echo overflow{i}\n".encode())

            await asyncio.sleep(1.5)

            scrollback = session.get_scrollback()
            # Deque maxlen=3 means at most 3 entries
            assert len(scrollback) <= 3
        finally:
            await session.stop()

    async def test_resize(self, session_config: SessionConfig) -> None:
        session = TerminalSession(session_config)
        await session.start()

        try:
            await session.resize(40, 120)
        finally:
            await session.stop()

    async def test_crash_detection(
        self, short_lived_config: SessionConfig
    ) -> None:
        """A short-lived process should trigger CRASHED state."""
        session = TerminalSession(short_lived_config)
        await session.start()

        # Wait for the short-lived process to exit and reader loop to detect it
        deadline = asyncio.get_event_loop().time() + 5.0
        while asyncio.get_event_loop().time() < deadline:
            if session.state in (SessionState.CRASHED, SessionState.STOPPED):
                break
            await asyncio.sleep(0.2)

        assert session.state in (SessionState.CRASHED, SessionState.STOPPED)

    async def test_started_event_emitted(
        self, session_config: SessionConfig
    ) -> None:
        session = TerminalSession(session_config)
        await session.start()

        try:
            event = await asyncio.wait_for(session.event_queue.get(), timeout=2.0)
            assert event.event_type == "started"
            assert event.session_id == "test-session"
        finally:
            await session.stop()

    async def test_stop_is_idempotent(
        self, session_config: SessionConfig
    ) -> None:
        session = TerminalSession(session_config)
        await session.start()
        await session.stop()
        await session.stop()  # Should not raise
        assert session.state == SessionState.STOPPED


class _ScriptedPtyBackend:
    """Test double for :class:`PtyBackend` driven by a scripted byte queue.

    ``script`` holds chunks the reader will receive in order, one per
    ``read()`` call. After the script is exhausted, ``read()`` blocks on
    ``_drained`` (released by ``terminate``/``close``) and then returns
    ``b""`` so :class:`TerminalSession._reader_loop` exits cleanly without
    racing the title coalescer. This keeps the test deterministic across
    platforms and lets the real OSC tap + coalescer pipeline run end-to-end.
    """

    def __init__(self, script: list[bytes]) -> None:
        self._script = list(script)
        self._lock = threading.Lock()
        self._drained = threading.Event()
        self._closed = False
        self._writes: list[bytes] = []

    def spawn(self, cmd, cwd, env, rows, cols):  # noqa: D401, ARG002
        return None

    def read(self, size: int = 4096) -> bytes:
        with self._lock:
            if self._script:
                return self._script.pop(0)
        # Park the executor thread until the test tears the session down.
        self._drained.wait(timeout=5.0)
        return b""

    def write(self, data: bytes) -> None:
        self._writes.append(data)

    def resize(self, rows: int, cols: int) -> None:
        return None

    def is_alive(self) -> bool:
        return not self._closed

    def get_pid(self) -> int | None:
        return 12345

    def terminate(self, force: bool = False) -> None:
        self._drained.set()

    def close(self) -> None:
        self._closed = True
        self._drained.set()


def _title_config() -> SessionConfig:
    return SessionConfig(
        session_id="title-session",
        command=["does-not-run"],
        rows=24,
        cols=80,
        scrollback_size=100,
    )


async def _drain_title_event(
    session: TerminalSession, timeout: float = 2.0
) -> SessionEvent:
    """Pull events off the queue until the first ``title`` event arrives.

    The reader-loop output and the synthetic ``started`` event share the
    queue; this helper drops them and returns only the coalescer's emission
    so callers don't have to know the interleaving.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        event = await asyncio.wait_for(
            session.event_queue.get(), timeout=remaining,
        )
        if event.event_type == "title":
            return event
    raise AssertionError("title event was not enqueued within the timeout")


@pytest.mark.timeout(15)
class TestTerminalSessionTitleCoalescer:
    """Integration tests for the OSC-title tap + trailing-edge coalescer.

    Uses :class:`_ScriptedPtyBackend` so the assertions are deterministic
    across Windows ConPTY / Linux PTY and do not depend on the host shell
    interpreting OSC escapes.
    """

    async def test_single_osc_title_publishes_after_debounce_window(self) -> None:
        backend = _ScriptedPtyBackend([b"\x1b]0;hello world\x07"])
        session = TerminalSession(
            _title_config(),
            backend=cast(PtyBackend, backend),
        )
        await session.start()
        try:
            event = await _drain_title_event(session, timeout=2.0)

            assert event.event_type == "title"
            assert event.session_id == "title-session"
            assert event.data is None
            assert session.to_info().title == "hello world"
        finally:
            await session.stop()

    async def test_rapid_title_bursts_collapse_to_last_value(self) -> None:
        # Ten different titles in a single read chunk — well inside the
        # 500 ms debounce window, regardless of executor latency.
        chunk = b"".join(
            f"\x1b]0;title-{i}\x07".encode() for i in range(10)
        )
        backend = _ScriptedPtyBackend([chunk])
        session = TerminalSession(
            _title_config(),
            backend=cast(PtyBackend, backend),
        )
        await session.start()
        try:
            event = await _drain_title_event(session, timeout=2.0)

            assert event.event_type == "title"
            assert event.session_id == "title-session"
            assert session.to_info().title == "title-9"

            # Allow one more debounce window to elapse and assert no
            # further title event is emitted — confirms last-value-wins
            # collapsing rather than 10 separate publishes.
            await asyncio.sleep(0.7)
            extra_titles = 0
            while not session.event_queue.empty():
                ev = session.event_queue.get_nowait()
                if ev.event_type == "title":
                    extra_titles += 1
            assert extra_titles == 0
        finally:
            await session.stop()


def _ring_config(ring_buffer_bytes: int) -> SessionConfig:
    return SessionConfig(
        session_id="ring-session",
        command=["does-not-run"],
        rows=24,
        cols=80,
        scrollback_size=100,
        ring_buffer_bytes=ring_buffer_bytes,
    )


class TestRingBufferCap:
    """``SessionConfig.ring_buffer_bytes`` is the per-session override the
    protocol-v3 chunked replay relies on: the field's default supplies the
    4 MB production cap, and tests use a small value here to exercise the
    overflow trim without producing megabytes of data.
    """

    def test_append_ring_below_cap_keeps_full_payload(self) -> None:
        # The session is never started — we drive ``_append_ring`` directly
        # the same way ``test_ws_binary_protocol::TestRingBufferReplay``
        # seeds the ring without depending on a real PTY.
        session = TerminalSession(_ring_config(ring_buffer_bytes=1024))
        session._append_ring(b"abc")
        session._append_ring(b"def")
        assert session.get_ring() == b"abcdef"

    def test_append_ring_overflow_trims_from_head(self) -> None:
        session = TerminalSession(_ring_config(ring_buffer_bytes=8))
        session._append_ring(b"AAAA")
        session._append_ring(b"BBBB")
        session._append_ring(b"CCCC")
        ring = session.get_ring()
        # 12 bytes pushed, 8 bytes cap → only the last 8 survive,
        # head-trimmed, never tail-trimmed.
        assert len(ring) == 8
        assert ring == b"AAAABBBBCCCC"[-8:]
        assert ring == b"BBBBCCCC"

    def test_append_ring_single_oversized_write_truncates_to_cap(self) -> None:
        session = TerminalSession(_ring_config(ring_buffer_bytes=4))
        session._append_ring(b"abcdefgh")
        ring = session.get_ring()
        assert len(ring) == 4
        assert ring == b"efgh"

    def test_ring_cap_is_per_session_not_module_global(self) -> None:
        # Two sessions with different caps must not share state — the field
        # has to be read off the config, not the module-level constant.
        small = TerminalSession(_ring_config(ring_buffer_bytes=4))
        large = TerminalSession(_ring_config(ring_buffer_bytes=64))
        small._append_ring(b"abcdefgh")
        large._append_ring(b"abcdefgh")
        assert len(small.get_ring()) == 4
        assert large.get_ring() == b"abcdefgh"


class TestIterRingChunks:
    """``iter_ring_chunks`` is the replay-sender's allocation-bounded path:
    the WS handshake in ``session_io.py`` and the relay mirror in
    ``relay/client.py`` both lean on it to keep individual WS frames small
    while never holding two copies of a multi-MB ring.
    """

    def test_empty_ring_yields_nothing(self) -> None:
        session = TerminalSession(_ring_config(ring_buffer_bytes=1024))
        assert list(session.iter_ring_chunks(chunk_size=64)) == []

    def test_chunks_concatenate_to_original_ring(self) -> None:
        session = TerminalSession(_ring_config(ring_buffer_bytes=1024))
        payload = bytes(range(256)) + bytes(range(256))
        session._append_ring(payload)
        chunks = list(session.iter_ring_chunks(chunk_size=64))
        assert b"".join(chunks) == payload
        assert b"".join(chunks) == session.get_ring()

    def test_ring_smaller_than_chunk_yields_single_chunk(self) -> None:
        session = TerminalSession(_ring_config(ring_buffer_bytes=1024))
        session._append_ring(b"hello")
        chunks = list(session.iter_ring_chunks(chunk_size=64))
        assert chunks == [b"hello"]

    def test_ring_aligned_to_chunk_boundary(self) -> None:
        session = TerminalSession(_ring_config(ring_buffer_bytes=1024))
        session._append_ring(b"A" * 128)
        chunks = list(session.iter_ring_chunks(chunk_size=64))
        # Exact multiple → exactly two full chunks, no empty trailing chunk.
        assert len(chunks) == 2
        assert all(len(c) == 64 for c in chunks)
        assert chunks[0] == b"A" * 64
        assert chunks[1] == b"A" * 64

    def test_ring_one_byte_over_chunk_boundary(self) -> None:
        # The N=2 case the change_summary calls out — 64 KB + 1 produces
        # one full chunk plus a one-byte tail chunk.
        session = TerminalSession(_ring_config(ring_buffer_bytes=128 * 1024))
        session._append_ring(b"A" * 64 + b"B")
        chunks = list(session.iter_ring_chunks(chunk_size=64))
        assert len(chunks) == 2
        assert chunks[0] == b"A" * 64
        assert chunks[1] == b"B"

    def test_chunks_are_bytes_not_bytearray_or_memoryview(self) -> None:
        # WS framing concatenates ``FRAME_OUTPUT + chunk`` — a bytearray
        # would coerce silently but a memoryview slice would raise. Pin the
        # contract explicitly so a refactor can't quietly change the type.
        session = TerminalSession(_ring_config(ring_buffer_bytes=64))
        session._append_ring(b"abc")
        for chunk in session.iter_ring_chunks(chunk_size=2):
            assert isinstance(chunk, bytes)

    def test_zero_chunk_size_raises_value_error(self) -> None:
        session = TerminalSession(_ring_config(ring_buffer_bytes=64))
        session._append_ring(b"abc")
        with pytest.raises(ValueError):
            list(session.iter_ring_chunks(chunk_size=0))

    def test_negative_chunk_size_raises_value_error(self) -> None:
        session = TerminalSession(_ring_config(ring_buffer_bytes=64))
        with pytest.raises(ValueError):
            list(session.iter_ring_chunks(chunk_size=-1))

    def test_default_chunk_size_is_replay_chunk_bytes(self) -> None:
        # Don't depend on the literal 64 KiB — import the module constant
        # so the test breaks loudly if the production value moves.
        from mobaide.core.session import REPLAY_CHUNK_BYTES

        session = TerminalSession(_ring_config(ring_buffer_bytes=4 * 1024 * 1024))
        session._append_ring(b"X" * (REPLAY_CHUNK_BYTES + 10))
        chunks = list(session.iter_ring_chunks())
        assert len(chunks) == 2
        assert len(chunks[0]) == REPLAY_CHUNK_BYTES
        assert len(chunks[1]) == 10


def _attention_config() -> SessionConfig:
    return SessionConfig(
        session_id="attn-session",
        command=["does-not-run"],
        rows=24,
        cols=80,
        scrollback_size=100,
    )


def _drain_attention_events(session: TerminalSession) -> list[SessionEvent]:
    """Pull every queued ``attention`` event without blocking.

    ``set_attention``/``clear_attention`` enqueue synchronously via
    ``put_nowait``; the session is never started, so the queue holds only what
    the call under test enqueued.
    """
    events: list[SessionEvent] = []
    while not session.event_queue.empty():
        ev = session.event_queue.get_nowait()
        if ev.event_type == "attention":
            events.append(ev)
    return events


class TestTerminalSessionAttention:
    """``set_attention`` / ``clear_attention`` are the hook-driven attention
    state the ``/internal/hook`` endpoint mutates; the enqueued ``attention``
    event is what the fanout turns into ``session.attention`` /
    ``session.updated``. The session is never started here — these methods
    only touch in-memory state and the bounded event queue.
    """

    async def test_set_attention_stamps_field_and_to_info(self) -> None:
        session = TerminalSession(_attention_config())
        session.set_attention("awaiting_permission")
        assert session.to_info().attention == "awaiting_permission"

    async def test_set_attention_enqueues_event_with_reason(self) -> None:
        session = TerminalSession(_attention_config())
        session.set_attention("turn_finished")
        events = _drain_attention_events(session)
        assert len(events) == 1
        assert events[0].event_type == "attention"
        assert events[0].session_id == "attn-session"
        assert events[0].attention == "turn_finished"

    async def test_set_attention_ended_records_exit_reason(self) -> None:
        session = TerminalSession(_attention_config())
        session.set_attention("ended", exit_reason="clear")
        info = session.to_info()
        assert info.attention == "ended"
        assert info.exit_reason == "clear"

    async def test_set_attention_without_exit_reason_leaves_it_none(self) -> None:
        session = TerminalSession(_attention_config())
        session.set_attention("turn_finished")
        assert session.to_info().exit_reason is None

    async def test_clear_attention_when_set_emits_none_event(self) -> None:
        session = TerminalSession(_attention_config())
        session.set_attention("awaiting_permission")
        _drain_attention_events(session)  # discard the set event

        session.clear_attention()

        assert session.to_info().attention is None
        events = _drain_attention_events(session)
        assert len(events) == 1
        assert events[0].attention is None

    async def test_clear_attention_when_unset_is_noop(self) -> None:
        # No attention pending → no event, so ordinary keystrokes don't spam
        # the fanout with redundant clears.
        session = TerminalSession(_attention_config())
        session.clear_attention()
        assert _drain_attention_events(session) == []

    async def test_clear_attention_preserves_exit_reason(self) -> None:
        # exit_reason is the human-readable end cause; clearing the live
        # attention badge must not wipe it from the snapshot.
        session = TerminalSession(_attention_config())
        session.set_attention("ended", exit_reason="other")
        _drain_attention_events(session)
        session.clear_attention()
        assert session.to_info().exit_reason == "other"

    async def test_write_clears_pending_attention(self) -> None:
        # User input means the user responded — write() must drop the badge
        # for both "typed next instruction" and "approved permission".
        backend = _ScriptedPtyBackend([])
        session = TerminalSession(
            _attention_config(), backend=cast(PtyBackend, backend),
        )
        session.set_attention("awaiting_permission")
        _drain_attention_events(session)

        await session.write(b"y\n")

        assert session.to_info().attention is None
        events = _drain_attention_events(session)
        assert len(events) == 1
        assert events[0].attention is None
        # The keystroke still reached the backend.
        assert backend._writes == [b"y\n"]

    async def test_write_without_pending_attention_emits_no_attention_event(
        self,
    ) -> None:
        backend = _ScriptedPtyBackend([])
        session = TerminalSession(
            _attention_config(), backend=cast(PtyBackend, backend),
        )
        await session.write(b"ls\n")
        assert _drain_attention_events(session) == []
        assert backend._writes == [b"ls\n"]


@pytest.mark.timeout(15)
class TestWriteSubmitAndExecute:
    """The CR must land as its own delayed PTY write — Ink TUIs treat a CR
    inside the same stdin chunk as pasted content, not the Enter key."""

    async def test_write_submit_writes_payload_then_cr(self) -> None:
        backend = _ScriptedPtyBackend([])
        session = TerminalSession(
            _attention_config(), backend=cast(PtyBackend, backend),
        )

        with patch(
            "mobaide.core.session.asyncio.sleep", new_callable=AsyncMock
        ) as sleep_mock:
            await session.write_submit(b"hello")

        assert backend._writes == [b"hello", b"\r"]
        sleep_mock.assert_awaited_once_with(SUBMIT_CR_DELAY_SECONDS)

    async def test_write_execute_writes_ctrl_c_then_payload_then_cr(self) -> None:
        backend = _ScriptedPtyBackend([])
        session = TerminalSession(
            _attention_config(), backend=cast(PtyBackend, backend),
        )

        with patch(
            "mobaide.core.session.asyncio.sleep", new_callable=AsyncMock
        ) as sleep_mock:
            await session.write_execute(b"ls -la")

        assert backend._writes == [b"\x03", b"ls -la", b"\r"]
        assert sleep_mock.await_args_list == [
            call(EXECUTE_CLEAR_DELAY_SECONDS),
            call(SUBMIT_CR_DELAY_SECONDS),
        ]

    async def test_write_submit_sleeps_between_payload_and_cr(self) -> None:
        """The pause must land BETWEEN the two writes — back-to-back writes
        with the sleep elsewhere would let Ink coalesce payload and CR into
        one read, reintroducing the paste-instead-of-Enter bug."""
        backend = _ScriptedPtyBackend([])
        session = TerminalSession(
            _attention_config(), backend=cast(PtyBackend, backend),
        )

        events: list[tuple[str, object]] = []
        scripted_write = backend.write

        def _recording_write(data: bytes) -> None:
            events.append(("write", data))
            scripted_write(data)

        backend.write = _recording_write  # type: ignore[method-assign]
        sleep_mock = AsyncMock(
            side_effect=lambda delay: events.append(("sleep", delay))
        )

        with patch("mobaide.core.session.asyncio.sleep", sleep_mock):
            await session.write_submit(b"hello")

        assert events == [
            ("write", b"hello"),
            ("sleep", SUBMIT_CR_DELAY_SECONDS),
            ("write", b"\r"),
        ]

    async def test_write_execute_sleeps_between_each_write(self) -> None:
        """Both pauses must interleave with the writes: the TUI needs the
        redraw pause after Ctrl+C before text arrives, and the submit pause
        after the text before the CR."""
        backend = _ScriptedPtyBackend([])
        session = TerminalSession(
            _attention_config(), backend=cast(PtyBackend, backend),
        )

        events: list[tuple[str, object]] = []
        scripted_write = backend.write

        def _recording_write(data: bytes) -> None:
            events.append(("write", data))
            scripted_write(data)

        backend.write = _recording_write  # type: ignore[method-assign]
        sleep_mock = AsyncMock(
            side_effect=lambda delay: events.append(("sleep", delay))
        )

        with patch("mobaide.core.session.asyncio.sleep", sleep_mock):
            await session.write_execute(b"ls -la")

        assert events == [
            ("write", b"\x03"),
            ("sleep", EXECUTE_CLEAR_DELAY_SECONDS),
            ("write", b"ls -la"),
            ("sleep", SUBMIT_CR_DELAY_SECONDS),
            ("write", b"\r"),
        ]


def _stall_config(startup_stall_seconds: float | None) -> SessionConfig:
    return SessionConfig(
        session_id="stall-session",
        command=["does-not-run"],
        rows=24,
        cols=80,
        scrollback_size=100,
        startup_stall_seconds=startup_stall_seconds,
    )


class TestStartupStallWatchdog:
    """The macOS-gated startup-stall watchdog raises ``awaiting_os_permission``
    when a freshly-spawned session stays RUNNING, sees no user input, and emits
    fewer than ``STARTUP_STALL_MIN_BYTES`` before the timeout — the signature of
    a child blocked on an invisible TCC file-access modal on the host.

    The watchdog body is driven directly with ``asyncio.sleep`` stubbed out so
    the assertions are deterministic on Linux CI regardless of host platform.
    The session is set RUNNING in-place (``start()`` would spawn a real PTY);
    this mirrors the direct-invocation style of ``TestTerminalSessionAttention``.
    """

    async def test_fires_when_starved_of_output(self) -> None:
        session = TerminalSession(_stall_config(0.01))
        session._state = SessionState.RUNNING

        with patch(
            "mobaide.core.session.asyncio.sleep", new_callable=AsyncMock
        ) as sleep_mock:
            await session._startup_stall_watchdog(0.01)

        sleep_mock.assert_awaited_once_with(0.01)
        assert session.to_info().attention == "awaiting_os_permission"
        events = _drain_attention_events(session)
        assert [e.attention for e in events] == ["awaiting_os_permission"]

    async def test_does_not_fire_when_output_meets_threshold(self) -> None:
        session = TerminalSession(_stall_config(0.01))
        session._state = SessionState.RUNNING
        session._startup_output_bytes = STARTUP_STALL_MIN_BYTES

        with patch(
            "mobaide.core.session.asyncio.sleep", new_callable=AsyncMock
        ):
            await session._startup_stall_watchdog(0.01)

        assert session.to_info().attention is None
        assert _drain_attention_events(session) == []

    async def test_does_not_fire_after_user_input(self) -> None:
        # A user who is typing is plainly not staring at a frozen screen, even
        # if the child has produced no output yet.
        session = TerminalSession(_stall_config(0.01))
        session._state = SessionState.RUNNING
        session._user_input_seen = True

        with patch(
            "mobaide.core.session.asyncio.sleep", new_callable=AsyncMock
        ):
            await session._startup_stall_watchdog(0.01)

        assert session.to_info().attention is None
        assert _drain_attention_events(session) == []

    async def test_does_not_clobber_preset_awaiting_permission(self) -> None:
        # A genuine Claude ``awaiting_permission`` hook must win — the
        # ``_attention is None`` guard keeps the watchdog from overwriting it.
        session = TerminalSession(_stall_config(0.01))
        session._state = SessionState.RUNNING
        session.set_attention("awaiting_permission")
        _drain_attention_events(session)

        with patch(
            "mobaide.core.session.asyncio.sleep", new_callable=AsyncMock
        ):
            await session._startup_stall_watchdog(0.01)

        assert session.to_info().attention == "awaiting_permission"
        assert _drain_attention_events(session) == []

    async def test_does_not_fire_when_not_running(self) -> None:
        # A session that already stopped/crashed before the timeout elapsed must
        # not raise a stale attention.
        session = TerminalSession(_stall_config(0.01))
        session._state = SessionState.STOPPED

        with patch(
            "mobaide.core.session.asyncio.sleep", new_callable=AsyncMock
        ):
            await session._startup_stall_watchdog(0.01)

        assert session.to_info().attention is None
        assert _drain_attention_events(session) == []

    async def test_reader_clears_badge_once_output_crosses_threshold(self) -> None:
        # End-to-end recovery: the watchdog raised the badge, then real output
        # flows (the user approved on the Mac). One chunk at/over the threshold
        # must self-clear the ``awaiting_os_permission`` attention.
        chunk = b"x" * STARTUP_STALL_MIN_BYTES
        backend = _ScriptedPtyBackend([chunk])
        session = TerminalSession(
            _stall_config(0.01), backend=cast(PtyBackend, backend),
        )
        session._state = SessionState.RUNNING
        session.set_attention("awaiting_os_permission")
        _drain_attention_events(session)

        await session._reader_loop()

        assert session._startup_output_bytes >= STARTUP_STALL_MIN_BYTES
        assert session.to_info().attention is None
        events = _drain_attention_events(session)
        assert events[-1].attention is None

    async def test_reader_below_threshold_keeps_badge(self) -> None:
        # A trickle of init bytes under the floor must NOT clear the badge —
        # the child is still effectively stalled.
        chunk = b"x" * (STARTUP_STALL_MIN_BYTES - 1)
        backend = _ScriptedPtyBackend([chunk])
        session = TerminalSession(
            _stall_config(0.01), backend=cast(PtyBackend, backend),
        )
        session._state = SessionState.RUNNING
        session.set_attention("awaiting_os_permission")
        _drain_attention_events(session)

        await session._reader_loop()

        assert session._startup_output_bytes < STARTUP_STALL_MIN_BYTES
        assert session.to_info().attention == "awaiting_os_permission"
        assert _drain_attention_events(session) == []


@pytest.mark.timeout(15)
class TestStartupStallWatchdogArming:
    """``start()`` arms the watchdog task only when the effective timeout is
    positive. The explicit ``startup_stall_seconds`` override keeps the gating
    deterministic on Linux CI; the platform-default branch is exercised by
    pinning ``sys.platform``.
    """

    async def test_armed_when_timeout_positive(self) -> None:
        backend = _ScriptedPtyBackend([])
        session = TerminalSession(
            _stall_config(0.01), backend=cast(PtyBackend, backend),
        )
        with patch(
            "mobaide.core.session.asyncio.sleep", new_callable=AsyncMock
        ):
            await session.start()
            try:
                assert session._stall_task is not None
            finally:
                await session.stop()

    async def test_disabled_when_zero(self) -> None:
        backend = _ScriptedPtyBackend([])
        session = TerminalSession(
            _stall_config(0.0), backend=cast(PtyBackend, backend),
        )
        await session.start()
        try:
            assert session._stall_task is None
        finally:
            await session.stop()

    async def test_none_disables_off_darwin(self) -> None:
        backend = _ScriptedPtyBackend([])
        session = TerminalSession(
            _stall_config(None), backend=cast(PtyBackend, backend),
        )
        with patch("mobaide.core.session.sys.platform", "linux"):
            await session.start()
            try:
                assert session._stall_task is None
            finally:
                await session.stop()

    async def test_none_enables_on_darwin(self) -> None:
        backend = _ScriptedPtyBackend([])
        session = TerminalSession(
            _stall_config(None), backend=cast(PtyBackend, backend),
        )
        with patch("mobaide.core.session.sys.platform", "darwin"), patch(
            "mobaide.core.session.asyncio.sleep", new_callable=AsyncMock
        ):
            await session.start()
            try:
                assert session._stall_task is not None
            finally:
                await session.stop()

    async def test_stop_cancels_watchdog_task(self) -> None:
        backend = _ScriptedPtyBackend([])
        session = TerminalSession(
            _stall_config(60.0), backend=cast(PtyBackend, backend),
        )
        await session.start()
        task = session._stall_task
        assert task is not None
        await session.stop()
        assert task.done()


class _TerminationSpyBackend:
    """Backend double that records ``terminate`` calls and lets the test drive
    ``is_alive``.

    The real :class:`_ScriptedPtyBackend` ties ``is_alive`` to ``close``, so it
    cannot model a child that survives SIGTERM and only dies on SIGKILL — the
    exact case the SIGTERM→SIGKILL escalation in ``_terminate_child`` exists for.
    ``alive_sequence`` is popped on each ``is_alive`` call; once exhausted the
    last value sticks.
    """

    def __init__(self, alive_sequence: list[bool]) -> None:
        self._alive_sequence = list(alive_sequence)
        self._last_alive = alive_sequence[-1] if alive_sequence else False
        self.terminate_calls: list[bool] = []

    def is_alive(self) -> bool:
        if self._alive_sequence:
            self._last_alive = self._alive_sequence.pop(0)
        return self._last_alive

    def terminate(self, force: bool = False) -> None:
        self.terminate_calls.append(force)


def _term_config() -> SessionConfig:
    return SessionConfig(
        session_id="term-session",
        command=["does-not-run"],
        rows=24,
        cols=80,
        scrollback_size=100,
    )


@pytest.mark.timeout(15)
class TestTerminateChildEscalation:
    """``_terminate_child`` is the primary fix: a single SIGTERM leaves
    SIGTERM-ignoring children (interactive login shells, TUIs) alive with the
    PTY slave open, stranding the reader's blocking ``os.read``. These drive the
    helper directly with a spy backend so the SIGTERM→SIGKILL decision is
    exercised deterministically on every platform — the real-child integration
    test (``test_stop_terminates_sigterm_ignoring_child``) is Linux/macOS-only.

    ``_await_child_exit`` is patched out so each test isolates the escalation
    branch; the polling loop itself is covered by ``TestAwaitChildExit``.
    """

    async def test_survives_sigterm_escalates_to_sigkill(self) -> None:
        # Child still alive after the graceful signal and after the grace
        # window → must escalate to a forceful kill.
        backend = _TerminationSpyBackend(alive_sequence=[True, True])
        session = TerminalSession(
            _term_config(), backend=cast(PtyBackend, backend),
        )
        loop = asyncio.get_running_loop()

        with patch.object(
            session, "_await_child_exit", new_callable=AsyncMock
        ) as await_exit:
            await session._terminate_child(loop, force=False)

        assert backend.terminate_calls == [False, True]
        await_exit.assert_awaited_once_with(GRACEFUL_TERM_SECONDS)

    async def test_dies_after_sigterm_does_not_escalate(self) -> None:
        # First is_alive (gate before the grace wait) sees the child alive, the
        # grace wait reaps it, the post-wait is_alive sees it gone → no SIGKILL.
        backend = _TerminationSpyBackend(alive_sequence=[True, False])
        session = TerminalSession(
            _term_config(), backend=cast(PtyBackend, backend),
        )
        loop = asyncio.get_running_loop()

        with patch.object(
            session, "_await_child_exit", new_callable=AsyncMock
        ) as await_exit:
            await session._terminate_child(loop, force=False)

        assert backend.terminate_calls == [False]
        await_exit.assert_awaited_once_with(GRACEFUL_TERM_SECONDS)

    async def test_already_dead_after_sigterm_skips_grace_window(self) -> None:
        # A well-behaved child reaped by the time the gate is checked must not
        # pay the grace window at all.
        backend = _TerminationSpyBackend(alive_sequence=[False])
        session = TerminalSession(
            _term_config(), backend=cast(PtyBackend, backend),
        )
        loop = asyncio.get_running_loop()

        with patch.object(
            session, "_await_child_exit", new_callable=AsyncMock
        ) as await_exit:
            await session._terminate_child(loop, force=False)

        assert backend.terminate_calls == [False]
        await_exit.assert_not_awaited()

    async def test_force_kills_immediately_without_grace(self) -> None:
        # force=True (used by stop_all on shutdown) sends the forceful signal
        # straight away and never enters the grace window, even if the child
        # would still report alive.
        backend = _TerminationSpyBackend(alive_sequence=[False])
        session = TerminalSession(
            _term_config(), backend=cast(PtyBackend, backend),
        )
        loop = asyncio.get_running_loop()

        with patch.object(
            session, "_await_child_exit", new_callable=AsyncMock
        ) as await_exit:
            await session._terminate_child(loop, force=True)

        assert backend.terminate_calls == [True]
        await_exit.assert_not_awaited()


@pytest.mark.timeout(15)
class TestAwaitChildExit:
    """``_await_child_exit`` polls the non-blocking ``is_alive`` until the child
    is gone or the timeout elapses, returning whether it died. Tiny real
    timeouts keep these deterministic without stubbing the clock.
    """

    async def test_returns_true_once_child_exits(self) -> None:
        # Alive on the first poll, gone on the second → returns True well within
        # the timeout, so the loop must exit on detection rather than spinning.
        backend = _TerminationSpyBackend(alive_sequence=[True, False])
        session = TerminalSession(
            _term_config(), backend=cast(PtyBackend, backend),
        )

        assert await session._await_child_exit(timeout=5.0) is True

    async def test_returns_false_when_child_never_exits(self) -> None:
        backend = _TerminationSpyBackend(alive_sequence=[True])
        session = TerminalSession(
            _term_config(), backend=cast(PtyBackend, backend),
        )

        assert await session._await_child_exit(timeout=0.02) is False

    async def test_immediate_exit_returns_true(self) -> None:
        backend = _TerminationSpyBackend(alive_sequence=[False])
        session = TerminalSession(
            _term_config(), backend=cast(PtyBackend, backend),
        )

        assert await session._await_child_exit(timeout=5.0) is True


@pytest.mark.timeout(15)
class TestJoinTask:
    """``_join_task`` is the true freeze guarantee: it cancels a background task
    and bounds the wait with ``asyncio.wait`` (not ``wait_for``), so a task
    parked in an uncancellable executor call is abandoned after the timeout
    instead of wedging teardown forever.
    """

    async def test_none_task_is_noop(self) -> None:
        await TerminalSession._join_task(None, timeout=0.01)

    async def test_already_done_task_is_noop(self) -> None:
        async def _immediate() -> None:
            return None

        task = asyncio.create_task(_immediate())
        await task
        assert task.done()

        await TerminalSession._join_task(task, timeout=0.01)
        # A completed task must not be re-cancelled into a CancelledError state.
        assert not task.cancelled()

    async def test_cancels_and_joins_a_live_task(self) -> None:
        async def _sleeper() -> None:
            await asyncio.sleep(60.0)

        task = asyncio.create_task(_sleeper())
        await asyncio.sleep(0)  # let it start running

        await TerminalSession._join_task(task, timeout=2.0)

        assert task.done()
        assert task.cancelled()

    async def test_returns_within_timeout_against_uncancellable_task(self) -> None:
        # The root-cause guarantee: a task that swallows cancellation (modelling
        # the reader parked in an uncancellable os.read) must NOT block the join
        # past its timeout. wait_for would re-await the still-pending task and
        # hang; asyncio.wait returns regardless.
        started = asyncio.Event()
        release = asyncio.Event()

        async def _uncancellable() -> None:
            started.set()
            while not release.is_set():
                try:
                    await asyncio.sleep(60.0)
                except asyncio.CancelledError:
                    pass  # refuse to die, like a stuck executor read

        task = asyncio.create_task(_uncancellable())
        await started.wait()

        loop = asyncio.get_running_loop()
        before = loop.time()
        await TerminalSession._join_task(task, timeout=0.1)
        elapsed = loop.time() - before

        # Join returned despite the task still running — that is the fix.
        assert elapsed < 2.0
        assert not task.done()

        # Cleanup: release the flag, then cancel to break the in-flight sleep —
        # the loop re-checks ``release`` after the CancelledError and exits, so
        # the orphan does not leak past the test.
        release.set()
        task.cancel()
        await asyncio.wait_for(task, timeout=2.0)
