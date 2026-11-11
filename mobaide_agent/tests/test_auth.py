"""Tests for JWT authentication endpoints."""
from __future__ import annotations

from tests.conftest import TEST_PASSWORD, TEST_USERNAME


class TestLogin:
    async def test_valid_credentials(self, client):
        resp = await client.post(
            "/api/auth/token",
            json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    async def test_invalid_password(self, client):
        resp = await client.post(
            "/api/auth/token",
            json={"username": TEST_USERNAME, "password": "wrong"},
        )
        assert resp.status_code == 401

    async def test_unknown_user(self, client):
        resp = await client.post(
            "/api/auth/token",
            json={"username": "nobody", "password": "pass"},
        )
        assert resp.status_code == 401


class TestRefresh:
    async def test_valid_refresh(self, client):
        # Get tokens
        resp = await client.post(
            "/api/auth/token",
            json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
        )
        refresh_token = resp.json()["refresh_token"]

        # Refresh
        resp = await client.post(
            "/api/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data

    async def test_refresh_with_access_token_fails(self, client):
        resp = await client.post(
            "/api/auth/token",
            json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
        )
        access_token = resp.json()["access_token"]

        resp = await client.post(
            "/api/auth/refresh",
            json={"refresh_token": access_token},
        )
        assert resp.status_code == 401

    async def test_refresh_with_garbage_token_fails(self, client):
        resp = await client.post(
            "/api/auth/refresh",
            json={"refresh_token": "not.a.token"},
        )
        assert resp.status_code == 401


class TestProtectedEndpoints:
    async def test_no_token_returns_401(self, client):
        resp = await client.get("/api/sessions")
        assert resp.status_code == 401

    async def test_invalid_token_returns_401(self, client):
        resp = await client.get(
            "/api/sessions",
            headers={"Authorization": "Bearer bad.token.here"},
        )
        assert resp.status_code == 401

    async def test_valid_token_succeeds(self, client, auth_headers):
        resp = await client.get("/api/sessions", headers=auth_headers)
        assert resp.status_code == 200
