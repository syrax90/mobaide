"""Unit tests for agents/service.py — AgentService business logic.

Covers the service layer independently from HTTP, focusing on:
- Ownership enforcement (reregister, delete)
- Routing table cleanup on agent deletion (online agent disconnect, mobile client close)
- Online status enrichment in list
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from central_server.agents.service import AgentService
from central_server.db.database import Database
from central_server.relay.routing import MobileClient, RoutingTable


@pytest.fixture
async def db(tmp_path) -> Database:
    database = Database(str(tmp_path / "svc_test.db"))
    await database.connect()
    yield database
    await database.close()


@pytest.fixture
def routing() -> RoutingTable:
    return RoutingTable()


@pytest.fixture
def service(db, routing) -> AgentService:
    return AgentService(db, routing)


async def _create_owner(db: Database) -> str:
    return await db.create_user(email="owner@test.local", password_hash="h")


class TestCreateAgent:
    async def test_create_agent_returns_id_and_token(self, service, db):
        owner = await _create_owner(db)
        agent_id, raw_token = await service.create_agent("my-agent", owner)
        assert len(agent_id) == 12
        assert len(raw_token) > 20
        # Agent is persisted in DB
        agent = await db.get_agent(agent_id)
        assert agent is not None
        assert agent["name"] == "my-agent"
        assert agent["owner_user_id"] == owner


class TestListUserAgents:
    async def test_list_enriches_online_status(self, service, db, routing):
        owner = await _create_owner(db)
        agent_id_1, _ = await service.create_agent("online-agent", owner)
        agent_id_2, _ = await service.create_agent("offline-agent", owner)

        # Register only the first agent as online
        ws = MagicMock()
        routing.register_agent(agent_id_1, ws)

        agents = await service.list_user_agents(owner)
        assert len(agents) == 2
        by_id = {a["agent_id"]: a for a in agents}
        assert by_id[agent_id_1]["online"] is True
        assert by_id[agent_id_2]["online"] is False

    async def test_list_returns_empty_for_no_agents(self, service, db):
        owner = await _create_owner(db)
        agents = await service.list_user_agents(owner)
        assert agents == []

    async def test_list_excludes_other_users_agents(self, service, db):
        owner = await _create_owner(db)
        other = await db.create_user(email="other@test.local", password_hash="h")
        await service.create_agent("my-agent", owner)
        await service.create_agent("their-agent", other)

        agents = await service.list_user_agents(owner)
        assert len(agents) == 1
        assert agents[0]["name"] == "my-agent"


class TestReregisterAgent:
    async def test_reregister_returns_new_token(self, service, db):
        owner = await _create_owner(db)
        agent_id, old_token = await service.create_agent("agent", owner)
        new_token = await service.reregister_agent(agent_id, owner)
        assert new_token != old_token
        assert len(new_token) > 20
        # Old token no longer works
        assert await db.verify_agent_token(agent_id, old_token) is False
        # New token works
        assert await db.verify_agent_token(agent_id, new_token) is True

    async def test_reregister_non_owner_raises(self, service, db):
        owner = await _create_owner(db)
        other = await db.create_user(email="other@test.local", password_hash="h")
        agent_id, _ = await service.create_agent("agent", owner)
        with pytest.raises(ValueError, match="Not the owner"):
            await service.reregister_agent(agent_id, other)

    async def test_reregister_nonexistent_agent_raises(self, service, db):
        owner = await _create_owner(db)
        with pytest.raises(ValueError, match="Not the owner"):
            await service.reregister_agent("nonexistent-id", owner)


class TestDeleteAgent:
    async def test_delete_removes_agent_from_db(self, service, db):
        owner = await _create_owner(db)
        agent_id, _ = await service.create_agent("doomed", owner)
        await service.delete_agent(agent_id, owner)
        assert await db.get_agent(agent_id) is None

    async def test_delete_non_owner_raises(self, service, db):
        owner = await _create_owner(db)
        other = await db.create_user(email="other@test.local", password_hash="h")
        agent_id, _ = await service.create_agent("agent", owner)
        with pytest.raises(ValueError, match="Not the owner"):
            await service.delete_agent(agent_id, other)

    async def test_delete_nonexistent_agent_raises(self, service, db):
        owner = await _create_owner(db)
        with pytest.raises(ValueError, match="Not the owner"):
            await service.delete_agent("nonexistent-id", owner)

    async def test_delete_disconnects_online_agent(self, service, db, routing):
        """Deleting an online agent should unregister it from routing and close mobile clients."""
        owner = await _create_owner(db)
        agent_id, _ = await service.create_agent("online-agent", owner)

        # Simulate an online agent with a connected mobile client
        agent_ws = MagicMock()
        conn = routing.register_agent(agent_id, agent_ws)

        mobile_ws = MagicMock()
        mobile_ws.close = AsyncMock()
        mobile_client = MobileClient(device_id="dev-1", websocket=mobile_ws)
        routing.register_mobile(agent_id, "session_io:s1", mobile_client)

        await service.delete_agent(agent_id, owner)

        # Agent should be removed from routing
        assert routing.is_agent_online(agent_id) is False
        # Mobile client websocket should have been closed with code 4010
        mobile_ws.close.assert_awaited_once_with(code=4010, reason="Agent deleted")
        # Agent should be deleted from DB
        assert await db.get_agent(agent_id) is None

    async def test_delete_offline_agent_skips_routing_cleanup(self, service, db, routing):
        """Deleting an offline agent should not attempt routing cleanup."""
        owner = await _create_owner(db)
        agent_id, _ = await service.create_agent("offline-agent", owner)

        # Agent is not in routing table — no routing cleanup needed
        assert routing.is_agent_online(agent_id) is False

        await service.delete_agent(agent_id, owner)
        assert await db.get_agent(agent_id) is None

    async def test_delete_handles_mobile_close_exception(self, service, db, routing):
        """If closing a mobile WS raises, the delete should still succeed."""
        owner = await _create_owner(db)
        agent_id, _ = await service.create_agent("agent", owner)

        agent_ws = MagicMock()
        conn = routing.register_agent(agent_id, agent_ws)

        mobile_ws = MagicMock()
        mobile_ws.close = AsyncMock(side_effect=RuntimeError("already closed"))
        mobile_client = MobileClient(device_id="dev-1", websocket=mobile_ws)
        routing.register_mobile(agent_id, "session_io:s1", mobile_client)

        # Should not raise despite the mobile close error
        await service.delete_agent(agent_id, owner)
        assert await db.get_agent(agent_id) is None

    async def test_delete_agent_with_qr_claimed_token(self, service, db):
        """Service-layer regression for the QR-pairing post-claim shape:
        a ``pairing_tokens`` row whose only FK to the agent is
        ``claimed_agent_id`` must not raise an FK violation out of
        ``service.delete_agent`` (which would surface as HTTP 500).
        """
        owner = await _create_owner(db)
        agent_id, _ = await service.create_agent("qr-agent", owner)

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

        await service.delete_agent(agent_id, owner)
        assert await db.get_agent(agent_id) is None
