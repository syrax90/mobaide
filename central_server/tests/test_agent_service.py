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


class TestReregisterAgent:
    async def test_reregister_non_owner_raises(self, service, db):
        owner = await _create_owner(db)
        other = await db.create_user(email="other@test.local", password_hash="h")
        agent_id, _ = await service.create_agent("agent", owner)
        with pytest.raises(ValueError, match="Not the owner"):
            await service.reregister_agent(agent_id, other)


class TestDeleteAgent:
    async def test_delete_non_owner_raises(self, service, db):
        owner = await _create_owner(db)
        other = await db.create_user(email="other@test.local", password_hash="h")
        agent_id, _ = await service.create_agent("agent", owner)
        with pytest.raises(ValueError, match="Not the owner"):
            await service.delete_agent(agent_id, other)

    async def test_delete_disconnects_online_agent(self, service, db, routing):
        """Deleting an online agent unregisters it and closes paired mobile WSs."""
        owner = await _create_owner(db)
        agent_id, _ = await service.create_agent("online-agent", owner)

        agent_ws = MagicMock()
        agent_ws.send_text = AsyncMock()
        agent_ws.close = AsyncMock()
        routing.register_agent(agent_id, agent_ws)

        mobile_ws = MagicMock()
        mobile_ws.send_text = AsyncMock()
        mobile_ws.close = AsyncMock()
        mobile_client = MobileClient(device_id="dev-1", websocket=mobile_ws)
        routing.register_mobile(agent_id, "session_io:s1", mobile_client)

        await service.delete_agent(agent_id, owner)

        assert routing.is_agent_online(agent_id) is False
        mobile_ws.close.assert_awaited_once_with(code=4010, reason="admin_revoked")
        agent_ws.close.assert_awaited_once_with(code=4010, reason="admin_revoked")
        assert await db.get_agent(agent_id) is None

    async def test_delete_as_admin_bypasses_ownership(self, service, db):
        """``as_admin=True`` skips the ownership check so the dashboard
        can revoke any agent regardless of owner.
        """
        owner = await _create_owner(db)
        agent_id, _ = await service.create_agent("agent", owner)
        admin_user_id = "admin-acting-user-id"

        # Admin (different user, not the owner) deletes the agent without raising.
        await service.delete_agent(agent_id, admin_user_id, as_admin=True)
        assert await db.get_agent(agent_id) is None


class TestDeleteAgentUninstall:
    async def test_offline_agent_gets_an_uninstall_request(self, service, db):
        owner = await _create_owner(db)
        agent_id, raw_token = await service.create_agent("offline", owner)

        await service.delete_agent(agent_id, owner, uninstall=True)

        assert await db.get_agent(agent_id) is None
        assert await db.match_uninstall_request(agent_id, raw_token) is True

    async def test_non_owner_cannot_record_an_uninstall_request(
        self, service, db
    ):
        owner = await _create_owner(db)
        other = await db.create_user(email="other@test.local", password_hash="h")
        agent_id, raw_token = await service.create_agent("agent", owner)

        with pytest.raises(ValueError, match="Not the owner"):
            await service.delete_agent(agent_id, other, uninstall=True)

        assert await db.get_agent(agent_id) is not None
        assert await db.match_uninstall_request(agent_id, raw_token) is False

    async def test_online_agent_gets_the_control_frame_and_its_own_reason(
        self, service, db, routing
    ):
        import json

        from central_server.relay.envelope import CH_CONTROL, decode

        owner = await _create_owner(db)
        agent_id, raw_token = await service.create_agent("online", owner)
        agent_ws = MagicMock()
        agent_ws.send_bytes = AsyncMock()
        agent_ws.send_text = AsyncMock()
        agent_ws.close = AsyncMock()
        routing.register_agent(agent_id, agent_ws)
        mobile_ws = MagicMock()
        mobile_ws.send_text = AsyncMock()
        mobile_ws.close = AsyncMock()
        routing.register_mobile(
            agent_id,
            "session_io:s1",
            MobileClient(device_id="dev-1", websocket=mobile_ws),
        )

        await service.delete_agent(agent_id, owner, uninstall=True)

        agent_ws.send_bytes.assert_awaited_once()
        channel, _, payload = decode(agent_ws.send_bytes.await_args.args[0])
        assert channel == CH_CONTROL
        assert json.loads(payload) == {"type": "agent_uninstall"}
        agent_ws.close.assert_awaited_once_with(
            code=4010, reason="agent_uninstalled"
        )
        mobile_ws.close.assert_awaited_once_with(code=4010, reason="admin_revoked")
        # Recorded too, so a frame lost to a crash is retried at re-auth.
        assert await db.match_uninstall_request(agent_id, raw_token) is True
        assert await db.get_agent(agent_id) is None
