"""Tests for the in-memory RoutingTable."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from central_server.relay.envelope import CH_CONTROL, decode
from central_server.relay.routing import (
    AgentConnection,
    MobileClient,
    RoutingTable,
    try_enqueue_control,
)


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


class TestListAttachedSessions:
    """``list_attached_sessions`` is used on agent reconnect to replay
    ``mobile_session_attach`` for every mobile that was already wired up
    before the tunnel died. It must:

    - project only ``session_io:*`` channels (not events, sessions_list, or autoscan);
    - return the raw session id with the channel-key prefix stripped;
    - ignore channels whose client set has been fully drained.
    """

    def test_returns_ids_for_session_io_channels(self, routing: RoutingTable):
        routing.register_agent("agent-1", _mock_ws())
        c1 = MobileClient(device_id="dev-1", websocket=_mock_ws())
        c2 = MobileClient(device_id="dev-2", websocket=_mock_ws())
        routing.register_mobile("agent-1", "session_io:s1", c1)
        routing.register_mobile("agent-1", "session_io:s2", c2)

        sids = sorted(routing.list_attached_sessions("agent-1"))
        assert sids == ["s1", "s2"]

    def test_excludes_non_session_io_channels(self, routing: RoutingTable):
        routing.register_agent("agent-1", _mock_ws())
        routing.register_mobile(
            "agent-1", "events",
            MobileClient(device_id="dev-1", websocket=_mock_ws()),
        )
        routing.register_mobile(
            "agent-1", "sessions_list",
            MobileClient(device_id="dev-1", websocket=_mock_ws()),
        )
        routing.register_mobile(
            "agent-1", "autoscan:sub-1",
            MobileClient(device_id="dev-1", websocket=_mock_ws()),
        )
        routing.register_mobile(
            "agent-1", "session_io:s1",
            MobileClient(device_id="dev-1", websocket=_mock_ws()),
        )

        assert list(routing.list_attached_sessions("agent-1")) == ["s1"]

    def test_returns_empty_when_agent_unknown(self, routing: RoutingTable):
        assert routing.list_attached_sessions("ghost") == []

    def test_drops_ids_after_last_client_unregistered(
        self, routing: RoutingTable,
    ):
        routing.register_agent("agent-1", _mock_ws())
        client = MobileClient(device_id="dev-1", websocket=_mock_ws())
        routing.register_mobile("agent-1", "session_io:s1", client)
        assert routing.list_attached_sessions("agent-1") == ["s1"]

        routing.unregister_mobile("agent-1", "session_io:s1", client)
        assert routing.list_attached_sessions("agent-1") == []


class TestTryEnqueueControl:
    """``try_enqueue_control`` is the shared wire-format helper used by
    both ``mobile_ws._send_session_lifecycle`` and
    ``agent_ws._enqueue_attach`` to encode a JSON body onto the agent's
    CH_CONTROL channel. It must return ``True`` on success, ``False``
    on a missing agent or a saturated queue, and never raise.
    """

    def test_returns_false_when_conn_is_none(self):
        assert try_enqueue_control(None, {"type": "ping"}) is False

    def test_enqueues_and_returns_true_on_free_queue(
        self, routing: RoutingTable,
    ):
        conn = routing.register_agent("agent-1", _mock_ws())

        assert try_enqueue_control(conn, {"type": "noop", "x": 1}) is True

        assert conn.send_queue.qsize() == 1
        channel, sid, payload = decode(conn.send_queue.get_nowait())
        assert channel == CH_CONTROL
        assert sid == ""
        assert json.loads(payload) == {"type": "noop", "x": 1}

    def test_returns_false_on_saturated_queue(self, routing: RoutingTable):
        conn = routing.register_agent("agent-1", _mock_ws())
        conn.send_queue = asyncio.Queue(maxsize=1)
        conn.send_queue.put_nowait(b"already-full")

        assert try_enqueue_control(conn, {"type": "ping"}) is False
        # Helper must not eat or replace the queued item.
        assert conn.send_queue.qsize() == 1
        assert conn.send_queue.get_nowait() == b"already-full"
