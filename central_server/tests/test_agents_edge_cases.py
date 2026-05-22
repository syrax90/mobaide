"""Edge-case tests for agent management — covering gaps not in test_agents.py.

Covers:
- Operations on nonexistent agent IDs (reregister, delete, get-token)
- Refresh token rotation after agent deletion (user_id fallback)
"""
from __future__ import annotations

import pytest

from tests.conftest import TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD


class TestNonexistentAgentEndpoints:
    """Reregister/delete/get-token on an agent_id that doesn't exist at all."""

    async def test_reregister_nonexistent_agent(self, client, api_auth_headers):
        resp = await client.post(
            "/api/agents/nonexistent-id/reregister",
            headers=api_auth_headers,
        )
        assert resp.status_code == 403

    async def test_delete_nonexistent_agent(self, client, api_auth_headers):
        resp = await client.delete(
            "/api/agents/nonexistent-id",
            headers=api_auth_headers,
        )
        assert resp.status_code == 403

    async def test_get_token_nonexistent_agent(self, client, api_auth_headers):
        resp = await client.post(
            "/api/agents/nonexistent-id/token",
            json={
                "mobile_device_id": "11111111-1111-4111-8111-111111111111",
                "mobile_device_name": "Pixel 8",
            },
            headers=api_auth_headers,
        )
        assert resp.status_code == 403


class TestRefreshAfterAgentDeleted:
    """Refresh token rotation when the agent has been deleted between issuance and refresh."""

    async def test_refresh_after_agent_deleted(self, client, app, api_auth_headers):
        # Create agent and get mobile tokens
        create_resp = await client.post(
            "/api/agents",
            json={"name": "doomed-agent"},
            headers=api_auth_headers,
        )
        agent_id = create_resp.json()["agent_id"]

        # Get mobile tokens for the agent
        token_resp = await client.post(
            f"/api/agents/{agent_id}/token",
            json={
                "mobile_device_id": "11111111-1111-4111-8111-111111111111",
                "mobile_device_name": "Pixel 8",
            },
            headers=api_auth_headers,
        )
        refresh_token = token_resp.json()["refresh_token"]

        # Delete the agent
        delete_resp = await client.delete(
            f"/api/agents/{agent_id}",
            headers=api_auth_headers,
        )
        assert delete_resp.status_code == 204

        # Attempt to refresh — the refresh token record was cascade-deleted
        refresh_resp = await client.post(
            "/api/mobile/refresh",
            json={"refresh_token": refresh_token},
        )
        assert refresh_resp.status_code == 401


class TestCreateAgentWithBearerAuth:
    """Verify the Bearer auth on agent endpoints works with tokens from login."""

    async def test_non_bearer_scheme_rejected(self, client):
        resp = await client.post(
            "/api/agents",
            json={"name": "test"},
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert resp.status_code == 401

    async def test_expired_token_rejected(self, client, app):
        import time
        import jwt
        from tests.conftest import TEST_JWT_SECRET

        expired = jwt.encode(
            {
                "sub": "user-id",
                "type": "dashboard",
                "email": "admin@test.local",
                "role": "admin",
                "iat": int(time.time()) - 7200,
                "exp": int(time.time()) - 3600,
            },
            TEST_JWT_SECRET,
            algorithm="HS256",
        )
        resp = await client.post(
            "/api/agents",
            json={"name": "test"},
            headers={"Authorization": f"Bearer {expired}"},
        )
        assert resp.status_code == 401
