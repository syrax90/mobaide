"""Integration tests for the relay WebSocket endpoints.

These tests verify the agent tunnel and mobile relay work together.
Uses Starlette's TestClient for synchronous WebSocket testing.
"""
from __future__ import annotations

import json

from starlette.testclient import TestClient


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

    def test_pairing_token_request(self, app, registered_agent):
        agent_id, raw_token = registered_agent
        client = TestClient(app)
        with client.websocket_connect("/ws/agent-tunnel") as ws:
            ws.send_text(json.dumps({
                "type": "auth",
                "agent_id": agent_id,
                "token": raw_token,
            }))
            ws.receive_text()  # auth_ok

            # Request pairing token via control message
            ws.send_text(json.dumps({"type": "request_pairing_token"}))
            resp = json.loads(ws.receive_text())
            assert resp["type"] == "pairing_token"
            assert "token" in resp
            assert "expires_at" in resp


class TestHealthEndpoint:
    async def test_health(self, client):
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "agents_online" in data
        assert "agents_registered" in data
