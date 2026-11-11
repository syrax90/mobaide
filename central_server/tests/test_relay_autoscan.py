"""Tests for the ``/relay/ws/autoscan/{agent_id}`` mobile relay endpoint.

The handler (``relay_autoscan`` in ``central_server/relay/mobile_ws.py``)
had zero coverage before these tests.  It is the central server's side of
the auto-scan bridge: it authenticates the mobile, allocates a
``subscription_id``, tells the agent to open a per-mobile stream via a
CH_CONTROL ``open_autoscan`` message, and enqueues mobile text frames as
CH_AUTOSCAN envelopes to the agent.  On teardown it unregisters the
mobile and sends ``close_autoscan`` best-effort so the agent can cancel
its matching stream task.

These are mostly split into:

* **Auth gating** — runs end-to-end via ``TestClient`` because the gates
  reject before any cross-loop ``asyncio.Queue`` operation happens.
* **Happy path / teardown** — uses the Starlette ``TestClient`` plus a
  pre-registered agent whose ``send_queue`` we read AFTER the TestClient
  has fully closed (so the background event loop is joined and the
  ``finally``'s ``close_autoscan`` enqueue is visible).

Run with: ``python -m pytest tests/test_relay_autoscan.py -x -q``.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient

from central_server.relay.envelope import (
    CH_AUTOSCAN,
    CH_CONTROL,
    decode,
)
from central_server.relay.mobile_proxy import MobileProxy
from central_server.relay.routing import AgentConnection


def _mock_agent_ws() -> MagicMock:
    """Stand-in for an agent's tunnel WebSocket — only the async close surface."""
    ws = MagicMock()
    ws.close = AsyncMock()
    return ws


async def _mobile_access_token(app, agent_id: str) -> str:
    """Mint a mobile_access JWT for the viewer user bound to ``agent_id``."""
    owner = await app.state.db.get_user_by_email("viewer@test.local")
    access, _refresh = await app.state.auth_service.create_mobile_tokens(
        owner["id"], agent_id, owner["id"]
    )
    return access


def _drain_agent_queue(agent_conn: AgentConnection) -> list[tuple[int, str, bytes]]:
    """Snapshot and return every envelope currently queued on the agent side."""
    out: list[tuple[int, str, bytes]] = []
    while True:
        try:
            envelope = agent_conn.send_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        try:
            out.append(decode(envelope))
        except ValueError:
            continue
    return out


# --------------------------------------------------------------------------- #
# Auth and gating (pre-registration failure paths — TestClient-safe)
# --------------------------------------------------------------------------- #


class TestAutoscanAuthGating:
    async def test_token_for_wrong_agent_closes_4003(self, app, registered_agent):
        """Token valid for agent A must not authorize a ws on agent B."""
        agent_id, _ = registered_agent
        access = await _mobile_access_token(app, agent_id)

        tc = TestClient(app)
        with pytest.raises(Exception):
            with tc.websocket_connect(
                f"/relay/ws/autoscan/other-agent?token={access}",
            ) as ws:
                ws.receive_text()

    async def test_agent_offline_closes_4012(self, app, registered_agent):
        """No agent connection registered in routing → handler closes 4012
        (``WS_CLOSE_AGENT_TRANSIENT_OFFLINE``).

        4012 — and crucially **not** 4010 (``WS_CLOSE_ADMIN_REVOKED``) —
        because agent offline is a transient, recoverable state: the
        agent process may come back any moment and the mobile is
        expected to keep its exponential-backoff reconnect loop alive.
        Collapsing this onto 4010 was a latent bug masked only by the
        Android side previously ignoring the code; once the client
        started treating 4010 as destructive admin-revoke, the bug
        would have permanently killed every per-agent socket on the
        device during an agent restart. ``get_agent`` returns None,
        the handler unregisters the mobile channel key (which was
        just created) and closes the socket.
        """
        agent_id, _ = registered_agent
        access = await _mobile_access_token(app, agent_id)
        assert app.state.routing.get_agent(agent_id) is None

        tc = TestClient(app)
        with pytest.raises(Exception):
            with tc.websocket_connect(
                f"/relay/ws/autoscan/{agent_id}?token={access}",
            ) as ws:
                ws.receive_text()


