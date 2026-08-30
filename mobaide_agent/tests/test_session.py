from __future__ import annotations

import asyncio
import sys
import threading
from typing import cast
from unittest.mock import AsyncMock, call, patch

import pytest

from mobaide.core.attention_gate import HookKind
from mobaide.core.da_responder import DA1_REPLY
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


async def _drain_event_of_type(
    session: TerminalSession, event_type: str, timeout: float = 2.0
) -> SessionEvent:
    """Pull events off the queue until the first event of ``event_type`` arrives.

    The synthetic ``started`` event (and, for ``"title"``, interleaved
    reader-loop output) share the queue; this drops the others and returns only
    the first matching emission so callers don't have to know the interleaving.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        event = await asyncio.wait_for(
            session.event_queue.get(), timeout=remaining,
        )
        if event.event_type == event_type:
            return event
    raise AssertionError(
        f"{event_type} event was not enqueued within the timeout"
    )


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
            event = await _drain_event_of_type(session, "title", timeout=2.0)

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
            event = await _drain_event_of_type(session, "title", timeout=2.0)

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


class TestRingScrub:
    """``_append_ring`` scrubs terminal *queries* (and DA replies) from the
    replay ring so a reconnect can't make the client emulator re-answer them and
    loop the reply into the input field — the ring-only half of the OSC/DSR fix.
    The live stream (the SessionEvent the reader enqueues) is untouched.
    """

    def test_osc_color_query_scrubbed(self) -> None:
        # OSC 10/11/12 color queries are removed from the ring (belt-and-braces
        # for the stateful OscColorResponder); both BEL and ST terminators.
        session = TerminalSession(_ring_config(ring_buffer_bytes=1024))
        session._append_ring(b"a\x1b]10;?\x07b\x1b]11;?\x1b\\c")
        assert session.get_ring() == b"abc"

    def test_dsr_cpr_queries_scrubbed(self) -> None:
        # DSR (CSI 5 n), CPR (CSI 6 n) and DECDSR (CSI ? 6 n) are scrubbed from
        # the ring — the ring-only neutralization the agent can't answer live.
        session = TerminalSession(_ring_config(ring_buffer_bytes=1024))
        session._append_ring(b"a\x1b[6nb\x1b[5nc\x1b[?6nd")
        assert session.get_ring() == b"abcd"

    def test_osc_compound_color_query_scrubbed(self) -> None:
        # Compound color queries (ESC]10;?;?…) are scrubbed from the ring too —
        # the belt-and-braces regex matches one-or-more ;? specs, matching the
        # stateful responder's compound handling.
        session = TerminalSession(_ring_config(ring_buffer_bytes=1024))
        session._append_ring(b"a\x1b]10;?;?\x07b\x1b]11;?;?;?\x1b\\c")
        assert session.get_ring() == b"abc"

    def test_color_set_and_rendering_survive(self) -> None:
        # A color *set* (byte-identical to a color reply), SGR, and titles are
        # legitimate rendering and must survive replay — only queries are scrubbed.
        session = TerminalSession(_ring_config(ring_buffer_bytes=1024))
        payload = (
            b"hello \x1b[31mred\x1b[0m"
            b"\x1b]0;title\x07"
            b"\x1b]11;rgb:1234/5678/9abc\x07"
        )
        session._append_ring(payload)
        assert session.get_ring() == payload


# Canonical synthetic replies the reader loop should write back to the PTY,
# mirroring the unit-level expectations in the dedicated responder test modules.
_OSC_FG_REPLY = b"\x1b]10;rgb:ffff/ffff/ffff\x07"
_OSC_BG_REPLY = b"\x1b]11;rgb:0000/0000/0000\x07"


def _osc_config() -> SessionConfig:
    return SessionConfig(
        session_id="osc-session",
        command=["does-not-run"],
        rows=24,
        cols=80,
        scrollback_size=100,
    )


async def _wait_for_writes(
    backend: _ScriptedPtyBackend, count: int, timeout: float = 2.0
) -> None:
    """Wait until ``backend`` has recorded at least ``count`` writes.

    The reader loop dispatches the query reply through ``run_in_executor`` as a
    fire-and-forget future, so the write lands on a thread-pool worker slightly
    after the matching output event is enqueued. Polling the recorded-write
    count (rather than sleeping a fixed interval) keeps the assertion
    deterministic without coupling to executor latency.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while len(backend._writes) < count:
        if asyncio.get_event_loop().time() >= deadline:
            raise AssertionError(
                f"backend recorded {len(backend._writes)} writes, "
                f"expected at least {count} within {timeout}s"
            )
        await asyncio.sleep(0.01)


