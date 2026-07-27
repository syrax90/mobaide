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
            envelope = conn.send_queue.get_nowait()
            assert isinstance(envelope, bytes)
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
        assert len(proxy._pending) == 0

    async def test_forward_includes_query_and_body(
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
            proxy.resolve(request_id, {"status": 500, "body": {"second": True}})

        task = asyncio.create_task(
            proxy.forward("agent-1", "GET", "/api/sessions", timeout=2.0)
        )
        resolve_task = asyncio.create_task(resolve_twice())

        result = await task
        await resolve_task
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
        resp = await client.get("/proxy/agent-1/api/sessions")
        assert resp.status_code == 401

    async def test_proxy_agent_id_mismatch_returns_403(self, client, app, registered_agent):
        agent_id, _ = registered_agent
        # Get mobile tokens directly (no QR pairing)
        owner = await app.state.db.get_user_by_email("viewer@test.local")
        access, _ = await app.state.auth_service.create_mobile_tokens(
            owner["id"], agent_id, owner["id"]
        )
        # Try to proxy to a different agent
        resp = await client.get(
            "/proxy/wrong-agent-id/api/sessions",
            headers={"Authorization": f"Bearer {access}"},
        )
        assert resp.status_code == 403
        assert "not valid for this agent" in resp.json()["detail"].lower()

    async def test_proxy_non_owner_returns_403(self, client, app, registered_agent):
        """A user who doesn't own the agent gets 403 on the ownership check."""
        agent_id, _ = registered_agent  # owned by viewer
        # Create a different user who does NOT own agent_id
        other_user_id = await app.state.db.create_user(
            email="other@test.local", password_hash="h"
        )
        # Create a mobile token where user_id=other_user but agent_id=viewer's agent
        # This simulates a token that passes the agent_id match but fails ownership
        access, _ = await app.state.auth_service.create_mobile_tokens(
            other_user_id, agent_id, other_user_id
        )
        app.state.mobile_proxy = MobileProxy(app.state.routing)

        resp = await client.get(
            f"/proxy/{agent_id}/api/sessions",
            headers={"Authorization": f"Bearer {access}"},
        )
        assert resp.status_code == 403
        assert "does not own" in resp.json()["detail"].lower()

    async def test_proxy_forwards_to_agent_and_returns_response(
        self, client, app, registered_agent
    ):
        agent_id, _ = registered_agent
        # Get mobile tokens directly
        owner = await app.state.db.get_user_by_email("viewer@test.local")
        access, _ = await app.state.auth_service.create_mobile_tokens(
            owner["id"], agent_id, owner["id"]
        )

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
            headers={"Authorization": f"Bearer {access}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["sessions"][0]["id"] == "s1"

    async def test_proxy_patch_forwards_method_and_body(
        self, client, app, registered_agent
    ):
        agent_id, _ = registered_agent
        owner = await app.state.db.get_user_by_email("viewer@test.local")
        access, _ = await app.state.auth_service.create_mobile_tokens(
            owner["id"], agent_id, owner["id"]
        )

        app.state.mobile_proxy = MobileProxy(app.state.routing)

        ws = _mock_ws()
        app.state.routing.register_agent(agent_id, ws)

        captured: dict = {}

        async def mock_forward(agent_id, method, path, query=None, body=None, timeout=30.0):
            captured["method"] = method
            captured["path"] = path
            captured["body"] = body
            return {"status": 200, "body": [{"name": "b"}, {"name": "a"}]}

        app.state.mobile_proxy.forward = mock_forward

        resp = await client.patch(
            f"/proxy/{agent_id}/api/projects/order",
            json={"names": ["b", "a"]},
            headers={"Authorization": f"Bearer {access}"},
        )
        assert resp.status_code == 200
        assert captured["method"] == "PATCH"
        assert captured["path"] == "/api/projects/order"
        assert captured["body"] == {"names": ["b", "a"]}

    async def test_proxy_route_accepts_all_agent_methods(self):
        """Every HTTP method used by agent REST routes is accepted by the proxy.

        Inspects the proxy router's own routes rather than ``app.routes``:
        newer FastAPI wraps included routers in path-less ``_IncludedRouter``
        objects, so the catch-all APIRoute is not visible on the app directly.
        """
        for route in router.routes:
            if getattr(route, "path", None) == "/proxy/{agent_id}/{path:path}":
                methods = getattr(route, "methods", set())
                assert methods >= {"GET", "POST", "PUT", "PATCH", "DELETE"}
                break
        else:
            pytest.fail("proxy catch-all route not found on the router")


class TestSessionCreateInterception:
    """The relay's strip-then-inject on ``POST /proxy/{agent_id}/api/sessions``.

    ``resolved_command`` / ``resolved_name`` are popped from every client
    dict body on this route; for a valid ``custom_<12hex>`` ``cli`` the
    stored command/name are looked up in the caller's registry and
    injected. Failures answer without forwarding: malformed id → 400,
    unknown/deleted/foreign id → 404, agent handshake protocol version
    NULL or < 7 → 409 with the ``agent_update_required`` marker.
    """

    async def _setup(
        self, app, registered_agent, proto: int | None = 7
    ) -> tuple[str, dict, dict[str, str], dict]:
        """Wire tokens, routing, a capturing forward; persist agent proto.

        ``proto=None`` leaves the freshly-registered agent's handshake
        column NULL (a pre-v7 agent that never reconnected).
        """
        agent_id, _ = registered_agent
        owner = await app.state.db.get_user_by_email("viewer@test.local")
        access, _ = await app.state.auth_service.create_mobile_tokens(
            owner["id"], agent_id, owner["id"]
        )
        if proto is not None:
            await app.state.db.update_agent_handshake_info(
                agent_id, "test-agent-v", proto
            )
        app.state.mobile_proxy = MobileProxy(app.state.routing)
        app.state.routing.register_agent(agent_id, _mock_ws())

        captured: dict = {}

        async def mock_forward(
            agent_id, method, path, query=None, body=None, timeout=30.0
        ):
            captured["method"] = method
            captured["path"] = path
            captured["body"] = body
            return {"status": 201, "body": {"session_id": "s1"}}

        app.state.mobile_proxy.forward = mock_forward
        headers = {"Authorization": f"Bearer {access}"}
        return agent_id, owner, headers, captured

    async def test_custom_cli_injects_stored_command_and_name(
        self, client, app, registered_agent
    ):
        """Happy path: client-supplied ``resolved_*`` are overwritten with
        the registry values; the rest of the body is forwarded intact."""
        agent_id, owner, headers, captured = await self._setup(
            app, registered_agent
        )
        row = await app.state.db.create_custom_agent_type(
            owner["id"], "Aider", "aider --yes"
        )

        resp = await client.post(
            f"/proxy/{agent_id}/api/sessions",
            json={
                "cli": f"custom_{row['id']}",
                "resolved_command": "client-forged",
                "resolved_name": "client-forged",
                "rows": 30,
            },
            headers=headers,
        )

        assert resp.status_code == 201
        assert captured["body"]["resolved_command"] == "aider --yes"
        assert captured["body"]["resolved_name"] == "Aider"
        assert captured["body"]["cli"] == f"custom_{row['id']}"
        assert captured["body"]["rows"] == 30

    async def test_non_custom_cli_strips_resolved_fields(
        self, client, app, registered_agent
    ):
        agent_id, _owner, headers, captured = await self._setup(
            app, registered_agent
        )

        resp = await client.post(
            f"/proxy/{agent_id}/api/sessions",
            json={
                "cli": "claude",
                "resolved_command": "forged",
                "resolved_name": "forged",
            },
            headers=headers,
        )

        assert resp.status_code == 201
        assert "resolved_command" not in captured["body"]
        assert "resolved_name" not in captured["body"]
        assert captured["body"]["cli"] == "claude"

    async def test_unknown_custom_id_returns_404_without_forwarding(
        self, client, app, registered_agent
    ):
        agent_id, _owner, headers, captured = await self._setup(
            app, registered_agent
        )

        resp = await client.post(
            f"/proxy/{agent_id}/api/sessions",
            json={"cli": "custom_" + "a" * 12},
            headers=headers,
        )

        assert resp.status_code == 404
        assert captured == {}

    async def test_deleted_custom_id_returns_404(
        self, client, app, registered_agent
    ):
        agent_id, owner, headers, captured = await self._setup(
            app, registered_agent
        )
        row = await app.state.db.create_custom_agent_type(
            owner["id"], "Gone", "gone-cmd"
        )
        await app.state.db.soft_delete_custom_agent_type(
            row["id"], owner["id"]
        )

        resp = await client.post(
            f"/proxy/{agent_id}/api/sessions",
            json={"cli": f"custom_{row['id']}"},
            headers=headers,
        )

        assert resp.status_code == 404
        assert captured == {}

    async def test_foreign_custom_id_returns_404(
        self, client, app, registered_agent
    ):
        """Another user's type id resolves against the caller's registry
        scope and must look identical to an unknown id."""
        agent_id, _owner, headers, captured = await self._setup(
            app, registered_agent
        )
        other_user = await app.state.db.create_user(
            email="other@test.local", password_hash="hash"
        )
        row = await app.state.db.create_custom_agent_type(
            other_user, "Theirs", "their-cmd"
        )

        resp = await client.post(
            f"/proxy/{agent_id}/api/sessions",
            json={"cli": f"custom_{row['id']}"},
            headers=headers,
        )

        assert resp.status_code == 404
        assert captured == {}

    @pytest.mark.parametrize(
        "bad_cli",
        ["custom_xyz", "custom_" + "a" * 11, "custom_" + "A" * 12, "custom_"],
    )
    async def test_malformed_custom_id_returns_400(
        self, client, app, registered_agent, bad_cli
    ):
        agent_id, _owner, headers, captured = await self._setup(
            app, registered_agent
        )

        resp = await client.post(
            f"/proxy/{agent_id}/api/sessions",
            json={"cli": bad_cli},
            headers=headers,
        )

        assert resp.status_code == 400
        assert captured == {}

    @pytest.mark.parametrize("proto", [None, 6])
    async def test_old_agent_proto_returns_409_update_marker(
        self, client, app, registered_agent, proto
    ):
        agent_id, owner, headers, captured = await self._setup(
            app, registered_agent, proto=proto
        )
        row = await app.state.db.create_custom_agent_type(
            owner["id"], "Aider", "aider"
        )

        resp = await client.post(
            f"/proxy/{agent_id}/api/sessions",
            json={"cli": f"custom_{row['id']}"},
            headers=headers,
        )

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["agent_update_required"] is True
        assert captured == {}

    async def test_array_body_is_forwarded_untouched(
        self, client, app, registered_agent
    ):
        agent_id, _owner, headers, captured = await self._setup(
            app, registered_agent
        )

        resp = await client.post(
            f"/proxy/{agent_id}/api/sessions",
            json=[{"cli": "custom_" + "a" * 12}],
            headers=headers,
        )

        assert resp.status_code == 201
        assert captured["body"] == [{"cli": "custom_" + "a" * 12}]

    async def test_other_post_paths_are_not_stripped(
        self, client, app, registered_agent
    ):
        agent_id, _owner, headers, captured = await self._setup(
            app, registered_agent
        )

        resp = await client.post(
            f"/proxy/{agent_id}/api/projects",
            json={"resolved_command": "kept", "name": "p"},
            headers=headers,
        )

        assert resp.status_code == 201
        assert captured["path"] == "/api/projects"
        assert captured["body"] == {"resolved_command": "kept", "name": "p"}

    async def test_put_to_sessions_path_is_not_intercepted(
        self, client, app, registered_agent
    ):
        """Only POST creates sessions; other verbs on the path keep their
        body verbatim."""
        agent_id, _owner, headers, captured = await self._setup(
            app, registered_agent
        )

        resp = await client.put(
            f"/proxy/{agent_id}/api/sessions",
            json={"cli": "custom_xyz", "resolved_command": "kept"},
            headers=headers,
        )

        assert resp.status_code == 201
        assert captured["body"] == {
            "cli": "custom_xyz",
            "resolved_command": "kept",
        }
