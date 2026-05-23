from __future__ import annotations

import asyncio
import threading
from typing import cast

import pytest

from mobaide.core.models import SessionConfig, SessionEvent, SessionState
from mobaide.core.pty_backend import PtyBackend
from mobaide.core.session import TerminalSession
from tests.conftest import IS_WINDOWS


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