@pytest.mark.timeout(15)
class TestReaderLoopQueryInterception:
    """End-to-end reader-loop wiring for terminal-query interception.

    Drives :meth:`TerminalSession._reader_loop` through ``_ScriptedPtyBackend``
    to verify the integration the isolated responder/ring tests cannot: that a
    color query is stripped from the *live* output event AND a synthetic reply
    is written back to the PTY master in the reader loop's combined write.
    """

    async def test_osc_color_query_stripped_from_live_event_and_answered(
        self,
    ) -> None:
        backend = _ScriptedPtyBackend(
            [b"banner\x1b]10;?\x07\x1b]11;?\x07more"]
        )
        session = TerminalSession(
            _osc_config(), backend=cast(PtyBackend, backend),
        )
        await session.start()
        try:
            event = await _drain_event_of_type(session, "output", timeout=2.0)

            # The live event the reader enqueues has the queries removed.
            assert event.event_type == "output"
            assert event.data == b"bannermore"

            # And the agent answered both queries back to the PTY master so the
            # child gets a color report without the client emulator ever seeing
            # the query. Order is fg-then-bg, matching the chunk's query order.
            await _wait_for_writes(backend, 1, timeout=2.0)
            assert b"".join(backend._writes) == _OSC_FG_REPLY + _OSC_BG_REPLY
        finally:
            await session.stop()

    async def test_combined_da_then_osc_reply_in_reader_loop(self) -> None:
        # A chunk carrying both a DA query and an OSC color query is answered in
        # one combined write. Replies are batched DA-then-OSC (``_da
        # .pending_replies() + _osc_color.pending_replies()``) regardless of
        # which type the child emitted first; here the DA query leads, so batch
        # order coincides with emission order.
        backend = _ScriptedPtyBackend([b"x\x1b[c\x1b]11;?\x07y"])
        session = TerminalSession(
            _osc_config(), backend=cast(PtyBackend, backend),
        )
        await session.start()
        try:
            event = await _drain_event_of_type(session, "output", timeout=2.0)
            assert event.data == b"xy"

            await _wait_for_writes(backend, 1, timeout=2.0)
            assert b"".join(backend._writes) == DA1_REPLY + _OSC_BG_REPLY
        finally:
            await session.stop()

    async def test_plain_output_writes_no_reply(self) -> None:
        # Output with no embedded query passes through verbatim and triggers no
        # write back to the PTY — guards against an over-eager interceptor.
        backend = _ScriptedPtyBackend([b"hello \x1b[31mworld\x1b[0m"])
        session = TerminalSession(
            _osc_config(), backend=cast(PtyBackend, backend),
        )
        await session.start()
        try:
            event = await _drain_event_of_type(session, "output", timeout=2.0)
            assert event.data == b"hello \x1b[31mworld\x1b[0m"

            # Let any (erroneous) reply write reach the executor, then assert none.
            await asyncio.sleep(0.1)
            assert backend._writes == []
        finally:
            await session.stop()

    async def test_split_cpr_across_reads_is_kept_out_of_ring(self) -> None:
        # A CPR/DSR query (``ESC[6n``) split across two PTY reads — ``ESC[`` in
        # the first, ``6n`` in the second. The ring scrubber is stateless, but
        # the upstream DaResponder carries the unfinished trailing CSI into the
        # next read, so ``_append_ring`` only ever sees the whole ``ESC[6n`` and
        # scrubs it. Without that upstream carry both fragments would reach the
        # ring and replay as a complete live query on reconnect.
        backend = _ScriptedPtyBackend([b"foo\x1b[", b"6n bar"])
        session = TerminalSession(
            _osc_config(), backend=cast(PtyBackend, backend),
        )
        await session.start()
        try:
            # Drain both reads' output events so the ring is fully populated.
            first = await _drain_event_of_type(session, "output", timeout=2.0)
            assert first.data == b"foo"
            second = await _drain_event_of_type(session, "output", timeout=2.0)
            # The live stream keeps the reassembled CPR query intact (an attached
            # client answers it inside the child's window); only the ring is scrubbed.
            assert second.data == b"\x1b[6n bar"

            # The reassembled CPR query never made it into the replay ring.
            assert session.get_ring() == b"foo bar"
            assert b"\x1b[6n" not in session.get_ring()

            # DSR/CPR is ring-only: the agent never writes a synthetic reply.
            await asyncio.sleep(0.1)
            assert backend._writes == []
        finally:
            await session.stop()


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


def _width_config(
    cols: int, ring_buffer_bytes: int = 1024, rows: int = 24
) -> SessionConfig:
    return SessionConfig(
        session_id="width-session",
        command=["does-not-run"],
        rows=rows,
        cols=cols,
        scrollback_size=100,
        ring_buffer_bytes=ring_buffer_bytes,
    )


