"""Tests for the web dashboard routes."""
from __future__ import annotations

import pytest

from tests.conftest import TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD


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