# --------------------------------------------------------------------------- #
# Agent-control emission — uses a shared event loop by registering the
# agent connection and letting TestClient drive the endpoint; reads the
# queue post-close so the background loop has finished mutating it.
# --------------------------------------------------------------------------- #


class TestOpenAndCloseControl:
    async def test_open_and_close_autoscan_bracket_connection(
        self, app, registered_agent
    ):
        """On a successful mobile autoscan connection, the handler must:
          1. Push ONE CH_CONTROL ``open_autoscan`` (with a freshly allocated
             ``subscription_id``) onto the agent's ``send_queue``.
          2. On disconnect, push ONE CH_CONTROL ``close_autoscan`` with the
             SAME ``subscription_id`` so the agent can cancel its matching
             stream task.

        Without step 2, every mobile disconnect leaks a task on the agent
        side. Without matching subscription ids the agent cannot correlate
        close messages to the right stream.
        """
        agent_id, _ = registered_agent
        agent_ws = _mock_agent_ws()
        agent_conn = app.state.routing.register_agent(agent_id, agent_ws)

        access = await _mobile_access_token(app, agent_id)
        app.state.mobile_proxy = MobileProxy(app.state.routing)

        tc = TestClient(app)
        with tc.websocket_connect(
            f"/relay/ws/autoscan/{agent_id}?token={access}",
        ):
            # Let the handler run its open path. The with-block exit runs
            # the handler's ``finally`` and enqueues ``close_autoscan``.
            await asyncio.sleep(0.05)

        # After TestClient's WS context has closed, drain the agent's queue.
        envelopes = _drain_agent_queue(agent_conn)

        control_msgs = []
        for channel, _sid, payload in envelopes:
            if channel != CH_CONTROL:
                continue
            try:
                control_msgs.append(json.loads(payload))
            except json.JSONDecodeError:
                continue

        open_msgs = [m for m in control_msgs if m.get("type") == "open_autoscan"]
        close_msgs = [m for m in control_msgs if m.get("type") == "close_autoscan"]

        assert len(open_msgs) == 1, f"expected 1 open_autoscan, got {control_msgs}"
        assert len(close_msgs) == 1, f"expected 1 close_autoscan, got {control_msgs}"

        sub_id = open_msgs[0]["subscription_id"]
        assert isinstance(sub_id, str) and len(sub_id) == 32  # uuid4().hex
        assert close_msgs[0]["subscription_id"] == sub_id


# --------------------------------------------------------------------------- #
# Mobile → agent forwarding (CH_AUTOSCAN envelopes)
# --------------------------------------------------------------------------- #


class TestMobileToAgentForwarding:
    async def test_mobile_text_frame_forwarded_as_ch_autoscan_envelope(
        self, app, registered_agent
    ):
        """Every text frame the mobile sends must be wrapped in a CH_AUTOSCAN
        envelope tagged with the connection's ``subscription_id`` and pushed
        into the agent's ``send_queue``.  Without this branch, mobile-origin
        ``subscribe`` / ``resume_from_seq`` frames never reach the agent's
        ``run_autoscan_stream`` reader.
        """
        agent_id, _ = registered_agent
        agent_ws = _mock_agent_ws()
        agent_conn = app.state.routing.register_agent(agent_id, agent_ws)

        access = await _mobile_access_token(app, agent_id)
        app.state.mobile_proxy = MobileProxy(app.state.routing)

        tc = TestClient(app)
        with tc.websocket_connect(
            f"/relay/ws/autoscan/{agent_id}?token={access}",
        ) as ws:
            ws.send_text(json.dumps({"subscribe": ["projectA"]}))
            # Give the ASGI background loop a chance to process the text
            # frame and enqueue its CH_AUTOSCAN envelope.
            await asyncio.sleep(0.1)

        envelopes = _drain_agent_queue(agent_conn)

        autoscan_envs = [
            (sid, payload)
            for (ch, sid, payload) in envelopes if ch == CH_AUTOSCAN
        ]
        assert len(autoscan_envs) == 1, (
            f"expected exactly 1 CH_AUTOSCAN envelope, got {envelopes}"
        )
        sid_on_wire, payload = autoscan_envs[0]
        assert len(sid_on_wire) == 32
        assert json.loads(payload) == {"subscribe": ["projectA"]}

        # Same subscription_id carried on every control message for the run.
        open_sub = next(
            json.loads(p)["subscription_id"]
            for (ch, _s, p) in envelopes
            if ch == CH_CONTROL and b"open_autoscan" in p
        )
        assert sid_on_wire == open_sub


