"""Tests for PairingService and pairing REST endpoints."""
from __future__ import annotations

import json

import pytest

from central_server.db.database import Database
from central_server.pairing.service import PairingError, PairingService


@pytest.fixture
async def db(tmp_path) -> Database:
    database = Database(str(tmp_path / "pairing_test.db"))
    await database.connect()
    yield database
    await database.close()


@pytest.fixture
def pairing_service(db, config) -> PairingService:
    return PairingService(db, config)


class TestPairingService:
    async def test_create_token(self, pairing_service: PairingService, db: Database):
        agent_id, _ = await db.register_agent("test")
        raw_token, expires_at = await pairing_service.create_pairing_token(agent_id)
        assert len(raw_token) > 20
        assert expires_at is not None

    async def test_consume_token(self, pairing_service: PairingService, db: Database):
        agent_id, _ = await db.register_agent("test")
        raw_token, _ = await pairing_service.create_pairing_token(agent_id)
        result_agent_id, pairing_id = await pairing_service.consume_pairing_token(
            raw_token, "device-1", "My Phone"
        )
        assert result_agent_id == agent_id
        assert len(pairing_id) == 12

        # Verify pairing exists
        paired = await db.is_mobile_paired_to_agent("device-1", agent_id)
        assert paired is True

    async def test_consume_invalid_token(self, pairing_service: PairingService, db: Database):
        with pytest.raises(PairingError, match="Invalid"):
            await pairing_service.consume_pairing_token("bogus", "device-1")

    async def test_single_use(self, pairing_service: PairingService, db: Database):
        agent_id, _ = await db.register_agent("test")
        raw_token, _ = await pairing_service.create_pairing_token(agent_id)
        await pairing_service.consume_pairing_token(raw_token, "device-1")
        with pytest.raises(PairingError):
            await pairing_service.consume_pairing_token(raw_token, "device-2")

    def test_qr_data(self, pairing_service: PairingService):
        data = pairing_service.generate_qr_data("token123", "agent-1", "https://relay.example.com")
        parsed = json.loads(data)
        assert parsed["type"] == "claude-pair"
        assert parsed["token"] == "token123"
        assert parsed["agent_id"] == "agent-1"
        assert parsed["server"] == "https://relay.example.com"

    def test_qr_image(self, pairing_service: PairingService):
        b64 = pairing_service.generate_qr_image_base64("test-data")
        assert len(b64) > 100  # Non-trivial base64 PNG

    async def test_list_pairings(self, pairing_service: PairingService, db: Database):
        agent_id, _ = await db.register_agent("test")
        raw_token, _ = await pairing_service.create_pairing_token(agent_id)
        await pairing_service.consume_pairing_token(raw_token, "device-1", "Phone")
        pairings = await pairing_service.list_pairings(agent_id)
        assert len(pairings) == 1

    async def test_revoke_pairing(self, pairing_service: PairingService, db: Database):
        agent_id, _ = await db.register_agent("test")
        raw_token, _ = await pairing_service.create_pairing_token(agent_id)
        _, pairing_id = await pairing_service.consume_pairing_token(raw_token, "device-1")
        assert await pairing_service.revoke_pairing(pairing_id) is True
        assert await db.is_mobile_paired_to_agent("device-1", agent_id) is False


class TestPairingEndpoints:
    """Integration tests for the pairing REST API."""

    async def test_complete_pairing(self, client, app, registered_agent):
        agent_id, _ = registered_agent
        # Create a pairing token via the service directly
        raw_token, _ = await app.state.pairing_service.create_pairing_token(agent_id)
        resp = await client.post(
            "/api/pairing/complete",
            json={"token": raw_token, "device_id": "dev-1", "device_name": "Test"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["agent_id"] == agent_id

    async def test_complete_pairing_invalid_token(self, client):
        resp = await client.post(
            "/api/pairing/complete",
            json={"token": "bogus", "device_id": "dev-1"},
        )
        assert resp.status_code == 400

    async def test_refresh_token(self, client, app, registered_agent):
        agent_id, _ = registered_agent
        raw_token, _ = await app.state.pairing_service.create_pairing_token(agent_id)
        # Complete pairing to get tokens
        resp = await client.post(
            "/api/pairing/complete",
            json={"token": raw_token, "device_id": "dev-1"},
        )
        refresh = resp.json()["refresh_token"]
        # Refresh
        resp2 = await client.post(
            "/api/mobile/refresh", json={"refresh_token": refresh}
        )
        assert resp2.status_code == 200
        assert "access_token" in resp2.json()

    async def test_register_agent(self, client, dashboard_headers):
        resp = await client.post(
            "/api/agents/register",
            json={"name": "new-agent"},
            headers=dashboard_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "agent_id" in data
        assert "token" in data

    async def test_list_agents(self, client, dashboard_headers, registered_agent):
        resp = await client.get("/api/agents", headers=dashboard_headers)
        assert resp.status_code == 200
        agents = resp.json()
        assert len(agents) >= 1
