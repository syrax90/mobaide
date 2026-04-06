"""Tests for WebSocket session I/O endpoint."""
from __future__ import annotations

import base64
import sys
import time

import pytest
from starlette.testclient import TestClient

from tests.conftest import TEST_PASSWORD, TEST_USERNAME


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


class TestWsAuth:
    def test_connect_with_valid_token(self, test_client, auth_token, session_id_sync):
        with test_client.websocket_connect(
            f"/ws/session/{session_id_sync}?token={auth_token}",
        ) as ws:
            # Should receive initial scrollback/state messages
            msg = ws.receive_json()
            assert msg["type"] in ("output", "state")

    def test_connect_with_invalid_token(self, test_client, session_id_sync):
        with pytest.raises(Exception):
            with test_client.websocket_connect(
                f"/ws/session/{session_id_sync}?token=bad.token",
            ) as ws:
                ws.receive_json()

    def test_nonexistent_session(self, test_client, auth_token):
        with pytest.raises(Exception):
            with test_client.websocket_connect(
                f"/ws/session/doesnotexist?token={auth_token}",
            ) as ws:
                ws.receive_json()


class TestWsIO:
    def test_receive_state_message(self, test_client, auth_token, session_id_sync):
        with test_client.websocket_connect(
            f"/ws/session/{session_id_sync}?token={auth_token}",
        ) as ws:
            # Read messages until we get a state message
            for _ in range(50):
                msg = ws.receive_json()
                if msg["type"] == "state":
                    assert msg["status"] in ("running", "starting")
                    return
            pytest.fail("Never received state message")

    def test_send_resize(self, test_client, auth_token, session_id_sync):
        with test_client.websocket_connect(
            f"/ws/session/{session_id_sync}?token={auth_token}",
        ) as ws:
            # Read through initial messages to state
            for _ in range(50):
                msg = ws.receive_json()
                if msg["type"] == "state":
                    break

            # Resize should not error
            ws.send_json({"type": "resize", "rows": 40, "cols": 120})
            time.sleep(0.3)

    def test_send_input(self, test_client, auth_token, session_id_sync):
        """Verify that input messages are accepted without error."""
        with test_client.websocket_connect(
            f"/ws/session/{session_id_sync}?token={auth_token}",
        ) as ws:
            # Read through initial messages to state
            for _ in range(50):
                msg = ws.receive_json()
                if msg["type"] == "state":
                    break

            # Send input — verify it's accepted (no error/close)
            text = "echo test\r\n" if sys.platform == "win32" else "echo test\n"
            encoded = base64.b64encode(text.encode()).decode()
            ws.send_json({"type": "input", "data": encoded})
            time.sleep(0.3)
