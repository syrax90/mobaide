from __future__ import annotations

import asyncio
import sys
import threading
from collections.abc import Callable
from typing import Any, cast
from unittest.mock import AsyncMock, call, patch

import pytest

from mobaide.core.attention_dedupe import SharedTurnDedupe, real_cwd
from mobaide.core.attention_gate import HookKind
from mobaide.core.da_responder import DA1_REPLY
from mobaide.core.models import SessionConfig, SessionEvent, SessionKind, SessionState
from mobaide.core.pty_backend import PtyBackend
from mobaide.core.session import (
    EXECUTE_CLEAR_DELAY_SECONDS,
    GRACEFUL_TERM_SECONDS,
    PASTE_PREFIX_DELAY_SECONDS,
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
        # The scripted child obeys SIGTERM, so ``stop()`` skips the grace wait.
        self._closed = True
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

    def test_chunks_concatenate_to_original_ring(self) -> None:
        session = TerminalSession(_ring_config(ring_buffer_bytes=1024))
        payload = bytes(range(256)) + bytes(range(256))
        session._append_ring(payload)
        chunks = list(session.iter_ring_chunks(chunk_size=64))
        assert b"".join(chunks) == payload
        assert b"".join(chunks) == session.get_ring()

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

    def test_zero_chunk_size_raises_value_error(self) -> None:
        session = TerminalSession(_ring_config(ring_buffer_bytes=64))
        session._append_ring(b"abc")
        with pytest.raises(ValueError):
            list(session.iter_ring_chunks(chunk_size=0))

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

    async def test_set_attention_enqueues_event_with_reason(self) -> None:
        session = TerminalSession(_attention_config())
        session.set_attention("turn_finished")
        events = _drain_attention_events(session)
        assert len(events) == 1
        assert events[0].event_type == "attention"
        assert events[0].session_id == "attn-session"
        assert events[0].attention == "turn_finished"

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


def _drain_event_types(session: TerminalSession) -> list[str]:
    """Pull every queued event without blocking and return their types in order."""
    types: list[str] = []
    while not session.event_queue.empty():
        types.append(session.event_queue.get_nowait().event_type)
    return types


class _ManualWindow:
    """A quiet window armed through ``loop.call_later`` that fires on demand."""

    def __init__(self, callback: Callable[[], None]) -> None:
        self.callback = callback
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


@pytest.fixture
async def manual_windows(monkeypatch) -> list[_ManualWindow]:
    windows: list[_ManualWindow] = []

    def call_later(_delay: float, callback: Callable[[], None]) -> _ManualWindow:
        window = _ManualWindow(callback)
        windows.append(window)
        return window

    monkeypatch.setattr(asyncio.get_running_loop(), "call_later", call_later)
    return windows


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

    async def test_user_input_mutes_a_pending_quiet_window(
        self, manual_windows,
    ) -> None:
        # The other half of the keystroke contract: a queued "your move" must
        # not fire at someone who is demonstrably already at the terminal. The
        # window still runs out, so the row stops showing "working" on time
        # even while the user scrolls.
        backend = _ScriptedPtyBackend([])
        session = _running_hook_session(60.0, backend)
        session.on_claude_hook(HookKind.PROMPT_SUBMIT, {"prompt_id": "p1"})
        session.on_claude_hook(HookKind.STOP, {"prompt_id": "p1"})
        _drain_event_types(session)

        await session.write(b"\x1b[B")
        (window,) = manual_windows
        assert not window.cancelled
        window.callback()

        info = session.to_info()
        assert info.activity == "idle"
        assert info.attention is None
        assert _drain_event_types(session) == ["activity"]

    @pytest.mark.parametrize("report", [b"\x1b[I", b"\x1b[O"], ids=["focus-in", "focus-out"])
    async def test_a_focus_report_mutes_a_pending_quiet_window(self, report) -> None:
        # Only program-status alerts survive a client's focus report; the Stop
        # window keeps the keystroke contract: muted, yet still running out
        # into ``idle``.
        backend = _ScriptedPtyBackend([])
        session = _running_hook_session(60.0, backend)
        session.on_claude_hook(HookKind.STOP, {"prompt_id": "p1"})
        window = session._attention_gate._timer
        assert window is not None

        await session.write(report)

        assert not window.cancelled()
        assert session._attention_gate._timer is window
        window.cancel()
        session._attention_gate._fire()
        assert session.to_info().activity == "idle"
        assert _drain_attention_events(session) == []
        assert backend._writes == [report]

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


_BLOCKED_REPORT = b"\x1b]7501;state=blocked:kind=permission:app=codex\x07"
_DONE_REPORT = b"\x1b]7501;state=done:app=codex\x1b\\"
_ERROR_REPORT = b"\x1b]7501;state=error\x07"
_WORKING_REPORT = b"\x1b]7501;state=working:app=codex\x07"
_CLEAR_REPORT = b"\x1b]7501;state=clear:app=codex\x07"
_STATUS_QUERY = b"\x1b]7501;?\x1b\\"
_STATUS_REPLY = (
    b"\x1b]7501;?:states=idle,working,done,blocked,error:kinds=permission,question,auth\x1b\\"
)
_CUSTOM_KIND = "custom_0123456789ab"
# Mobaide's managed Codex launch sets the reasoning effort with ``-c``.
_MANAGED_CODEX = ["codex", "-c", "model_reasoning_effort=high"]


class _MutingDedupe:
    """A shared-turn dedupe that mutes every alert it is asked about and
    remembers each question."""

    def __init__(self) -> None:
        self.asked: list[dict[str, Any]] = []

    def admit(self, **claim: Any) -> bool:
        self.asked.append(claim)
        return False


def _status_config(
    kind: SessionKind | str,
    *,
    attention_quiet_seconds: float = 0.0,
    cwd: str | None = None,
    command: list[str] | None = None,
) -> SessionConfig:
    return SessionConfig(
        session_id="status-session",
        command=command or ["does-not-run"],
        cwd=cwd,
        rows=24,
        cols=80,
        scrollback_size=100,
        kind=kind,
        startup_stall_seconds=0.0,
        attention_quiet_seconds=attention_quiet_seconds,
    )


async def _read_script(
    session: TerminalSession, backend: _ScriptedPtyBackend,
) -> list[str | None]:
    """Run the reader over ``backend``'s whole script, as a child that wrote
    it and exited, and return the attention values raised, in order."""
    backend.close()
    session._state = SessionState.RUNNING
    await session._reader_loop()
    return [e.attention for e in _drain_attention_events(session)]


async def _next_output(
    session: TerminalSession, timeout: float = 2.0,
) -> tuple[list[str | None], SessionEvent]:
    """The next ``output`` event and the attention values queued ahead of it.

    The reader hands a read's reports to the gate before it enqueues that
    read's output, so whatever one read raised is queued in front of it.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    attention: list[str | None] = []
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise AssertionError("no output event within the timeout")
        event = await asyncio.wait_for(session.event_queue.get(), timeout=remaining)
        if event.event_type == "attention":
            attention.append(event.attention)
        elif event.event_type == "output":
            return attention, event


# Short enough to wait out, long enough that a client write lands inside it.
_TEST_ALERT_SPACING_SECONDS = 0.5


def _deferred_prompt_session(backend: _ScriptedPtyBackend) -> TerminalSession:
    """A Codex session whose alerts are ``_TEST_ALERT_SPACING_SECONDS`` apart.

    Fed ``blocked``, ``working``, ``blocked`` in one read, the first prompt
    alerts and is answered, and the second arrives inside the gap, so it waits
    on the gate's timer instead of alerting.
    """
    with patch(
        "mobaide.core.program_status.PROGRAM_STATUS_MIN_ALERT_SPACING_SECONDS",
        _TEST_ALERT_SPACING_SECONDS,
    ):
        return TerminalSession(
            _status_config(SessionKind.CODEX), backend=cast(PtyBackend, backend),
        )


@pytest.mark.timeout(15)
class TestProgramStatusAttention:
    """Every session without Claude Code hooks raises attention from the OSC
    7501 reports its CLI writes; Claude and Auto Scan sessions keep the hooks
    as their only source, so one event can never alert twice. The gate's own
    rules are covered in ``test_program_status.py``.
    """

    @pytest.mark.parametrize(
        "kind",
        [SessionKind.SHELL, SessionKind.CODEX, SessionKind.ANTIGRAVITY, _CUSTOM_KIND],
        ids=["shell", "codex", "antigravity", "custom"],
    )
    async def test_hookless_sessions_alert_from_a_blocked_report(self, kind) -> None:
        backend = _ScriptedPtyBackend([b"Allow? " + _BLOCKED_REPORT])
        session = TerminalSession(_status_config(kind), backend=cast(PtyBackend, backend))

        assert await _read_script(session, backend) == ["awaiting_permission"]
        assert session.to_info().attention == "awaiting_permission"

    @pytest.mark.parametrize(
        ("report", "reason"),
        [(_DONE_REPORT, "turn_finished"), (_ERROR_REPORT, "turn_failed")],
    )
    async def test_turn_end_reports_alert(self, report, reason) -> None:
        backend = _ScriptedPtyBackend([report])
        session = TerminalSession(
            _status_config(SessionKind.SHELL), backend=cast(PtyBackend, backend),
        )

        assert await _read_script(session, backend) == [reason]

    @pytest.mark.parametrize(
        "kind", [SessionKind.CLAUDE, SessionKind.AUTO_SCAN], ids=["claude", "auto_scan"],
    )
    async def test_hook_driven_sessions_ignore_reports(self, kind) -> None:
        # Claude Code reports through OSC 7501 too once a terminal answers its
        # query; listening here as well would alert every turn twice.
        backend = _ScriptedPtyBackend([_BLOCKED_REPORT + _DONE_REPORT + _ERROR_REPORT])
        session = TerminalSession(_status_config(kind), backend=cast(PtyBackend, backend))

        assert await _read_script(session, backend) == []
        assert session.to_info().attention is None

    async def test_the_support_query_is_answered_before_the_da_reply(self) -> None:
        # agy sends the query right before DA1 and reads a DA1 reply that
        # arrives first as "unsupported".
        backend = _ScriptedPtyBackend([b"a" + _STATUS_QUERY + b"\x1b[c" + b"b"])
        session = TerminalSession(
            _status_config(SessionKind.ANTIGRAVITY), backend=cast(PtyBackend, backend),
        )
        await session.start()
        try:
            _, event = await _next_output(session)
            assert event.data == b"ab"

            await _wait_for_writes(backend, 1)
            assert b"".join(backend._writes) == _STATUS_REPLY + DA1_REPLY
            # A replayed query would make a client emulator answer it again.
            assert session.get_ring() == b"ab"
        finally:
            await session.stop()

    async def test_a_claude_session_leaves_the_support_query_unanswered(self) -> None:
        # Unanswered, Claude Code never starts reporting at all.
        backend = _ScriptedPtyBackend([b"a" + _STATUS_QUERY + b"\x1b[c" + b"b"])
        session = TerminalSession(
            _status_config(SessionKind.CLAUDE), backend=cast(PtyBackend, backend),
        )
        await session.start()
        try:
            _, event = await _next_output(session)
            assert event.data == b"a" + _STATUS_QUERY + b"b"

            await _wait_for_writes(backend, 1)
            assert b"".join(backend._writes) == DA1_REPLY
        finally:
            await session.stop()

    @pytest.mark.parametrize("cut", range(1, len(_DONE_REPORT)))
    async def test_a_report_split_across_reads_alerts_once(self, cut) -> None:
        # The query responder holds a fragment that might become a query
        # before the tap sees the stream; the two carries must not lose or
        # double a report between them.
        backend = _ScriptedPtyBackend([_DONE_REPORT[:cut], _DONE_REPORT[cut:]])
        session = TerminalSession(
            _status_config(SessionKind.CODEX), backend=cast(PtyBackend, backend),
        )

        assert await _read_script(session, backend) == ["turn_finished"]

    @pytest.mark.parametrize(
        "report", [_WORKING_REPORT, _CLEAR_REPORT], ids=["answered", "program-exited"],
    )
    async def test_a_report_that_ends_the_prompt_drops_the_permission_badge(
        self, report,
    ) -> None:
        # The prompt was answered on the PC, or the program exited with it
        # still open: the phone's badge must go too.
        backend = _ScriptedPtyBackend([_BLOCKED_REPORT, report])
        session = TerminalSession(
            _status_config(SessionKind.CODEX), backend=cast(PtyBackend, backend),
        )

        assert await _read_script(session, backend) == ["awaiting_permission", None]
        assert session.to_info().attention is None

    async def test_a_replayed_log_of_prompt_flips_alerts_once(self) -> None:
        # ``cat`` of a recorded session replays every report in one burst.
        backend = _ScriptedPtyBackend([(_BLOCKED_REPORT + _WORKING_REPORT) * 1000])
        session = TerminalSession(
            _status_config(SessionKind.CODEX), backend=cast(PtyBackend, backend),
        )

        assert await _read_script(session, backend) == ["awaiting_permission", None]
        assert session._status_gate._timer is None

    async def test_client_input_cancels_a_pending_done_window(self) -> None:
        backend = _ScriptedPtyBackend([_DONE_REPORT])
        session = TerminalSession(
            _status_config(SessionKind.CODEX, attention_quiet_seconds=60.0),
            backend=cast(PtyBackend, backend),
        )
        await session.start()
        try:
            assert (await _next_output(session))[0] == []
            window = session._status_gate._timer
            assert window is not None

            await session.write(b"\x1b[A")

            assert window.cancelled()
            assert _drain_attention_events(session) == []
        finally:
            await session.stop()

    @pytest.mark.parametrize("report", [b"\x1b[I", b"\x1b[O"], ids=["focus-in", "focus-out"])
    async def test_a_focus_report_keeps_a_prompt_waiting_out_the_gap(self, report) -> None:
        # A client emulator writes one on a tab switch or a dismissed keyboard.
        # The CLI does not repeat an identical ``blocked``, so a dropped wait
        # would leave the prompt unannounced everywhere.
        backend = _ScriptedPtyBackend([_BLOCKED_REPORT + _WORKING_REPORT + _BLOCKED_REPORT])
        session = _deferred_prompt_session(backend)
        await session.start()
        try:
            assert (await _next_output(session))[0] == ["awaiting_permission", None]
            deferred = session._status_gate._timer
            assert deferred is not None

            await session.write(report)

            assert not deferred.cancelled()
            assert session._status_gate._timer is deferred
            event = await asyncio.wait_for(
                session.event_queue.get(), timeout=_TEST_ALERT_SPACING_SECONDS + 2.0,
            )
            assert event.event_type == "attention"
            assert event.attention == "awaiting_permission"
            assert backend._writes == [report]
        finally:
            await session.stop(force=True)

    @pytest.mark.parametrize(
        "data", [b"x", b"\x1b[Ox"], ids=["keystroke", "focus-report-with-a-keystroke"],
    )
    async def test_client_input_drops_a_prompt_waiting_out_the_gap(self, data) -> None:
        backend = _ScriptedPtyBackend([_BLOCKED_REPORT + _WORKING_REPORT + _BLOCKED_REPORT])
        session = _deferred_prompt_session(backend)
        await session.start()
        try:
            assert (await _next_output(session))[0] == ["awaiting_permission", None]
            deferred = session._status_gate._timer
            assert deferred is not None

            await session.write(data)

            assert deferred.cancelled()
            assert session._status_gate._timer is None
            assert _drain_attention_events(session) == []
            assert backend._writes == [data]
        finally:
            await session.stop(force=True)

    async def test_stop_cancels_a_pending_done_window(self) -> None:
        backend = _ScriptedPtyBackend([_DONE_REPORT])
        session = TerminalSession(
            _status_config(SessionKind.CODEX, attention_quiet_seconds=60.0),
            backend=cast(PtyBackend, backend),
        )
        await session.start()
        await _next_output(session)
        window = session._status_gate._timer
        assert window is not None

        await session.stop(force=True)

        assert window.cancelled()
        assert _drain_attention_events(session) == []

    @pytest.mark.parametrize(
        ("kind", "report"),
        [
            (SessionKind.SHELL, _DONE_REPORT),
            (SessionKind.CODEX, b"\x1b]7501;state=done\x07"),
        ],
        ids=["app-from-the-report", "app-from-the-kind"],
    )
    async def test_a_turn_a_chat_already_announced_stays_quiet(
        self, tmp_path, kind, report,
    ) -> None:
        # A Codex chat live-attached to this TUI's thread alerted the same
        # turn a moment ago.
        dedupe = SharedTurnDedupe()
        assert dedupe.admit(
            session_id="chat-1", ui="chat", app="codex",
            cwd=real_cwd(str(tmp_path)), reason="turn_finished",
        )
        backend = _ScriptedPtyBackend([report])
        session = TerminalSession(
            _status_config(kind, cwd=str(tmp_path)),
            backend=cast(PtyBackend, backend),
            attention_dedupe=dedupe,
        )

        assert await _read_script(session, backend) == []
        assert session.to_info().attention is None

    async def test_a_chat_in_another_folder_does_not_quiet_it(self, tmp_path) -> None:
        other = tmp_path / "other"
        other.mkdir()
        dedupe = SharedTurnDedupe()
        dedupe.admit(
            session_id="chat-1", ui="chat", app="codex",
            cwd=real_cwd(str(other)), reason="turn_finished",
        )
        backend = _ScriptedPtyBackend([_DONE_REPORT])
        session = TerminalSession(
            _status_config(SessionKind.CODEX, cwd=str(tmp_path)),
            backend=cast(PtyBackend, backend),
            attention_dedupe=dedupe,
        )

        assert await _read_script(session, backend) == ["turn_finished"]

    async def test_a_codex_tui_on_its_own_app_server_is_never_quieted(self, tmp_path) -> None:
        # ``-c`` overrides give the TUI a private app server: the chat that
        # alerted in this folder cannot be on its thread.
        dedupe = SharedTurnDedupe()
        assert dedupe.admit(
            session_id="chat-1", ui="chat", app="codex",
            cwd=real_cwd(str(tmp_path)), reason="turn_finished",
        )
        backend = _ScriptedPtyBackend([_DONE_REPORT])
        session = TerminalSession(
            _status_config(SessionKind.CODEX, cwd=str(tmp_path), command=_MANAGED_CODEX),
            backend=cast(PtyBackend, backend),
            attention_dedupe=dedupe,
        )

        assert await _read_script(session, backend) == ["turn_finished"]

    async def test_a_codex_tui_on_its_own_app_server_leaves_no_claim(self, tmp_path) -> None:
        # Otherwise its alert would mute a chat in the folder on another thread.
        dedupe = SharedTurnDedupe()
        backend = _ScriptedPtyBackend([_DONE_REPORT])
        session = TerminalSession(
            _status_config(SessionKind.CODEX, cwd=str(tmp_path), command=_MANAGED_CODEX),
            backend=cast(PtyBackend, backend),
            attention_dedupe=dedupe,
        )

        assert await _read_script(session, backend) == ["turn_finished"]
        assert dedupe.admit(
            session_id="chat-1", ui="chat", app="codex",
            cwd=real_cwd(str(tmp_path)), reason="turn_finished",
        )

    @pytest.mark.parametrize(
        "kind", [SessionKind.SHELL, _CUSTOM_KIND], ids=["shell", "custom"],
    )
    async def test_a_dash_c_outside_a_managed_codex_launch_still_pairs(
        self, tmp_path, kind,
    ) -> None:
        # ``sh -c codex`` can run the TUI on the shared daemon.
        dedupe = SharedTurnDedupe()
        assert dedupe.admit(
            session_id="chat-1", ui="chat", app="codex",
            cwd=real_cwd(str(tmp_path)), reason="turn_finished",
        )
        backend = _ScriptedPtyBackend([_DONE_REPORT])
        session = TerminalSession(
            _status_config(kind, cwd=str(tmp_path), command=["/bin/sh", "-c", "codex"]),
            backend=cast(PtyBackend, backend),
            attention_dedupe=dedupe,
        )

        assert await _read_script(session, backend) == []

    @pytest.mark.parametrize(
        ("kind", "report"),
        [
            (SessionKind.ANTIGRAVITY, b"\x1b]7501;state=done\x07"),
            (SessionKind.SHELL, b"\x1b]7501;state=done:app=antigravity\x07"),
            (SessionKind.SHELL, b"\x1b]7501;state=done\x07"),
            (_CUSTOM_KIND, b"\x1b]7501;state=done\x07"),
        ],
        ids=["antigravity", "agy-in-a-shell", "shell", "custom"],
    )
    async def test_a_cli_without_shared_threads_never_consults_the_dedupe(
        self, tmp_path, kind, report,
    ) -> None:
        # Only Codex threads can be shared; every other session alerts on its
        # own and must not resolve its folder on the event loop for nothing.
        dedupe = _MutingDedupe()
        resolved: list[str | None] = []

        def recording_real_cwd(cwd: str | None) -> str | None:
            resolved.append(cwd)
            return cwd

        backend = _ScriptedPtyBackend([report])
        session = TerminalSession(
            _status_config(kind, cwd=str(tmp_path)),
            backend=cast(PtyBackend, backend),
            attention_dedupe=cast(SharedTurnDedupe, dedupe),
        )

        with patch("mobaide.core.session.real_cwd", recording_real_cwd):
            assert await _read_script(session, backend) == ["turn_finished"]
        assert dedupe.asked == []
        assert resolved == []


@pytest.mark.timeout(15)
class TestClaudeHookActivity:
    """``activity`` is the always-current "agent is working" field the session
    row carries. The gate decides the transitions (``test_attention_gate.py``);
    the session publishes each change once, as a row refresh rather than an
    attention event, and only while it is running.
    """

    async def test_prompt_submit_reports_working_without_an_attention_event(
        self,
    ) -> None:
        session = _running_hook_session(0.0)
        session.on_claude_hook(HookKind.PROMPT_SUBMIT, {"prompt_id": "p1"})

        assert session.to_info().activity == "working"
        assert _drain_event_types(session) == ["activity"]

    async def test_a_repeated_activity_emits_once(self) -> None:
        # Every ``Stop`` of a background-agent burst re-reports ``working``;
        # each emission is a ``session.updated`` on every device.
        session = _running_hook_session(0.0)
        session.on_claude_hook(HookKind.PROMPT_SUBMIT, {"prompt_id": "p1"})
        for _ in range(3):
            session.on_claude_hook(
                HookKind.STOP,
                {"prompt_id": "p1", "background_tasks": [{"status": "running"}]},
            )

        assert session.to_info().activity == "working"
        assert _drain_event_types(session) == ["activity"]

    async def test_the_end_of_a_turn_reports_idle_before_the_alert(self) -> None:
        session = _running_hook_session(0.0)
        session.on_claude_hook(HookKind.PROMPT_SUBMIT, {"prompt_id": "p1"})
        session.on_claude_hook(HookKind.STOP, {"prompt_id": "p1"})

        info = session.to_info()
        assert info.activity == "idle"
        assert info.attention == "turn_finished"
        assert _drain_event_types(session) == ["activity", "activity", "attention"]

    @pytest.mark.parametrize(
        "state", [SessionState.STOPPING, SessionState.STOPPED, SessionState.CRASHED],
    )
    async def test_only_a_running_session_reports_its_activity(self, state) -> None:
        # A session that died mid-turn never gets the ``Stop`` that would turn
        # it idle; its last ``working`` must not keep a row spinning.
        session = _running_hook_session(0.0)
        session.on_claude_hook(HookKind.PROMPT_SUBMIT, {"prompt_id": "p1"})

        session._state = state

        assert session.to_info().activity is None


@pytest.mark.timeout(15)
class TestWriteSubmitAndExecute:
    """The CR must land as its own delayed PTY write — Ink TUIs treat a CR
    inside the same stdin chunk as pasted content, not the Enter key."""

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

    async def test_write_submit_splits_line_clear_prefix_from_bracketed_paste(self) -> None:
        """Claude CLI swallows the CR after a collapsed paste that shared its
        stdin chunk with the clients' Ctrl+A/Ctrl+K prefix, so the prefix
        must land as its own write, a pause ahead of the paste."""
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

        paste = b"\x1b[200~line one\nline two\x1b[201~"
        with patch("mobaide.core.session.asyncio.sleep", sleep_mock):
            await session.write_submit(b"\x01\x0b" + paste)

        assert events == [
            ("write", b"\x01\x0b"),
            ("sleep", PASTE_PREFIX_DELAY_SECONDS),
            ("write", paste),
            ("sleep", SUBMIT_CR_DELAY_SECONDS),
            ("write", b"\r"),
        ]

    async def test_write_submit_keeps_unprefixed_paste_in_one_write(self) -> None:
        backend = _ScriptedPtyBackend([])
        session = TerminalSession(
            _attention_config(), backend=cast(PtyBackend, backend),
        )

        paste = b"\x1b[200~line one\nline two\x1b[201~"
        with patch(
            "mobaide.core.session.asyncio.sleep", new_callable=AsyncMock
        ) as sleep_mock:
            await session.write_submit(paste)

        assert backend._writes == [paste, b"\r"]
        sleep_mock.assert_awaited_once_with(SUBMIT_CR_DELAY_SECONDS)

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
        backend = _ScriptedPtyBackend([chunk, b""])
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
        backend = _ScriptedPtyBackend([chunk, b""])
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

@pytest.mark.timeout(15)
class TestJoinTask:
    """``_join_task`` is the true freeze guarantee: it cancels a background task
    and bounds the wait with ``asyncio.wait`` (not ``wait_for``), so a task
    parked in an uncancellable executor call is abandoned after the timeout
    instead of wedging teardown forever.
    """

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
