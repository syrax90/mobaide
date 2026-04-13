"""Integration test: full auth → create → WS connect → scrollback → stop flow."""
from __future__ import annotations

import json
import struct
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

    # 3. Connect WebSocket (binary protocol) and verify I/O channel works
    with test_client.websocket_connect(
        f"/ws/session/{session_id}?token={access_token}",
    ) as ws:
        # First frame: state
        first = ws.receive_bytes()
        assert first[:1] == b"s"
        state_payload = json.loads(first[1:].decode("utf-8"))
        assert state_payload["status"] in ("starting", "running")

        # Second frame: replay marker
        marker = ws.receive_bytes()
        assert marker == b"b"

        # Send input (CR-terminated) and resize as binary frames.
        text = b"echo ok\r\n" if sys.platform == "win32" else b"echo ok\r"
        ws.send_bytes(b"i" + text)
        ws.send_bytes(b"r" + struct.pack(">HH", 30, 100))
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
