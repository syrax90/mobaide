"""REST endpoint tests for the projects CRUD API (CLA-39).

Covers the full surface defined in the Linear issue: GET / POST / PUT /
DELETE / PATCH /order, secret redaction, rename-active-scan 409,
delete-active-scan 409, reorder set-mismatch 400, and the ``projects.changed``
bus broadcast emitted after every mutation.
"""
from __future__ import annotations

from pathlib import PureWindowsPath

import pytest
from httpx import ASGITransport, AsyncClient

from mobaide.agent.app import create_app
from mobaide.agent.config import AgentConfig
from mobaide.core import ProjectRecord, projects_store

from .conftest import TEST_JWT_SECRET, TEST_USERNAME


@pytest.fixture
def windows_paths(monkeypatch):
    """Parse paths with Windows rules, as the agent does on a Windows host.

    On POSIX a backslash is a legal filename character, so the backslash
    normalization only happens on Windows.
    """
    monkeypatch.setattr(projects_store, "PurePath", PureWindowsPath)


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
async def auth_headers_p(app_with_projects):
    # Mint the token directly: a login would spend ~2 s of bcrypt per test.
    token = app_with_projects.state.jwt_service.create_access_token(TEST_USERNAME)
    return {"Authorization": f"Bearer {token}"}


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
    @pytest.mark.usefixtures("windows_paths")
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


