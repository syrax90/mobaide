"""Size arbitration state on ``TerminalSession`` (proto-v8).

Covers the effective-resize contract (``resize() -> bool`` with dedupe,
validation and non-RUNNING no-op), the stored authoritative size, the
attached-client token registry, and the ``winsize`` SessionEvent whose data is
the packed S->C ``w`` frame payload. Sessions are never started — ``resize``
and the registry only touch in-memory state, the injected backend, and the
bounded event queue (mirrors ``TestTerminalSessionAttention``).
"""
from __future__ import annotations

import struct
from typing import cast

from mobaide.core.models import SessionConfig, SessionEvent, SessionState
from mobaide.core.pty_backend import FakePtyBackend, PtyBackend
from mobaide.core.session import MAX_GRID_DIM, TerminalSession


def _config(rows: int = 24, cols: int = 80) -> SessionConfig:
    return SessionConfig(
        session_id="winsize-session",
        command=["does-not-run"],
        rows=rows,
        cols=cols,
        scrollback_size=100,
    )


def _running_session(backend: FakePtyBackend | None = None) -> TerminalSession:
    backend = backend or FakePtyBackend()
    session = TerminalSession(_config(), backend=cast(PtyBackend, backend))
    session._state = SessionState.RUNNING
    return session


def _drain_winsize_events(session: TerminalSession) -> list[SessionEvent]:
    events: list[SessionEvent] = []
    while not session.event_queue.empty():
        ev = session.event_queue.get_nowait()
        if ev.event_type == "winsize":
            events.append(ev)
    return events


class TestEffectiveResize:
    async def test_effective_resize_applies_stores_and_emits_winsize(self) -> None:
        backend = FakePtyBackend()
        session = _running_session(backend)

        assert await session.resize(40, 120) is True

        assert (session.rows, session.cols) == (40, 120)
        assert backend.resize_calls == [(40, 120)]
        events = _drain_winsize_events(session)
        assert len(events) == 1
        assert events[0].session_id == "winsize-session"
        # The event data IS the wire payload of the ``w`` frame: rows, cols,
        # attached-client count as big-endian u16 — no clients registered here.
        assert events[0].data == struct.pack(">HHH", 40, 120, 0)

    async def test_equal_size_is_deduped(self) -> None:
        # The dedupe is what stops the multi-client resize storm: a client
        # re-asserting the size the PTY already has must not touch the backend
        # nor broadcast a winsize.
        backend = FakePtyBackend()
        session = _running_session(backend)

        assert await session.resize(24, 80) is False

        assert backend.resize_calls == []
        assert _drain_winsize_events(session) == []
        assert (session.rows, session.cols) == (24, 80)

    async def test_out_of_range_values_are_rejected(self) -> None:
        backend = FakePtyBackend()
        session = _running_session(backend)

        assert await session.resize(0, 80) is False
        assert await session.resize(24, 0) is False
        assert await session.resize(0x10000, 80) is False
        assert await session.resize(24, 0x10000) is False

        assert backend.resize_calls == []
        assert _drain_winsize_events(session) == []
        assert (session.rows, session.cols) == (24, 80)

    async def test_grid_envelope_bound_is_inclusive(self) -> None:
        # MAX_GRID_DIM is the largest grid the agent will store and broadcast:
        # followers ADOPT the broadcast size into their own emulators, so an
        # absurd grid from one client would make every attached client
        # allocate a matching buffer.
        backend = FakePtyBackend()
        session = _running_session(backend)

        assert await session.resize(MAX_GRID_DIM, MAX_GRID_DIM) is True
        assert backend.resize_calls == [(MAX_GRID_DIM, MAX_GRID_DIM)]
        assert (session.rows, session.cols) == (MAX_GRID_DIM, MAX_GRID_DIM)

    async def test_grid_above_the_envelope_is_rejected(self) -> None:
        backend = FakePtyBackend()
        session = _running_session(backend)

        assert await session.resize(MAX_GRID_DIM + 1, 80) is False
        assert await session.resize(24, MAX_GRID_DIM + 1) is False
        assert await session.resize(0xFFFF, 0xFFFF) is False

        assert backend.resize_calls == []
        assert _drain_winsize_events(session) == []
        assert (session.rows, session.cols) == (24, 80)

    async def test_spawn_seed_is_clamped_into_the_envelope(self) -> None:
        # A create request can carry any int the >HHHH spawn pack accepts; the
        # stored/broadcast grid (and the PTY spawn itself) must stay inside
        # the envelope so the handshake `w` cannot amplify an absurd seed.
        backend = FakePtyBackend()
        session = TerminalSession(
            _config(rows=5000, cols=5000),
            backend=backend,
        )

        assert (session.rows, session.cols) == (MAX_GRID_DIM, MAX_GRID_DIM)
        assert session.winsize_payload() == struct.pack(
            ">HHH", MAX_GRID_DIM, MAX_GRID_DIM, 0,
        )

    async def test_resize_when_not_running_is_noop(self) -> None:
        backend = FakePtyBackend()
        session = TerminalSession(_config(), backend=cast(PtyBackend, backend))
        session._state = SessionState.STOPPED

        assert await session.resize(40, 120) is False

        assert backend.resize_calls == []
        assert (session.rows, session.cols) == (24, 80)
        assert _drain_winsize_events(session) == []


class TestClientRegistry:
    def test_register_emits_winsize_with_new_count(self) -> None:
        session = _running_session()

        session.register_client("direct:a")

        assert session.attached_client_count == 1
        events = _drain_winsize_events(session)
        assert [e.data for e in events] == [struct.pack(">HHH", 24, 80, 1)]

    def test_double_register_same_token_is_silent(self) -> None:
        # An idempotent re-attach (e.g. a replayed relay attach) must not
        # broadcast a count that did not change.
        session = _running_session()
        session.register_client("direct:a")
        _drain_winsize_events(session)

        session.register_client("direct:a")

        assert session.attached_client_count == 1
        assert _drain_winsize_events(session) == []

    def test_unregister_emits_winsize_with_dropped_count(self) -> None:
        session = _running_session()
        session.register_client("direct:a")
        session.register_client("relay:b")
        _drain_winsize_events(session)

        session.unregister_client("direct:a")

        assert session.attached_client_count == 1
        events = _drain_winsize_events(session)
        assert [e.data for e in events] == [struct.pack(">HHH", 24, 80, 1)]

    def test_unregister_unknown_token_is_silent(self) -> None:
        session = _running_session()

        session.unregister_client("relay:never-registered")

        assert session.attached_client_count == 0
        assert _drain_winsize_events(session) == []

    async def test_winsize_payload_reflects_current_size_and_count(self) -> None:
        session = _running_session()
        session.register_client("direct:a")
        session.register_client("relay:b")
        await session.resize(50, 132)

        assert session.winsize_payload() == struct.pack(">HHH", 50, 132, 2)
