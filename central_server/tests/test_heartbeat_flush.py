"""Heartbeat write-behind: heartbeats are in-memory only; the DB sees them
via the periodic batched flush (``_flush_agent_heartbeats``), the disconnect
flush in ``agent_ws``, and the shutdown flush in the lifespan.

The conftest ``app`` fixture bypasses the lifespan, so no flusher task runs
here — the only ``last_seen_at`` writers in these tests are the handshake
and the disconnect flush, which keeps the zero-DB-writes assertions
deterministic.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

from starlette.testclient import TestClient

from central_server.app import _flush_agent_heartbeats, create_app
from central_server.config import CentralServerConfig
from central_server.db.database import Database
from central_server.relay import agent_ws as agent_ws_module
from central_server.relay.envelope import SUPPORTED_PROTOCOL_VERSIONS
from central_server.relay.routing import RoutingTable

_PROTO = max(SUPPORTED_PROTOCOL_VERSIONS)


def _authenticate(ws, agent_id: str, raw_token: str) -> None:
    ws.send_text(json.dumps({
        "type": "auth",
        "agent_id": agent_id,
        "token": raw_token,
        "protocol_version": _PROTO,
    }))
    assert json.loads(ws.receive_text())["type"] == "auth_ok"


async def _wait_until(predicate, timeout: float = 2.0) -> bool:
    """Poll a sync predicate until true or timeout (portal-thread races)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


class TestFlushSweep:
    async def test_flush_sweeps_online_agents(self, db, viewer_user):
        a1, _ = await db.register_agent("flush-a1", viewer_user["id"])
        a2, _ = await db.register_agent("flush-a2", viewer_user["id"])
        t1 = datetime(2031, 1, 1, tzinfo=timezone.utc)
        t2 = datetime(2031, 2, 2, tzinfo=timezone.utc)

        routing = RoutingTable()
        routing.register_agent(a1, MagicMock()).last_heartbeat = t1
        routing.register_agent(a2, MagicMock()).last_heartbeat = t2

        assert await _flush_agent_heartbeats(db, routing) == 2
        assert (await db.get_agent(a1))["last_seen_at"] == t1.isoformat()
        assert (await db.get_agent(a2))["last_seen_at"] == t2.isoformat()

    async def test_flush_empty_routing_returns_zero(self, db):
        assert await _flush_agent_heartbeats(db, RoutingTable()) == 0

    async def test_flush_tolerates_agent_missing_from_db(self, db):
        routing = RoutingTable()
        conn = routing.register_agent("never-registered", MagicMock())
        conn.last_heartbeat = datetime(2031, 3, 3, tzinfo=timezone.utc)
        # The UPDATE matches no row — must not raise.
        assert await _flush_agent_heartbeats(db, routing) == 1


class TestHeartbeatControlMessage:
    async def test_heartbeat_does_not_write_db(self, app, registered_agent):
        agent_id, raw_token = registered_agent
        client = TestClient(app)
        with client.websocket_connect("/ws/agent-tunnel") as ws:
            _authenticate(ws, agent_id, raw_token)
            # The handshake bumps last_seen_at once; that is the baseline.
            baseline = (await app.state.db.get_agent(agent_id))["last_seen_at"]
            assert baseline is not None

            conn = app.state.routing.get_agent(agent_id)
            before_hb = conn.last_heartbeat
            ws.send_text(json.dumps({"type": "heartbeat"}))
            # In-memory state advancing proves the reader processed the
            # heartbeat; only then is the no-DB-write assertion meaningful.
            assert await _wait_until(lambda: conn.last_heartbeat > before_hb)

            assert (
                await app.state.db.get_agent(agent_id)
            )["last_seen_at"] == baseline


