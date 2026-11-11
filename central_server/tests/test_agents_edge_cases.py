"""Edge-case tests for agent management — covering gaps not in test_agents.py.

Covers:
- Minting mobile tokens for a nonexistent agent ID
- Refresh token rotation after agent deletion (user_id fallback)
"""
from __future__ import annotations


class TestNonexistentAgentEndpoints:
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
