"""Tests for the web dashboard routes."""
from __future__ import annotations

import pytest

from tests.conftest import TEST_DASHBOARD_PASSWORD, TEST_DASHBOARD_USER


class TestDashboardLogin:
    async def test_login_page(self, client):
        resp = await client.get("/dashboard/login")
        assert resp.status_code == 200
        assert "Login" in resp.text

    async def test_login_success(self, client):
        resp = await client.post(
            "/dashboard/login",
            data={"username": TEST_DASHBOARD_USER, "password": TEST_DASHBOARD_PASSWORD},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/dashboard"
        assert "dashboard_token" in resp.headers.get("set-cookie", "")

    async def test_login_failure(self, client):
        resp = await client.post(
            "/dashboard/login",
            data={"username": "wrong", "password": "wrong"},
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

    async def test_register_agent_page(self, client, dashboard_headers):
        resp = await client.get(
            "/dashboard/agents/register", headers=dashboard_headers
        )
        assert resp.status_code == 200

    async def test_register_agent_submit(self, client, dashboard_headers):
        resp = await client.post(
            "/dashboard/agents/register",
            data={"name": "my-agent"},
            headers=dashboard_headers,
        )
        assert resp.status_code == 200
        assert "my-agent" in resp.text or "token" in resp.text.lower()

    async def test_generate_qr(self, client, dashboard_headers, registered_agent):
        agent_id, _ = registered_agent
        resp = await client.post(
            f"/dashboard/agents/{agent_id}/pair",
            headers=dashboard_headers,
        )
        assert resp.status_code == 200
        assert "data:image/png;base64" in resp.text
