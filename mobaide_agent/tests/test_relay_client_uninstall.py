"""Tests for the relay's ``agent_uninstall`` order in ``RelayClient``.

* Registering an uninstall callback is what advertises the ``uninstall``
  capability in the auth handshake.
* The order arrives either as the auth reply (agent removed while offline)
  or as a control frame on the live tunnel; both stop reconnecting for good
  and run the callback exactly once.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from mobaide.relay.client import AgentUninstallRequested, RelayClient


@pytest.fixture
def client(
    relay_config,
    mock_session_manager,
    mock_event_bus,
    mock_broadcaster,
    mock_app_state,
):
    return RelayClient(
        config=relay_config,
        session_manager=mock_session_manager,
        event_bus=mock_event_bus,
        broadcaster=mock_broadcaster,
        app_state=mock_app_state,
    )


@pytest.fixture
def uninstall_calls(client) -> list[str]:
    calls: list[str] = []

    async def on_uninstall() -> None:
        calls.append("uninstall")

    client.set_uninstall_callback(on_uninstall)
    return calls


async def _settle(client: RelayClient) -> None:
    if client._uninstall_task is not None:
        await client._uninstall_task


class TestCapabilityAdvertisement:
    async def test_callback_advertises_uninstall_capability(
        self, client, uninstall_calls
    ):
        ws = AsyncMock()
        ws.recv = AsyncMock(return_value=json.dumps({"type": "auth_ok"}))

        await client._authenticate(ws)

        sent = json.loads(ws.send.call_args[0][0])
        assert sent["capabilities"] == {"uninstall": True}


class TestUninstallAsAuthReply:
    async def test_auth_reply_raises_agent_uninstall_requested(self, client):
        ws = AsyncMock()
        ws.recv = AsyncMock(return_value=json.dumps({"type": "agent_uninstall"}))

        with pytest.raises(AgentUninstallRequested):
            await client._authenticate(ws)

    async def test_connect_loop_runs_callback_once_and_stops_reconnecting(
        self, client, uninstall_calls, monkeypatch
    ):
        attempts: list[int] = []

        async def removed_agent_connection() -> None:
            attempts.append(1)
            if len(attempts) > 1:
                client._stopping = True
            raise AgentUninstallRequested("Relay removed this agent")

        monkeypatch.setattr(client, "_run_connection", removed_agent_connection)

        await client._connect_loop()
        await _settle(client)

        assert attempts == [1]
        assert uninstall_calls == ["uninstall"]

    async def test_connect_loop_stops_even_without_a_callback(
        self, client, monkeypatch
    ):
        attempts: list[int] = []

        async def removed_agent_connection() -> None:
            attempts.append(1)
            if len(attempts) > 1:
                client._stopping = True
            raise AgentUninstallRequested("Relay removed this agent")

        monkeypatch.setattr(client, "_run_connection", removed_agent_connection)

        await client._connect_loop()

        assert attempts == [1]


class TestUninstallControlFrame:
    async def test_control_frame_runs_callback_and_stops_reconnecting(
        self, client, uninstall_calls
    ):
        await client._handle_control(
            json.dumps({"type": "agent_uninstall"}).encode(), AsyncMock(),
        )
        await _settle(client)

        assert uninstall_calls == ["uninstall"]
        assert client._stopping is True
        assert client._unknown_control_type_total == 0

    async def test_repeated_order_runs_callback_once(
        self, client, uninstall_calls
    ):
        frame = json.dumps({"type": "agent_uninstall"}).encode()

        await client._handle_control(frame, AsyncMock())
        await client._handle_control(frame, AsyncMock())
        await _settle(client)

        assert uninstall_calls == ["uninstall"]

    async def test_callback_failure_is_swallowed(self, client):
        async def boom() -> None:
            raise RuntimeError("disk gone")

        client.set_uninstall_callback(boom)

        await client._handle_control(
            json.dumps({"type": "agent_uninstall"}).encode(), AsyncMock(),
        )
        await _settle(client)

        assert client._stopping is True
