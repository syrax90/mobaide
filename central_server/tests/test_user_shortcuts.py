"""Tests for the user-scoped saved-shortcut API.

Covers REST CRUD (GET / POST / PATCH / DELETE), the POST upsert semantics
(re-saving a live (kind, chord_token) updates the name instead of
duplicating), the ``""``-clears-name PATCH sentinel (``name`` itself is
required — an empty body 422s), ownership isolation,
soft-delete semantics, admin blocking, and the user-scoped push-event
broadcast trio (``shortcut.created`` / ``shortcut.updated`` /
``shortcut.deleted``).
"""
from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from central_server.relay.routing import RoutingTable, UserClient


def _make_user_client(user_id: str) -> UserClient:
    """Build a UserClient with no real WS — only the send_queue is used."""
    return UserClient(user_id=user_id, device_id="dev-x", websocket=None)  # type: ignore[arg-type]


async def _create_shortcut(
    client, headers, kind="claude", chord_token="ctrl+x", name=None
):
    body = {"kind": kind, "chord_token": chord_token}
    if name is not None:
        body["name"] = name
    resp = await client.post("/api/shortcuts", json=body, headers=headers)
    assert resp.status_code == 201
    return resp.json()


class TestCreateShortcut:
    async def test_create_shortcut(self, client, api_auth_headers):
        resp = await client.post(
            "/api/shortcuts",
            json={"kind": "codex", "chord_token": "ctrl+alt+k", "name": "Klear"},
            headers=api_auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["kind"] == "codex"
        assert data["chord_token"] == "ctrl+alt+k"
        assert data["name"] == "Klear"
        assert data["position"] == 0
        assert data["version"] == 1
        assert len(data["shortcut_id"]) == 12

    async def test_create_empty_name_stored_as_null(
        self, client, api_auth_headers
    ):
        data = await _create_shortcut(
            client, api_auth_headers, name=""
        )
        assert data["name"] is None

    async def test_create_requires_auth(self, client):
        resp = await client.post(
            "/api/shortcuts", json={"kind": "claude", "chord_token": "ctrl+x"}
        )
        assert resp.status_code == 401

    async def test_create_rejects_invalid_kind(self, client, api_auth_headers):
        resp = await client.post(
            "/api/shortcuts",
            json={"kind": "Not A Kind!", "chord_token": "ctrl+x"},
            headers=api_auth_headers,
        )
        assert resp.status_code == 422


class TestListShortcuts:
    async def test_list_returns_only_owned(self, client, app, api_auth_headers):
        await _create_shortcut(client, api_auth_headers, chord_token="ctrl+m")
        other_user = await app.state.db.create_user(
            email="other@test.local", password_hash="hash"
        )
        await app.state.db.create_user_shortcut(
            other_user, "claude", "ctrl+t", None
        )

        resp = await client.get("/api/shortcuts", headers=api_auth_headers)
        items = resp.json()
        assert len(items) == 1
        assert items[0]["chord_token"] == "ctrl+m"

    async def test_list_excludes_soft_deleted(self, client, api_auth_headers):
        created = await _create_shortcut(client, api_auth_headers)
        sid = created["shortcut_id"]

        await client.delete(f"/api/shortcuts/{sid}", headers=api_auth_headers)

        resp = await client.get("/api/shortcuts", headers=api_auth_headers)
        assert all(s["shortcut_id"] != sid for s in resp.json())

    async def test_list_returns_all_kinds_with_per_kind_positions(
        self, client, api_auth_headers
    ):
        await _create_shortcut(
            client, api_auth_headers, kind="claude", chord_token="ctrl+a"
        )
        await _create_shortcut(
            client, api_auth_headers, kind="codex", chord_token="ctrl+b"
        )
        await _create_shortcut(
            client, api_auth_headers, kind="claude", chord_token="ctrl+c"
        )

        resp = await client.get("/api/shortcuts", headers=api_auth_headers)
        items = resp.json()
        # One list across kinds; position counters are per (owner, kind).
        by_chord = {s["chord_token"]: s["position"] for s in items}
        assert by_chord == {"ctrl+a": 0, "ctrl+b": 0, "ctrl+c": 1}
        # Within a kind the list follows position order.
        claude_chords = [
            s["chord_token"] for s in items if s["kind"] == "claude"
        ]
        assert claude_chords == ["ctrl+a", "ctrl+c"]


class TestUpsert:
    async def test_resave_same_chord_returns_existing_row(
        self, client, api_auth_headers
    ):
        first = await _create_shortcut(client, api_auth_headers, name="One")
        again = await _create_shortcut(client, api_auth_headers)

        assert again["shortcut_id"] == first["shortcut_id"]
        assert again["name"] == "One"  # no name supplied → kept
        assert again["version"] == first["version"]

        listing = await client.get("/api/shortcuts", headers=api_auth_headers)
        assert len(listing.json()) == 1

    async def test_resave_with_new_name_updates_in_place(
        self, client, api_auth_headers
    ):
        first = await _create_shortcut(client, api_auth_headers, name="Old")
        again = await _create_shortcut(client, api_auth_headers, name="New")

        assert again["shortcut_id"] == first["shortcut_id"]
        assert again["name"] == "New"
        assert again["version"] == first["version"] + 1

    async def test_same_chord_different_kind_creates_second_row(
        self, client, api_auth_headers
    ):
        first = await _create_shortcut(client, api_auth_headers, kind="claude")
        second = await _create_shortcut(client, api_auth_headers, kind="codex")

        assert second["shortcut_id"] != first["shortcut_id"]
        listing = await client.get("/api/shortcuts", headers=api_auth_headers)
        assert len(listing.json()) == 2

    async def test_resave_after_delete_creates_fresh_row(
        self, client, api_auth_headers
    ):
        first = await _create_shortcut(client, api_auth_headers, name="Old")
        await client.delete(
            f"/api/shortcuts/{first['shortcut_id']}", headers=api_auth_headers
        )

        fresh = await _create_shortcut(client, api_auth_headers)
        assert fresh["shortcut_id"] != first["shortcut_id"]
        assert fresh["name"] is None
        assert fresh["version"] == 1

    async def test_delete_racing_name_update_creates_fresh_row(
        self, app, viewer_user, monkeypatch
    ):
        """A delete committing between the upsert probe and the name update
        must not echo the stale row back (which would resurrect the deleted
        button client-side) — the save re-probes and creates a fresh row,
        broadcasting ``shortcut.created``."""
        db = app.state.db
        service = app.state.shortcut_service
        c = _make_user_client(viewer_user["id"])
        app.state.routing.register_user_client(viewer_user["id"], c)

        first = await service.create_shortcut(
            viewer_user["id"], "claude", "ctrl+x", "Old"
        )
        c.send_queue.get_nowait()  # drain shortcut.created

        real_update = db.update_user_shortcut_name

        async def racing_update(shortcut_id, name):
            monkeypatch.setattr(db, "update_user_shortcut_name", real_update)
            await db.soft_delete_user_shortcut(shortcut_id)
            return await real_update(shortcut_id, name)

        monkeypatch.setattr(db, "update_user_shortcut_name", racing_update)

        fresh = await service.create_shortcut(
            viewer_user["id"], "claude", "ctrl+x", "New"
        )

        assert fresh["shortcut_id"] != first["shortcut_id"]
        assert fresh["name"] == "New"
        assert fresh["version"] == 1

        envelope = json.loads(c.send_queue.get_nowait())
        assert envelope["type"] == "shortcut.created"
        assert envelope["payload"]["shortcut_id"] == fresh["shortcut_id"]
        with pytest.raises(asyncio.QueueEmpty):
            c.send_queue.get_nowait()

    async def test_delete_racing_update_readback_creates_fresh_row(
        self, app, viewer_user, monkeypatch
    ):
        """A delete committing after the name UPDATE but before its
        readback (a distinct gap from the pre-update race above) must not
        broadcast the tombstone as ``shortcut.updated`` — the save
        re-probes and creates a fresh row, broadcasting
        ``shortcut.created``."""
        db = app.state.db
        service = app.state.shortcut_service
        c = _make_user_client(viewer_user["id"])
        app.state.routing.register_user_client(viewer_user["id"], c)

        first = await service.create_shortcut(
            viewer_user["id"], "claude", "ctrl+x", "Old"
        )
        c.send_queue.get_nowait()  # drain shortcut.created

        real_get = db.get_user_shortcut

        async def racing_get(shortcut_id):
            monkeypatch.setattr(db, "get_user_shortcut", real_get)
            await db.soft_delete_user_shortcut(shortcut_id)
            return await real_get(shortcut_id)

        monkeypatch.setattr(db, "get_user_shortcut", racing_get)

        fresh = await service.create_shortcut(
            viewer_user["id"], "claude", "ctrl+x", "New"
        )

        assert fresh["shortcut_id"] != first["shortcut_id"]
        assert fresh["name"] == "New"
        assert fresh["version"] == 1

        envelope = json.loads(c.send_queue.get_nowait())
        assert envelope["type"] == "shortcut.created"
        assert envelope["payload"]["shortcut_id"] == fresh["shortcut_id"]
        with pytest.raises(asyncio.QueueEmpty):
            c.send_queue.get_nowait()

    async def test_lost_insert_race_reprobes_and_updates_winner(
        self, app, viewer_user, monkeypatch
    ):
        """When a concurrent save wins the INSERT race, the partial unique
        index rejects the loser, which must re-probe and apply its name to
        the winning row instead of raising or duplicating the button."""
        db = app.state.db
        service = app.state.shortcut_service
        real_create = db.create_user_shortcut
        winner_ids = []

        async def racing_create(owner_user_id, kind, chord_token, name):
            monkeypatch.setattr(db, "create_user_shortcut", real_create)
            winner = await real_create(
                owner_user_id, kind, chord_token, "Winner"
            )
            winner_ids.append(winner["shortcut_id"])
            raise sqlite3.IntegrityError("UNIQUE constraint failed")

        monkeypatch.setattr(db, "create_user_shortcut", racing_create)

        result = await service.create_shortcut(
            viewer_user["id"], "claude", "ctrl+x", "Mine"
        )

        assert result["shortcut_id"] == winner_ids[0]
        assert result["name"] == "Mine"
        assert result["version"] == 2
        rows = await db.list_user_shortcuts_for_user(viewer_user["id"])
        assert len(rows) == 1


class TestRenameShortcut:
    async def test_patch_sets_name(self, client, api_auth_headers):
        created = await _create_shortcut(client, api_auth_headers)
        sid = created["shortcut_id"]

        resp = await client.patch(
            f"/api/shortcuts/{sid}",
            json={"name": "Interrupt"},
            headers=api_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Interrupt"
        assert data["chord_token"] == created["chord_token"]  # unchanged
        assert data["version"] == created["version"] + 1

    async def test_patch_empty_string_clears_name(
        self, client, api_auth_headers
    ):
        created = await _create_shortcut(client, api_auth_headers, name="Named")
        resp = await client.patch(
            f"/api/shortcuts/{created['shortcut_id']}",
            json={"name": ""},
            headers=api_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] is None

    async def test_patch_empty_body_returns_422_and_changes_nothing(
        self, client, app, viewer_user, api_auth_headers
    ):
        """``name`` is required, so a bare ``{}`` must fail validation —
        it must not silently clear the name, bump the version, or
        broadcast a ``shortcut.updated``."""
        created = await _create_shortcut(client, api_auth_headers, name="Named")
        sid = created["shortcut_id"]
        c = _make_user_client(viewer_user["id"])
        app.state.routing.register_user_client(viewer_user["id"], c)

        resp = await client.patch(
            f"/api/shortcuts/{sid}", json={}, headers=api_auth_headers
        )
        assert resp.status_code == 422

        listing = await client.get("/api/shortcuts", headers=api_auth_headers)
        row = next(s for s in listing.json() if s["shortcut_id"] == sid)
        assert row["name"] == "Named"
        assert row["version"] == created["version"]
        with pytest.raises(asyncio.QueueEmpty):
            c.send_queue.get_nowait()

    async def test_patch_not_owner_returns_403(
        self, client, app, api_auth_headers
    ):
        other_user = await app.state.db.create_user(
            email="other@test.local", password_hash="hash"
        )
        row = await app.state.db.create_user_shortcut(
            other_user, "claude", "ctrl+x", None
        )
        resp = await client.patch(
            f"/api/shortcuts/{row['shortcut_id']}",
            json={"name": "hacked"},
            headers=api_auth_headers,
        )
        assert resp.status_code == 403

    async def test_patch_after_delete_returns_403(
        self, client, api_auth_headers
    ):
        created = await _create_shortcut(client, api_auth_headers)
        sid = created["shortcut_id"]
        await client.delete(f"/api/shortcuts/{sid}", headers=api_auth_headers)

        resp = await client.patch(
            f"/api/shortcuts/{sid}",
            json={"name": "ghost"},
            headers=api_auth_headers,
        )
        assert resp.status_code == 403

    async def test_rename_racing_delete_raises_not_found(
        self, app, viewer_user, monkeypatch
    ):
        """A delete committing between the rename's UPDATE and its
        readback must surface as not-found (403 at the router), not
        broadcast the tombstone as ``shortcut.updated``."""
        db = app.state.db
        service = app.state.shortcut_service
        c = _make_user_client(viewer_user["id"])
        app.state.routing.register_user_client(viewer_user["id"], c)

        row = await service.create_shortcut(
            viewer_user["id"], "claude", "ctrl+x", None
        )
        c.send_queue.get_nowait()  # drain shortcut.created

        real_get = db.get_user_shortcut

        async def racing_get(shortcut_id):
            monkeypatch.setattr(db, "get_user_shortcut", real_get)
            await db.soft_delete_user_shortcut(shortcut_id)
            return await real_get(shortcut_id)

        monkeypatch.setattr(db, "get_user_shortcut", racing_get)

        with pytest.raises(ValueError, match="Shortcut not found"):
            await service.rename_shortcut(
                row["shortcut_id"], viewer_user["id"], "Renamed"
            )

        with pytest.raises(asyncio.QueueEmpty):
            c.send_queue.get_nowait()


class TestDeleteShortcut:
    async def test_soft_delete(self, client, app, api_auth_headers):
        created = await _create_shortcut(client, api_auth_headers)
        sid = created["shortcut_id"]

        resp = await client.delete(
            f"/api/shortcuts/{sid}", headers=api_auth_headers
        )
        assert resp.status_code == 204

        # Row still exists with deleted_at set.
        row = await app.state.db.get_user_shortcut(sid)
        assert row is not None
        assert row["deleted_at"] is not None

    async def test_delete_not_owner_returns_403(
        self, client, app, api_auth_headers
    ):
        other_user = await app.state.db.create_user(
            email="other@test.local", password_hash="hash"
        )
        row = await app.state.db.create_user_shortcut(
            other_user, "claude", "ctrl+x", None
        )
        resp = await client.delete(
            f"/api/shortcuts/{row['shortcut_id']}",
            headers=api_auth_headers,
        )
        assert resp.status_code == 403


class TestAdminBlocked:
    """Admin tokens are blocked from /api/shortcuts the same way they are
    from /api/templates — admins are platform operators, not product users."""

    async def test_admin_cannot_create(self, client, admin_api_auth_headers):
        resp = await client.post(
            "/api/shortcuts",
            json={"kind": "claude", "chord_token": "ctrl+x"},
            headers=admin_api_auth_headers,
        )
        assert resp.status_code == 403


class TestBroadcast:
    """Every effective mutation enqueues a JSON envelope on each of the
    owner's user-scoped client send queues; a no-op upsert stays silent."""

    async def test_create_broadcasts(self, app, viewer_user):
        routing: RoutingTable = app.state.routing
        client_a = _make_user_client(viewer_user["id"])
        client_b = _make_user_client(viewer_user["id"])
        routing.register_user_client(viewer_user["id"], client_a)
        routing.register_user_client(viewer_user["id"], client_b)

        await app.state.shortcut_service.create_shortcut(
            viewer_user["id"], "claude", "ctrl+x", "Cut"
        )

        for c in (client_a, client_b):
            envelope = json.loads(c.send_queue.get_nowait())
            assert envelope["type"] == "shortcut.created"
            assert envelope["payload"]["kind"] == "claude"
            assert envelope["payload"]["chord_token"] == "ctrl+x"
            assert envelope["payload"]["name"] == "Cut"

    async def test_upsert_name_change_broadcasts_updated(
        self, app, viewer_user
    ):
        routing: RoutingTable = app.state.routing
        c = _make_user_client(viewer_user["id"])
        routing.register_user_client(viewer_user["id"], c)

        row = await app.state.shortcut_service.create_shortcut(
            viewer_user["id"], "claude", "ctrl+x", "Old"
        )
        c.send_queue.get_nowait()  # drain shortcut.created

        await app.state.shortcut_service.create_shortcut(
            viewer_user["id"], "claude", "ctrl+x", "New"
        )
        envelope = json.loads(c.send_queue.get_nowait())
        assert envelope["type"] == "shortcut.updated"
        assert envelope["payload"]["shortcut_id"] == row["shortcut_id"]
        assert envelope["payload"]["name"] == "New"

    async def test_noop_upsert_does_not_broadcast(self, app, viewer_user):
        routing: RoutingTable = app.state.routing
        c = _make_user_client(viewer_user["id"])
        routing.register_user_client(viewer_user["id"], c)

        await app.state.shortcut_service.create_shortcut(
            viewer_user["id"], "claude", "ctrl+x", "Same"
        )
        c.send_queue.get_nowait()  # drain shortcut.created

        await app.state.shortcut_service.create_shortcut(
            viewer_user["id"], "claude", "ctrl+x", None
        )
        with pytest.raises(asyncio.QueueEmpty):
            c.send_queue.get_nowait()

    async def test_delete_broadcasts_tombstone(self, app, viewer_user):
        routing: RoutingTable = app.state.routing
        c = _make_user_client(viewer_user["id"])
        routing.register_user_client(viewer_user["id"], c)

        row = await app.state.shortcut_service.create_shortcut(
            viewer_user["id"], "claude", "ctrl+x", None
        )
        c.send_queue.get_nowait()

        await app.state.shortcut_service.delete_shortcut(
            row["shortcut_id"], viewer_user["id"]
        )
        envelope = json.loads(c.send_queue.get_nowait())
        assert envelope["type"] == "shortcut.deleted"
        assert envelope["payload"]["shortcut_id"] == row["shortcut_id"]
        assert "version" in envelope["payload"]
        assert "updated_at" in envelope["payload"]

    async def test_does_not_broadcast_to_other_users(self, app, viewer_user):
        """Cross-user scoping: another user's clients receive nothing."""
        routing: RoutingTable = app.state.routing
        other_user = await app.state.db.create_user(
            email="other@test.local", password_hash="hash"
        )
        owner_client = _make_user_client(viewer_user["id"])
        other_client = _make_user_client(other_user)
        routing.register_user_client(viewer_user["id"], owner_client)
        routing.register_user_client(other_user, other_client)

        await app.state.shortcut_service.create_shortcut(
            viewer_user["id"], "claude", "ctrl+x", None
        )

        assert not owner_client.send_queue.empty()
        with pytest.raises(asyncio.QueueEmpty):
            other_client.send_queue.get_nowait()
