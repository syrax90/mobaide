"""Tests for the account-scoped custom AI agent type registry API.

Covers REST CRUD (GET / POST / PATCH / DELETE) on
``/api/custom-agent-types``, validation (trim, blank, length, empty
PATCH body), the two distinct 409s (per-account cap vs case-insensitive
name conflict), ownership isolation (403), soft-delete semantics
including the kind-scoped shortcut cascade, admin blocking, and the
user-scoped push-event trio (``custom_type.created`` /
``custom_type.updated`` / ``custom_type.deleted``).
"""
from __future__ import annotations

import asyncio
import json

import pytest

from central_server.custom_agent_types import service as custom_service_module
from central_server.relay.routing import RoutingTable, UserClient

DTO_KEYS = {"id", "name", "command", "created_at", "updated_at"}


def _make_user_client(user_id: str) -> UserClient:
    """Build a UserClient with no real WS — only the send_queue is used."""
    return UserClient(user_id=user_id, device_id="dev-x", websocket=None)  # type: ignore[arg-type]


async def _create_type(client, headers, name="Aider", command="aider --model x"):
    resp = await client.post(
        "/api/custom-agent-types",
        json={"name": name, "command": command},
        headers=headers,
    )
    assert resp.status_code == 201
    return resp.json()


class TestCreate:
    async def test_create_returns_201_with_exact_dto_shape(
        self, client, api_auth_headers
    ):
        data = await _create_type(client, api_auth_headers)
        assert set(data.keys()) == DTO_KEYS
        assert data["name"] == "Aider"
        assert data["command"] == "aider --model x"
        assert len(data["id"]) == 12
        assert data["created_at"] == data["updated_at"]

    async def test_create_trims_name_and_command(self, client, api_auth_headers):
        data = await _create_type(
            client, api_auth_headers, name="  Goose  ", command="  goose run  "
        )
        assert data["name"] == "Goose"
        assert data["command"] == "goose run"

    @pytest.mark.parametrize(
        "body",
        [
            {"name": "   ", "command": "run"},
            {"name": "Aider", "command": "   "},
            {"command": "run"},
            {"name": "Aider"},
            {"name": "x" * 61, "command": "run"},
            {"name": "Aider", "command": "c" * 1001},
        ],
    )
    async def test_create_invalid_body_returns_422(
        self, client, api_auth_headers, body
    ):
        resp = await client.post(
            "/api/custom-agent-types", json=body, headers=api_auth_headers
        )
        assert resp.status_code == 422

    async def test_create_duplicate_name_returns_409(
        self, client, api_auth_headers
    ):
        await _create_type(client, api_auth_headers, name="Aider")
        resp = await client.post(
            "/api/custom-agent-types",
            json={"name": "Aider", "command": "other"},
            headers=api_auth_headers,
        )
        assert resp.status_code == 409
        assert "name" in resp.json()["detail"].lower()

    async def test_create_case_variant_duplicate_returns_409(
        self, client, api_auth_headers
    ):
        await _create_type(client, api_auth_headers, name="Aider")
        resp = await client.post(
            "/api/custom-agent-types",
            json={"name": "AIDER", "command": "other"},
            headers=api_auth_headers,
        )
        assert resp.status_code == 409

    async def test_create_at_cap_returns_409(
        self, client, api_auth_headers, monkeypatch
    ):
        monkeypatch.setattr(custom_service_module, "MAX_TYPES_PER_ACCOUNT", 2)
        await _create_type(client, api_auth_headers, name="One", command="c1")
        await _create_type(client, api_auth_headers, name="Two", command="c2")

        resp = await client.post(
            "/api/custom-agent-types",
            json={"name": "Three", "command": "c3"},
            headers=api_auth_headers,
        )
        assert resp.status_code == 409
        assert "limit" in resp.json()["detail"].lower()

    async def test_deleted_types_do_not_count_toward_cap(
        self, client, api_auth_headers, monkeypatch
    ):
        monkeypatch.setattr(custom_service_module, "MAX_TYPES_PER_ACCOUNT", 1)
        first = await _create_type(client, api_auth_headers, name="One")
        resp = await client.delete(
            f"/api/custom-agent-types/{first['id']}", headers=api_auth_headers
        )
        assert resp.status_code == 204

        fresh = await _create_type(client, api_auth_headers, name="Two")
        assert fresh["id"] != first["id"]

    async def test_name_reusable_after_delete(self, client, api_auth_headers):
        first = await _create_type(client, api_auth_headers, name="Aider")
        await client.delete(
            f"/api/custom-agent-types/{first['id']}", headers=api_auth_headers
        )

        fresh = await _create_type(client, api_auth_headers, name="Aider")
        assert fresh["id"] != first["id"]

    async def test_create_requires_auth(self, client):
        resp = await client.post(
            "/api/custom-agent-types", json={"name": "A", "command": "c"}
        )
        assert resp.status_code == 401


