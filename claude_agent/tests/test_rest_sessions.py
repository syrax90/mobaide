"""Tests for session REST endpoints."""
from __future__ import annotations

import pytest


class TestCreateSession:
    async def test_create_returns_201(self, client, auth_headers):
        resp = await client.post("/api/sessions", headers=auth_headers, json={})
        assert resp.status_code == 201
        data = resp.json()
        assert "session_id" in data
        assert data["state"] == "running"
        assert data["pid"] is not None

    async def test_create_with_custom_command(self, client, auth_headers):
        import sys
        cmd = ["cmd.exe", "/c", "echo hello"] if sys.platform == "win32" else ["/bin/echo", "hello"]
        resp = await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={"command": cmd},
        )
        assert resp.status_code == 201


    async def test_create_exceeds_max_sessions_returns_409(self, client, auth_headers):
        """Creating sessions beyond max_sessions limit returns 409 Conflict."""
        # agent_config fixture sets max_sessions=4
        for _ in range(4):
            resp = await client.post("/api/sessions", headers=auth_headers, json={})
            assert resp.status_code == 201

        resp = await client.post("/api/sessions", headers=auth_headers, json={})
        assert resp.status_code == 409
        assert "limit" in resp.json()["detail"].lower()


class TestListSessions:
    async def test_empty_list(self, client, auth_headers):
        resp = await client.get("/api/sessions", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_after_create(self, client, auth_headers):
        await client.post("/api/sessions", headers=auth_headers, json={})
        resp = await client.get("/api/sessions", headers=auth_headers)
        assert resp.status_code == 200
        sessions = resp.json()
        assert len(sessions) == 1


class TestStopSession:
    async def test_stop_existing(self, client, auth_headers):
        resp = await client.post("/api/sessions", headers=auth_headers, json={})
        sid = resp.json()["session_id"]

        resp = await client.delete(f"/api/sessions/{sid}", headers=auth_headers)
        assert resp.status_code == 204

    async def test_stop_nonexistent_returns_404(self, client, auth_headers):
        resp = await client.delete("/api/sessions/nope", headers=auth_headers)
        assert resp.status_code == 404


class TestScrollback:
    async def test_scrollback_existing(self, client, auth_headers):
        resp = await client.post("/api/sessions", headers=auth_headers, json={})
        sid = resp.json()["session_id"]

        resp = await client.get(
            f"/api/sessions/{sid}/scrollback",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == sid
        assert isinstance(data["lines"], list)
        assert isinstance(data["total"], int)

    async def test_scrollback_nonexistent_returns_404(self, client, auth_headers):
        resp = await client.get(
            "/api/sessions/nope/scrollback",
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestHealth:
    async def test_health_no_auth_required(self, client):
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "sessions" in data
        assert "max_sessions" in data
