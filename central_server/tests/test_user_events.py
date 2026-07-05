"""Tests for the user-scoped event channel and supporting plumbing.

Covers:
- RoutingTable.register_user_client / unregister_user_client / get_user_clients
- Database.get_agent_owner
- agent_ws._broadcast_status helper
- /ws/user-events end-to-end delivery (connect, status_changed online/offline,
  user scoping — user B's client receives nothing for user A's agent)
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient

from central_server.db.database import Database
from central_server.relay.agent_ws import _broadcast_status
from central_server.relay.envelope import PROTOCOL_VERSION
from central_server.relay.routing import RoutingTable, UserClient

from tests.conftest import (
    TEST_ADMIN_EMAIL,
    TEST_ADMIN_PASSWORD,
    TEST_VIEWER_EMAIL,
    TEST_VIEWER_PASSWORD,
)


# ---- Unit: RoutingTable user-client registry --------------------------------


@pytest.fixture
def routing() -> RoutingTable:
    return RoutingTable()


def _make_user_client(user_id: str, device_id: str = "dev-x") -> UserClient:
    ws = MagicMock()
    ws.close = AsyncMock()
    return UserClient(user_id=user_id, device_id=device_id, websocket=ws)


class TestRoutingUserClients:
    def test_per_user_scoping_user_b_does_not_see_user_a(
        self, routing: RoutingTable
    ):
        a = _make_user_client("user-a", "dev-a")
        b = _make_user_client("user-b", "dev-b")
        routing.register_user_client("user-a", a)
        routing.register_user_client("user-b", b)

        assert routing.get_user_clients("user-a") == {a}
        assert routing.get_user_clients("user-b") == {b}
        # Each set is the correct size; no cross-pollination.
        assert a not in routing.get_user_clients("user-b")
        assert b not in routing.get_user_clients("user-a")

    def test_multiple_clients_same_user(self, routing: RoutingTable):
        c1 = _make_user_client("user-a", "dev-1")
        c2 = _make_user_client("user-a", "dev-2")
        routing.register_user_client("user-a", c1)
        routing.register_user_client("user-a", c2)
        clients = routing.get_user_clients("user-a")
        assert len(clients) == 2
        assert c1 in clients and c2 in clients

    def test_unregister_removes_client(self, routing: RoutingTable):
        client = _make_user_client("user-a")
        routing.register_user_client("user-a", client)
        routing.unregister_user_client("user-a", client)
        assert routing.get_user_clients("user-a") == set()

    def test_unregister_cleans_up_empty_user_bucket(
        self, routing: RoutingTable
    ):
        # After unregistering the last client, the user_id key should be
        # cleaned up (so the registry doesn't grow without bound).
        client = _make_user_client("user-a")
        routing.register_user_client("user-a", client)
        routing.unregister_user_client("user-a", client)
        # Internal bucket gone — exercised by `get_user_clients` returning
        # an empty set even though we no longer hold a ref to the dict entry.
        assert "user-a" not in routing._user_clients


# ---- Unit: Database.get_agent_owner -----------------------------------------


@pytest.fixture
async def owner_db(tmp_path) -> Database:
    db = Database(str(tmp_path / "owner_test.db"))
    await db.connect()
    yield db
    await db.close()


class TestGetAgentOwner:
    async def test_returns_owner_for_existing_agent(self, owner_db: Database):
        owner = await owner_db.create_user(
            email="owner@test.local", password_hash="h"
        )
        agent_id, _ = await owner_db.register_agent("my-agent", owner)

        result = await owner_db.get_agent_owner(agent_id)
        assert result == owner

    async def test_returns_none_for_unknown_agent(self, owner_db: Database):
        assert await owner_db.get_agent_owner("nonexistent") is None

    async def test_returns_correct_owner_among_multiple(
        self, owner_db: Database
    ):
        # Regression guard: the SELECT must filter by agent_id, not just
        # pick the first row.
        o1 = await owner_db.create_user(email="a@test.local", password_hash="h")
        o2 = await owner_db.create_user(email="b@test.local", password_hash="h")
        a1, _ = await owner_db.register_agent("a1", o1)
        a2, _ = await owner_db.register_agent("a2", o2)

        assert await owner_db.get_agent_owner(a1) == o1
        assert await owner_db.get_agent_owner(a2) == o2


# ---- Unit: _broadcast_status helper -----------------------------------------


class TestBroadcastStatus:
    def test_enqueues_online_envelope_for_each_user_client(
        self, routing: RoutingTable
    ):
        c1 = _make_user_client("user-a", "dev-1")
        c2 = _make_user_client("user-a", "dev-2")
        routing.register_user_client("user-a", c1)
        routing.register_user_client("user-a", c2)

        _broadcast_status(routing, "user-a", "agent-1", online=True)

        for c in (c1, c2):
            assert c.send_queue.qsize() == 1
            raw = c.send_queue.get_nowait()
            envelope = json.loads(raw)
            assert envelope == {
                "type": "agent.status_changed",
                "payload": {"agent_id": "agent-1", "online": True},
            }

    def test_offline_envelope_has_online_false(self, routing: RoutingTable):
        c = _make_user_client("user-a")
        routing.register_user_client("user-a", c)

        _broadcast_status(routing, "user-a", "agent-1", online=False)

        envelope = json.loads(c.send_queue.get_nowait())
        assert envelope["payload"]["online"] is False
        assert envelope["payload"]["agent_id"] == "agent-1"
        assert envelope["type"] == "agent.status_changed"

    def test_does_not_broadcast_to_other_users(self, routing: RoutingTable):
        # User scoping at the helper level: only user-a's clients get the
        # envelope when broadcasting for user-a.
        a = _make_user_client("user-a")
        b = _make_user_client("user-b")
        routing.register_user_client("user-a", a)
        routing.register_user_client("user-b", b)

        _broadcast_status(routing, "user-a", "agent-1", online=True)

        assert a.send_queue.qsize() == 1
        assert b.send_queue.qsize() == 0

    def test_no_clients_registered_is_safe(self, routing: RoutingTable):
        # No clients for the user → must not raise.
        _broadcast_status(routing, "nobody", "agent-1", online=True)

    def test_full_queue_does_not_raise(self, routing: RoutingTable):
        # Saturate the send queue, then broadcast — the helper must swallow
        # asyncio.QueueFull (the docstring guarantees drop-on-full behavior).
        c = _make_user_client("user-a")
        # Queue maxsize = 5_000 (see routing._MOBILE_QUEUE_SIZE). Swap the
        # queue for a tiny one so we don't have to fill 5_000 slots.
        c.send_queue = asyncio.Queue(maxsize=1)
        c.send_queue.put_nowait("already-full")
        routing.register_user_client("user-a", c)

        # Must not raise even though the queue is full.
        _broadcast_status(routing, "user-a", "agent-1", online=True)

        # Queue still has exactly the pre-existing item; new envelope dropped.
        assert c.send_queue.qsize() == 1
        assert c.send_queue.get_nowait() == "already-full"


# ---- Integration: /ws/user-events -------------------------------------------


async def _user_token(app, email: str, password: str) -> str:
    _uid, _role, tok, _scheduled = await app.state.auth_service.login_user(email, password)
    return tok


async def _make_second_user(app) -> tuple[str, str]:
    """Create a second user account and return (user_id, dashboard_token)."""
    email = "second@test.local"
    password = "secondpass123"
    result = await app.state.auth_service.register_user(email, password)
    assert result.access_token is not None
    return result.user_id, result.access_token


class TestUserEventsEndpoint:
    def test_connect_without_token_closes(self, app):
        client = TestClient(app)
        # No ?token=... — auth dep closes with 4001 before accepting.
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/user-events") as ws:
                ws.receive_text()

    def test_connect_with_invalid_token_closes(self, app):
        client = TestClient(app)
        with pytest.raises(Exception):
            with client.websocket_connect(
                "/ws/user-events?token=not-a-jwt"
            ) as ws:
                ws.receive_text()

    async def test_connect_registers_user_client_in_routing(self, app):
        # After successful auth, the endpoint must call
        # routing.register_user_client so subsequent broadcasts reach it.
        token = await _user_token(app, TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD)
        admin = await app.state.db.get_user_by_email(TEST_ADMIN_EMAIL)

        client = TestClient(app)
        with client.websocket_connect(
            f"/ws/user-events?token={token}"
        ) as ws:  # noqa: F841 — context holds connection open
            # The endpoint accepts, registers, then runs the writer loop.
            # Give the server a moment to finish the register step.
            # Then inspect the routing table state directly.
            # (TestClient runs in a thread pool; the registration is sync
            # inside the event loop, so by the time we reach this line the
            # client is already in the set.)
            clients = app.state.routing.get_user_clients(admin["id"])
            assert len(clients) == 1

    async def test_receives_status_changed_when_broadcast(self, app):
        token = await _user_token(app, TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD)
        admin = await app.state.db.get_user_by_email(TEST_ADMIN_EMAIL)

        client = TestClient(app)
        with client.websocket_connect(
            f"/ws/user-events?token={token}"
        ) as ws:
            # Broadcast an online transition directly. In production this
            # would be invoked from inside agent_ws on agent register.
            _broadcast_status(
                app.state.routing,
                admin["id"],
                "agent-xyz",
                online=True,
            )
            text = ws.receive_text()
            envelope = json.loads(text)
            assert envelope == {
                "type": "agent.status_changed",
                "payload": {"agent_id": "agent-xyz", "online": True},
            }

            # Then an offline transition.
            _broadcast_status(
                app.state.routing,
                admin["id"],
                "agent-xyz",
                online=False,
            )
            text = ws.receive_text()
            envelope = json.loads(text)
            assert envelope["payload"] == {
                "agent_id": "agent-xyz",
                "online": False,
            }

    async def test_user_scoping_other_user_does_not_receive(self, app):
        # User A connects; we broadcast for user B's agent. A must not
        # receive anything — the admin's server-side send_queue stays
        # empty because the broadcast is targeted at user B's (separate,
        # empty) UserClient set.
        admin_token = await _user_token(
            app, TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD
        )
        other_user_id, _ = await _make_second_user(app)
        admin = await app.state.db.get_user_by_email(TEST_ADMIN_EMAIL)

        client = TestClient(app)
        with client.websocket_connect(
            f"/ws/user-events?token={admin_token}"
        ) as ws:  # noqa: F841 — context holds the connection open
            # Broadcast for the OTHER user's agent.
            _broadcast_status(
                app.state.routing,
                other_user_id,
                "agent-of-other-user",
                online=True,
            )

            # Broadcasting for user B never enqueues anything on user A's
            # client queues. Inspect the routing-table queues directly on
            # the server side to assert no delivery happened.
            admin_clients = app.state.routing.get_user_clients(admin["id"])
            assert len(admin_clients) == 1
            for c in admin_clients:
                assert c.send_queue.qsize() == 0

            # For a positive control, broadcast *to* the admin and prove
            # the same socket does receive that envelope — so the "0"
            # above isn't just a matter of the writer having drained
            # something we missed.
            _broadcast_status(
                app.state.routing,
                admin["id"],
                "agent-of-admin",
                online=True,
            )
            text = ws.receive_text()
            envelope = json.loads(text)
            assert envelope["payload"]["agent_id"] == "agent-of-admin"

    async def test_reconnect_race_no_spurious_offline(
        self, app, registered_agent
    ):
        """CRITICAL-1 regression: when a new agent tunnel replaces an older
        one (agent reconnect), the stale tunnel's finally-block must NOT
        broadcast ``agent.status_changed {online: false}`` — doing so
        re-introduces the exact bug this feature is meant to fix (mobile
        UI stuck on "Offline" while the agent is actually online via the
        newer tunnel).

        Strategy: after the race window closes, enqueue a unique *canary*
        status-change event for a different agent_id. The canary is
        enqueued AFTER any would-be spurious offline. If ``receive_text``
        yields the canary directly, no spurious offline was broadcast.
        """
        agent_id, raw_token = registered_agent
        # Agent is owned by the viewer (admins never own agents); the
        # user_ws observer must log in as the viewer to receive events.
        token = await _user_token(app, TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD)
        owner = await app.state.db.get_user_by_email(TEST_VIEWER_EMAIL)

        client = TestClient(app)
        with client.websocket_connect(
            f"/ws/user-events?token={token}"
        ) as user_ws:
            # First tunnel — observer sees online=true.
            with client.websocket_connect("/ws/agent-tunnel") as agent_a1:
                agent_a1.send_text(
                    json.dumps(
                        {
                            "type": "auth",
                            "agent_id": agent_id,
                            "token": raw_token,
                            "protocol_version": PROTOCOL_VERSION,
                        }
                    )
                )
                assert json.loads(agent_a1.receive_text())["type"] == "auth_ok"
                env = json.loads(user_ws.receive_text())
                assert env["payload"] == {"agent_id": agent_id, "online": True}

                # Reconnect: second tunnel replaces A1 in the routing
                # table (register_agent unconditionally overwrites).
                with client.websocket_connect("/ws/agent-tunnel") as agent_a2:
                    agent_a2.send_text(
                        json.dumps(
                            {
                                "type": "auth",
                                "agent_id": agent_id,
                                "token": raw_token,
                                "protocol_version": PROTOCOL_VERSION,
                            }
                        )
                    )
                    assert (
                        json.loads(agent_a2.receive_text())["type"] == "auth_ok"
                    )
                    # Observer sees A2's online (redundant but expected —
                    # a subsequent broadcast with the same state is a
                    # no-op client-side because AgentRepository.updateAgentOnline
                    # is idempotent).
                    env = json.loads(user_ws.receive_text())
                    assert env["payload"] == {
                        "agent_id": agent_id,
                        "online": True,
                    }

                    # Close A1 first, while A2 is still active.
                    agent_a1.close()
                    # Give A1's server-side finally: block a moment to
                    # run. If the guard is broken, this is when the
                    # spurious offline event would be enqueued.
                    await asyncio.sleep(0.15)

                    # Agent must still be reported online (A2 holds it).
                    assert app.state.routing.is_agent_online(agent_id)

                    # Canary: a broadcast AFTER any spurious offline
                    # would be — if the guard is broken — behind it in
                    # the observer's queue. If we read the canary
                    # directly, no offline was broadcast.
                    _broadcast_status(
                        app.state.routing,
                        owner["id"],
                        "canary-agent",
                        online=True,
                    )
                    env = json.loads(user_ws.receive_text())
                    assert env["payload"] == {
                        "agent_id": "canary-agent",
                        "online": True,
                    }, (
                        "stale tunnel's finally: broadcast a spurious "
                        "offline before our canary — the CRITICAL-1 "
                        "guard regressed"
                    )

    async def test_disconnect_unregisters_user_client(self, app):
        token = await _user_token(app, TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD)
        admin = await app.state.db.get_user_by_email(TEST_ADMIN_EMAIL)

        client = TestClient(app)
        with client.websocket_connect(
            f"/ws/user-events?token={token}"
        ) as ws:
            assert len(app.state.routing.get_user_clients(admin["id"])) == 1

        # After the context exits, the server's finally: block must
        # unregister the client. Give the server event loop a moment to
        # run the cleanup.
        for _ in range(20):
            if not app.state.routing.get_user_clients(admin["id"]):
                break
            await asyncio.sleep(0.05)
        assert app.state.routing.get_user_clients(admin["id"]) == set()
