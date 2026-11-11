"""Tests for WebSocket session list broadcast endpoint."""
from __future__ import annotations

import json
import threading

import pytest
from starlette.testclient import TestClient

from tests.conftest import TEST_USERNAME


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

    def test_two_concurrent_subscribers_receive_same_broadcast(
        self, test_client, auth_token,
    ):
        """Two /ws/sessions clients connected at the same time both receive
        the initial empty snapshot and the same broadcast when a session is
        created.
        """
        with test_client.websocket_connect(
            f"/ws/sessions?token={auth_token}",
        ) as ws_a:
            with test_client.websocket_connect(
                f"/ws/sessions?token={auth_token}",
            ) as ws_b:
                # Initial empty snapshots for both.
                snap_a = ws_a.receive_json()
                snap_b = ws_b.receive_json()
                assert snap_a == []
                assert snap_b == []

                # Create a session from a worker thread.
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

                # Both clients must observe an update containing the new session.
                # The broadcaster emits raw text frames; session_list handler
                # forwards them as text, so receive_text + json.loads is the
                # reliable read path here.
                msg_a = json.loads(ws_a.receive_text())
                msg_b = json.loads(ws_b.receive_text())
                assert isinstance(msg_a, list)
                assert isinstance(msg_b, list)
                assert len(msg_a) == 1
                assert len(msg_b) == 1
                assert msg_a[0]["session_id"] == msg_b[0]["session_id"]
                assert msg_a[0]["state"] in {"starting", "running"}

    def test_unsubscribe_on_disconnect(self, test_client, auth_token, app):
        """Closing the websocket must remove the subscriber from the
        broadcaster's internal set.
        """
        broadcaster = app.state.broadcaster
        baseline = len(broadcaster._subscribers)

        with test_client.websocket_connect(
            f"/ws/sessions?token={auth_token}",
        ) as ws:
            # Drain initial snapshot to ensure the handler has subscribed.
            ws.receive_json()
            assert len(broadcaster._subscribers) == baseline + 1

        # After disconnect, the finally-block must have unsubscribed the queue.
        assert len(broadcaster._subscribers) == baseline
