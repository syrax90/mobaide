"""Tests for the binary WebSocket session I/O endpoint."""
from __future__ import annotations

import json
import struct
import sys
import time

import pytest
from starlette.testclient import TestClient

from tests.conftest import TEST_USERNAME


@pytest.fixture
def test_client(app):
    return TestClient(app)


@pytest.fixture
def auth_token(app) -> str:
    return app.state.jwt_service.create_access_token(TEST_USERNAME)


@pytest.fixture
def session_id_sync(test_client, auth_token) -> str:
    """Create a session via REST and return its ID."""
    resp = test_client.post(
        "/api/sessions",
        headers={"Authorization": f"Bearer {auth_token}"},
        json={},
    )
    assert resp.status_code == 201
    return resp.json()["session_id"]


def _state(frame: bytes) -> dict | None:
    if not frame or frame[:1] != b"s":
        return None
    return json.loads(frame[1:].decode("utf-8"))


class TestWsAuth:
    def test_connect_with_valid_token(self, test_client, auth_token, session_id_sync):
        with test_client.websocket_connect(
            f"/ws/session/{session_id_sync}?token={auth_token}",
        ) as ws:
            # First frame must be a state frame.
            frame = ws.receive_bytes()
            payload = _state(frame)
            assert payload is not None
            assert payload["status"] in ("starting", "running")

    def test_connect_with_invalid_token(self, test_client, session_id_sync):
        with pytest.raises(Exception):
            with test_client.websocket_connect(
                f"/ws/session/{session_id_sync}?token=bad.token",
            ) as ws:
                ws.receive_bytes()

    def test_connect_without_token_rejected(self, test_client, session_id_sync):
        """First-message JSON auth was removed; missing token must close 4001."""
        with pytest.raises(Exception):
            with test_client.websocket_connect(
                f"/ws/session/{session_id_sync}",
            ) as ws:
                ws.receive_bytes()

    def test_nonexistent_session(self, test_client, auth_token):
        with pytest.raises(Exception):
            with test_client.websocket_connect(
                f"/ws/session/doesnotexist?token={auth_token}",
            ) as ws:
                ws.receive_bytes()

    def test_connect_with_refresh_token_rejected(self, test_client, app, session_id_sync):
        refresh_token = app.state.jwt_service.create_refresh_token(TEST_USERNAME)
        with pytest.raises(Exception):
            with test_client.websocket_connect(
                f"/ws/session/{session_id_sync}?token={refresh_token}",
            ) as ws:
                ws.receive_bytes()


class TestWsBinaryFrames:
    def test_initial_handshake_sequence(
        self, test_client, auth_token, session_id_sync,
    ):
        """On connect we expect: state frame, then replay marker."""
        with test_client.websocket_connect(
            f"/ws/session/{session_id_sync}?token={auth_token}",
        ) as ws:
            first = ws.receive_bytes()
            assert first[:1] == b"s"
            second = ws.receive_bytes()
            assert second[:1] == b"b"
            assert second == b"b"  # marker has empty payload

    def test_send_resize_binary(self, test_client, auth_token, session_id_sync):
        with test_client.websocket_connect(
            f"/ws/session/{session_id_sync}?token={auth_token}",
        ) as ws:
            ws.receive_bytes()  # state
            ws.receive_bytes()  # replay marker
            ws.send_bytes(b"r" + struct.pack(">HH", 40, 120))
            time.sleep(0.3)

    def test_send_input_binary(self, test_client, auth_token, session_id_sync):
        """Verify that input frames are accepted without error."""
        with test_client.websocket_connect(
            f"/ws/session/{session_id_sync}?token={auth_token}",
        ) as ws:
            ws.receive_bytes()  # state
            ws.receive_bytes()  # replay marker
            text = b"echo test\r\n" if sys.platform == "win32" else b"echo test\r"
            ws.send_bytes(b"i" + text)
            time.sleep(0.3)
