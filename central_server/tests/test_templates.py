"""Tests for the user-scoped prompt-template management API.

Covers REST CRUD (GET / POST / PATCH / DELETE / POST /touch), ownership
isolation, soft-delete semantics, admin blocking, and the user-scoped
push-event broadcast on every mutation.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from central_server.relay.routing import RoutingTable, UserClient


def _make_user_client(user_id: str) -> UserClient:
    """Build a UserClient with no real WS — only the send_queue is used."""
    return UserClient(user_id=user_id, device_id="dev-x", websocket=None)  # type: ignore[arg-type]


class TestCreateTemplate:
    async def test_create_template(self, client, api_auth_headers):
        resp = await client.post(
            "/api/templates",
            json={"name": "explain-stack-trace", "body": "explain this stack trace"},
            headers=api_auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "explain-stack-trace"
        assert data["body"] == "explain this stack trace"
        assert data["is_favorite"] is False
        assert data["last_used_at"] is None
        assert data["version"] == 1
        assert len(data["template_id"]) == 12

    async def test_create_template_requires_auth(self, client):
        resp = await client.post(
            "/api/templates", json={"name": "x", "body": "y"}
        )
        assert resp.status_code == 401


class TestListTemplates:
    async def test_list_empty(self, client, api_auth_headers):
        resp = await client.get("/api/templates", headers=api_auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_returns_only_owned(self, client, app, api_auth_headers):
        # Caller's template
        await client.post(
            "/api/templates",
            json={"name": "mine", "body": "mine-body"},
            headers=api_auth_headers,
        )
        # Another user's template directly via DB
        other_user = await app.state.db.create_user(
            email="other@test.local", password_hash="hash"
        )
        await app.state.db.create_template(other_user, "theirs", "theirs-body")

        resp = await client.get("/api/templates", headers=api_auth_headers)
        items = resp.json()
        assert len(items) == 1
        assert items[0]["name"] == "mine"

    async def test_list_excludes_soft_deleted(
        self, client, api_auth_headers
    ):
        create = await client.post(
            "/api/templates",
            json={"name": "doomed", "body": "x"},
            headers=api_auth_headers,
        )
        tid = create.json()["template_id"]

        await client.delete(f"/api/templates/{tid}", headers=api_auth_headers)

        resp = await client.get("/api/templates", headers=api_auth_headers)
        assert all(t["template_id"] != tid for t in resp.json())

    async def test_list_requires_auth(self, client):
        resp = await client.get("/api/templates")
        assert resp.status_code == 401


class TestUpdateTemplate:
    async def test_patch_partial_name_only(self, client, api_auth_headers):
        create = await client.post(
            "/api/templates",
            json={"name": "old", "body": "body"},
            headers=api_auth_headers,
        )
        tid = create.json()["template_id"]
        v0 = create.json()["version"]

        resp = await client.patch(
            f"/api/templates/{tid}",
            json={"name": "new"},
            headers=api_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "new"
        assert data["body"] == "body"  # unchanged
        assert data["version"] == v0 + 1

    async def test_patch_favorite_toggle(self, client, api_auth_headers):
        create = await client.post(
            "/api/templates",
            json={"name": "x", "body": "y"},
            headers=api_auth_headers,
        )
        tid = create.json()["template_id"]

        resp = await client.patch(
            f"/api/templates/{tid}",
            json={"is_favorite": True},
            headers=api_auth_headers,
        )
        assert resp.json()["is_favorite"] is True

        resp = await client.patch(
            f"/api/templates/{tid}",
            json={"is_favorite": False},
            headers=api_auth_headers,
        )
        assert resp.json()["is_favorite"] is False

    async def test_patch_not_owner_returns_403(
        self, client, app, api_auth_headers
    ):
        other_user = await app.state.db.create_user(
            email="other@test.local", password_hash="hash"
        )
        row = await app.state.db.create_template(other_user, "theirs", "x")
        resp = await client.patch(
            f"/api/templates/{row['template_id']}",
            json={"name": "hacked"},
            headers=api_auth_headers,
        )
        assert resp.status_code == 403

    async def test_update_racing_delete_raises_not_found(
        self, app, viewer_user, monkeypatch
    ):
        """A delete committing between the update's UPDATE and its readback
        must surface as not-found (403 at the router), never broadcast the
        tombstone as ``template.updated``."""
        db = app.state.db
        service = app.state.template_service
        c = _make_user_client(viewer_user["id"])
        app.state.routing.register_user_client(viewer_user["id"], c)

        row = await service.create_template(viewer_user["id"], "n", "b")
        c.send_queue.get_nowait()  # drain template.created

        real_get = db.get_template

        async def racing_get(template_id):
            monkeypatch.setattr(db, "get_template", real_get)
            await db.soft_delete_template(template_id)
            return await real_get(template_id)

        monkeypatch.setattr(db, "get_template", racing_get)

        with pytest.raises(ValueError, match="Template not found"):
            await service.update_template(
                row["template_id"], viewer_user["id"], is_favorite=True
            )

        with pytest.raises(asyncio.QueueEmpty):
            c.send_queue.get_nowait()


class TestDeleteTemplate:
    async def test_soft_delete(self, client, app, api_auth_headers):
        create = await client.post(
            "/api/templates",
            json={"name": "doomed", "body": "x"},
            headers=api_auth_headers,
        )
        tid = create.json()["template_id"]

        resp = await client.delete(
            f"/api/templates/{tid}", headers=api_auth_headers
        )
        assert resp.status_code == 204

        # Row still exists with deleted_at set.
        row = await app.state.db.get_template(tid)
        assert row is not None
        assert row["deleted_at"] is not None

        # GET filters it out.
        listing = await client.get("/api/templates", headers=api_auth_headers)
        assert all(t["template_id"] != tid for t in listing.json())

    async def test_delete_not_owner_returns_403(
        self, client, app, api_auth_headers
    ):
        other_user = await app.state.db.create_user(
            email="other@test.local", password_hash="hash"
        )
        row = await app.state.db.create_template(other_user, "theirs", "x")
        resp = await client.delete(
            f"/api/templates/{row['template_id']}",
            headers=api_auth_headers,
        )
        assert resp.status_code == 403

    async def test_delete_idempotent_after_already_deleted(
        self, client, api_auth_headers
    ):
        create = await client.post(
            "/api/templates",
            json={"name": "x", "body": "y"},
            headers=api_auth_headers,
        )
        tid = create.json()["template_id"]
        await client.delete(f"/api/templates/{tid}", headers=api_auth_headers)
        # Calling again is a no-op (404 from owner-check, since the row is
        # filtered out of is_template_owner).
        resp2 = await client.delete(
            f"/api/templates/{tid}", headers=api_auth_headers
        )
        assert resp2.status_code == 403


class TestTouchTemplate:
    async def test_touch_bumps_last_used_at(self, client, api_auth_headers):
        create = await client.post(
            "/api/templates",
            json={"name": "x", "body": "y"},
            headers=api_auth_headers,
        )
        tid = create.json()["template_id"]
        assert create.json()["last_used_at"] is None

        resp = await client.post(
            f"/api/templates/{tid}/touch", headers=api_auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["last_used_at"] is not None
        assert data["version"] == create.json()["version"] + 1

    async def test_touch_not_owner_returns_403(
        self, client, app, api_auth_headers
    ):
        other_user = await app.state.db.create_user(
            email="other@test.local", password_hash="hash"
        )
        row = await app.state.db.create_template(other_user, "theirs", "x")
        resp = await client.post(
            f"/api/templates/{row['template_id']}/touch",
            headers=api_auth_headers,
        )
        assert resp.status_code == 403

    async def test_touch_racing_delete_raises_not_found(
        self, app, viewer_user, monkeypatch
    ):
        """A delete committing between touch's UPDATE and its readback must
        surface as not-found, never broadcast the tombstone as
        ``template.updated``."""
        db = app.state.db
        service = app.state.template_service
        c = _make_user_client(viewer_user["id"])
        app.state.routing.register_user_client(viewer_user["id"], c)

        row = await service.create_template(viewer_user["id"], "n", "b")
        c.send_queue.get_nowait()  # drain template.created

        real_get = db.get_template

        async def racing_get(template_id):
            monkeypatch.setattr(db, "get_template", real_get)
            await db.soft_delete_template(template_id)
            return await real_get(template_id)

        monkeypatch.setattr(db, "get_template", racing_get)

        with pytest.raises(ValueError, match="Template not found"):
            await service.touch_template(row["template_id"], viewer_user["id"])

        with pytest.raises(asyncio.QueueEmpty):
            c.send_queue.get_nowait()


class TestAdminBlocked:
    """Admin tokens are blocked from /api/templates the same way they are
    from /api/agents — admins are platform operators, not product users."""

    async def test_admin_cannot_create(self, client, admin_api_auth_headers):
        resp = await client.post(
            "/api/templates",
            json={"name": "x", "body": "y"},
            headers=admin_api_auth_headers,
        )
        assert resp.status_code == 403

    async def test_admin_cannot_list(self, client, admin_api_auth_headers):
        resp = await client.get(
            "/api/templates", headers=admin_api_auth_headers
        )
        assert resp.status_code == 403


class TestBroadcast:
    """Every successful mutation enqueues a JSON envelope on each of the
    owner's user-scoped client send queues. Mirrors the
    ``agent.status_changed`` test in ``test_user_events.py``."""

    async def test_create_broadcasts(self, app, viewer_user):
        routing: RoutingTable = app.state.routing
        client_a = _make_user_client(viewer_user["id"])
        client_b = _make_user_client(viewer_user["id"])
        routing.register_user_client(viewer_user["id"], client_a)
        routing.register_user_client(viewer_user["id"], client_b)

        await app.state.template_service.create_template(
            viewer_user["id"], "n", "b"
        )

        for c in (client_a, client_b):
            envelope = json.loads(c.send_queue.get_nowait())
            assert envelope["type"] == "template.created"
            assert envelope["payload"]["name"] == "n"
            assert envelope["payload"]["body"] == "b"

    async def test_update_broadcasts(self, app, viewer_user):
        routing: RoutingTable = app.state.routing
        c = _make_user_client(viewer_user["id"])
        routing.register_user_client(viewer_user["id"], c)

        row = await app.state.template_service.create_template(
            viewer_user["id"], "n", "b"
        )
        # Drain the create event.
        c.send_queue.get_nowait()

        await app.state.template_service.update_template(
            row["template_id"], viewer_user["id"], is_favorite=True
        )
        envelope = json.loads(c.send_queue.get_nowait())
        assert envelope["type"] == "template.updated"
        assert envelope["payload"]["is_favorite"] is True

    async def test_delete_broadcasts_tombstone(self, app, viewer_user):
        routing: RoutingTable = app.state.routing
        c = _make_user_client(viewer_user["id"])
        routing.register_user_client(viewer_user["id"], c)

        row = await app.state.template_service.create_template(
            viewer_user["id"], "n", "b"
        )
        c.send_queue.get_nowait()

        await app.state.template_service.delete_template(
            row["template_id"], viewer_user["id"]
        )
        envelope = json.loads(c.send_queue.get_nowait())
        assert envelope["type"] == "template.deleted"
        assert envelope["payload"]["template_id"] == row["template_id"]
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

        await app.state.template_service.create_template(
            viewer_user["id"], "n", "b"
        )

        # Owner sees the event; the other user's queue stays empty.
        assert not owner_client.send_queue.empty()
        with pytest.raises(asyncio.QueueEmpty):
            other_client.send_queue.get_nowait()
