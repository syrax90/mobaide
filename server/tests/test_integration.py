"""Integration test: full auth → create → WS connect → scrollback → stop flow."""
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


@pytest.mark.timeout(30)
def test_full_flow(test_client):
    """Full end-to-end: authenticate → create session → WS connect + I/O → scrollback → stop."""

    # 1. Authenticate
    resp = test_client.post(
        "/api/auth/token",
        json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    assert resp.status_code == 200
    tokens = resp.json()
    access_token = tokens["access_token"]
    auth = {"Authorization": f"Bearer {access_token}"}

    # 2. Create session
    resp = test_client.post("/api/sessions", headers=auth, json={})
    assert resp.status_code == 201
    session_id = resp.json()["session_id"]
    assert resp.json()["state"] == "running"

    # 3. Connect WebSocket and verify I/O channel works
    with test_client.websocket_connect(
        f"/ws/session/{session_id}?token={access_token}",
    ) as ws:
        # Read initial burst until state message
        got_output = False
        got_state = False
        for _ in range(50):
            msg = ws.receive_json()
            if msg["type"] == "output":
                got_output = True
            if msg["type"] == "state":
                got_state = True
                assert msg["status"] == "running"
                break

        assert got_state, "Expected state message"

        # Send input and resize (verify they're accepted)
        text = "echo ok\r\n" if sys.platform == "win32" else "echo ok\n"
        encoded = base64.b64encode(text.encode()).decode()
        ws.send_json({"type": "input", "data": encoded})
        ws.send_json({"type": "resize", "rows": 30, "cols": 100})
        time.sleep(0.5)

    # 4. Get scrollback
    resp = test_client.get(
        f"/api/sessions/{session_id}/scrollback?lines=100",
        headers=auth,
    )
    assert resp.status_code == 200
    scrollback = resp.json()
    assert scrollback["session_id"] == session_id
    assert isinstance(scrollback["lines"], list)

    # 5. Stop session
    resp = test_client.delete(f"/api/sessions/{session_id}", headers=auth)
    assert resp.status_code == 204

    # 6. Verify session is gone
    resp = test_client.get("/api/sessions", headers=auth)
    assert resp.status_code == 200
    assert len(resp.json()) == 0

    # 7. Health check
    resp = test_client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["sessions"] == 0


@pytest.mark.timeout(15)
def test_refresh_token_rotation(test_client):
    """Verify refresh token rotation works end-to-end."""
    # Login
    resp = test_client.post(
        "/api/auth/token",
        json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    assert resp.status_code == 200
    refresh_token = resp.json()["refresh_token"]

    # Refresh
    resp = test_client.post(
        "/api/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert resp.status_code == 200
    new_access = resp.json()["access_token"]

    # Use new access token
    resp = test_client.get(
        "/api/sessions",
        headers={"Authorization": f"Bearer {new_access}"},
    )
    assert resp.status_code == 200
