"""Tests for auth/router.py — REST API endpoints for user authentication.

Covers:
- POST /api/auth/register (happy path, duplicate email, response shape)
- POST /api/auth/login (happy path, wrong password, nonexistent email, response shape)
- POST /api/auth/google (not configured error)
"""
from __future__ import annotations

import pytest

from tests.conftest import TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD


class TestRegisterEndpoint:
    async def test_register_success(self, client):
        resp = await client.post(
            "/api/auth/register",
            json={"email": "new@example.com", "password": "strongpass123"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["email"] == "new@example.com"
        assert body["role"] == "viewer"
        assert body["token_type"] == "bearer"
        assert len(body["access_token"]) > 20
        assert len(body["user_id"]) == 12

    async def test_register_duplicate_email_returns_409(self, client):
        await client.post(
            "/api/auth/register",
            json={"email": "dup@example.com", "password": "pass1"},
        )
        resp = await client.post(
            "/api/auth/register",
            json={"email": "dup@example.com", "password": "pass2"},
        )
        assert resp.status_code == 409
        assert "already registered" in resp.json()["detail"]

    async def test_register_seeded_admin_email_returns_409(self, client):
        """The admin seed email is taken during app startup."""
        resp = await client.post(
            "/api/auth/register",
            json={"email": TEST_ADMIN_EMAIL, "password": "anything"},
        )
        assert resp.status_code == 409


class TestLoginEndpoint:
    async def test_login_seeded_admin(self, client):
        resp = await client.post(
            "/api/auth/login",
            json={"email": TEST_ADMIN_EMAIL, "password": TEST_ADMIN_PASSWORD},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["email"] == TEST_ADMIN_EMAIL
        assert body["role"] == "admin"
        assert body["token_type"] == "bearer"
        assert len(body["access_token"]) > 20
        assert len(body["user_id"]) == 12

    async def test_login_registered_user(self, client):
        await client.post(
            "/api/auth/register",
            json={"email": "user@example.com", "password": "mypass"},
        )
        resp = await client.post(
            "/api/auth/login",
            json={"email": "user@example.com", "password": "mypass"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["email"] == "user@example.com"
        assert body["role"] == "viewer"

    async def test_login_wrong_password_returns_401(self, client):
        await client.post(
            "/api/auth/register",
            json={"email": "user@example.com", "password": "correct"},
        )
        resp = await client.post(
            "/api/auth/login",
            json={"email": "user@example.com", "password": "wrong"},
        )
        assert resp.status_code == 401
        assert "Invalid" in resp.json()["detail"]

    async def test_login_nonexistent_email_returns_401(self, client):
        resp = await client.post(
            "/api/auth/login",
            json={"email": "nobody@example.com", "password": "pass"},
        )
        assert resp.status_code == 401


class TestGoogleEndpoint:
    async def test_google_not_configured_returns_401(self, client):
        """When google_client_id is not set, the endpoint should return 401."""
        resp = await client.post(
            "/api/auth/google",
            json={"id_token": "fake-google-token"},
        )
        assert resp.status_code == 401
        assert "not configured" in resp.json()["detail"]
