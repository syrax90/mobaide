"""Tests for WebSocket session list broadcast endpoint."""
from __future__ import annotations

import json
import threading

import pytest
from starlette.testclient import TestClient

from tests.conftest import TEST_PASSWORD, TEST_USERNAME


@pytest.fixture
def test_client(app):
    return TestClient(app)


@pytest.fixture
def auth_token(app) -> str:
    return app.state.jwt_service.create_access_token(TEST_USERNAME)


class TestSessionListWs:
    def test_receives_initial_empty_list(self, test_client, auth_token):
        with test_client.websocket_connect(
            f"/ws/sessions?token={auth_token}",
        ) as ws:
            msg = ws.receive_json()
            assert isinstance(msg, list)
            assert len(msg) == 0

    def test_receives_update_on_session_create(self, test_client, auth_token):
        with test_client.websocket_connect(
            f"/ws/sessions?token={auth_token}",
        ) as ws:
            # Initial empty list
            msg = ws.receive_json()
            assert isinstance(msg, list)
            assert len(msg) == 0

            # Create a session via REST (from another thread since TestClient blocks)
            def create_session():
                resp = test_client.post(
                    "/api/sessions",
                    headers={"Authorization": f"Bearer {auth_token}"},
                    json={},
                )
                assert resp.status_code == 201

            t = threading.Thread(target=create_session)
            t.start()
            t.join(timeout=10)

            # Should receive an update with 1 session
            msg = ws.receive_json()
            assert isinstance(msg, list)
            assert len(msg) == 1
            assert msg[0]["state"] == "running"
