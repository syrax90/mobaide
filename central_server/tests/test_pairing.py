"""Tests for PairingService and pairing REST endpoints."""
from __future__ import annotations

import pytest

from central_server.db.database import Database
from central_server.pairing.service import PairingService


@pytest.fixture
async def db(tmp_path) -> Database:
    database = Database(str(tmp_path / "pairing_test.db"))
    await database.connect()
    yield database
    await database.close()


async def _create_test_user(db: Database) -> str:
    return await db.create_user(email="owner@test.local", password_hash="hash")


@pytest.fixture
def pairing_service(db) -> PairingService:
    return PairingService(db)


class TestPairingService:
    async def test_list_pairings(self, pairing_service: PairingService, db: Database):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        await db.create_pairing(agent_id, "device-1", "Phone")
        pairings = await pairing_service.list_pairings(agent_id)
        assert len(pairings) == 1

    async def test_revoke_pairing(self, pairing_service: PairingService, db: Database):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        pairing_id = await db.create_pairing(agent_id, "device-1")
        assert await pairing_service.revoke_pairing(pairing_id) is True
        assert await db.is_mobile_paired_to_agent("device-1", agent_id) is False


class TestPairingEndpoints:
    """Integration tests for the pairing REST API."""

    async def test_refresh_token(self, client, app, registered_agent):
        agent_id, _ = registered_agent
        # Create mobile tokens directly (QR pairing is removed)
        owner = await app.state.db.get_user_by_email("viewer@test.local")
        access, refresh = await app.state.auth_service.create_mobile_tokens(
            owner["id"], agent_id, owner["id"]
        )
        # Refresh
        resp = await client.post(
            "/api/mobile/refresh", json={"refresh_token": refresh}
        )
        assert resp.status_code == 200
        assert "access_token" in resp.json()


class TestPairingRevokeRoleGate:
    """`DELETE /api/pairings/{id}` is the JSON twin of the dashboard HTMX
    revoke route. Both must be admin-only — viewer tokens get 403.
    """

    async def test_admin_can_revoke_via_api(
        self, client, app, dashboard_headers, registered_agent
    ):
        agent_id, _ = registered_agent
        pairing_id = await app.state.db.create_pairing(agent_id, "device-1")
        resp = await client.delete(
            f"/api/pairings/{pairing_id}", headers=dashboard_headers
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "revoked"

    async def test_viewer_cannot_revoke_via_api(
        self, client, app, registered_agent
    ):
        agent_id, _ = registered_agent
        pairing_id = await app.state.db.create_pairing(agent_id, "device-1")
        # Log in as the viewer owner (agent is viewer-owned per conftest).
        _uid, _role, token = await app.state.auth_service.login_user(
            "viewer@test.local", "viewerpass123"
        )
        resp = await client.delete(
            f"/api/pairings/{pairing_id}",
            headers={"Cookie": f"dashboard_token={token}"},
        )
        assert resp.status_code == 403
