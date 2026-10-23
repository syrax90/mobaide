"""End-to-end test: removing an agent with the uninstall option.

The live path uses a real agent subprocess of its own (``spawn_agent``), never
the shared ``e2e_stack`` agent. It runs with ``MOBAIDE_ENV=test`` from a scratch
prefix, so it advertises the ``uninstall`` capability but, not being an
installed release, deletes nothing when ordered to.

The offline path needs no agent process: a raw tunnel client presents the
removed agent's old credentials, exactly as a real agent does when it comes
back online.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest
import websockets

from mock_mobile_client import PROTOCOL_VERSION

pytestmark = pytest.mark.timeout(120)

# Short reconnect backoff, so an agent that wrongly kept reconnecting after the
# order would do so many times inside the observation window.
_FAST_RECONNECT_ENV = {"RELAY_RECONNECT_BASE": "0.1", "RELAY_RECONNECT_MAX": "0.3"}
_NO_RECONNECT_WINDOW_S = 2.0


def _wait_until(predicate, *, timeout: float = 30.0, interval: float = 0.25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    raise TimeoutError(f"condition not met within {timeout}s")


def _agent_summary(client, agent_id: str) -> dict | None:
    return next((a for a in client.list_agents() if a["agent_id"] == agent_id), None)


async def _authenticate(ws_base: str, agent_id: str, token: str):
    """Run the agent side of the tunnel handshake; return every text frame the
    relay sent before closing, plus the close code and reason."""
    frames: list[dict] = []
    async with websockets.connect(f"{ws_base}/ws/agent-tunnel") as ws:
        await ws.send(json.dumps({
            "type": "auth",
            "agent_id": agent_id,
            "token": token,
            "protocol_version": PROTOCOL_VERSION,
            "agent_version": "0.0.0-e2e",
            "capabilities": {"uninstall": True},
        }))
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                frames.append(json.loads(raw))
        except websockets.ConnectionClosed:
            pass
        return frames, ws.close_code, ws.close_reason


def test_connected_test_mode_agent_advertises_uninstall(e2e_stack, spawn_agent):
    agent = spawn_agent("e2e-uninstall-advertise")

    summary = _wait_until(
        lambda: (s := _agent_summary(e2e_stack.client, agent.agent_id))
        and s["online"]
        and s,
    )

    assert summary["supports_uninstall"] is True


def test_live_uninstall_order_stops_the_agent_for_good(e2e_stack, spawn_agent):
    client = e2e_stack.client
    agent = spawn_agent("e2e-uninstall-live", extra_env=_FAST_RECONNECT_ENV)
    _wait_until(
        lambda: (s := _agent_summary(client, agent.agent_id)) and s["online"]
    )

    client.delete_agent(agent.agent_id, uninstall=True)

    _wait_until(lambda: "relay.uninstall_requested" in agent.log_text())
    time.sleep(_NO_RECONNECT_WINDOW_S)
    after_order = agent.log_text().split("relay.uninstall_requested", 1)[1]
    assert "relay.reconnecting" not in after_order
    assert "agent.uninstall.skipped_not_installed" in after_order
    assert agent.prefix.is_dir()
    assert _agent_summary(client, agent.agent_id) is None


def test_offline_agent_gets_the_order_when_it_next_connects(e2e_stack):
    client = e2e_stack.client
    agent_id, agent_token = client.create_agent("e2e-uninstall-offline")

    client.delete_agent(agent_id, uninstall=True)
    frames, code, reason = asyncio.run(
        _authenticate(client.ws_base, agent_id, agent_token)
    )

    assert frames == [{"type": "agent_uninstall"}]
    assert (code, reason) == (4001, "Agent removed")


def test_plain_removal_sends_no_order_to_old_credentials(e2e_stack):
    client = e2e_stack.client
    agent_id, agent_token = client.create_agent("e2e-plain-removal")

    client.delete_agent(agent_id, uninstall=False)
    frames, code, reason = asyncio.run(
        _authenticate(client.ws_base, agent_id, agent_token)
    )

    assert frames == []
    assert (code, reason) == (4001, "Invalid agent credentials")