class TestList:
    async def test_list_empty(self, client, api_auth_headers):
        resp = await client.get(
            "/api/custom-agent-types", headers=api_auth_headers
        )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_returns_only_owned(self, client, app, api_auth_headers):
        mine = await _create_type(client, api_auth_headers, name="Mine")
        other_user = await app.state.db.create_user(
            email="other@test.local", password_hash="hash"
        )
        await app.state.db.create_custom_agent_type(
            other_user, "Theirs", "their-cmd"
        )

        resp = await client.get(
            "/api/custom-agent-types", headers=api_auth_headers
        )
        items = resp.json()
        assert [i["id"] for i in items] == [mine["id"]]
        assert set(items[0].keys()) == DTO_KEYS

    async def test_list_excludes_soft_deleted(self, client, api_auth_headers):
        kept = await _create_type(client, api_auth_headers, name="Kept")
        gone = await _create_type(client, api_auth_headers, name="Gone")
        await client.delete(
            f"/api/custom-agent-types/{gone['id']}", headers=api_auth_headers
        )

        resp = await client.get(
            "/api/custom-agent-types", headers=api_auth_headers
        )
        assert [i["id"] for i in resp.json()] == [kept["id"]]

    async def test_list_requires_auth(self, client):
        resp = await client.get("/api/custom-agent-types")
        assert resp.status_code == 401