def _width_session(
    cols: int = 80, ring_buffer_bytes: int = 1024, rows: int = 24
) -> TerminalSession:
    """A never-started session that can still take a ``resize()``.

    Unlike the other ring tests this one needs a backend double: ``resize()``
    reaches :meth:`PtyBackend.resize`, which asserts a spawned PTY. The
    scripted double's ``read()`` is never called because no reader loop runs.
    """
    session = TerminalSession(
        _width_config(cols, ring_buffer_bytes, rows),
        backend=cast(PtyBackend, _ScriptedPtyBackend([])),
    )
    session._state = SessionState.RUNNING
    return session


def _on_alt_screen(session: TerminalSession) -> TerminalSession:
    """Put the child on the alternate screen, where the cut applies.

    Only there does a stale-width replay actually break: the program paints
    absolute positions. On the normal screen the replay is line-oriented text
    the client re-wraps, so history is kept — covered separately below.
    """
    session._track_modes(b"\x1b[?1049h")
    return session


def _replay(session: TerminalSession) -> bytes:
    return b"".join(session.iter_ring_chunks(chunk_size=64))


def _replay_body(session: TerminalSession) -> bytes:
    """The replay minus the mode-restoring prefix a cut span carries.

    The prefix is asserted on its own in ``test_a_cut_replay_restores_the_modes``;
    everywhere else the question is which *output* survived the cut.
    """
    return _replay(session).removeprefix(session._span.restore_prefix())