class TestDisconnectFlush:
    async def test_disconnect_persists_final_heartbeat(
        self, app, registered_agent
    ):
        agent_id, raw_token = registered_agent
        sentinel = datetime(2031, 4, 4, tzinfo=timezone.utc)
        client = TestClient(app)
        with client.websocket_connect("/ws/agent-tunnel") as ws:
            _authenticate(ws, agent_id, raw_token)
            app.state.routing.get_agent(agent_id).last_heartbeat = sentinel

        # The endpoint's finally runs in the portal thread — poll.
        async def _persisted() -> bool:
            row = await app.state.db.get_agent(agent_id)
            return row["last_seen_at"] == sentinel.isoformat()

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not await _persisted():
            await asyncio.sleep(0.02)
        assert await _persisted()

    async def test_stale_disconnect_does_not_overwrite_newer_conn(
        self, app, db, registered_agent
    ):
        agent_id, raw_token = registered_agent
        sentinel_a = datetime(2031, 5, 5, tzinfo=timezone.utc)
        sentinel_b = datetime(2031, 6, 6, tzinfo=timezone.utc)

        calls: list[tuple[str, datetime | None]] = []
        original = db.touch_agent_last_seen

        async def _spy(agent_id: str, last_seen=None) -> None:
            calls.append((agent_id, last_seen))
            await original(agent_id, last_seen)

        db.touch_agent_last_seen = _spy  # instance attr shadows the method
        try:
            client = TestClient(app)
            ws_a_cm = client.websocket_connect("/ws/agent-tunnel")
            ws_a = ws_a_cm.__enter__()
            a_closed = False
            try:
                _authenticate(ws_a, agent_id, raw_token)
                conn_a = app.state.routing.get_agent(agent_id)

                ws_b_cm = client.websocket_connect("/ws/agent-tunnel")
                ws_b = ws_b_cm.__enter__()
                try:
                    _authenticate(ws_b, agent_id, raw_token)
                    conn_b = app.state.routing.get_agent(agent_id)
                    assert conn_b is not conn_a  # B replaced A in routing

                    conn_a.last_heartbeat = sentinel_a
                    conn_b.last_heartbeat = sentinel_b

                    # Stale teardown: A's conn_id no longer matches, so its
                    # finally must neither unregister B nor flush sentinel_a.
                    ws_a_cm.__exit__(None, None, None)
                    a_closed = True
                    await asyncio.sleep(0.2)
                    assert calls == []
                    assert app.state.routing.get_agent(agent_id) is conn_b
                finally:
                    ws_b_cm.__exit__(None, None, None)

                # B's teardown is the real disconnect: exactly one flush,
                # carrying B's heartbeat. (Spy stays installed until the
                # portal-thread flush has been observed.)
                assert await _wait_until(lambda: len(calls) >= 1)
                assert calls == [(agent_id, sentinel_b)]
            finally:
                if not a_closed:
                    ws_a_cm.__exit__(None, None, None)
        finally:
            db.touch_agent_last_seen = original

    async def test_flush_failure_does_not_suppress_offline_broadcast(
        self, app, db, registered_agent, monkeypatch
    ):
        agent_id, raw_token = registered_agent
        flush_attempts: list[str] = []

        async def _boom(agent_id: str, last_seen=None) -> None:
            flush_attempts.append(agent_id)
            raise RuntimeError("db closed")

        broadcasts: list[tuple[str, bool]] = []

        def _record_status(routing, user_id, agent_id, *, online):
            broadcasts.append((agent_id, online))

        monkeypatch.setattr(db, "touch_agent_last_seen", _boom)
        monkeypatch.setattr(
            agent_ws_module, "_broadcast_status", _record_status
        )

        client = TestClient(app)
        with client.websocket_connect("/ws/agent-tunnel") as ws:
            _authenticate(ws, agent_id, raw_token)

        # The endpoint's finally runs in the portal thread — poll. A flush
        # that raises must be logged and swallowed, never short-circuiting
        # the offline notification.
        assert await _wait_until(lambda: (agent_id, False) in broadcasts)
        assert flush_attempts == [agent_id]


class TestShutdownFlush:
    async def test_shutdown_flushes_dirty_heartbeats_before_close(
        self, tmp_path
    ):
        """Lifespan shutdown sweeps online agents' heartbeats into the DB.

        Unlike the conftest ``app`` fixture, ``TestClient`` used as a
        context manager drives the REAL lifespan: startup on enter,
        shutdown (cancel loops → final sweep → db.close) on exit. The
        flush interval is set huge so only the shutdown sweep can write.
        """
        config = CentralServerConfig(
            jwt_secret_key="test-secret-key-for-shutdown-flush",
            db_path=str(tmp_path / "lifespan.db"),
            manifest_store_path=str(tmp_path / "manifest.json"),
            heartbeat_flush_interval_seconds=3600,
            email_verification_required=False,
        )
        application = create_app(config=config)
        sentinel = datetime(2031, 7, 7, tzinfo=timezone.utc)

        with TestClient(application):
            db = application.state.db
            owner = await db.create_user(
                email="shutdown@test.local", password_hash="h"
            )
            agent_id, _ = await db.register_agent("shutdown-agent", owner)
            conn = application.state.routing.register_agent(
                agent_id, MagicMock()
            )
            conn.last_heartbeat = sentinel

        # The lifespan's db is closed now — verify on a fresh connection.
        verify_db = Database(config.db_path)
        await verify_db.connect()
        try:
            row = await verify_db.get_agent(agent_id)
            assert row is not None
            assert row["last_seen_at"] == sentinel.isoformat()
        finally:
            await verify_db.close()
