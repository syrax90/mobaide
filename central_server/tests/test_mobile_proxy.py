"""Tests for the MobileProxy request-response correlator and the proxy_to_agent route."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from central_server.relay.mobile_proxy import MobileProxy, router
from central_server.relay.routing import AgentConnection, RoutingTable


# ---------------------------------------------------------------------------
# MobileProxy unit tests
# ---------------------------------------------------------------------------


@pytest.fixture
def routing() -> RoutingTable:
    return RoutingTable()


def _mock_ws() -> MagicMock:
    ws = MagicMock()
    ws.close = AsyncMock()
    return ws


@pytest.fixture
def proxy(routing: RoutingTable) -> MobileProxy:
    return MobileProxy(routing)


class TestMobileProxyForward:
    """Tests for MobileProxy.forward() — request-response correlation."""

    async def test_forward_happy_path(self, proxy: MobileProxy, routing: RoutingTable):
        ws = _mock_ws()
        routing.register_agent("agent-1", ws)

        async def resolve_soon():
            await asyncio.sleep(0.01)
            # Find the pending request_id and resolve it
            assert len(proxy._pending) == 1
            request_id = next(iter(proxy._pending))
            proxy.resolve(request_id, {"status": 200, "body": {"sessions": []}})

        result_task = asyncio.create_task(
            proxy.forward("agent-1", "GET", "/api/sessions", timeout=2.0)
        )
        resolve_task = asyncio.create_task(resolve_soon())

        result = await result_task
        await resolve_task

        assert result["status"] == 200
        assert result["body"] == {"sessions": []}

    async def test_forward_sends_control_envelope_to_agent_queue(
        self, proxy: MobileProxy, routing: RoutingTable
    ):
        ws = _mock_ws()
        conn = routing.register_agent("agent-1", ws)

        async def resolve_after_checking():
            await asyncio.sleep(0.01)
            # The envelope should be on the agent's send_queue
            envelope = conn.send_queue.get_nowait()
            assert isinstance(envelope, bytes)
            # Resolve
            request_id = next(iter(proxy._pending))
            proxy.resolve(request_id, {"status": 200, "body": {}})

        task = asyncio.create_task(
            proxy.forward("agent-1", "POST", "/api/sessions", body={"name": "test"}, timeout=2.0)
        )
        check_task = asyncio.create_task(resolve_after_checking())

        result = await task
        await check_task
        assert result["status"] == 200

    async def test_forward_agent_offline_raises_503(self, proxy: MobileProxy):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await proxy.forward("nonexistent-agent", "GET", "/api/sessions")
        assert exc_info.value.status_code == 503
        assert "offline" in exc_info.value.detail.lower()

    async def test_forward_timeout_raises_504(self, proxy: MobileProxy, routing: RoutingTable):
        from fastapi import HTTPException

        ws = _mock_ws()
        routing.register_agent("agent-1", ws)

        with pytest.raises(HTTPException) as exc_info:
            await proxy.forward("agent-1", "GET", "/api/sessions", timeout=0.05)
        assert exc_info.value.status_code == 504
        assert "not respond" in exc_info.value.detail.lower()

    async def test_forward_cleans_up_pending_on_timeout(
        self, proxy: MobileProxy, routing: RoutingTable
    ):
        from fastapi import HTTPException

        ws = _mock_ws()
        routing.register_agent("agent-1", ws)

        with pytest.raises(HTTPException):
            await proxy.forward("agent-1", "GET", "/api/sessions", timeout=0.05)
        # After timeout, pending dict should be empty
        assert len(proxy._pending) == 0

    async def test_forward_includes_query_and_body(
        self, proxy: MobileProxy, routing: RoutingTable
    ):
        ws = _mock_ws()
        conn = routing.register_agent("agent-1", ws)

        async def check_and_resolve():
            await asyncio.sleep(0.01)
            envelope = conn.send_queue.get_nowait()
            # Decode the envelope: skip 1 byte channel + 1 byte sid_len + sid bytes
            ch = envelope[0]
            sid_len = envelope[1]
            payload_bytes = envelope[2 + sid_len:]
            ctrl = json.loads(payload_bytes)
            assert ctrl["type"] == "proxy_request"
            assert ctrl["method"] == "POST"
            assert ctrl["path"] == "/api/sessions"
            assert ctrl["query"] == {"project": "myproject"}
            assert ctrl["body"] == {"name": "test-session"}
            proxy.resolve(ctrl["request_id"], {"status": 201, "body": {"id": "s1"}})

        task = asyncio.create_task(
            proxy.forward(
                "agent-1", "POST", "/api/sessions",
                query={"project": "myproject"},
                body={"name": "test-session"},
                timeout=2.0,
            )
        )
        check = asyncio.create_task(check_and_resolve())

        result = await task
        await check
        assert result["status"] == 201

    async def test_forward_omits_query_and_body_when_none(
        self, proxy: MobileProxy, routing: RoutingTable
    ):
        ws = _mock_ws()
        conn = routing.register_agent("agent-1", ws)

        async def check_and_resolve():
            await asyncio.sleep(0.01)
            envelope = conn.send_queue.get_nowait()
            ch = envelope[0]
            sid_len = envelope[1]
            payload_bytes = envelope[2 + sid_len:]
            ctrl = json.loads(payload_bytes)
            assert "query" not in ctrl
            assert "body" not in ctrl
            proxy.resolve(ctrl["request_id"], {"status": 200, "body": None})

        task = asyncio.create_task(
            proxy.forward("agent-1", "GET", "/api/sessions", timeout=2.0)
        )
        check = asyncio.create_task(check_and_resolve())

        await task
        await check


class TestMobileProxyResolve:
    """Tests for MobileProxy.resolve() — Future resolution."""

    async def test_resolve_unknown_request_id_is_noop(self, proxy: MobileProxy):
        # Should not raise
        proxy.resolve("nonexistent-id", {"status": 200, "body": {}})

    async def test_resolve_already_done_future_is_noop(
        self, proxy: MobileProxy, routing: RoutingTable
    ):
        ws = _mock_ws()
        routing.register_agent("agent-1", ws)

        async def resolve_twice():
            await asyncio.sleep(0.01)
            request_id = next(iter(proxy._pending))
            proxy.resolve(request_id, {"status": 200, "body": {"first": True}})
            # Second resolve should be a no-op, not raise
            proxy.resolve(request_id, {"status": 500, "body": {"second": True}})

        task = asyncio.create_task(
            proxy.forward("agent-1", "GET", "/api/sessions", timeout=2.0)
        )
        resolve_task = asyncio.create_task(resolve_twice())

        result = await task
        await resolve_task
        # First resolution wins
        assert result["body"]["first"] is True


# ---------------------------------------------------------------------------
# proxy_to_agent endpoint integration tests
# ---------------------------------------------------------------------------


class TestProxyEndpoint:
    """Integration tests for the /proxy/{agent_id}/{path} catch-all route.

    Uses the shared conftest app/client fixtures which provide a full
    FastAPI app with initialized state.
    """

    async def test_proxy_requires_auth(self, client):
        # No Authorization header → 401
        resp = await client.get("/proxy/agent-1/api/sessions")
        assert resp.status_code == 401

    async def test_proxy_agent_id_mismatch_returns_403(self, client, app, registered_agent):
        agent_id, _ = registered_agent
        # Create a mobile token for this agent
        raw_token, _ = await app.state.pairing_service.create_pairing_token(agent_id)
        pair_resp = await client.post(
            "/api/pairing/complete",
            json={"token": raw_token, "device_id": "dev-1"},
        )
        access_token = pair_resp.json()["access_token"]

        # Try to proxy to a different agent
        resp = await client.get(
            "/proxy/wrong-agent-id/api/sessions",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert resp.status_code == 403
        assert "not valid for this agent" in resp.json()["detail"].lower()

    async def test_proxy_unpaired_device_returns_403(self, client, app, registered_agent):
        agent_id, _ = registered_agent
        # Register a second agent and pair device to it
        agent_id2, _ = await app.state.db.register_agent("other-agent")
        raw_token, _ = await app.state.pairing_service.create_pairing_token(agent_id2)
        pair_resp = await client.post(
            "/api/pairing/complete",
            json={"token": raw_token, "device_id": "dev-1"},
        )
        access_token = pair_resp.json()["access_token"]

        # The token is for agent_id2, but even if we could forge agent_id
        # in the path, the pairing check would fail. Let's hit agent_id2
        # path but with dev-1 NOT paired to agent_id (the first one).
        # Actually, dev-1 IS paired to agent_id2, so let's create a
        # different scenario: pair dev-1 to agent-1, revoke, then try.
        raw_token2, _ = await app.state.pairing_service.create_pairing_token(agent_id)
        pair_resp2 = await client.post(
            "/api/pairing/complete",
            json={"token": raw_token2, "device_id": "dev-2"},
        )
        access_token2 = pair_resp2.json()["access_token"]

        # Revoke the pairing
        pairings = await app.state.pairing_service.list_pairings(agent_id)
        for p in pairings:
            await app.state.pairing_service.revoke_pairing(p["pairing_id"])

        # Now dev-2 is no longer paired to agent_id
        resp = await client.get(
            f"/proxy/{agent_id}/api/sessions",
            headers={"Authorization": f"Bearer {access_token2}"},
        )
        assert resp.status_code == 403
        assert "not paired" in resp.json()["detail"].lower()

    async def test_proxy_forwards_to_agent_and_returns_response(
        self, client, app, registered_agent
    ):
        agent_id, _ = registered_agent
        # Set up pairing
        raw_token, _ = await app.state.pairing_service.create_pairing_token(agent_id)
        pair_resp = await client.post(
            "/api/pairing/complete",
            json={"token": raw_token, "device_id": "dev-1"},
        )
        access_token = pair_resp.json()["access_token"]

        # The conftest app fixture bypasses the lifespan, so mobile_proxy
        # is not wired.  Install it manually from the routing table.
        app.state.mobile_proxy = MobileProxy(app.state.routing)

        # Register the agent in the routing table (simulating a connected agent)
        ws = _mock_ws()
        app.state.routing.register_agent(agent_id, ws)

        # Mock the mobile_proxy.forward to return a canned response
        async def mock_forward(agent_id, method, path, query=None, body=None, timeout=30.0):
            return {"status": 200, "body": {"sessions": [{"id": "s1"}]}}

        app.state.mobile_proxy.forward = mock_forward

        resp = await client.get(
            f"/proxy/{agent_id}/api/sessions",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["sessions"][0]["id"] == "s1"