class TestUpdate:
    async def test_patch_name_only_keeps_command(self, client, api_auth_headers):
        created = await _create_type(client, api_auth_headers)
        resp = await client.patch(
            f"/api/custom-agent-types/{created['id']}",
            json={"name": "Renamed"},
            headers=api_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Renamed"
        assert data["command"] == created["command"]
        assert data["id"] == created["id"]

    async def test_patch_command_only_keeps_name(self, client, api_auth_headers):
        created = await _create_type(client, api_auth_headers)
        resp = await client.patch(
            f"/api/custom-agent-types/{created['id']}",
            json={"command": "new-cmd --flag"},
            headers=api_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["command"] == "new-cmd --flag"
        assert data["name"] == created["name"]

    async def test_patch_both_fields(self, client, api_auth_headers):
        created = await _create_type(client, api_auth_headers)
        resp = await client.patch(
            f"/api/custom-agent-types/{created['id']}",
            json={"name": "Both", "command": "both-cmd"},
            headers=api_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Both"
        assert resp.json()["command"] == "both-cmd"

    @pytest.mark.parametrize(
        "body", [{}, {"name": "   "}, {"command": "   "}, {"name": None}]
    )
    async def test_patch_empty_or_blank_returns_422(
        self, client, api_auth_headers, body
    ):
        created = await _create_type(client, api_auth_headers)
        resp = await client.patch(
            f"/api/custom-agent-types/{created['id']}",
            json=body,
            headers=api_auth_headers,
        )
        assert resp.status_code == 422

    async def test_patch_not_owner_returns_403(
        self, client, app, api_auth_headers
    ):
        other_user = await app.state.db.create_user(
            email="other@test.local", password_hash="hash"
        )
        row = await app.state.db.create_custom_agent_type(
            other_user, "Theirs", "their-cmd"
        )
        resp = await client.patch(
            f"/api/custom-agent-types/{row['id']}",
            json={"name": "hacked"},
            headers=api_auth_headers,
        )
        assert resp.status_code == 403

    async def test_patch_after_delete_returns_403(self, client, api_auth_headers):
        created = await _create_type(client, api_auth_headers)
        await client.delete(
            f"/api/custom-agent-types/{created['id']}", headers=api_auth_headers
        )
        resp = await client.patch(
            f"/api/custom-agent-types/{created['id']}",
            json={"name": "ghost"},
            headers=api_auth_headers,
        )
        assert resp.status_code == 403

    async def test_rename_onto_other_live_name_returns_409(
        self, client, api_auth_headers
    ):
        await _create_type(client, api_auth_headers, name="Taken")
        mine = await _create_type(client, api_auth_headers, name="Mine")
        resp = await client.patch(
            f"/api/custom-agent-types/{mine['id']}",
            json={"name": "taken"},
            headers=api_auth_headers,
        )
        assert resp.status_code == 409

    async def test_case_variant_self_rename_is_allowed(
        self, client, api_auth_headers
    ):
        created = await _create_type(client, api_auth_headers, name="aider")
        resp = await client.patch(
            f"/api/custom-agent-types/{created['id']}",
            json={"name": "AIDER"},
            headers=api_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "AIDER"


class TestDelete:
    async def test_delete_soft_deletes(self, client, app, api_auth_headers):
        created = await _create_type(client, api_auth_headers)
        resp = await client.delete(
            f"/api/custom-agent-types/{created['id']}", headers=api_auth_headers
        )
        assert resp.status_code == 204

        row = await app.state.db.get_custom_agent_type(created["id"])
        assert row is not None
        assert row["deleted_at"] is not None

    async def test_second_delete_returns_403(self, client, api_auth_headers):
        created = await _create_type(client, api_auth_headers)
        await client.delete(
            f"/api/custom-agent-types/{created['id']}", headers=api_auth_headers
        )
        resp = await client.delete(
            f"/api/custom-agent-types/{created['id']}", headers=api_auth_headers
        )
        assert resp.status_code == 403

    async def test_delete_not_owner_returns_403(
        self, client, app, api_auth_headers
    ):
        other_user = await app.state.db.create_user(
            email="other@test.local", password_hash="hash"
        )
        row = await app.state.db.create_custom_agent_type(
            other_user, "Theirs", "their-cmd"
        )
        resp = await client.delete(
            f"/api/custom-agent-types/{row['id']}", headers=api_auth_headers
        )
        assert resp.status_code == 403

    async def test_delete_cascades_only_kind_scoped_shortcuts(
        self, client, app, viewer_user, api_auth_headers
    ):
        """Deleting a type soft-deletes the owner's shortcuts whose kind is
        exactly ``custom_<id>`` (with a version bump), leaving shortcuts of
        other kinds untouched."""
        db = app.state.db
        created = await _create_type(client, api_auth_headers)
        custom_kind = f"custom_{created['id']}"
        scoped = await db.create_user_shortcut(
            viewer_user["id"], custom_kind, "ctrl+x", "Run"
        )
        unrelated = await db.create_user_shortcut(
            viewer_user["id"], "claude", "ctrl+x", "Chat"
        )

        resp = await client.delete(
            f"/api/custom-agent-types/{created['id']}", headers=api_auth_headers
        )
        assert resp.status_code == 204

        scoped_after = await db.get_user_shortcut(scoped["shortcut_id"])
        assert scoped_after["deleted_at"] is not None
        assert scoped_after["version"] == scoped["version"] + 1

        unrelated_after = await db.get_user_shortcut(unrelated["shortcut_id"])
        assert unrelated_after["deleted_at"] is None
        assert unrelated_after["version"] == unrelated["version"]


class TestAdminBlocked:
    """Admin tokens are blocked from /api/custom-agent-types the same way
    they are from /api/shortcuts — admins are platform operators, not
    product users."""

    async def test_admin_cannot_create(self, client, admin_api_auth_headers):
        resp = await client.post(
            "/api/custom-agent-types",
            json={"name": "Aider", "command": "aider"},
            headers=admin_api_auth_headers,
        )
        assert resp.status_code == 403

    async def test_admin_cannot_list(self, client, admin_api_auth_headers):
        resp = await client.get(
            "/api/custom-agent-types", headers=admin_api_auth_headers
        )
        assert resp.status_code == 403


class TestBroadcast:
    """Every mutation enqueues a JSON envelope on each of the owner's
    user-scoped client send queues; delete sends exactly one event even
    when it cascades shortcut rows."""

    async def test_create_broadcasts_full_dto(self, app, viewer_user):
        routing: RoutingTable = app.state.routing
        client_a = _make_user_client(viewer_user["id"])
        client_b = _make_user_client(viewer_user["id"])
        routing.register_user_client(viewer_user["id"], client_a)
        routing.register_user_client(viewer_user["id"], client_b)

        row = await app.state.custom_agent_type_service.create_type(
            viewer_user["id"], "Aider", "aider --yes"
        )

        for c in (client_a, client_b):
            envelope = json.loads(c.send_queue.get_nowait())
            assert envelope["type"] == "custom_type.created"
            assert envelope["payload"] == row
            assert set(envelope["payload"].keys()) == DTO_KEYS

    async def test_update_broadcasts_full_dto(self, app, viewer_user):
        routing: RoutingTable = app.state.routing
        c = _make_user_client(viewer_user["id"])
        routing.register_user_client(viewer_user["id"], c)
        service = app.state.custom_agent_type_service

        row = await service.create_type(viewer_user["id"], "Aider", "aider")
        c.send_queue.get_nowait()  # drain custom_type.created

        updated = await service.update_type(
            row["id"], viewer_user["id"], name="Renamed", command=None
        )
        envelope = json.loads(c.send_queue.get_nowait())
        assert envelope["type"] == "custom_type.updated"
        assert envelope["payload"] == updated
        assert envelope["payload"]["name"] == "Renamed"

    async def test_delete_broadcasts_exactly_one_id_event(
        self, app, viewer_user
    ):
        """Even with a cascading shortcut in place, delete emits a single
        ``custom_type.deleted`` ``{"id"}`` envelope — no companion
        ``shortcut.deleted`` events."""
        routing: RoutingTable = app.state.routing
        c = _make_user_client(viewer_user["id"])
        routing.register_user_client(viewer_user["id"], c)
        service = app.state.custom_agent_type_service

        row = await service.create_type(viewer_user["id"], "Aider", "aider")
        c.send_queue.get_nowait()  # drain custom_type.created
        await app.state.db.create_user_shortcut(
            viewer_user["id"], f"custom_{row['id']}", "ctrl+x", None
        )

        await service.delete_type(row["id"], viewer_user["id"])

        envelope = json.loads(c.send_queue.get_nowait())
        assert envelope["type"] == "custom_type.deleted"
        assert envelope["payload"] == {"id": row["id"]}
        with pytest.raises(asyncio.QueueEmpty):
            c.send_queue.get_nowait()

    async def test_does_not_broadcast_to_other_users(self, app, viewer_user):
        routing: RoutingTable = app.state.routing
        other_user = await app.state.db.create_user(
            email="other@test.local", password_hash="hash"
        )
        owner_client = _make_user_client(viewer_user["id"])
        other_client = _make_user_client(other_user)
        routing.register_user_client(viewer_user["id"], owner_client)
        routing.register_user_client(other_user, other_client)

        await app.state.custom_agent_type_service.create_type(
            viewer_user["id"], "Aider", "aider"
        )

        assert not owner_client.send_queue.empty()
        with pytest.raises(asyncio.QueueEmpty):
            other_client.send_queue.get_nowait()
