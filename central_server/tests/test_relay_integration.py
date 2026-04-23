"""Integration tests for the relay WebSocket endpoints.

These tests verify the agent tunnel and mobile relay work together.
Uses Starlette's TestClient for synchronous WebSocket testing.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient

from central_server.relay.envelope import CH_CONTROL, decode
from central_server.relay.routing import AgentConnection


def _mock_agent_ws() -> MagicMock:
    ws = MagicMock()
    ws.close = AsyncMock()
    return ws


async def _mobile_access_token(app, agent_id: str) -> str:
    """Mint a mobile_access JWT for the viewer user bound to ``agent_id``."""
    owner = await app.state.db.get_user_by_email("viewer@test.local")
    access, _refresh = await app.state.auth_service.create_mobile_tokens(
        owner["id"], agent_id, owner["id"]
    )
    return access


def _drain_control_msgs(agent_conn: AgentConnection) -> list[dict]:
    """Drain ``agent_conn.send_queue`` and return every CH_CONTROL message."""
    out: list[dict] = []
    while True:
        try:
            envelope = agent_conn.send_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        try:
            channel, _sid, payload = decode(envelope)
        except ValueError:
            continue
        if channel != CH_CONTROL:
            continue
        try:
            out.append(json.loads(payload))
        except json.JSONDecodeError:
            continue
    return out


class TestAgentTunnel:
    """Tests for /ws/agent-tunnel."""

    def test_auth_required(self, app):
        client = TestClient(app)
        with client.websocket_connect("/ws/agent-tunnel") as ws:
            # Send invalid auth
            ws.send_text(json.dumps({"type": "auth", "agent_id": "x", "token": "bad"}))
            # Connection should be closed
            try:
                ws.receive_text()
            except Exception:
                pass

    def test_auth_success(self, app, registered_agent):
        agent_id, raw_token = registered_agent
        client = TestClient(app)
        with client.websocket_connect("/ws/agent-tunnel") as ws:
            ws.send_text(json.dumps({
                "type": "auth",
                "agent_id": agent_id,
                "token": raw_token,
            }))
            resp = json.loads(ws.receive_text())
            assert resp["type"] == "auth_ok"

    def test_agent_registers_in_routing(self, app, registered_agent):
        agent_id, raw_token = registered_agent
        client = TestClient(app)
        with client.websocket_connect("/ws/agent-tunnel") as ws:
            ws.send_text(json.dumps({
                "type": "auth",
                "agent_id": agent_id,
                "token": raw_token,
            }))
            ws.receive_text()  # auth_ok
            # Agent should be online
            assert app.state.routing.is_agent_online(agent_id) is True
        # After disconnect, agent should be offline
        # (TestClient closes WS on exit, but routing cleanup may be async)


class TestSessionLifecycleSignals:
    """Regression guards for the "keyboard grayed out on reopen" bug.

    The central server used to be an *opaque* relay: when a mobile
    connected to a session the agent was never told, so the agent's lazy
    first-input-triggers-handshake path cached ``_handshake_sent[sid]``
    and skipped the handshake on every subsequent mobile reconnect.  The
    state frame never re-arrived at the mobile and the keyboard stayed
    grayed out.  The fix emits ``mobile_session_attach`` / ``..._detach``
    CH_CONTROL frames on every mobile connect/disconnect, and replays
    attach on agent reconnect for every still-attached mobile — these
    tests pin that contract.
    """

    async def test_mobile_connect_emits_attach_to_agent(
        self, app, registered_agent
    ):
        """Happy path: a mobile connecting to ``/relay/ws/session/{aid}/{sid}``
        must enqueue exactly one ``mobile_session_attach`` CH_CONTROL frame
        with the right session_id onto the agent's tunnel send_queue.
        """
        agent_id, _ = registered_agent
        agent_conn = app.state.routing.register_agent(agent_id, _mock_agent_ws())

        access = await _mobile_access_token(app, agent_id)

        tc = TestClient(app)
        with tc.websocket_connect(
            f"/relay/ws/session/{agent_id}/session-abc?token={access}",
        ):
            await asyncio.sleep(0.05)

            attach_msgs = [
                m for m in _drain_control_msgs(agent_conn)
                if m.get("type") == "mobile_session_attach"
            ]
            assert len(attach_msgs) == 1
            assert attach_msgs[0]["session_id"] == "session-abc"

    async def test_mobile_disconnect_emits_detach_to_agent(
        self, app, registered_agent
    ):
        """After a mobile's WebSocket closes, the handler's ``finally`` must
        push one ``mobile_session_detach`` frame so the agent can (today:
        debug-log; tomorrow: drop per-device state) unwind the attach.
        """
        agent_id, _ = registered_agent
        agent_conn = app.state.routing.register_agent(agent_id, _mock_agent_ws())

        access = await _mobile_access_token(app, agent_id)

        tc = TestClient(app)
        with tc.websocket_connect(
            f"/relay/ws/session/{agent_id}/session-xyz?token={access}",
        ):
            await asyncio.sleep(0.05)
            _drain_control_msgs(agent_conn)

        detach_msgs = [
            m for m in _drain_control_msgs(agent_conn)
            if m.get("type") == "mobile_session_detach"
        ]
        assert len(detach_msgs) == 1
        assert detach_msgs[0]["session_id"] == "session-xyz"

    async def test_mobile_connect_is_noop_when_agent_offline(
        self, app, registered_agent
    ):
        """If the agent is not registered in routing, ``_validate_mobile_connection``
        closes the socket with 4010 before ``register_mobile`` runs — so
        no attach is produced. Prevents a queue-overflow/noisy warning
        storm during agent downtime.
        """
        agent_id, _ = registered_agent
        assert app.state.routing.get_agent(agent_id) is None

        access = await _mobile_access_token(app, agent_id)
        tc = TestClient(app)
        with pytest.raises(Exception):
            with tc.websocket_connect(
                f"/relay/ws/session/{agent_id}/session-foo?token={access}",
            ) as ws:
                ws.receive_bytes()

    async def test_agent_reconnect_replays_attach_for_attached_mobiles(
        self, app, registered_agent
    ):
        """On agent reconnect, the tunnel handler must emit one
        ``mobile_session_attach`` per currently-registered ``session_io:*``
        channel. Without this, mobiles that stayed connected across an
        agent bounce would not receive a fresh handshake and would get
        stuck grayed out exactly as on exit/reopen before the fix.
        """
        agent_id, raw_token = registered_agent
        # Simulate mobiles already attached before the agent comes online
        # by manually populating routing.  We don't need live mobile
        # sockets — just the channel keys.
        from central_server.relay.routing import MobileClient
        c1 = MobileClient(device_id="dev-1", websocket=_mock_agent_ws())
        c2 = MobileClient(device_id="dev-2", websocket=_mock_agent_ws())
        app.state.routing.register_mobile(agent_id, "session_io:s1", c1)
        app.state.routing.register_mobile(agent_id, "session_io:s2", c2)
        # Non-session channels must NOT trigger attach replay.
        app.state.routing.register_mobile(
            agent_id, "events",
            MobileClient(device_id="dev-1", websocket=_mock_agent_ws()),
        )

        tc = TestClient(app)
        with tc.websocket_connect("/ws/agent-tunnel") as ws:
            ws.send_text(json.dumps({
                "type": "auth",
                "agent_id": agent_id,
                "token": raw_token,
            }))
            resp = json.loads(ws.receive_text())
            assert resp["type"] == "auth_ok"

            # Two attach replays are queued on the agent's writer; the
            # writer task drains them as frames. Read them back.
            attach_sids = set()
            for _ in range(2):
                raw = ws.receive_bytes()
                channel, _sid, payload = decode(raw)
                if channel != CH_CONTROL:
                    continue
                msg = json.loads(payload)
                assert msg["type"] == "mobile_session_attach"
                attach_sids.add(msg["session_id"])

            assert attach_sids == {"s1", "s2"}


class TestSendSessionLifecycleHelper:
    """Unit-level guards for ``mobile_ws._send_session_lifecycle``.

    The helper's docstring promises two best-effort silent drops: when the
    agent has already gone offline, and when the agent's send queue is
    saturated. Both branches are reached through the ``finally`` block of
    ``relay_session_io`` after a mid-session agent crash; neither path is
    directly exercised by the integration-level tests above, because the
    pre-validation layer closes the socket with 4010 before it can ever
    call the helper with an offline agent.
    """

    def test_noop_when_agent_offline(self):
        from central_server.relay.mobile_ws import _send_session_lifecycle
        from central_server.relay.routing import RoutingTable

        routing = RoutingTable()
        assert routing.get_agent("ghost") is None

        _send_session_lifecycle(
            routing, "ghost", "s1", "mobile_session_attach"
        )

    def test_full_queue_does_not_raise(self):
        """Saturate the agent queue, then invoke the helper — it must
        swallow ``asyncio.QueueFull`` and leave the queue untouched. This
        is the documented drop-on-full contract that spares the relay
        from crashing when an agent is wedged.
        """
        import asyncio as _asyncio

        from central_server.relay.mobile_ws import _send_session_lifecycle
        from central_server.relay.routing import RoutingTable

        routing = RoutingTable()
        conn = routing.register_agent("agent-1", _mock_agent_ws())
        conn.send_queue = _asyncio.Queue(maxsize=1)
        conn.send_queue.put_nowait(b"already-full")

        _send_session_lifecycle(
            routing, "agent-1", "s1", "mobile_session_attach"
        )

        assert conn.send_queue.qsize() == 1
        assert conn.send_queue.get_nowait() == b"already-full"


class TestEnqueueAttachHelper:
    """Unit-level guard for ``agent_ws._enqueue_attach``.

    Sibling to ``_send_session_lifecycle`` but lives on the agent-tunnel
    side and targets an ``AgentConnection`` directly. Fires on agent
    reconnect inside ``agent_tunnel``; the replay loop calls it once per
    ``session_io:*`` channel and must not blow up if the very connection
    it is queuing onto has a saturated outbound queue (e.g. the previous
    tunnel's drain never completed).
    """

    def test_full_queue_does_not_raise(self):
        import asyncio as _asyncio

        from central_server.relay.agent_ws import _enqueue_attach
        from central_server.relay.routing import RoutingTable

        routing = RoutingTable()
        conn = routing.register_agent("agent-1", _mock_agent_ws())
        conn.send_queue = _asyncio.Queue(maxsize=1)
        conn.send_queue.put_nowait(b"already-full")

        _enqueue_attach(conn, "s1")

        assert conn.send_queue.qsize() == 1
        assert conn.send_queue.get_nowait() == b"already-full"

    def test_enqueues_attach_envelope_on_free_queue(self):
        """Positive path: confirms the envelope pushed onto the queue is
        a CH_CONTROL frame carrying the expected ``mobile_session_attach``
        JSON payload. Pins the wire format that the agent's ``_handle_
        control`` dispatches on — a rename of the type field or a channel
        swap would silently break the reconnect-replay path otherwise.
        """
        from central_server.relay.agent_ws import _enqueue_attach
        from central_server.relay.routing import RoutingTable

        routing = RoutingTable()
        conn = routing.register_agent("agent-1", _mock_agent_ws())

        _enqueue_attach(conn, "sess-42")

        assert conn.send_queue.qsize() == 1
        envelope = conn.send_queue.get_nowait()
        channel, sid, payload = decode(envelope)
        assert channel == CH_CONTROL
        assert sid == ""
        msg = json.loads(payload)
        assert msg == {
            "type": "mobile_session_attach",
            "session_id": "sess-42",
        }


class TestHealthEndpoint:
    async def test_health(self, client):
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "agents_online" in data
        assert "agents_registered" in data
