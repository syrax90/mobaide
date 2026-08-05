"""Tests for the agent-emitted ``user_event`` control message.

Pins the relay contract: an allowlisted kind is broadcast to the owner's
user-scoped clients with ``agent_id`` stamped from the authenticated tunnel,
and the push dispatcher is always invoked, told which devices those sockets
already reach (WebSocket delivery wins per device, never per account).
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from central_server.relay.agent_ws import (
    _handle_control_json,
    _handle_user_event,
)
from central_server.relay.routing import RoutingTable, UserClient


AGENT_ID = "agent-1"
OWNER = "user-owner"


def _make_ws(dispatcher) -> MagicMock:
    ws = MagicMock()
    ws.app.state.push_dispatcher = dispatcher
    return ws


def _make_dispatcher() -> MagicMock:
    dispatcher = MagicMock()
    dispatcher.schedule_attention = MagicMock()
    return dispatcher


def _make_user_client(
    user_id: str = OWNER, bound_device_id: str | None = None
) -> UserClient:
    ws = MagicMock()
    ws.close = AsyncMock()
    return UserClient(
        user_id=user_id,
        device_id="dev-x",
        websocket=ws,
        bound_device_id=bound_device_id,
    )


def _ctrl(**overrides) -> dict:
    ctrl = {
        "type": "user_event",
        "kind": "session.attention",
        "payload": {
            "session_id": "s1",
            "reason": "turn_finished",
            "title": "Fix the bug",
            "project_name": "proj",
        },
    }
    ctrl.update(overrides)
    return ctrl


@pytest.fixture
def routing() -> RoutingTable:
    table = RoutingTable()
    conn = table.register_agent(AGENT_ID, MagicMock())
    conn.owner_user_id = OWNER
    return table


class TestHandleUserEvent:
    def test_broadcasts_to_owner_clients_with_stamped_agent_id(self, routing):
        client = _make_user_client()
        routing.register_user_client(OWNER, client)
        dispatcher = _make_dispatcher()

        _handle_user_event(_make_ws(dispatcher), AGENT_ID, _ctrl(), routing)

        envelope = json.loads(client.send_queue.get_nowait())
        assert envelope["type"] == "session.attention"
        assert envelope["payload"] == {
            "session_id": "s1",
            "reason": "turn_finished",
            "title": "Fix the bug",
            "project_name": "proj",
            "agent_id": AGENT_ID,
        }

    def test_agent_supplied_agent_id_is_overwritten(self, routing):
        client = _make_user_client()
        routing.register_user_client(OWNER, client)
        ctrl = _ctrl()
        ctrl["payload"]["agent_id"] = "spoofed-agent"

        _handle_user_event(
            _make_ws(_make_dispatcher()), AGENT_ID, ctrl, routing
        )

        envelope = json.loads(client.send_queue.get_nowait())
        assert envelope["payload"]["agent_id"] == AGENT_ID

    def test_bound_socket_excludes_only_its_own_device(self, routing):
        # The regression this replaces: one awake client used to mute the push
        # for every other device on the account, so a second phone learnt about
        # the attention only when it was next opened.
        routing.register_user_client(
            OWNER, _make_user_client(bound_device_id="dev-a")
        )
        dispatcher = _make_dispatcher()

        _handle_user_event(_make_ws(dispatcher), AGENT_ID, _ctrl(), routing)

        dispatcher.schedule_attention.assert_called_once()
        assert dispatcher.schedule_attention.call_args.kwargs[
            "exclude_device_ids"
        ] == {"dev-a"}

    def test_unbound_socket_excludes_no_device(self, routing):
        # A client that sent no verifiable device hint cannot stand in for any
        # device, so it must not keep one from being pushed.
        routing.register_user_client(OWNER, _make_user_client())
        dispatcher = _make_dispatcher()

        _handle_user_event(_make_ws(dispatcher), AGENT_ID, _ctrl(), routing)

        dispatcher.schedule_attention.assert_called_once()
        assert (
            dispatcher.schedule_attention.call_args.kwargs["exclude_device_ids"]
            == set()
        )

    def test_no_user_socket_schedules_push(self, routing):
        dispatcher = _make_dispatcher()

        _handle_user_event(_make_ws(dispatcher), AGENT_ID, _ctrl(), routing)

        dispatcher.schedule_attention.assert_called_once()
        user_id, payload = dispatcher.schedule_attention.call_args[0]
        assert user_id == OWNER
        assert payload["session_id"] == "s1"
        assert payload["agent_id"] == AGENT_ID
        assert (
            dispatcher.schedule_attention.call_args.kwargs["exclude_device_ids"]
            == set()
        )

    def test_other_users_clients_receive_nothing(self, routing):
        foreign = _make_user_client("user-other", bound_device_id="dev-other")
        routing.register_user_client("user-other", foreign)
        dispatcher = _make_dispatcher()

        _handle_user_event(_make_ws(dispatcher), AGENT_ID, _ctrl(), routing)

        assert foreign.send_queue.empty()
        # The owner has no socket, so the push path fires for the owner — and
        # another account's live device never counts as reached.
        dispatcher.schedule_attention.assert_called_once_with(
            OWNER,
            dispatcher.schedule_attention.call_args[0][1],
            exclude_device_ids=set(),
        )

    @pytest.mark.parametrize(
        "ctrl",
        [
            _ctrl(kind="template.created"),
            _ctrl(kind="agent.status_changed"),
            _ctrl(kind=None),
            _ctrl(payload=None),
            _ctrl(payload="not a dict"),
            _ctrl(payload={"reason": "turn_finished"}),
            _ctrl(payload={"session_id": "s1"}),
            _ctrl(payload={"session_id": "", "reason": "turn_finished"}),
            _ctrl(payload={"session_id": "s1", "reason": 7}),
        ],
    )
    def test_invalid_kind_or_payload_is_dropped(self, routing, ctrl):
        client = _make_user_client()
        routing.register_user_client(OWNER, client)
        dispatcher = _make_dispatcher()

        _handle_user_event(_make_ws(dispatcher), AGENT_ID, ctrl, routing)

        assert client.send_queue.empty()
        dispatcher.schedule_attention.assert_not_called()

    def test_non_string_optional_fields_are_dropped(self, routing):
        client = _make_user_client()
        routing.register_user_client(OWNER, client)
        ctrl = _ctrl()
        ctrl["payload"]["title"] = 42
        ctrl["payload"]["exit_reason"] = ["weird"]

        _handle_user_event(
            _make_ws(_make_dispatcher()), AGENT_ID, ctrl, routing
        )

        payload = json.loads(client.send_queue.get_nowait())["payload"]
        assert "title" not in payload
        assert "exit_reason" not in payload

    def test_unknown_agent_connection_is_dropped(self):
        routing = RoutingTable()  # agent never registered → no owner
        dispatcher = _make_dispatcher()

        _handle_user_event(_make_ws(dispatcher), AGENT_ID, _ctrl(), routing)

        dispatcher.schedule_attention.assert_not_called()

    def test_missing_owner_is_dropped(self, routing):
        routing.get_agent(AGENT_ID).owner_user_id = None
        dispatcher = _make_dispatcher()

        _handle_user_event(_make_ws(dispatcher), AGENT_ID, _ctrl(), routing)

        dispatcher.schedule_attention.assert_not_called()

    def test_missing_push_dispatcher_state_is_safe(self, routing):
        ws = MagicMock()
        ws.app.state = object()  # no push_dispatcher attribute

        _handle_user_event(ws, AGENT_ID, _ctrl(), routing)


class TestControlDispatch:
    async def test_control_json_routes_user_event(self, routing):
        client = _make_user_client()
        routing.register_user_client(OWNER, client)

        await _handle_control_json(
            _make_ws(_make_dispatcher()), AGENT_ID, _ctrl(), routing
        )

        assert json.loads(client.send_queue.get_nowait())["type"] == (
            "session.attention"
        )