class TestRingWidthMark:
    """Every replay must be width-homogeneous.

    Ring bytes carry no width of their own and are re-rendered at whatever grid
    the client has now, so replaying output laid out at a different column count
    desynchronizes a full-screen TUI's cursor arithmetic for the rest of the
    span — and nothing repairs it, because a same-size ``TIOCSWINSZ`` raises no
    ``SIGWINCH``. ``_ring_width_start`` is the cut: ``iter_ring_chunks`` starts
    there, so history older than the last column change is deliberately dropped.
    """

    async def test_column_change_drops_the_old_width_from_the_replay(self) -> None:
        session = _on_alt_screen(_width_session(cols=80))
        session._append_ring(b"eighty-column prologue")

        await session.resize(24, 120)
        session._append_ring(b"one-twenty output")

        assert _replay_body(session) == b"one-twenty output"
        # Only the replay window moved — the bytes themselves are untouched.
        assert session.get_ring() == b"eighty-column prologueone-twenty output"

    async def test_rows_only_change_keeps_the_whole_replay(self) -> None:
        # Rows do not affect wrapping, so a rows-only resize must not throw
        # perfectly renderable history away.
        session = _width_session(cols=80)
        session._append_ring(b"eighty-column output")

        await session.resize(60, 80)
        session._append_ring(b" and more")

        assert _replay(session) == b"eighty-column output and more"

    async def test_resize_to_the_spawn_width_is_not_a_width_change(self) -> None:
        # The mark is seeded from ``config.cols``, not from "no width yet".
        # Clients assert their size on first connect; at the width the PTY was
        # spawned with that must be inert, or the prologue is lost for nothing.
        session = _width_session(cols=120)
        session._append_ring(b"spawned at one-twenty")

        await session.resize(40, 120)

        assert _replay(session) == b"spawned at one-twenty"

    async def test_re_asserting_the_new_width_does_not_cut_again(self) -> None:
        # Clients re-assert their cached size on every reconnect. Once the mark
        # has moved to a width, repeating that width has to be inert — otherwise
        # each reconnect would truncate the replay to whatever arrived since the
        # previous one.
        session = _on_alt_screen(_width_session(cols=80))
        session._append_ring(b"eighty-column prologue")

        await session.resize(24, 120)
        session._append_ring(b"one-twenty output")
        await session.resize(24, 120)

        assert _replay_body(session) == b"one-twenty output"

    async def test_head_trim_moves_the_mark_with_the_bytes(self) -> None:
        # The mark is an absolute offset into a ring whose head is trimmed on
        # overflow, so it has to be decremented by the same amount or it drifts
        # forward over bytes it was meant to keep.
        session = _on_alt_screen(_width_session(cols=80, ring_buffer_bytes=8))
        session._append_ring(b"AAAA")

        await session.resize(24, 120)
        session._append_ring(b"BBBB")
        session._append_ring(b"CC")

        # 10 bytes pushed against an 8-byte cap → two head bytes of the
        # 80-column run are trimmed, and the mark follows them from 4 to 2.
        assert session.get_ring() == b"AABBBBCC"
        assert _replay_body(session) == b"BBBBCC"
        assert session._ring_width_start == 2

    async def test_normal_screen_history_survives_a_width_change(self) -> None:
        # Off the alternate screen the replay is line-oriented text, which the
        # client's emulator re-wraps at whatever grid it has. Cutting it would
        # delete usable scrollback — and since every client clears scrollback
        # before a replay, the user would come back to an erased terminal.
        session = _width_session(cols=80)
        session._append_ring(b"shell history laid out at eighty\n")

        await session.resize(24, 55)
        session._append_ring(b"and what came after")

        assert _replay(session) == b"shell history laid out at eighty\nand what came after"

    async def test_a_dead_session_keeps_its_last_output(self) -> None:
        # The master fd outlives the child until the liveness sweep, so a resize
        # can still land on a crashed session. It will never repaint, so cutting
        # its history just blanks whatever it died showing.
        session = _on_alt_screen(_width_session(cols=80))
        session._append_ring(b"what it died showing")
        session._state = SessionState.CRASHED

        await session.resize(24, 120)

        assert _replay_body(session) == b"what it died showing"

    async def test_a_cut_replay_restores_the_modes(self) -> None:
        # The sequences that set these are behind the cut, and a client's replay
        # wipe clears the display, not the emulator's mode state. Without the
        # prefix the retained paint is parsed on the primary screen, with the
        # wrong scrolling and the wrong input encoding.
        session = _width_session(cols=80)
        session._track_modes(b"\x1b[?1049h\x1b[?2004h\x1b[?1006h\x1b[?25l")
        session._append_ring(b"paint at eighty")

        await session.resize(24, 120)
        session._append_ring(b"repaint at one-twenty")

        replay = _replay(session)
        assert replay.endswith(b"repaint at one-twenty")
        prefix = replay.removesuffix(b"repaint at one-twenty")
        assert b"\x1b[?1049h" in prefix
        assert b"\x1b[?2004h" in prefix
        assert b"\x1b[?1006h" in prefix
        assert b"\x1b[?25l" in prefix

    async def test_an_uncut_replay_carries_no_prefix(self) -> None:
        # Nothing was dropped, so the modes are still established by the bytes
        # themselves; prepending them again would be redundant noise.
        session = _width_session(cols=80)
        session._track_modes(b"\x1b[?1049h")
        session._append_ring(b"only ever one width")

        assert _replay(session) == b"only ever one width"

    async def test_batched_private_modes_are_recognised(self) -> None:
        # A TUI may set several private modes in one sequence. Matching a single
        # number followed by h/l would miss the alternate-screen transition
        # entirely, and with it every cut the transition is supposed to gate.
        session = _width_session(cols=80)

        session._track_modes(b"\x1b[?1049;25h")
        assert session._alt_screen
        assert session._live.modes[25] is True

        session._track_modes(b"\x1b[?1049;25l")
        assert not session._alt_screen

    async def test_a_private_mode_split_across_reads_is_tracked(self) -> None:
        # 4 KiB reads cut wherever they cut; the tracker is stateful for the
        # same reason the DA and OSC processors on these bytes are.
        session = _width_session(cols=80)

        session._track_modes(b"trailing output\x1b[?104")
        session._track_modes(b"9;2004h")

        assert session._alt_screen
        assert session._live.modes[2004] is True

    async def test_the_prefix_survives_an_overflow_that_clamps_the_mark(self) -> None:
        # The trim can drive the mark to zero while the retained bytes still
        # begin after the transitions that established the modes. Gating the
        # prefix on a non-zero mark would drop it exactly then, and a fresh
        # emulator would parse the repaint on the primary screen.
        session = _on_alt_screen(_width_session(cols=80, ring_buffer_bytes=32))
        session._track_modes(b"\x1b[?2004h")
        session._append_ring(b"old paint")

        await session.resize(24, 55)
        session._append_ring(b"X" * 60)

        assert session._ring_width_start == 0
        replay = _replay(session)
        assert b"\x1b[?1049h" in replay
        assert b"\x1b[?2004h" in replay

    async def test_modes_trimmed_out_of_the_span_are_folded_into_the_prefix(self) -> None:
        # A long-running session loses its own mode transitions off the head as
        # the ring rolls. The span has to carry them even when no cut happened,
        # or the replay is parsed without them.
        session = _width_session(cols=80, ring_buffer_bytes=16)
        session._track_modes(b"\x1b[?1049h")
        session._append_ring(b"\x1b[?1049h")

        session._append_ring(b"Z" * 24)

        assert b"\x1b[?1049h" in _replay(session)

    async def test_application_cursor_mode_is_restored(self) -> None:
        # DECCKM decides whether arrows arrive as SS3 or CSI. A pager that
        # enabled it before the cut would otherwise receive the wrong ones.
        session = _on_alt_screen(_width_session(cols=80))
        session._track_modes(b"\x1b[?1h")
        session._append_ring(b"paint")

        await session.resize(24, 55)
        session._append_ring(b"repaint")

        assert b"\x1b[?1h" in session._span.restore_prefix()

    async def test_the_screen_aliases_are_one_state(self) -> None:
        # 47, 1047 and 1049 select the same buffer, so a program may enter with
        # one and leave with another. Tracked apart, the exit would be missed:
        # a later resize would cut primary-screen history, and the prefix would
        # put a fresh client back on the alternate buffer.
        session = _width_session(cols=80)

        session._track_modes(b"\x1b[?1049h")
        assert session._alt_screen
        session._track_modes(b"\x1b[?47l")
        assert not session._alt_screen

        session._append_ring(b"primary-screen history")
        await session.resize(24, 55)
        session._append_ring(b" continues")

        replay = _replay(session)
        assert b"primary-screen history" in replay
        assert b"1049h" not in replay

    async def test_a_rows_only_change_cuts_on_the_alternate_screen(self) -> None:
        # There a program paints absolute positions, so rows drive cursor
        # addresses and scrolling regions exactly as columns drive wrapping —
        # and rows change routinely right after create, where every client's
        # row estimate still includes chrome the terminal subtracts.
        session = _on_alt_screen(_width_session(cols=80, rows=54))
        session._append_ring(b"painted for fifty-four rows")

        await session.resize(40, 80)
        session._append_ring(b"painted for forty rows")

        assert _replay_body(session) == b"painted for forty rows"

    async def test_a_child_that_dies_before_repainting_keeps_its_screen(self) -> None:
        # The state check cannot cover this on its own: death can follow the
        # resize as easily as precede it, and a session that dies in between
        # would replay nothing but a mode prefix.
        session = _on_alt_screen(_width_session(cols=80))
        session._append_ring(b"what it died showing")

        await session.resize(24, 55)
        session._state = SessionState.CRASHED

        assert b"what it died showing" in _replay(session)

    async def test_the_landing_chunk_cannot_rewrite_the_span_state(self) -> None:
        # The reader tracks a whole read before appending it, so a chunk that
        # repaints and then leaves the alternate screen would otherwise seed the
        # span with the state at its *end* — and the retained repaint, produced
        # on the alternate screen, would be parsed on the primary one.
        session = _on_alt_screen(_width_session(cols=80))
        session._append_ring(b"old paint")

        await session.resize(24, 55)
        landing = b"fresh paint\x1b[?1049l"
        session._track_modes(landing)
        session._append_ring(landing)

        assert b"\x1b[?1049h" in _replay(session)

    async def test_a_grid_excursion_that_returns_cancels_its_cut(self) -> None:
        # Two resizes before any output, ending back where the span started —
        # a transient layout, or two clients asserting in turn. Comparing
        # against the last resize instead of the span's own grid would leave
        # the cut armed and discard history the span never stopped matching.
        session = _on_alt_screen(_width_session(cols=80))
        session._append_ring(b"eighty-column history")

        await session.resize(24, 120)
        await session.resize(24, 80)

        assert session._pending_cut is None
        session._append_ring(b" continues")
        assert b"eighty-column history" in _replay(session)

    async def test_a_grid_change_that_settles_elsewhere_still_cuts(self) -> None:
        session = _on_alt_screen(_width_session(cols=80))
        session._append_ring(b"eighty-column history")

        await session.resize(24, 120)
        await session.resize(24, 132)
        session._append_ring(b"one-thirty-two output")

        assert _replay_body(session) == b"one-thirty-two output"

    async def test_a_long_batched_sequence_survives_a_late_split(self) -> None:
        # Carrying a fixed-size suffix drops the opening `ESC[?` of a batched
        # DECSET longer than the window, losing every mode in it — including
        # the alternate-screen entry the cut depends on.
        session = _width_session(cols=80)
        sequence = b"\x1b[?1;25;2004;1000;1002;1003;1006;1015;1049h"

        session._track_modes(b"padding" + sequence[:38])
        session._track_modes(sequence[38:])

        assert session._alt_screen
        assert session._live.modes[2004] is True
        assert session._live.modes[1] is True

    async def test_an_unterminated_sequence_does_not_grow_the_carry(self) -> None:
        # The cap is what keeps a stream of `ESC[?` with no terminator from
        # growing the carried tail without limit.
        session = _width_session(cols=80)

        session._track_modes(b"\x1b[?" + b"9" * 400)

        assert len(session._mode_tail) <= 2

    async def test_a_chunk_read_before_the_resize_does_not_land_the_cut(self) -> None:
        # The reader hands a chunk to the executor before the resize arrives, so
        # the bytes it returns are old-grid output. Landing the cut on it would
        # start the span grid-mixed — exactly what the cut exists to prevent.
        session = _on_alt_screen(_width_session(cols=80))
        session._read_seq = 5
        session._append_ring(b"history", read_seq=5)

        await session.resize(24, 55)
        session._append_ring(b"stale old-grid tail", read_seq=5)
        session._append_ring(b"fresh repaint", read_seq=6)

        assert _replay_body(session) == b"fresh repaint"

    async def test_a_partial_span_on_the_primary_screen_says_so(self) -> None:
        # A client's emulator is retained across reconnects and the replay wipe
        # clears the display, not the selected buffer. If the trim ate the exit
        # sequence, an emulator left on the alternate buffer would parse this
        # primary-screen history there.
        session = _width_session(cols=80, ring_buffer_bytes=24)
        session._track_modes(b"\x1b[?1049h")
        session._append_ring(b"A" * 20, read_seq=1)
        session._track_modes(b"\x1b[?1049l")
        session._append_ring(b"\x1b[?1049l" + b"B" * 18, read_seq=2)

        assert not session._span.alt_screen
        assert _replay(session).startswith(b"\x1b[?1049l")

    async def test_a_pristine_span_carries_no_synthesised_bytes(self) -> None:
        # A span that still starts at the child's first byte establishes every
        # mode it needs on its own; prefixing it would put bytes on the wire the
        # session never produced.
        session = _width_session(cols=80)
        session._append_ring(b"untouched", read_seq=1)

        assert _replay(session) == b"untouched"

    async def test_a_mode_bisected_by_the_trim_still_reaches_the_span(self) -> None:
        # The trim boundary is byte-granular, so it can split a sequence across
        # the dropped and retained sides — neither half establishes the mode,
        # and without a lookahead it would be lost entirely.
        session = _width_session(cols=80, ring_buffer_bytes=24)
        session._append_ring(b"A" * 20 + b"\x1b[?104", read_seq=1)
        session._append_ring(b"9h" + b"B" * 18, read_seq=2)

        assert session._span.alt_screen

    async def test_head_trim_clamps_the_mark_at_zero(self) -> None:
        # An overflow larger than the mark would drive it negative, and a
        # negative slice start silently reads from the *tail* — the replay would
        # lose most of the current width's output rather than none of it.
        session = _on_alt_screen(_width_session(cols=80, ring_buffer_bytes=8))
        session._append_ring(b"AAAA")

        await session.resize(24, 120)
        session._append_ring(b"B" * 12)

        assert session.get_ring() == b"B" * 8
        assert _replay_body(session) == b"B" * 8


