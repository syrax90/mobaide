"""Tests for the pairing REST endpoints."""
from __future__ import annotations

import uuid

import pytest

from central_server.db.database import Database


@pytest.fixture
async def db(tmp_path) -> Database:
    database = Database(str(tmp_path / "pairing_test.db"))
    await database.connect()
    yield database
    await database.close()


class TestPairingEndpoints:
    """Integration tests for the pairing REST API."""

    async def test_refresh_token(self, client, app, registered_agent):
        agent_id, _ = registered_agent
        owner = await app.state.db.get_user_by_email("viewer@test.local")
        device_id = uuid.uuid4().hex
        await app.state.db.upsert_mobile_device(
            device_id=device_id, user_id=owner["id"], name="Test Pixel"
        )
        access, refresh = await app.state.auth_service.create_mobile_tokens(
            device_id, agent_id, owner["id"]
        )
        resp = await client.post(
            "/api/mobile/refresh", json={"refresh_token": refresh}
        )
        assert resp.status_code == 200
        assert "access_token" in resp.json()
