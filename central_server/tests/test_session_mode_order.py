"""Tests for the account-scoped create-session mode picker order.

Covers ``GET`` / ``PUT /api/session-mode-order`` (default before the first
save, full replace, version bump, validation, auth and admin blocking,
per-account isolation), the ``session_mode_order.updated`` push (sent on a
change, not on a repeated save), the account purge, and the runtime
``user_version`` 23 migration.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from central_server.db.database import Database
from central_server.relay.routing import RoutingTable, UserClient

URL = "/api/session-mode-order"
DTO_KEYS = {"order", "version", "updated_at"}


def _make_user_client(user_id: str) -> UserClient:
    """Build a UserClient with no real WS — only the send_queue is used."""
    return UserClient(user_id=user_id, device_id="dev-x", websocket=None)  # type: ignore[arg-type]


async def _put_order(client, headers, order):
    resp = await client.put(URL, json={"order": order}, headers=headers)
    assert resp.status_code == 200
    return resp.json()


class TestGet:
    async def test_default_before_first_save(self, client, api_auth_headers):
        resp = await client.get(URL, headers=api_auth_headers)

        assert resp.status_code == 200
        assert resp.json() == {"order": [], "version": 0, "updated_at": None}

    async def test_requires_auth(self, client):
        resp = await client.get(URL)
        assert resp.status_code == 401


class TestPut:
    async def test_first_save_returns_version_1_with_exact_dto_shape(
        self, client, api_auth_headers
    ):
        data = await _put_order(client, api_auth_headers, ["codex", "claude"])

        assert set(data.keys()) == DTO_KEYS
        assert data["order"] == ["codex", "claude"]
        assert data["version"] == 1
        assert data["updated_at"] is not None

    async def test_each_change_bumps_version_and_replaces_whole_order(
        self, client, api_auth_headers
    ):
        await _put_order(client, api_auth_headers, ["codex", "claude", "shell"])

        data = await _put_order(client, api_auth_headers, ["shell"])

        assert data["order"] == ["shell"]
        assert data["version"] == 2

    async def test_repeated_save_of_same_order_keeps_version(
        self, client, api_auth_headers
    ):
        first = await _put_order(client, api_auth_headers, ["codex", "claude"])

        again = await _put_order(client, api_auth_headers, ["codex", "claude"])

        assert again == first

    async def test_empty_order_resets_to_default_and_bumps_version(
        self, client, api_auth_headers
    ):
        await _put_order(client, api_auth_headers, ["codex", "claude"])

        data = await _put_order(client, api_auth_headers, [])

        assert data["order"] == []
        assert data["version"] == 2

    async def test_unknown_ids_are_stored_opaquely(self, client, api_auth_headers):
        # A mode a newer client added must survive an older server.
        data = await _put_order(
            client, api_auth_headers, ["future_mode", "custom_ffffffffffff"]
        )
        assert data["order"] == ["future_mode", "custom_ffffffffffff"]

    @pytest.mark.parametrize(
        "body",
        [
            {"order": ["claude", "claude"]},
            {"order": ["Claude"]},
            {"order": ["custom-ab12"]},
            {"order": [""]},
            {"order": ["x" * 33]},
            {"order": [f"custom_{i:012d}" for i in range(65)]},
        ],
    )
    async def test_invalid_body_returns_422(self, client, api_auth_headers, body):
        resp = await client.put(URL, json=body, headers=api_auth_headers)
        assert resp.status_code == 422

    async def test_invalid_body_leaves_stored_order_untouched(
        self, client, api_auth_headers
    ):
        saved = await _put_order(client, api_auth_headers, ["codex"])
        await client.put(
            URL, json={"order": ["claude", "claude"]}, headers=api_auth_headers
        )

        resp = await client.get(URL, headers=api_auth_headers)
        assert resp.json() == saved

    async def test_order_is_per_account(self, client, app, api_auth_headers):
        other_user = await app.state.db.create_user(
            email="other@test.local", password_hash="hash"
        )
        await app.state.session_mode_order_service.set_order(
            other_user, ["shell"]
        )

        resp = await client.get(URL, headers=api_auth_headers)
        assert resp.json() == {"order": [], "version": 0, "updated_at": None}

        mine = await _put_order(client, api_auth_headers, ["codex"])
        assert mine["version"] == 1
        theirs = await app.state.session_mode_order_service.get_order(other_user)
        assert theirs["order"] == ["shell"]


class TestAdminBlocked:
    async def test_admin_cannot_put(self, client, admin_api_auth_headers):
        resp = await client.put(
            URL, json={"order": ["claude"]}, headers=admin_api_auth_headers
        )
        assert resp.status_code == 403


class TestBroadcast:
    async def test_change_pushes_full_payload_to_every_owner_client(
        self, app, viewer_user
    ):
        routing: RoutingTable = app.state.routing
        client_a = _make_user_client(viewer_user["id"])
        client_b = _make_user_client(viewer_user["id"])
        routing.register_user_client(viewer_user["id"], client_a)
        routing.register_user_client(viewer_user["id"], client_b)

        saved = await app.state.session_mode_order_service.set_order(
            viewer_user["id"], ["codex", "claude"]
        )

        for c in (client_a, client_b):
            envelope = json.loads(c.send_queue.get_nowait())
            assert envelope["type"] == "session_mode_order.updated"
            assert envelope["payload"] == saved
            assert set(envelope["payload"].keys()) == DTO_KEYS

    async def test_unchanged_save_pushes_nothing(self, app, viewer_user):
        routing: RoutingTable = app.state.routing
        c = _make_user_client(viewer_user["id"])
        routing.register_user_client(viewer_user["id"], c)
        service = app.state.session_mode_order_service
        await service.set_order(viewer_user["id"], ["codex", "claude"])
        c.send_queue.get_nowait()  # drain the first change

        await service.set_order(viewer_user["id"], ["codex", "claude"])

        with pytest.raises(asyncio.QueueEmpty):
            c.send_queue.get_nowait()

    async def test_does_not_push_to_other_users(self, app, viewer_user):
        routing: RoutingTable = app.state.routing
        other_user = await app.state.db.create_user(
            email="other@test.local", password_hash="hash"
        )
        owner_client = _make_user_client(viewer_user["id"])
        other_client = _make_user_client(other_user)
        routing.register_user_client(viewer_user["id"], owner_client)
        routing.register_user_client(other_user, other_client)

        await app.state.session_mode_order_service.set_order(
            viewer_user["id"], ["shell"]
        )

        assert not owner_client.send_queue.empty()
        with pytest.raises(asyncio.QueueEmpty):
            other_client.send_queue.get_nowait()


class TestDatabase:
    async def test_set_returns_row_and_none_when_unchanged(self, db: Database):
        owner = await db.create_user(email="owner@test.local", password_hash="h")

        first = await db.set_session_mode_order(owner, '["codex"]')
        assert first["mode_order"] == '["codex"]'
        assert first["version"] == 1

        assert await db.set_session_mode_order(owner, '["codex"]') is None
        stored = await db.get_session_mode_order(owner)
        assert stored == first

        second = await db.set_session_mode_order(owner, '["shell"]')
        assert second["version"] == 2

    async def test_unchanged_set_leaves_no_open_transaction(self, db: Database):
        owner = await db.create_user(email="owner@test.local", password_hash="h")
        await db.set_session_mode_order(owner, '["codex"]')

        await db.set_session_mode_order(owner, '["codex"]')

        assert db.conn.in_transaction is False

    async def test_delete_user_purges_the_order(self, db: Database):
        owner = await db.create_user(email="owner@test.local", password_hash="h")
        survivor = await db.create_user(
            email="other@test.local", password_hash="h"
        )
        await db.set_session_mode_order(owner, '["codex"]')
        await db.set_session_mode_order(survivor, '["shell"]')

        await db.delete_user(owner)

        assert await db.get_session_mode_order(owner) is None
        assert await db.get_session_mode_order(survivor) is not None


class TestMigration:
    async def test_v23_creates_table_on_existing_db(self, tmp_path):
        db_path = str(tmp_path / "pre_v23.db")
        db = Database(db_path)
        await db.connect()
        owner = await db.create_user(email="owner@test.local", password_hash="h")
        cursor = await db.conn.execute("PRAGMA user_version")
        head = (await cursor.fetchone())[0]
        # Simulate a pre-v23 on-disk schema: drop the new table and roll
        # user_version back so reopening replays the v23 block.
        await db.conn.execute("DROP TABLE user_session_mode_orders")
        await db.conn.execute("PRAGMA user_version = 22")
        await db.conn.commit()
        await db.close()

        db = Database(db_path)
        await db.connect()
        cursor = await db.conn.execute("PRAGMA user_version")
        assert (await cursor.fetchone())[0] == head
        row = await db.set_session_mode_order(owner, '["codex"]')
        assert row["version"] == 1
        await db.close()

        # Idempotent: a second reopen at head keeps the row.
        db = Database(db_path)
        await db.connect()
        cursor = await db.conn.execute("PRAGMA user_version")
        assert (await cursor.fetchone())[0] == head
        assert (await db.get_session_mode_order(owner))["mode_order"] == '["codex"]'
        await db.close()
