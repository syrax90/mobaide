"""Per-connection identity on the routing table (proto-v8).

``MobileClient.conn_id`` is what lets the agent address one client
(CH_SESSION_IO_TARGETED) and keep an accurate attached-client count;
``iter_attached_session_clients`` is the per-client counterpart of
``list_attached_sessions`` used by the v8 agent-reconnect replay.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from central_server.relay.routing import MobileClient, RoutingTable


def _client(device_id: str = "device-1") -> MobileClient:
    return MobileClient(device_id=device_id, websocket=MagicMock())


class TestConnId:
    def test_conn_id_is_generated_and_unique_per_connection(self):
        # Per connection, not per device: two sockets from one device (two
        # browser tabs) must carry distinct ids or targeted frames and the
        # attached-client count both collapse them into one.
        a = _client("same-device")
        b = _client("same-device")

        assert a.conn_id
        assert b.conn_id
        assert a.conn_id != b.conn_id

    def test_conn_id_fits_the_targeted_envelope_cid_prefix(self):
        # encode_targeted stores the cid length in one byte; the generated id
        # must always fit or the agent could never target this client.
        cid = _client().conn_id
        assert 0 < len(cid.encode("utf-8")) <= 255


class TestIterAttachedSessionClients:
    def test_yields_one_pair_per_attached_client(self):
        routing = RoutingTable()
        c1, c2, c3 = _client("d1"), _client("d2"), _client("d3")
        routing.register_mobile("agent-1", "session_io:s1", c1)
        routing.register_mobile("agent-1", "session_io:s1", c2)
        routing.register_mobile("agent-1", "session_io:s2", c3)

        pairs = routing.iter_attached_session_clients("agent-1")

        assert sorted(sid for sid, _ in pairs) == ["s1", "s1", "s2"]
        assert {c.conn_id for _, c in pairs} == {
            c1.conn_id, c2.conn_id, c3.conn_id,
        }
        # The per-session view stays coarse — one entry per session.
        assert sorted(routing.list_attached_sessions("agent-1")) == ["s1", "s2"]

    def test_ignores_non_session_io_channels(self):
        routing = RoutingTable()
        routing.register_mobile("agent-1", "sessions_list", _client("d1"))
        routing.register_mobile("agent-1", "events", _client("d2"))

        assert routing.iter_attached_session_clients("agent-1") == []

    def test_unknown_agent_yields_nothing(self):
        assert RoutingTable().iter_attached_session_clients("ghost") == []

    def test_unregistered_client_disappears_from_the_iteration(self):
        routing = RoutingTable()
        c1, c2 = _client("d1"), _client("d2")
        routing.register_mobile("agent-1", "session_io:s1", c1)
        routing.register_mobile("agent-1", "session_io:s1", c2)

        routing.unregister_mobile("agent-1", "session_io:s1", c1)

        pairs = routing.iter_attached_session_clients("agent-1")
        assert [(sid, c.conn_id) for sid, c in pairs] == [("s1", c2.conn_id)]
