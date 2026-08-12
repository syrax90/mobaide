"""CH_SESSION_IO_TARGETED delivery and the per-client attach replay (proto-v8).

Two layers, matching how much stack each behavior needs:

* ``_reader`` unit tests with a scripted agent socket and a real
  ``RoutingTable`` — targeted frames must reach exactly the mobile whose
  ``conn_id`` matches, and malformed frames must be dropped without killing
  the dispatch loop.
* Full ``/ws/agent-tunnel`` handshakes over TestClient — an agent that
  negotiated protocol >= 8 gets the attach replay one-per-client with
  ``conn_id``; a v7 agent keeps the legacy one-per-session replay without it
  (it would answer per-client attaches with N broadcast re-handshakes).
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from starlette.testclient import TestClient

from central_server.relay.agent_ws import _reader
from central_server.relay.envelope import (
    CH_CONTROL,
    CH_SESSION_IO,
    decode,
    encode,
    encode_targeted,
)
from central_server.relay.routing import MobileClient, RoutingTable


class _ScriptedAgentSocket:
    """Yields the scripted messages, then a websocket.disconnect."""

    def __init__(self, messages: list[dict]) -> None:
        self._messages = list(messages)

    async def receive(self) -> dict:
        if self._messages:
            return self._messages.pop(0)
        return {"type": "websocket.disconnect"}


def _mobile(device_id: str) -> MobileClient:
    return MobileClient(device_id=device_id, websocket=MagicMock())


def _drain(client: MobileClient) -> list[bytes | str]:
    out: list[bytes | str] = []
    while not client.send_queue.empty():
        out.append(client.send_queue.get_nowait())
    return out


class TestReaderTargetedDispatch:
    async def test_targeted_frame_reaches_only_the_matching_conn_id(self):
        routing = RoutingTable()
        target, bystander = _mobile("d1"), _mobile("d2")
        routing.register_mobile("agent-1", "session_io:s1", target)
        routing.register_mobile("agent-1", "session_io:s1", bystander)
        frame = b"s" + b'{"status":"running"}'
        ws = _ScriptedAgentSocket(
            [{"bytes": encode_targeted("s1", target.conn_id, frame)}]
        )

        await _reader(ws, "agent-1", routing)

        # The unwrapped inner frame — not the targeted envelope payload —
        # is what the mobile receives, indistinguishable from a broadcast.
        assert _drain(target) == [frame]
        assert _drain(bystander) == []

    async def test_unknown_conn_id_is_dropped_silently(self):
        routing = RoutingTable()
        attached = _mobile("d1")
        routing.register_mobile("agent-1", "session_io:s1", attached)
        ws = _ScriptedAgentSocket(
            [{"bytes": encode_targeted("s1", "already-detached", b"e")}]
        )

        await _reader(ws, "agent-1", routing)

        assert _drain(attached) == []

    async def test_malformed_targeted_payload_does_not_kill_the_loop(self):
        # A truncated cid must be skipped; the broadcast frame queued behind
        # it must still be dispatched.
        routing = RoutingTable()
        attached = _mobile("d1")
        routing.register_mobile("agent-1", "session_io:s1", attached)
        truncated = encode(0x05, "s1", b"\x20short")
        broadcast = encode(CH_SESSION_IO, "s1", b"o hello")
        ws = _ScriptedAgentSocket([{"bytes": truncated}, {"bytes": broadcast}])

        await _reader(ws, "agent-1", routing)

        assert _drain(attached) == [b"o hello"]

    async def test_targeted_frame_for_another_session_is_not_crossed(self):
        # conn_id matching is scoped to the envelope's session channel; a
        # client attached to a different session must never receive it.
        routing = RoutingTable()
        other = _mobile("d1")
        routing.register_mobile("agent-1", "session_io:s2", other)
        ws = _ScriptedAgentSocket(
            [{"bytes": encode_targeted("s1", other.conn_id, b"e")}]
        )

        await _reader(ws, "agent-1", routing)

        assert _drain(other) == []


def _auth_and_read_attaches(
    app, agent_id: str, token: str, protocol_version: int, expected: int
) -> list[dict]:
    client = TestClient(app)
    attaches: list[dict] = []
    with client.websocket_connect("/ws/agent-tunnel") as ws:
        ws.send_text(json.dumps({
            "type": "auth",
            "agent_id": agent_id,
            "token": token,
            "protocol_version": protocol_version,
        }))
        assert json.loads(ws.receive_text())["type"] == "auth_ok"
        for _ in range(expected):
            channel, sid, payload = decode(ws.receive_bytes())
            assert channel == CH_CONTROL
            assert sid == ""
            ctrl = json.loads(payload)
            assert ctrl["type"] == "mobile_session_attach"
            attaches.append(ctrl)
    return attaches


class TestReconnectAttachReplay:
    async def test_v8_agent_gets_one_attach_per_client_with_conn_id(
        self, app, registered_agent
    ):
        agent_id, raw_token = registered_agent
        c1, c2 = _mobile("d1"), _mobile("d2")
        app.state.routing.register_mobile(agent_id, "session_io:s1", c1)
        app.state.routing.register_mobile(agent_id, "session_io:s1", c2)

        attaches = _auth_and_read_attaches(
            app, agent_id, raw_token, protocol_version=8, expected=2
        )

        assert [a["session_id"] for a in attaches] == ["s1", "s1"]
        assert {a["conn_id"] for a in attaches} == {c1.conn_id, c2.conn_id}

    async def test_v7_agent_keeps_the_per_session_replay_without_conn_id(
        self, app, registered_agent
    ):
        agent_id, raw_token = registered_agent
        app.state.routing.register_mobile(agent_id, "session_io:s1", _mobile("d1"))
        app.state.routing.register_mobile(agent_id, "session_io:s1", _mobile("d2"))

        attaches = _auth_and_read_attaches(
            app, agent_id, raw_token, protocol_version=7, expected=1
        )

        assert attaches == [
            {"type": "mobile_session_attach", "session_id": "s1"},
        ]