# --------------------------------------------------------------------------- #
# Routing registration lifecycle
# --------------------------------------------------------------------------- #


class TestRoutingRegistration:
    async def test_channel_registered_and_released_around_connection(
        self, app, registered_agent
    ):
        """The mobile client must appear under the
        ``autoscan:{subscription_id}`` channel key while connected, and be
        fully released after disconnect (the ``finally`` in
        ``relay_autoscan`` calls ``unregister_mobile``).
        """
        agent_id, _ = registered_agent
        agent_ws = _mock_agent_ws()
        agent_conn = app.state.routing.register_agent(agent_id, agent_ws)

        access = await _mobile_access_token(app, agent_id)
        app.state.mobile_proxy = MobileProxy(app.state.routing)

        tc = TestClient(app)
        # Capture channels-for-agent before we connect so we can diff.
        baseline_channels = len(
            app.state.routing._mobile_clients.get(agent_id, {})
        )

        with tc.websocket_connect(
            f"/relay/ws/autoscan/{agent_id}?token={access}",
        ):
            # Let the handler register the mobile client.
            await asyncio.sleep(0.05)
            # Mid-connection: there must be an autoscan:* channel now.
            channels = app.state.routing._mobile_clients.get(agent_id, {})
            autoscan_channels = [
                k for k in channels if k.startswith("autoscan:")
            ]
            assert len(autoscan_channels) == 1, (
                f"expected 1 autoscan channel, got {list(channels)}"
            )

        # After close, ALL autoscan:* channels for this agent must be gone.
        channels_after = app.state.routing._mobile_clients.get(agent_id, {})
        autoscan_after = [
            k for k in channels_after if k.startswith("autoscan:")
        ]
        assert autoscan_after == []
        assert len(channels_after) == baseline_channels


class TestAgentOriginatedCloseAutoscan:
    """When the agent sends a ``close_autoscan`` control message (e.g. after
    rejecting an ``open_autoscan`` for ``capacity_reached`` or
    ``autoscan_not_configured``), the central server must close every mobile
    WebSocket registered under that ``subscription_id`` so the client sees a
    clean disconnect instead of hanging with no stream serving it.
    """

    async def test_close_autoscan_control_closes_matching_mobile_ws(
        self, app, registered_agent
    ):
        from central_server.relay.agent_ws import (
            AUTOSCAN_DECLINED_CODE,
            _handle_control_json,
        )
        from central_server.relay.routing import MobileClient

        agent_id, _ = registered_agent

        subscription_id = "sub-close-test"
        channel_key = f"autoscan:{subscription_id}"

        mobile_ws = MagicMock()
        mobile_ws.close = AsyncMock()
        client = MobileClient(device_id="device-xyz", websocket=mobile_ws)
        app.state.routing.register_mobile(agent_id, channel_key, client)

        ctrl = {
            "type": "close_autoscan",
            "subscription_id": subscription_id,
            "reason": "capacity_reached",
        }
        await _handle_control_json(
            websocket=MagicMock(app=app),
            agent_id=agent_id,
            ctrl=ctrl,
            routing=app.state.routing,
        )

        mobile_ws.close.assert_awaited_once()
        _args, kwargs = mobile_ws.close.await_args
        assert kwargs.get("code") == AUTOSCAN_DECLINED_CODE
        assert kwargs.get("reason") == "capacity_reached"

        app.state.routing.unregister_mobile(agent_id, channel_key, client)
