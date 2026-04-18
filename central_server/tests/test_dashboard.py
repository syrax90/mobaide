"""Tests for the web dashboard routes."""
from __future__ import annotations

import pytest

from tests.conftest import (
    TEST_ADMIN_EMAIL,
    TEST_ADMIN_PASSWORD,
    TEST_VIEWER_EMAIL,
    TEST_VIEWER_PASSWORD,
)


@pytest.fixture
async def viewer_dashboard_headers(app, viewer_user) -> dict[str, str]:
    """Dashboard cookie for a viewer — dashboard must reject with 403."""
    del viewer_user  # fixture ordering: ensures viewer exists before login
    auth_service = app.state.auth_service
    _uid, _role, token = await auth_service.login_user(
        TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD
    )
    return {"Cookie": f"dashboard_token={token}"}


class TestDashboardLogin:
    async def test_login_page(self, client):
        resp = await client.get("/dashboard/login")
        assert resp.status_code == 200
        assert "Login" in resp.text

    async def test_login_success(self, client):
        resp = await client.post(
            "/dashboard/login",
            data={"username": TEST_ADMIN_EMAIL, "password": TEST_ADMIN_PASSWORD},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/dashboard"
        assert "dashboard_token" in resp.headers.get("set-cookie", "")

    async def test_login_failure(self, client):
        resp = await client.post(
            "/dashboard/login",
            data={"username": "wrong@test.com", "password": "wrong"},
        )
        assert resp.status_code == 401
        assert "Invalid" in resp.text

    async def test_logout(self, client, dashboard_headers):
        resp = await client.post(
            "/dashboard/logout",
            headers=dashboard_headers,
            follow_redirects=False,
        )
        assert resp.status_code == 303


class TestDashboardPages:
    async def test_agents_page_requires_auth(self, client):
        resp = await client.get("/dashboard", follow_redirects=False)
        assert resp.status_code == 303

    async def test_agents_page(self, client, dashboard_headers):
        resp = await client.get("/dashboard", headers=dashboard_headers)
        assert resp.status_code == 200
        assert "Agents" in resp.text

    async def test_agents_page_shows_owner(self, client, dashboard_headers, registered_agent):
        resp = await client.get("/dashboard", headers=dashboard_headers)
        assert resp.status_code == 200
        assert "Owner" in resp.text

    async def test_agent_detail(self, client, dashboard_headers, registered_agent):
        agent_id, _ = registered_agent
        resp = await client.get(
            f"/dashboard/agents/{agent_id}", headers=dashboard_headers
        )
        assert resp.status_code == 200
        assert agent_id in resp.text

    async def test_agent_detail_not_found(self, client, dashboard_headers):
        resp = await client.get(
            "/dashboard/agents/nonexistent", headers=dashboard_headers
        )
        assert resp.status_code == 404


class TestDashboardRoleGate:
    """Dashboard is an admin-only platform-operator surface.

    Viewers (agent owners) use the mobile app, not the dashboard. Any
    authenticated viewer hitting a protected dashboard route must get 403.
    """

    async def test_viewer_cannot_view_agents_page(
        self, client, viewer_dashboard_headers
    ):
        resp = await client.get("/dashboard", headers=viewer_dashboard_headers)
        assert resp.status_code == 403

    async def test_viewer_cannot_view_agent_detail(
        self, client, viewer_dashboard_headers, registered_agent
    ):
        agent_id, _ = registered_agent
        resp = await client.get(
            f"/dashboard/agents/{agent_id}",
            headers=viewer_dashboard_headers,
        )
        assert resp.status_code == 403

    async def test_viewer_cannot_revoke_pairing(
        self, client, app, viewer_dashboard_headers, registered_agent
    ):
        agent_id, _ = registered_agent
        pairing_id = await app.state.db.create_pairing(agent_id, "device-1")
        resp = await client.delete(
            f"/dashboard/pairings/{pairing_id}",
            headers=viewer_dashboard_headers,
        )
        assert resp.status_code == 403
