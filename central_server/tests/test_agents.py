"""Tests for the user-scoped agent management API."""
from __future__ import annotations

import pytest

from tests.conftest import TEST_ADMIN_EMAIL


class TestCreateAgent:
    async def test_create_agent(self, client, api_auth_headers):
        resp = await client.post(
            "/api/agents",
            json={"name": "my-agent"},
            headers=api_auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "my-agent"
        assert len(data["agent_id"]) == 12
        assert len(data["token"]) > 20

    async def test_create_agent_requires_auth(self, client):
        resp = await client.post("/api/agents", json={"name": "x"})
        assert resp.status_code == 401


class TestListAgents:
    async def test_list_agents_empty(self, client, api_auth_headers):
        resp = await client.get("/api/agents", headers=api_auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_agents_returns_only_owned(self, client, app, api_auth_headers):
        # Create an agent as admin
        resp = await client.post(
            "/api/agents",
            json={"name": "admin-agent"},
            headers=api_auth_headers,
        )
        assert resp.status_code == 201

        # Create a second user and agent directly in DB
        other_user = await app.state.db.create_user(
            email="other@test.local", password_hash="hash"
        )
        await app.state.db.register_agent("other-agent", other_user)

        # Admin should only see their own agent
        resp = await client.get("/api/agents", headers=api_auth_headers)
        agents = resp.json()
        assert len(agents) == 1
        assert agents[0]["name"] == "admin-agent"

    async def test_list_agents_includes_online_status(self, client, app, api_auth_headers):
        resp = await client.post(
            "/api/agents",
            json={"name": "agent"},
            headers=api_auth_headers,
        )
        agent_id = resp.json()["agent_id"]

        resp = await client.get("/api/agents", headers=api_auth_headers)
        agent = resp.json()[0]
        assert agent["online"] is False

    async def test_list_agents_requires_auth(self, client):
        resp = await client.get("/api/agents")
        assert resp.status_code == 401


class TestReregisterAgent:
    async def test_reregister_agent(self, client, api_auth_headers, app):
        # Create agent
        create_resp = await client.post(
            "/api/agents",
            json={"name": "agent"},
            headers=api_auth_headers,
        )
        agent_id = create_resp.json()["agent_id"]
        old_token = create_resp.json()["token"]

        # Reregister
        resp = await client.post(
            f"/api/agents/{agent_id}/reregister",
            headers=api_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_id"] == agent_id
        assert len(data["token"]) > 20
        assert data["token"] != old_token

        # Old token no longer works
        assert await app.state.db.verify_agent_token(agent_id, old_token) is False
        # New token works
        assert await app.state.db.verify_agent_token(agent_id, data["token"]) is True

    async def test_reregister_not_owner_returns_403(self, client, app, api_auth_headers):
        # Create agent owned by another user
        other_user = await app.state.db.create_user(
            email="other@test.local", password_hash="hash"
        )
        agent_id, _ = await app.state.db.register_agent("other-agent", other_user)

        resp = await client.post(
            f"/api/agents/{agent_id}/reregister",
            headers=api_auth_headers,
        )
        assert resp.status_code == 403


class TestDeleteAgent:
    async def test_delete_agent(self, client, api_auth_headers, app):
        # Create agent
        create_resp = await client.post(
            "/api/agents",
            json={"name": "doomed"},
            headers=api_auth_headers,
        )
        agent_id = create_resp.json()["agent_id"]

        # Delete
        resp = await client.delete(
            f"/api/agents/{agent_id}", headers=api_auth_headers
        )
        assert resp.status_code == 204

        # Verify deleted
        assert await app.state.db.get_agent(agent_id) is None

    async def test_delete_not_owner_returns_403(self, client, app, api_auth_headers):
        other_user = await app.state.db.create_user(
            email="other@test.local", password_hash="hash"
        )
        agent_id, _ = await app.state.db.register_agent("other-agent", other_user)

        resp = await client.delete(
            f"/api/agents/{agent_id}", headers=api_auth_headers
        )
        assert resp.status_code == 403

    async def test_delete_cascade(self, client, app, api_auth_headers):
        """Deleting an agent should cascade to pairing-tokens and refresh-tokens.

        ``mobile_devices`` is user-scoped and intentionally NOT touched by
        ``delete_agent`` — a device outlives any one agent.
        """
        create_resp = await client.post(
            "/api/agents",
            json={"name": "cascade-test"},
            headers=api_auth_headers,
        )
        agent_id = create_resp.json()["agent_id"]

        db = app.state.db
        await db.create_pairing_token(agent_id, 300)
        await db.store_refresh_token("r1", "device-1", agent_id, 60)

        resp = await client.delete(
            f"/api/agents/{agent_id}", headers=api_auth_headers
        )
        assert resp.status_code == 204

        assert await db.get_pending_pairing_tokens(agent_id) == []

    async def test_delete_qr_paired_agent_returns_204(
        self, client, app, api_auth_headers
    ):
        """End-to-end regression: an agent paired through the QR flow has a
        ``pairing_tokens`` row linked only via ``claimed_agent_id`` (the
        original ``agent_id`` was NULL pre-claim and stays NULL after claim).
        Before the fix, ``DELETE /api/agents/{id}`` raised aiosqlite
        ``IntegrityError`` out of FastAPI's default handler and surfaced as
        HTTP 500 in the mobile UI.
        """
        create_resp = await client.post(
            "/api/agents",
            json={"name": "qr-paired"},
            headers=api_auth_headers,
        )
        agent_id = create_resp.json()["agent_id"]

        db = app.state.db
        raw_nonce, _ = await db.create_pairing_token(
            agent_id=None, ttl_seconds=300
        )
        claimed = await db.claim_pairing_nonce(
            raw_token=raw_nonce,
            mobile_device_id="device-qr",
            claimed_agent_id=agent_id,
            claimed_token_raw="agent-token-stub",
        )
        assert claimed is True

        resp = await client.delete(
            f"/api/agents/{agent_id}", headers=api_auth_headers
        )
        assert resp.status_code == 204
        assert await db.get_agent(agent_id) is None
        cur = await db.conn.execute(
            "SELECT 1 FROM pairing_tokens WHERE claimed_agent_id = ?",
            (agent_id,),
        )
        assert await cur.fetchone() is None


class TestDeleteAgentUninstall:
    async def test_uninstall_query_records_the_request(
        self, client, app, api_auth_headers, registered_agent
    ):
        agent_id, raw_token = registered_agent

        resp = await client.delete(
            f"/api/agents/{agent_id}",
            params={"uninstall": "true"},
            headers=api_auth_headers,
        )

        assert resp.status_code == 204
        assert await app.state.db.get_agent(agent_id) is None
        assert await app.state.db.match_uninstall_request(agent_id, raw_token) is True

    async def test_without_query_no_request_is_recorded(
        self, client, app, api_auth_headers, registered_agent
    ):
        agent_id, raw_token = registered_agent

        resp = await client.delete(
            f"/api/agents/{agent_id}", headers=api_auth_headers
        )

        assert resp.status_code == 204
        assert await app.state.db.match_uninstall_request(agent_id, raw_token) is False


class TestListAgentsSupportsUninstall:
    async def test_list_reports_the_handshake_capability(
        self, client, app, api_auth_headers, registered_agent, viewer_user
    ):
        capable_id, _ = registered_agent
        legacy_id, _ = await app.state.db.register_agent(
            "legacy", viewer_user["id"]
        )
        await app.state.db.update_agent_handshake_info(
            capable_id, "0.9.0", 8, supports_uninstall=True
        )

        resp = await client.get("/api/agents", headers=api_auth_headers)

        by_id = {a["agent_id"]: a for a in resp.json()}
        assert by_id[capable_id]["supports_uninstall"] is True
        assert by_id[legacy_id]["supports_uninstall"] is False


class TestGetMobileTokens:
    async def test_get_mobile_tokens(self, client, api_auth_headers):
        create_resp = await client.post(
            "/api/agents",
            json={"name": "agent"},
            headers=api_auth_headers,
        )
        agent_id = create_resp.json()["agent_id"]

        resp = await client.post(
            f"/api/agents/{agent_id}/token",
            json={
                "mobile_device_id": "11111111-1111-4111-8111-111111111111",
                "mobile_device_name": "Pixel 8",
            },
            headers=api_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    async def test_get_mobile_tokens_not_owner_returns_403(self, client, app, api_auth_headers):
        other_user = await app.state.db.create_user(
            email="other@test.local", password_hash="hash"
        )
        agent_id, _ = await app.state.db.register_agent("other-agent", other_user)

        resp = await client.post(
            f"/api/agents/{agent_id}/token",
            json={
                "mobile_device_id": "11111111-1111-4111-8111-111111111111",
                "mobile_device_name": "Pixel 8",
            },
            headers=api_auth_headers,
        )
        assert resp.status_code == 403


class TestAdminBlocked:
    """Admin users must not own or manage agents.

    All ``/api/agents/*`` endpoints reject admin tokens with 403. Covered:
    POST create, GET list, POST reregister, DELETE, POST token. Also covers
    the service-layer guard in ``AgentService.create_agent``.
    """

    async def test_admin_cannot_create_agent(self, client, admin_api_auth_headers):
        resp = await client.post(
            "/api/agents",
            json={"name": "forbidden"},
            headers=admin_api_auth_headers,
        )
        assert resp.status_code == 403
        assert "admin" in resp.json()["detail"].lower()

    async def test_admin_cannot_list_agents(self, client, admin_api_auth_headers):
        resp = await client.get("/api/agents", headers=admin_api_auth_headers)
        assert resp.status_code == 403

    async def test_admin_cannot_reregister_agent(
        self, client, admin_api_auth_headers, registered_agent
    ):
        agent_id, _ = registered_agent  # viewer-owned
        resp = await client.post(
            f"/api/agents/{agent_id}/reregister",
            headers=admin_api_auth_headers,
        )
        assert resp.status_code == 403

    async def test_admin_cannot_delete_agent(
        self, client, admin_api_auth_headers, registered_agent
    ):
        agent_id, _ = registered_agent
        resp = await client.delete(
            f"/api/agents/{agent_id}",
            headers=admin_api_auth_headers,
        )
        assert resp.status_code == 403

    async def test_admin_cannot_get_mobile_tokens(
        self, client, admin_api_auth_headers, registered_agent
    ):
        agent_id, _ = registered_agent
        resp = await client.post(
            f"/api/agents/{agent_id}/token",
            json={
                "mobile_device_id": "11111111-1111-4111-8111-111111111111",
                "mobile_device_name": "Pixel 8",
            },
            headers=admin_api_auth_headers,
        )
        assert resp.status_code == 403

    async def test_service_layer_rejects_admin_owner(self, app):
        """Defense-in-depth: bypassing the router still fails."""
        admin = await app.state.db.get_user_by_email(TEST_ADMIN_EMAIL)
        with pytest.raises(ValueError, match="[Aa]dmin"):
            await app.state.agent_service.create_agent("x", admin["id"])