def _attention_config(
    *,
    session_id: str = "attn-session",
    startup_stall_seconds: float | None = None,
    attention_quiet_seconds: float | None = None,
) -> SessionConfig:
    return SessionConfig(
        session_id=session_id,
        command=["does-not-run"],
        rows=24,
        cols=80,
        scrollback_size=100,
        startup_stall_seconds=startup_stall_seconds,
        attention_quiet_seconds=attention_quiet_seconds,
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


class TestSetAttentionEqualityGuard:
    """The ``attention`` event is a one-shot notification trigger — the fanout
    turns every emission into an alert on every device — so re-raising the
    reason already pending must refresh the stored state without re-emitting.
    """

    async def test_same_reason_twice_emits_once(self) -> None:
        session = TerminalSession(_attention_config())
        session.set_attention("turn_finished")
        session.set_attention("turn_finished")
        assert session.to_info().attention == "turn_finished"
        assert [e.attention for e in _drain_attention_events(session)] == [
            "turn_finished",
        ]

    async def test_a_different_reason_still_emits(self) -> None:
        session = TerminalSession(_attention_config())
        session.set_attention("turn_finished")
        session.set_attention("awaiting_permission")
        assert session.to_info().attention == "awaiting_permission"
        assert [e.attention for e in _drain_attention_events(session)] == [
            "turn_finished", "awaiting_permission",
        ]

    async def test_the_same_reason_after_a_clear_emits_again(self) -> None:
        # The guard is about the reason currently pending, not a permanent
        # latch: once the badge is gone the same reason is news again.
        session = TerminalSession(_attention_config())
        session.set_attention("awaiting_permission")
        session.clear_attention()
        session.set_attention("awaiting_permission")
        assert [e.attention for e in _drain_attention_events(session)] == [
            "awaiting_permission", None, "awaiting_permission",
        ]

    async def test_a_changed_exit_reason_emits_again(self) -> None:
        # ``SessionEnd`` fires with reason ``clear`` for ``/clear`` while the
        # PTY keeps running, so ``ended`` is left publicly set and a genuine
        # end arrives as a repeat. Refreshing ``exit_reason`` silently would
        # cost the user the only "Session ended" push they get.
        session = TerminalSession(_attention_config())
        session.set_attention("ended", exit_reason="clear")
        session.set_attention("ended", exit_reason="logout")
        assert session.to_info().exit_reason == "logout"
        assert [e.attention for e in _drain_attention_events(session)] == [
            "ended", "ended",
        ]

    async def test_an_identical_end_repeat_still_emits_once(self) -> None:
        # Only a *changed* reason is news; the same end re-raised is the
        # one-shot trigger the guard exists to swallow.
        session = TerminalSession(_attention_config())
        session.set_attention("ended", exit_reason="clear")
        session.set_attention("ended", exit_reason="clear")
        assert session.to_info().exit_reason == "clear"
        assert [e.attention for e in _drain_attention_events(session)] == ["ended"]


def _running_hook_session(
    attention_quiet_seconds: float, backend: _ScriptedPtyBackend | None = None,
) -> TerminalSession:
    """A session the gate sees as live, without spawning a PTY.

    ``start()`` would spawn a real child; the gate only reads the state, so it
    is set in place as in ``TestStartupStallWatchdog``.
    """
    session = TerminalSession(
        _attention_config(
            session_id="hook-session",
            startup_stall_seconds=0.0,
            attention_quiet_seconds=attention_quiet_seconds,
        ),
        backend=cast(PtyBackend, backend or _ScriptedPtyBackend([])),
    )
    session._state = SessionState.RUNNING
    return session


@pytest.mark.timeout(15)
class TestClaudeHookAttention:
    """``on_claude_hook`` is what the ``/internal/hook`` endpoint calls. The
    quiet window is configured to zero here so a decision is observable without
    scheduling; the gate's own timing and suppression rules are covered in
    ``test_attention_gate.py``.
    """

    async def test_stop_hook_raises_turn_finished(self) -> None:
        session = _running_hook_session(0.0)
        session.on_claude_hook(HookKind.STOP, {"prompt_id": "p1"})
        assert session.to_info().attention == "turn_finished"
        assert [e.attention for e in _drain_attention_events(session)] == [
            "turn_finished",
        ]

    async def test_suppressed_stop_leaves_the_public_attention_unset(self) -> None:
        # The public field is what a full-state snapshot carries, and clients
        # re-post a notification for any session that arrives carrying one — so
        # a suppressed turn must not write it, only skip the event.
        session = _running_hook_session(0.0)
        session.on_claude_hook(
            HookKind.STOP,
            {"prompt_id": "p1", "background_tasks": [{"status": "running"}]},
        )
        assert session.to_info().attention is None
        assert _drain_attention_events(session) == []

    async def test_permission_hook_still_alerts_immediately(self) -> None:
        session = _running_hook_session(0.0)
        session.on_claude_hook(HookKind.PERMISSION, {})
        assert session.to_info().attention == "awaiting_permission"

    async def test_session_end_hook_records_the_exit_reason(self) -> None:
        session = _running_hook_session(0.0)
        session.on_claude_hook(HookKind.END, {"reason": "clear"})
        info = session.to_info()
        assert info.attention == "ended"
        assert info.exit_reason == "clear"

    async def test_user_input_does_not_reopen_the_turn(self) -> None:
        # ``write`` is the sink for every input frame, and both apps push
        # terminal *scrolling* through it as arrow/wheel escape sequences. A
        # scroll gesture discarding the dedupe key would re-open the storm
        # while background agents are still draining, so the badge clears but
        # the turn stays alerted.
        backend = _ScriptedPtyBackend([])
        session = _running_hook_session(0.0, backend)
        session.on_claude_hook(HookKind.STOP, {"prompt_id": "p1"})

        await session.write(b"\x1b[A")
        session.on_claude_hook(HookKind.STOP, {"prompt_id": "p1"})

        assert session.to_info().attention is None
        assert [e.attention for e in _drain_attention_events(session)] == [
            "turn_finished", None,
        ]
        assert backend._writes == [b"\x1b[A"]

    async def test_user_input_cancels_a_pending_quiet_window(self) -> None:
        # The other half of the keystroke contract: a queued "your move" must
        # not fire at someone who is demonstrably already at the terminal.
        backend = _ScriptedPtyBackend([])
        session = _running_hook_session(60.0, backend)
        session.on_claude_hook(HookKind.STOP, {"prompt_id": "p1"})
        window = session._attention_gate._timer
        assert window is not None

        await session.write(b"\x1b[B")

        assert window.cancelled()
        assert session._attention_gate._timer is None
        assert _drain_attention_events(session) == []

    async def test_prompt_submit_reopens_the_turn(self) -> None:
        # ``UserPromptSubmit`` is the one unambiguous new-turn signal. The same
        # ``prompt_id`` alerting twice is what proves the dedupe key was
        # dropped here and not by the PTY write ahead of it.
        backend = _ScriptedPtyBackend([])
        session = _running_hook_session(0.0, backend)
        session.on_claude_hook(HookKind.STOP, {"prompt_id": "p1"})

        await session.write(b"go on\n")
        session.on_claude_hook(HookKind.PROMPT_SUBMIT, {})
        session.on_claude_hook(HookKind.STOP, {"prompt_id": "p1"})

        assert session.to_info().attention == "turn_finished"
        assert [e.attention for e in _drain_attention_events(session)] == [
            "turn_finished", None, "turn_finished",
        ]
        assert backend._writes == [b"go on\n"]

    async def test_answering_on_the_host_does_not_mute_later_turns(self) -> None:
        # The scenario is a user who replied to Claude at the host terminal
        # instead of from the app — or an unattended Auto Scan session — so
        # there is deliberately no ``write`` here and the PTY-write clear never
        # runs. ``UserPromptSubmit`` has to drop the badge itself: otherwise
        # ``turn_finished`` stays latched and the gate's "already on screen"
        # guard mutes every turn after the first, permanently and silently.
        session = _running_hook_session(0.0)
        session.on_claude_hook(HookKind.STOP, {"prompt_id": "p1"})

        session.on_claude_hook(HookKind.PROMPT_SUBMIT, {"prompt_id": "p2"})
        session.on_claude_hook(HookKind.STOP, {"prompt_id": "p2"})

        assert session.to_info().attention == "turn_finished"
        assert [e.attention for e in _drain_attention_events(session)] == [
            "turn_finished", None, "turn_finished",
        ]

    async def test_ended_badge_does_not_mute_later_turns(self) -> None:
        # ``ended`` is the gate's other blocking reason, and ``/clear`` reaches
        # it with the PTY still running, so the session keeps taking turns
        # underneath a latched badge. Again no ``write``: the prompt submission
        # is the only thing that can clear it.
        session = _running_hook_session(0.0)
        session.on_claude_hook(HookKind.END, {"reason": "clear"})

        session.on_claude_hook(HookKind.PROMPT_SUBMIT, {"prompt_id": "p1"})
        session.on_claude_hook(HookKind.STOP, {"prompt_id": "p1"})

        assert session.to_info().attention == "turn_finished"
        assert [e.attention for e in _drain_attention_events(session)] == [
            "ended", None, "turn_finished",
        ]

    async def test_prompt_submit_also_drops_a_pending_quiet_window(self) -> None:
        session = _running_hook_session(60.0)
        session.on_claude_hook(HookKind.STOP, {"prompt_id": "p1"})
        window = session._attention_gate._timer
        assert window is not None

        session.on_claude_hook(HookKind.PROMPT_SUBMIT, {})

        assert window.cancelled()
        assert _drain_attention_events(session) == []

    async def test_reader_auto_clear_does_not_reopen_the_turn(self) -> None:
        # The reader loop calls ``clear_attention`` on its own when output
        # resumes after an ``awaiting_os_permission`` stall. That is the host
        # waking up, not the user acting, so the turn must stay deduped —
        # otherwise the storm returns through the back door.
        session = _running_hook_session(0.0)
        session.on_claude_hook(HookKind.STOP, {"prompt_id": "p1"})

        session.clear_attention()
        session.on_claude_hook(HookKind.STOP, {"prompt_id": "p1"})

        assert session.to_info().attention is None
        assert [e.attention for e in _drain_attention_events(session)] == [
            "turn_finished", None,
        ]

    async def test_stop_cancels_a_pending_quiet_window(self) -> None:
        # A window surviving teardown would alert for a session already gone.
        # The handle is read back because a cancelled ``TimerHandle`` is the
        # only trace an unfired window leaves.
        session = _running_hook_session(60.0)
        session.on_claude_hook(HookKind.STOP, {"prompt_id": "p1"})
        window = session._attention_gate._timer
        assert window is not None

        await session.stop(force=True)

        assert window.cancelled()
        assert _drain_attention_events(session) == []


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
