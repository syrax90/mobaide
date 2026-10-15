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
    UserClient,
    safe_send_reason_and_close,
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

    def test_heartbeat(self, routing: RoutingTable):
        ws = _mock_ws()
        conn = routing.register_agent("agent-1", ws)
        old_hb = conn.last_heartbeat
        routing.agent_heartbeat("agent-1")
        assert conn.last_heartbeat >= old_hb


class TestMobileOps:
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


def _admin_ws() -> MagicMock:
    """Mock WebSocket with both send_text and close as AsyncMock.

    The default ``_mock_ws`` only mocks ``close``; admin disconnect paths
    also call ``send_text`` to deliver the JSON reason frame.
    """
    ws = MagicMock()
    ws.send_text = AsyncMock()
    ws.close = AsyncMock()
    return ws


class TestUserClientOps:
    """Cover ``register_user_client``/``unregister_user_client`` and the
    admin destructive ``disconnect_user`` helper.
    """

    def test_unregister_pops_user_when_last_client_leaves(
        self, routing: RoutingTable
    ):
        client = UserClient(
            user_id="u1", device_id="dev-1", websocket=_admin_ws()
        )
        routing.register_user_client("u1", client)
        routing.unregister_user_client("u1", client)
        assert routing.get_user_clients("u1") == set()
        assert "u1" not in routing._user_clients

    def test_get_user_device_ids_reports_only_verified_bindings(
        self, routing: RoutingTable
    ):
        bound = UserClient(
            user_id="u1",
            device_id="log-1",
            websocket=_admin_ws(),
            bound_device_id="dev-1",
        )
        unbound = UserClient(
            user_id="u1", device_id="log-2", websocket=_admin_ws()
        )
        other_user = UserClient(
            user_id="u2",
            device_id="log-3",
            websocket=_admin_ws(),
            bound_device_id="dev-3",
        )
        routing.register_user_client("u1", bound)
        routing.register_user_client("u1", unbound)
        routing.register_user_client("u2", other_user)

        # The log-correlation ``device_id`` is not a device identity, and one
        # account's live device says nothing about another's.
        assert routing.get_user_device_ids("u1") == {"dev-1"}
        assert routing.get_user_device_ids("u2") == {"dev-3"}
        assert routing.get_user_device_ids("nobody") == set()

    def test_get_user_device_ids_releases_the_device_on_disconnect(
        self, routing: RoutingTable
    ):
        # A binding that outlived its socket would mute that device's pushes
        # for good.
        client = UserClient(
            user_id="u1",
            device_id="log-1",
            websocket=_admin_ws(),
            bound_device_id="dev-1",
        )
        routing.register_user_client("u1", client)
        routing.unregister_user_client("u1", client)

        assert routing.get_user_device_ids("u1") == set()

    async def test_disconnect_user_sends_reason_and_closes_each_client(
        self, routing: RoutingTable
    ):
        ws1 = _admin_ws()
        ws2 = _admin_ws()
        c1 = UserClient(user_id="u1", device_id="dev-1", websocket=ws1)
        c2 = UserClient(user_id="u1", device_id="dev-2", websocket=ws2)
        routing.register_user_client("u1", c1)
        routing.register_user_client("u1", c2)

        count = await routing.disconnect_user("u1")

        assert count == 2
        for ws in (ws1, ws2):
            ws.send_text.assert_awaited_once_with(
                json.dumps({"reason": "admin_revoked"})
            )
            ws.close.assert_awaited_once_with(
                code=4010, reason="admin_revoked"
            )
        assert "u1" not in routing._user_clients

    async def test_disconnect_user_no_clients_returns_zero(
        self, routing: RoutingTable
    ):
        count = await routing.disconnect_user("ghost")
        assert count == 0

    async def test_disconnect_user_custom_code_and_reason(
        self, routing: RoutingTable
    ):
        ws = _admin_ws()
        client = UserClient(
            user_id="u1", device_id="dev-1", websocket=ws
        )
        routing.register_user_client("u1", client)

        await routing.disconnect_user(
            "u1", code=4011, reason_code="admin_force_disconnect"
        )

        ws.send_text.assert_awaited_once_with(
            json.dumps({"reason": "admin_force_disconnect"})
        )
        ws.close.assert_awaited_once_with(
            code=4011, reason="admin_force_disconnect"
        )

    async def test_disconnect_user_tolerates_already_closed_socket(
        self, routing: RoutingTable
    ):
        ws = MagicMock()
        ws.send_text = AsyncMock(side_effect=RuntimeError("already closed"))
        ws.close = AsyncMock(side_effect=RuntimeError("already closed"))
        client = UserClient(
            user_id="u1", device_id="dev-1", websocket=ws
        )
        routing.register_user_client("u1", client)

        count = await routing.disconnect_user("u1")
        assert count == 1


