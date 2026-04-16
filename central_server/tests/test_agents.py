"""Tests for the user-scoped agent management API."""
from __future__ import annotations

import pytest

from tests.conftest import TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD


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

    async def test_create_agent_missing_name(self, client, api_auth_headers):
        resp = await client.post(
            "/api/agents", json={}, headers=api_auth_headers
        )
        assert resp.status_code == 422


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
        """Deleting an agent should cascade to pairings and tokens."""
        create_resp = await client.post(
            "/api/agents",
            json={"name": "cascade-test"},
            headers=api_auth_headers,
        )
        agent_id = create_resp.json()["agent_id"]

        # Create related records
        db = app.state.db
        await db.create_pairing(agent_id, "device-1")
        await db.create_pairing_token(agent_id, 300)
        await db.store_refresh_token("r1", "device-1", agent_id, 60)

        resp = await client.delete(
            f"/api/agents/{agent_id}", headers=api_auth_headers
        )
        assert resp.status_code == 204

        # All related data should be gone
        assert await db.list_pairings_for_agent(agent_id) == []
        assert await db.get_pending_pairing_tokens(agent_id) == []


class TestGetMobileTokens:
    async def test_get_mobile_tokens(self, client, api_auth_headers):
        # Create agent
        create_resp = await client.post(
            "/api/agents",
            json={"name": "agent"},
            headers=api_auth_headers,
        )
        agent_id = create_resp.json()["agent_id"]

        # Get mobile tokens
        resp = await client.post(
            f"/api/agents/{agent_id}/token",
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
            headers=api_auth_headers,
        )
        assert resp.status_code == 403