class TestAutoScanFilterOnTheWire:
    """The per-project filter must round-trip through GET / POST / PUT and
    appear on the public read model so the Android form can populate."""

    async def test_get_includes_default_filter(
        self, client_with_projects, auth_headers_p,
    ):
        resp = await client_with_projects.get(
            "/api/projects", headers=auth_headers_p,
        )
        assert resp.status_code == 200
        rows = {p["name"]: p for p in resp.json()}
        # `alpha` was seeded directly via the store; it gets the default
        # filter on construction.
        assert "auto_scan_filter" in rows["alpha"]
        f = rows["alpha"]["auto_scan_filter"]
        assert f["state_names"] == ["Proposals"]
        assert f["label_names"] == ["AutoPipeline"]
        assert f["active_state"] == "In Progress"
        assert f["done_state"] == "Done"
        assert f["removal_label"] == "AutoPipeline"

    async def test_post_with_explicit_filter_round_trips(
        self, client_with_projects, auth_headers_p,
    ):
        resp = await client_with_projects.post(
            "/api/projects",
            headers=auth_headers_p,
            json={
                "name": "delta",
                "path": "C:/delta",
                "auto_scan_filter": {
                    "state_names": ["Proposals", "Backlog"],
                    "label_names": ["AutoPipeline", "AI-ready"],
                    "active_state": "Doing",
                    "done_state": "Released",
                    "removal_label": "AI-ready",
                },
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["auto_scan_filter"]["state_names"] == ["Proposals", "Backlog"]
        assert body["auto_scan_filter"]["active_state"] == "Doing"
        assert body["auto_scan_filter"]["removal_label"] == "AI-ready"

    async def test_put_filter_replaces_wholesale(
        self, client_with_projects, auth_headers_p,
    ):
        resp = await client_with_projects.put(
            "/api/projects/alpha",
            headers=auth_headers_p,
            json={
                "auto_scan_filter": {
                    "state_names": ["Backlog"],
                    "label_names": ["AI-ready"],
                    "active_state": "Doing",
                    "done_state": "Released",
                    "removal_label": "AI-ready",
                },
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["auto_scan_filter"]["state_names"] == ["Backlog"]
        assert body["auto_scan_filter"]["label_names"] == ["AI-ready"]

    async def test_put_without_filter_keeps_existing(
        self, client_with_projects, auth_headers_p, app_with_projects,
    ):
        # Seed a non-default filter directly on the store, then PUT without
        # the auto_scan_filter key — the existing filter must survive.
        from mobaide.core.projects_config import AutoScanFilter
        store = app_with_projects.state.projects_store
        store._projects["alpha"].auto_scan_filter = AutoScanFilter(
            state_names=["Backlog"],
            label_names=["AI-ready"],
            active_state="Doing",
            done_state="Released",
            removal_label="AI-ready",
        )

        resp = await client_with_projects.put(
            "/api/projects/alpha",
            headers=auth_headers_p,
            json={"conda_env": "ml"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["auto_scan_filter"]["state_names"] == ["Backlog"]
        assert body["auto_scan_filter"]["active_state"] == "Doing"

    async def test_post_with_empty_state_names_returns_422(
        self, client_with_projects, auth_headers_p,
    ):
        # ``state_names`` and ``label_names`` have ``min_length=1`` on the
        # wire model — empty lists would silently match every issue, which
        # is almost certainly not what the user wants.
        resp = await client_with_projects.post(
            "/api/projects",
            headers=auth_headers_p,
            json={
                "name": "delta",
                "path": "C:/delta",
                "auto_scan_filter": {
                    "state_names": [],
                    "label_names": ["AutoPipeline"],
                    "active_state": "In Progress",
                    "done_state": "Done",
                    "removal_label": "AutoPipeline",
                },
            },
        )
        assert resp.status_code == 422


class TestLinearDiscoveryEndpoints:
    """``GET /api/projects/{name}/linear/labels`` and ``/states`` instantiate
    a one-shot ``LinearApiClient`` with the project's stored API key. We
    monkey-patch the client class so no real network call is made."""

    async def test_labels_404_when_project_unknown(
        self, client_with_projects, auth_headers_p,
    ):
        resp = await client_with_projects.get(
            "/api/projects/missing/linear/labels", headers=auth_headers_p,
        )
        assert resp.status_code == 404

    async def test_labels_400_when_no_linear_api_key(
        self, client_with_projects, auth_headers_p,
    ):
        # `beta` is seeded without a linear_api_key.
        resp = await client_with_projects.get(
            "/api/projects/beta/linear/labels", headers=auth_headers_p,
        )
        assert resp.status_code == 400

    async def test_labels_returns_client_response(
        self, client_with_projects, auth_headers_p, monkeypatch,
    ):
        captured = {}

        class _Stub:
            def __init__(self, api_key, rate_limiter=None):
                captured["api_key"] = api_key

            async def get_labels(self):
                return [
                    {"name": "AutoPipeline", "color": "#ff0000"},
                    {"name": "AI-ready", "color": "#00ff00"},
                ]

        monkeypatch.setattr(
            "mobaide.agent.projects.router.LinearApiClient", _Stub,
        )
        resp = await client_with_projects.get(
            "/api/projects/alpha/linear/labels", headers=auth_headers_p,
        )
        assert resp.status_code == 200
        # The stub was instantiated with the seeded project's API key.
        assert captured["api_key"] == "lin_secret"
        assert resp.json() == [
            {"name": "AutoPipeline", "color": "#ff0000"},
            {"name": "AI-ready", "color": "#00ff00"},
        ]

    async def test_states_502_on_upstream_failure(
        self, client_with_projects, auth_headers_p, monkeypatch,
    ):
        class _Boom:
            def __init__(self, *args, **kwargs):
                pass

            async def get_workflow_states(self):
                raise RuntimeError("linear is down")

        monkeypatch.setattr(
            "mobaide.agent.projects.router.LinearApiClient", _Boom,
        )
        resp = await client_with_projects.get(
            "/api/projects/alpha/linear/states", headers=auth_headers_p,
        )
        assert resp.status_code == 502
        # The internal exception detail is not echoed to the client.
        assert "linear is down" not in resp.text

    async def test_states_returns_client_response(
        self, client_with_projects, auth_headers_p, monkeypatch,
    ):
        class _Stub:
            def __init__(self, *args, **kwargs):
                pass

            async def get_workflow_states(self):
                return [
                    {"name": "Proposals", "type": "backlog"},
                    {"name": "In Progress", "type": "started"},
                    {"name": "Done", "type": "completed"},
                ]

        monkeypatch.setattr(
            "mobaide.agent.projects.router.LinearApiClient", _Stub,
        )
        resp = await client_with_projects.get(
            "/api/projects/alpha/linear/states", headers=auth_headers_p,
        )
        assert resp.status_code == 200
        names = [s["name"] for s in resp.json()]
        assert names == ["Proposals", "In Progress", "Done"]

    async def test_endpoints_require_auth(self, client_with_projects):
        resp = await client_with_projects.get(
            "/api/projects/alpha/linear/labels",
        )
        assert resp.status_code in (401, 403)
        resp = await client_with_projects.get(
            "/api/projects/alpha/linear/states",
        )
        assert resp.status_code in (401, 403)


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
