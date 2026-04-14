"""Tests for the in-memory RoutingTable."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from central_server.relay.routing import AgentConnection, MobileClient, RoutingTable


@pytest.fixture
def routing() -> RoutingTable:
    return RoutingTable()


def _mock_ws() -> MagicMock:
    ws = MagicMock()
    ws.close = AsyncMock()
    return ws


class TestAgentOps:
    def test_register_and_is_online(self, routing: RoutingTable):
        ws = _mock_ws()
        conn = routing.register_agent("agent-1", ws)
        assert isinstance(conn, AgentConnection)
        assert routing.is_agent_online("agent-1") is True
        assert routing.is_agent_online("agent-2") is False

    def test_unregister(self, routing: RoutingTable):
        ws = _mock_ws()
        routing.register_agent("agent-1", ws)
        clients = routing.unregister_agent("agent-1")
        assert routing.is_agent_online("agent-1") is False
        assert clients == []

    def test_unregister_returns_mobile_clients(self, routing: RoutingTable):
        ws = _mock_ws()
        routing.register_agent("agent-1", ws)
        client = MobileClient(device_id="dev-1", websocket=_mock_ws())
        routing.register_mobile("agent-1", "session_io:s1", client)
        clients = routing.unregister_agent("agent-1")
        assert len(clients) == 1
        assert clients[0].device_id == "dev-1"

    def test_get_agent(self, routing: RoutingTable):
        ws = _mock_ws()
        routing.register_agent("agent-1", ws)
        assert routing.get_agent("agent-1") is not None
        assert routing.get_agent("nonexistent") is None

    def test_list_online(self, routing: RoutingTable):
        routing.register_agent("a1", _mock_ws())
        routing.register_agent("a2", _mock_ws())
        assert len(routing.list_online_agents()) == 2

    def test_heartbeat(self, routing: RoutingTable):
        ws = _mock_ws()
        conn = routing.register_agent("agent-1", ws)
        old_hb = conn.last_heartbeat
        routing.agent_heartbeat("agent-1")
        assert conn.last_heartbeat >= old_hb


class TestMobileOps:
    def test_register_and_get(self, routing: RoutingTable):
        routing.register_agent("agent-1", _mock_ws())
        client = MobileClient(device_id="dev-1", websocket=_mock_ws())
        routing.register_mobile("agent-1", "session_io:s1", client)
        clients = routing.get_mobile_clients("agent-1", "session_io:s1")
        assert client in clients

    def test_multiple_clients(self, routing: RoutingTable):
        routing.register_agent("agent-1", _mock_ws())
        c1 = MobileClient(device_id="dev-1", websocket=_mock_ws())
        c2 = MobileClient(device_id="dev-2", websocket=_mock_ws())
        routing.register_mobile("agent-1", "session_io:s1", c1)
        routing.register_mobile("agent-1", "session_io:s1", c2)
        assert len(routing.get_mobile_clients("agent-1", "session_io:s1")) == 2

    def test_unregister_mobile(self, routing: RoutingTable):
        routing.register_agent("agent-1", _mock_ws())
        client = MobileClient(device_id="dev-1", websocket=_mock_ws())
        routing.register_mobile("agent-1", "events", client)
        routing.unregister_mobile("agent-1", "events", client)
        assert len(routing.get_mobile_clients("agent-1", "events")) == 0

    def test_get_all_mobile(self, routing: RoutingTable):
        routing.register_agent("agent-1", _mock_ws())
        c1 = MobileClient(device_id="dev-1", websocket=_mock_ws())
        c2 = MobileClient(device_id="dev-2", websocket=_mock_ws())
        routing.register_mobile("agent-1", "session_io:s1", c1)
        routing.register_mobile("agent-1", "events", c2)
        all_clients = routing.get_all_mobile_for_agent("agent-1")
        assert len(all_clients) == 2

    def test_empty_channels(self, routing: RoutingTable):
        assert routing.get_mobile_clients("none", "none") == set()