class TestDisconnectAgent:
    """``RoutingTable.disconnect_agent`` is the symmetric admin helper:
    closes the agent's WS and every paired mobile-client WS, after
    sending a JSON reason frame on each.
    """

    async def test_no_op_when_agent_offline(self, routing: RoutingTable):
        count = await routing.disconnect_agent("ghost")
        assert count == 0

    async def test_closes_agent_and_mobiles(self, routing: RoutingTable):
        agent_ws = _admin_ws()
        routing.register_agent("a1", agent_ws)
        mobile_ws_1 = _admin_ws()
        mobile_ws_2 = _admin_ws()
        routing.register_mobile(
            "a1",
            "session_io:s1",
            MobileClient(device_id="dev-1", websocket=mobile_ws_1),
        )
        routing.register_mobile(
            "a1",
            "events",
            MobileClient(device_id="dev-2", websocket=mobile_ws_2),
        )

        count = await routing.disconnect_agent("a1")

        # 1 agent + 2 mobiles
        assert count == 3
        assert routing.is_agent_online("a1") is False
        for ws in (agent_ws, mobile_ws_1, mobile_ws_2):
            ws.send_text.assert_awaited_once_with(
                json.dumps({"reason": "admin_revoked"})
            )
            ws.close.assert_awaited_once_with(
                code=4010, reason="admin_revoked"
            )


    async def test_agent_control_is_written_on_the_socket_before_close(
        self, routing: RoutingTable
    ):
        agent_ws = _RecordingWs()
        conn = routing.register_agent("a1", agent_ws)
        mobile_ws = _RecordingWs()
        routing.register_mobile(
            "a1",
            "session_io:s1",
            MobileClient(device_id="dev-1", websocket=mobile_ws),
        )

        await routing.disconnect_agent(
            "a1",
            agent_control={"type": "agent_uninstall"},
            agent_reason_code="agent_uninstalled",
        )

        assert [kind for kind, _ in agent_ws.events] == ["bytes", "text", "close"]
        channel, session_id, payload = decode(agent_ws.events[0][1])
        assert channel == CH_CONTROL
        assert session_id == ""
        assert json.loads(payload) == {"type": "agent_uninstall"}
        assert agent_ws.events[1][1] == json.dumps({"reason": "agent_uninstalled"})
        assert agent_ws.events[2][1] == (4010, "agent_uninstalled")
        # Bypasses the writer queue, which dies with the close.
        assert conn.send_queue.empty()
        # Mobiles keep the plain admin_revoked reason and get no control frame.
        assert mobile_ws.events == [
            ("text", json.dumps({"reason": "admin_revoked"})),
            ("close", (4010, "admin_revoked")),
        ]

    async def test_agent_control_send_failure_still_closes(
        self, routing: RoutingTable
    ):
        agent_ws = _RecordingWs(fail_bytes=True)
        routing.register_agent("a1", agent_ws)

        count = await routing.disconnect_agent(
            "a1",
            agent_control={"type": "agent_uninstall"},
            agent_reason_code="agent_uninstalled",
        )

        assert count == 1
        assert agent_ws.events[-1] == ("close", (4010, "agent_uninstalled"))
        assert routing.is_agent_online("a1") is False


class _RecordingWs:
    """WebSocket fake that records every send/close in call order."""

    def __init__(self, *, fail_bytes: bool = False) -> None:
        self.events: list[tuple[str, object]] = []
        self._fail_bytes = fail_bytes

    async def send_bytes(self, data: bytes) -> None:
        if self._fail_bytes:
            raise RuntimeError("socket already gone")
        self.events.append(("bytes", data))

    async def send_text(self, data: str) -> None:
        self.events.append(("text", data))

    async def close(self, code: int, reason: str) -> None:
        self.events.append(("close", (code, reason)))


class TestSafeSendReasonAndClose:
    """The shared helper used by both disconnect_user and disconnect_agent.
    Each step is wrapped in try/except so an already-closed socket is a no-op.
    """

    async def test_sends_then_closes(self):
        ws = _admin_ws()
        await safe_send_reason_and_close(ws, 4010, "admin_revoked")
        ws.send_text.assert_awaited_once_with(
            json.dumps({"reason": "admin_revoked"})
        )
        ws.close.assert_awaited_once_with(
            code=4010, reason="admin_revoked"
        )

    async def test_swallows_send_error(self):
        ws = MagicMock()
        ws.send_text = AsyncMock(side_effect=RuntimeError("dead"))
        ws.close = AsyncMock()
        # Must not raise — admin paths cannot let one bad socket fail the bulk close.
        await safe_send_reason_and_close(ws, 4001, "boot")
        ws.close.assert_awaited_once_with(code=4001, reason="boot")

    async def test_swallows_close_error(self):
        ws = MagicMock()
        ws.send_text = AsyncMock()
        ws.close = AsyncMock(side_effect=RuntimeError("dead"))
        await safe_send_reason_and_close(ws, 4001, "boot")
        ws.send_text.assert_awaited_once()
