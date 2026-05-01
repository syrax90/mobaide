"""REST endpoint tests for the projects CRUD API (CLA-39).

Covers the full surface defined in the Linear issue: GET / POST / PUT /
DELETE / PATCH /order, secret redaction, rename-active-scan 409,
delete-active-scan 409, reorder set-mismatch 400, and the ``projects.changed``
bus broadcast emitted after every mutation.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from mobaide.agent.app import create_app
from mobaide.agent.config import AgentConfig
from mobaide.core import ProjectRecord

from .conftest import TEST_JWT_SECRET, TEST_PASSWORD, TEST_USERNAME


@pytest.fixture
async def app_with_projects(tmp_path):
    cfg = AgentConfig(
        jwt_secret_key=TEST_JWT_SECRET,
        user_store_path=str(tmp_path / "users.json"),
        projects_store_path=str(tmp_path / "projects.json"),
        max_sessions=4,
        log_level="DEBUG",
    )
    application = create_app(config=cfg)
    application.state.user_store.add_user(TEST_USERNAME, TEST_PASSWORD)
    # Seed two projects directly on the store so we don't have to go through
    # the API to set up the baseline state.
    store = application.state.projects_store
    store.create(
        ProjectRecord(
            name="alpha",
            path=str(tmp_path / "alpha"),
            linear_api_key="lin_secret",
        ),
    )
    store.create(
        ProjectRecord(name="beta", path=str(tmp_path / "beta")),
    )
    yield application
    await application.state.session_manager.shutdown()


@pytest.fixture
async def client_with_projects(app_with_projects):
    transport = ASGITransport(app=app_with_projects)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def auth_headers_p(client_with_projects):
    resp = await client_with_projects.post(
        "/api/auth/token",
        json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


class TestListProjects:
    async def test_returns_seeded_projects(
        self, client_with_projects, auth_headers_p,
    ):
        resp = await client_with_projects.get(
            "/api/projects", headers=auth_headers_p,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert {p["name"] for p in body} == {"alpha", "beta"}

    async def test_never_echoes_linear_api_key(
        self, client_with_projects, auth_headers_p,
    ):
        resp = await client_with_projects.get(
            "/api/projects", headers=auth_headers_p,
        )
        assert "lin_secret" not in resp.text
        body = resp.json()
        by_name = {p["name"]: p for p in body}
        assert by_name["alpha"]["has_linear_key"] is True
        assert by_name["beta"]["has_linear_key"] is False

    async def test_requires_auth(self, client_with_projects):
        resp = await client_with_projects.get("/api/projects")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# POST
# ---------------------------------------------------------------------------


class TestCreateProject:
    async def test_creates_and_returns_201(
        self, client_with_projects, auth_headers_p,
    ):
        resp = await client_with_projects.post(
            "/api/projects",
            headers=auth_headers_p,
            json={
                "name": "gamma",
                "path": "C:/gamma",
                "linear_api_key": "lin_new_secret",
                "default_flags": {"verbose": True, "n": 3},
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "gamma"
        assert body["has_linear_key"] is True
        assert "linear_api_key" not in body
        assert body["default_flags"] == {"verbose": True, "n": 3}

    async def test_duplicate_name_returns_409(
        self, client_with_projects, auth_headers_p,
    ):
        resp = await client_with_projects.post(
            "/api/projects",
            headers=auth_headers_p,
            json={"name": "alpha", "path": "C:/dup"},
        )
        assert resp.status_code == 409

    async def test_missing_required_fields_returns_422(
        self, client_with_projects, auth_headers_p,
    ):
        resp = await client_with_projects.post(
            "/api/projects",
            headers=auth_headers_p,
            json={"path": "C:/no-name"},
        )
        assert resp.status_code == 422

    async def test_does_not_echo_secret_in_response(
        self, client_with_projects, auth_headers_p,
    ):
        resp = await client_with_projects.post(
            "/api/projects",
            headers=auth_headers_p,
            json={
                "name": "gamma",
                "path": "C:/gamma",
                "linear_api_key": "lin_SHOULD_NOT_LEAK",
            },
        )
        assert resp.status_code == 201
        assert "lin_SHOULD_NOT_LEAK" not in resp.text

    async def test_422_scrubs_linear_api_key_input_value(
        self, client_with_projects, auth_headers_p,
    ):
        """A malformed `linear_api_key` payload must not be echoed back.

        Guards the "secret never leaves the server" invariant for the
        validation-error path — Pydantic's default 422 body includes the
        raw input, which would defeat SecretStr redaction if the caller
        sent a non-string containing the secret.
        """
        resp = await client_with_projects.post(
            "/api/projects",
            headers=auth_headers_p,
            json={
                "name": "gamma",
                "path": "C:/gamma",
                "linear_api_key": ["lin_SECRET_IN_LIST", "extra"],
            },
        )
        assert resp.status_code == 422
        assert "lin_SECRET_IN_LIST" not in resp.text

    async def test_requires_auth(self, client_with_projects):
        resp = await client_with_projects.post(
            "/api/projects", json={"name": "x", "path": "y"},
        )
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# PUT (update + rename)
# ---------------------------------------------------------------------------


class TestUpdateProject:
    async def test_partial_update(
        self, client_with_projects, auth_headers_p,
    ):
        resp = await client_with_projects.put(
            "/api/projects/beta",
            headers=auth_headers_p,
            json={"path": "C:/new/beta"},
        )
        assert resp.status_code == 200
        assert resp.json()["path"] == "C:/new/beta"

    async def test_linear_api_key_null_keeps_existing(
        self, app_with_projects, client_with_projects, auth_headers_p,
    ):
        resp = await client_with_projects.put(
            "/api/projects/alpha",
            headers=auth_headers_p,
            json={"path": "C:/unchanged-key", "linear_api_key": None},
        )
        assert resp.status_code == 200
        # Confirm the key is still present on the record.
        store = app_with_projects.state.projects_store
        rec = store.get("alpha")
        assert rec is not None
        assert rec.linear_api_key == "lin_secret"

    async def test_linear_api_key_empty_string_clears(
        self, app_with_projects, client_with_projects, auth_headers_p,
    ):
        resp = await client_with_projects.put(
            "/api/projects/alpha",
            headers=auth_headers_p,
            json={"linear_api_key": ""},
        )
        assert resp.status_code == 200
        assert resp.json()["has_linear_key"] is False
        rec = app_with_projects.state.projects_store.get("alpha")
        assert rec is not None
        assert rec.linear_api_key is None

    async def test_linear_api_key_value_replaces(
        self, app_with_projects, client_with_projects, auth_headers_p,
    ):
        resp = await client_with_projects.put(
            "/api/projects/alpha",
            headers=auth_headers_p,
            json={"linear_api_key": "lin_rotated"},
        )
        assert resp.status_code == 200
        rec = app_with_projects.state.projects_store.get("alpha")
        assert rec is not None
        assert rec.linear_api_key == "lin_rotated"

    async def test_rename(
        self, app_with_projects, client_with_projects, auth_headers_p,
    ):
        resp = await client_with_projects.put(
            "/api/projects/beta",
            headers=auth_headers_p,
            json={"name": "beta_renamed"},
        )
        assert resp.status_code == 200
        store = app_with_projects.state.projects_store
        assert store.get("beta") is None
        assert store.get("beta_renamed") is not None

    async def test_rename_while_active_scan_returns_409(
        self, app_with_projects, client_with_projects, auth_headers_p,
    ):
        # Stub the autoscan manager to report an active scan for "alpha".
        app_with_projects.state.autoscan_manager = _FakeScanManager(
            active={"alpha"},
        )

        resp = await client_with_projects.put(
            "/api/projects/alpha",
            headers=auth_headers_p,
            json={"name": "alpha_renamed"},
        )
        assert resp.status_code == 409
        assert "auto-scan" in resp.json()["detail"].lower()

    async def test_unknown_name_returns_404(
        self, client_with_projects, auth_headers_p,
    ):
        resp = await client_with_projects.put(
            "/api/projects/ghost",
            headers=auth_headers_p,
            json={"path": "C:/x"},
        )
        assert resp.status_code == 404

    async def test_requires_auth(self, client_with_projects):
        resp = await client_with_projects.put(
            "/api/projects/alpha", json={"path": "C:/x"},
        )
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


class TestDeleteProject:
    async def test_deletes_and_returns_204(
        self, app_with_projects, client_with_projects, auth_headers_p,
    ):
        resp = await client_with_projects.delete(
            "/api/projects/beta", headers=auth_headers_p,
        )
        assert resp.status_code == 204
        assert app_with_projects.state.projects_store.get("beta") is None

    async def test_active_scan_returns_409(
        self, app_with_projects, client_with_projects, auth_headers_p,
    ):
        app_with_projects.state.autoscan_manager = _FakeScanManager(
            active={"alpha"},
        )
        resp = await client_with_projects.delete(
            "/api/projects/alpha", headers=auth_headers_p,
        )
        assert resp.status_code == 409
        # The project must still exist — the delete was refused.
        assert app_with_projects.state.projects_store.get("alpha") is not None

    async def test_unknown_name_returns_404(
        self, client_with_projects, auth_headers_p,
    ):
        resp = await client_with_projects.delete(
            "/api/projects/ghost", headers=auth_headers_p,
        )
        assert resp.status_code == 404

    async def test_requires_auth(self, client_with_projects):
        resp = await client_with_projects.delete("/api/projects/alpha")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# PATCH /order
# ---------------------------------------------------------------------------


class TestReorderProjects:
    async def test_atomically_replaces_ordering(
        self, client_with_projects, auth_headers_p,
    ):
        resp = await client_with_projects.patch(
            "/api/projects/order",
            headers=auth_headers_p,
            json={"names": ["beta", "alpha"]},
        )
        assert resp.status_code == 200
        names = [p["name"] for p in resp.json()]
        assert names == ["beta", "alpha"]

    async def test_400_on_set_mismatch(
        self, client_with_projects, auth_headers_p,
    ):
        resp = await client_with_projects.patch(
            "/api/projects/order",
            headers=auth_headers_p,
            json={"names": ["alpha"]},
        )
        assert resp.status_code == 400

    async def test_400_on_duplicates(
        self, client_with_projects, auth_headers_p,
    ):
        resp = await client_with_projects.patch(
            "/api/projects/order",
            headers=auth_headers_p,
            json={"names": ["alpha", "alpha"]},
        )
        assert resp.status_code == 400

    async def test_400_on_unknown_name(
        self, client_with_projects, auth_headers_p,
    ):
        resp = await client_with_projects.patch(
            "/api/projects/order",
            headers=auth_headers_p,
            json={"names": ["alpha", "beta", "ghost"]},
        )
        assert resp.status_code == 400

    async def test_requires_auth(self, client_with_projects):
        resp = await client_with_projects.patch(
            "/api/projects/order", json={"names": []},
        )
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Event broadcast
# ---------------------------------------------------------------------------


class TestBroadcast:
    async def test_projects_changed_published_on_create(
        self, app_with_projects, client_with_projects, auth_headers_p,
    ):
        bus = app_with_projects.state.event_bus
        q = bus.subscribe()

        resp = await client_with_projects.post(
            "/api/projects",
            headers=auth_headers_p,
            json={"name": "gamma", "path": "C:/gamma"},
        )
        assert resp.status_code == 201

        env = await q.get()
        assert env.type == "projects.changed"
        names = {p["name"] for p in env.payload["projects"]}
        assert "gamma" in names

    async def test_projects_changed_published_on_update(
        self, app_with_projects, client_with_projects, auth_headers_p,
    ):
        bus = app_with_projects.state.event_bus
        q = bus.subscribe()

        resp = await client_with_projects.put(
            "/api/projects/beta",
            headers=auth_headers_p,
            json={"path": "C:/new"},
        )
        assert resp.status_code == 200

        env = await q.get()
        assert env.type == "projects.changed"

    async def test_projects_changed_published_on_delete(
        self, app_with_projects, client_with_projects, auth_headers_p,
    ):
        bus = app_with_projects.state.event_bus
        q = bus.subscribe()

        resp = await client_with_projects.delete(
            "/api/projects/beta", headers=auth_headers_p,
        )
        assert resp.status_code == 204

        env = await q.get()
        assert env.type == "projects.changed"
        names = {p["name"] for p in env.payload["projects"]}
        assert "beta" not in names

    async def test_projects_changed_published_on_reorder(
        self, app_with_projects, client_with_projects, auth_headers_p,
    ):
        bus = app_with_projects.state.event_bus
        q = bus.subscribe()

        resp = await client_with_projects.patch(
            "/api/projects/order",
            headers=auth_headers_p,
            json={"names": ["beta", "alpha"]},
        )
        assert resp.status_code == 200

        env = await q.get()
        assert env.type == "projects.changed"


# ---------------------------------------------------------------------------
# Path normalization across REST boundary
# ---------------------------------------------------------------------------


class TestPathNormalizationAcrossRest:
    async def test_create_windows_path_is_normalized_to_posix(
        self, client_with_projects, auth_headers_p,
    ):
        """A Windows-native path posted by the Android client is stored
        and echoed back in POSIX form, so subsequent reads — and error
        messages — show the same string everywhere."""
        resp = await client_with_projects.post(
            "/api/projects",
            headers=auth_headers_p,
            json={
                "name": "win_project",
                "path": "D:\\ML\\Projects\\mobaide",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["path"] == "D:/ML/Projects/mobaide"

        # GET round-trip confirms the stored form is POSIX, not the raw input.
        listing = await client_with_projects.get(
            "/api/projects", headers=auth_headers_p,
        )
        by_name = {p["name"]: p for p in listing.json()}
        assert by_name["win_project"]["path"] == "D:/ML/Projects/mobaide"

    async def test_update_normalizes_path(
        self, app_with_projects, client_with_projects, auth_headers_p,
    ):
        resp = await client_with_projects.put(
            "/api/projects/beta",
            headers=auth_headers_p,
            json={"path": "D:\\new\\beta"},
        )
        assert resp.status_code == 200
        assert resp.json()["path"] == "D:/new/beta"
        rec = app_with_projects.state.projects_store.get("beta")
        assert rec is not None
        assert rec.path == "D:/new/beta"

    async def test_update_path_none_preserves_existing(
        self, app_with_projects, client_with_projects, auth_headers_p,
    ):
        """Regression: the normalizer must not hijack the ``None`` = keep
        contract. Omitting ``path`` from the body must leave it untouched."""
        before = app_with_projects.state.projects_store.get("alpha")
        assert before is not None
        original_path = before.path

        resp = await client_with_projects.put(
            "/api/projects/alpha",
            headers=auth_headers_p,
            json={"conda_env": "irrelevant"},
        )
        assert resp.status_code == 200

        after = app_with_projects.state.projects_store.get("alpha")
        assert after is not None
        assert after.path == original_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeScanManager:
    """Minimal stub that matches the surface ``service.py`` reads: ``get``."""

    def __init__(self, *, active: set[str]) -> None:
        self._active = active

    def get(self, name: str):  # noqa: ANN201 — matches real signature shape.
        if name in self._active:
            # Any truthy sentinel — the service only checks for None vs non-None.
            return object()
        return None
