"""Tests for the unified ``/ws/events`` WebSocket channel."""
from __future__ import annotations

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


class TestWsEventsSnapshots:
    def test_receives_initial_snapshots(
        self, test_client, auth_token, app,
    ):
        """Post-CLA-39: autoscan_manager is always wired in, so the handler
        sends three snapshot envelopes on connect:
          1. sessions.changed
          2. projects.changed
          3. autoscan.list.changed
        """
        assert app.state.autoscan_manager is not None

        with test_client.websocket_connect(
            f"/ws/events?token={auth_token}",
        ) as ws:
            first = ws.receive_json()
            second = ws.receive_json()
            third = ws.receive_json()

            assert first["type"] == "sessions.changed"
            assert "sessions" in first["payload"]
            assert isinstance(first["payload"]["sessions"], list)
            assert first["payload"]["sessions"] == []

            assert second["type"] == "projects.changed"
            assert "projects" in second["payload"]
            assert isinstance(second["payload"]["projects"], list)

            assert third["type"] == "autoscan.list.changed"
            assert "scans" in third["payload"]
            assert third["payload"]["available_projects"] == []

    def test_connect_without_token_rejected(self, test_client, auth_token):
        """Missing token closes 4001 before accept(); connect raises."""
        with pytest.raises(Exception):
            with test_client.websocket_connect("/ws/events") as ws:
                ws.receive_json()

        # A subsequent valid connect still works (no shared state leak).
        with test_client.websocket_connect(
            f"/ws/events?token={auth_token}",
        ) as ws:
            msg = ws.receive_json()
            assert msg["type"] == "sessions.changed"

    def test_connect_with_invalid_token_rejected(self, test_client):
        with pytest.raises(Exception):
            with test_client.websocket_connect(
                "/ws/events?token=not.a.real.jwt",
            ) as ws:
                ws.receive_json()


class TestWsEventsLiveForwarding:
    def test_forwards_published_event_after_snapshots(
        self, test_client, auth_token,
    ):
        """While connected, creating a session from another thread triggers
        a real ``sessions.changed`` publish via the fanout pump — the client
        must then receive that envelope.
        """
        with test_client.websocket_connect(
            f"/ws/events?token={auth_token}",
        ) as ws:
            # Drain initial snapshots (3 envelopes in test config post-CLA-39).
            snap1 = ws.receive_json()
            snap2 = ws.receive_json()
            snap3 = ws.receive_json()
            assert snap1["type"] == "sessions.changed"
            assert snap2["type"] == "projects.changed"
            assert snap3["type"] == "autoscan.list.changed"

            # Trigger a real event publish by creating a session from a worker
            # thread (TestClient blocks the current thread on receive_json).
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

            # Read forwarded events until we see sessions.changed with >=1 session.
            # The pump may also publish session.updated — just look for what
            # we care about within a bounded number of envelopes.
            saw_sessions_changed_with_one = False
            for _ in range(8):
                envelope = ws.receive_json()
                assert "type" in envelope
                assert "payload" in envelope
                if (
                    envelope["type"] == "sessions.changed"
                    and len(envelope["payload"].get("sessions", [])) >= 1
                ):
                    session_dict = envelope["payload"]["sessions"][0]
                    assert "session_id" in session_dict
                    assert session_dict["state"] in {"starting", "running"}
                    saw_sessions_changed_with_one = True
                    break
            assert saw_sessions_changed_with_one, (
                "expected sessions.changed with >=1 session after POST /api/sessions"
            )

    def test_unsubscribe_on_disconnect(self, test_client, auth_token, app):
        """Subscriber count must return to its pre-connect value after the
        ``with`` block exits.
        """
        bus = app.state.event_bus
        baseline = bus.subscriber_count

        with test_client.websocket_connect(
            f"/ws/events?token={auth_token}",
        ) as ws:
            # Drain initial snapshots so we know the handler has subscribed.
            ws.receive_json()
            ws.receive_json()
            ws.receive_json()
            # While connected, the bus has exactly one more subscriber.
            assert bus.subscriber_count == baseline + 1

        # After disconnect, the finally-block must have run bus.unsubscribe(q).
        assert bus.subscriber_count == baseline

    def test_two_concurrent_events_subscribers(self, test_client, auth_token, app):
        """Two /ws/events clients connected at the same time both receive
        initial snapshots without interfering with each other.
        """
        bus = app.state.event_bus
        baseline = bus.subscriber_count

        with test_client.websocket_connect(
            f"/ws/events?token={auth_token}",
        ) as ws_a:
            with test_client.websocket_connect(
                f"/ws/events?token={auth_token}",
            ) as ws_b:
                # Drain snapshots for both.
                a1 = ws_a.receive_json()
                a2 = ws_a.receive_json()
                a3 = ws_a.receive_json()
                b1 = ws_b.receive_json()
                b2 = ws_b.receive_json()
                b3 = ws_b.receive_json()

                assert a1["type"] == "sessions.changed"
                assert a2["type"] == "projects.changed"
                assert a3["type"] == "autoscan.list.changed"
                assert b1["type"] == "sessions.changed"
                assert b2["type"] == "projects.changed"
                assert b3["type"] == "autoscan.list.changed"

                # Both clients should be live subscribers on the bus.
                assert bus.subscriber_count == baseline + 2

        # Both finally-blocks have run by the time both with-blocks exit.
        assert bus.subscriber_count == baseline
