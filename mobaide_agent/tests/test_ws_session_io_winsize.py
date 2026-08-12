"""The ``w`` (winsize) frame on the direct WS session channel (proto-v8).

Handshake-level coverage over the real endpoint and a real PTY, like
``test_ws_session_io.py``. Live fan-out of winsize *events* to already-open
sockets is deliberately not asserted here: Starlette's TestClient runs each
websocket in its own portal event loop, so the shared fanout pump (created in
the REST request's loop) can never deliver across sockets in-process — that
path is unit-covered in ``test_session_winsize.py`` (event emission) and
belongs to the e2e harness end-to-end. What IS deterministic here: the
handshake sequence and its ``w`` fields, the pre-registered-peer count, the
resize applied through the socket reader, and the detach unregistration.
"""
from __future__ import annotations

import struct
import time

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocket, WebSocketDisconnect

from tests.conftest import TEST_USERNAME, drain_text_preamble


@pytest.fixture
def test_client(app):
    return TestClient(app)


@pytest.fixture
def auth_token(app) -> str:
    return app.state.jwt_service.create_access_token(TEST_USERNAME)


@pytest.fixture
def session_id_sync(test_client, auth_token) -> str:
    resp = test_client.post(
        "/api/sessions",
        headers={"Authorization": f"Bearer {auth_token}"},
        json={},
    )
    assert resp.status_code == 201
    return resp.json()["session_id"]


def _session_of(app, session_id: str):
    return app.state.session_manager._sessions[session_id]


def _unpack_winsize(frame: bytes) -> tuple[int, int, int]:
    assert frame[:1] == b"w", f"expected a 'w' frame, got {frame[:1]!r}"
    assert len(frame) == 7
    return struct.unpack(">HHH", frame[1:])


def _drain_handshake(ws) -> tuple[int, int, int]:
    """Consume the fixed attach sequence and return the ``w`` frame's fields.

    Wire order (proto-v8): state -> winsize -> replay-start -> output… ->
    winsize -> replay-end. The first ``w`` sits before the replay span so a
    client can arbitrate before its replay-end re-assert decision; the second
    re-states size + count just before the end marker, covering changes that
    landed during a long replay while this socket was not yet subscribed to
    live broadcasts.
    """
    drain_text_preamble(ws)
    assert ws.receive_bytes()[:1] == b"s"
    dims = _unpack_winsize(ws.receive_bytes())
    assert ws.receive_bytes()[:1] == b"b"
    trailing = None
    while True:
        frame = ws.receive_bytes()
        if frame[:1] == b"e":
            assert trailing is not None, "expected a re-emitted 'w' before 'e'"
            return dims
        if frame[:1] == b"w":
            trailing = _unpack_winsize(frame)
            continue
        assert frame[:1] == b"o"


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    pytest.fail("condition not met within timeout")


@pytest.mark.timeout(15)
class TestWinsizeHandshake:
    def test_handshake_reports_spawn_size_and_single_client(
        self, test_client, auth_token, session_id_sync
    ):
        with test_client.websocket_connect(
            f"/ws/session/{session_id_sync}?token={auth_token}",
        ) as ws:
            rows, cols, count = _drain_handshake(ws)
            # The agent spawns at the config default and nothing resized yet;
            # this socket registered itself before the handshake, so it is
            # already counted.
            assert (rows, cols) == (24, 80)
            assert count == 1

    def test_handshake_counts_previously_attached_clients(
        self, app, test_client, auth_token, session_id_sync
    ):
        # A client attaching to a session someone else is driving must learn
        # it is not alone from the handshake itself — that is what lets a
        # fresh follower adopt instead of stealing.
        _session_of(app, session_id_sync).register_client("direct:test-peer")

        with test_client.websocket_connect(
            f"/ws/session/{session_id_sync}?token={auth_token}",
        ) as ws:
            assert _drain_handshake(ws)[2] == 2

    def test_resize_frame_updates_authoritative_size(
        self, app, test_client, auth_token, session_id_sync
    ):
        session = _session_of(app, session_id_sync)
        with test_client.websocket_connect(
            f"/ws/session/{session_id_sync}?token={auth_token}",
        ) as ws:
            _drain_handshake(ws)

            ws.send_bytes(b"r" + struct.pack(">HH", 40, 120))

            _wait_until(lambda: (session.rows, session.cols) == (40, 120))
            # The next attach's ``w`` will carry the new grid.
            assert session.winsize_payload() == struct.pack(">HHH", 40, 120, 1)

    def test_disconnect_unregisters_the_socket(
        self, app, test_client, auth_token, session_id_sync
    ):
        session = _session_of(app, session_id_sync)
        with test_client.websocket_connect(
            f"/ws/session/{session_id_sync}?token={auth_token}",
        ) as ws:
            _drain_handshake(ws)
            assert session.attached_client_count == 1

        # The finally block dropped this socket's token, so the count a
        # surviving client would see falls back to solo.
        _wait_until(lambda: session.attached_client_count == 0)

    def test_mid_handshake_disconnect_still_unregisters(
        self, app, test_client, auth_token, session_id_sync, monkeypatch
    ):
        # A client that dies inside the handshake (the ring replay can span
        # megabytes) raises out of one of the sends before the pump loop even
        # exists — only the endpoint's own try/finally can unregister then. A
        # token that outlived its socket would inflate the attached-client
        # count for the session's whole life, keeping every real client a
        # follower of a ghost.
        session = _session_of(app, session_id_sync)
        real_send_bytes = WebSocket.send_bytes
        sent = {"count": 0}

        async def dying_send_bytes(self, data):
            sent["count"] += 1
            if sent["count"] >= 3:  # state + w delivered; die in the replay span
                raise WebSocketDisconnect(code=1006)
            await real_send_bytes(self, data)

        monkeypatch.setattr(WebSocket, "send_bytes", dying_send_bytes)

        with pytest.raises(WebSocketDisconnect):
            with test_client.websocket_connect(
                f"/ws/session/{session_id_sync}?token={auth_token}",
            ) as ws:
                drain_text_preamble(ws)
                assert ws.receive_bytes()[:1] == b"s"
                # Registered before the handshake: its own ``w`` counts it.
                assert _unpack_winsize(ws.receive_bytes())[2] == 1

        _wait_until(lambda: session.attached_client_count == 0)
