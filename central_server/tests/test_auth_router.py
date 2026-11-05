"""Tests for auth/router.py — REST API endpoints for user authentication.

Covers:
- POST /api/auth/register (happy path, duplicate email, response shape)
- POST /api/auth/login (happy path, wrong password, nonexistent email, response shape)
- POST /api/auth/google (not configured error)
- GET /api/auth/me (auth required, response shape, first_paired_at mirror)
"""
from __future__ import annotations

import pytest

from tests.conftest import TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD, TEST_VIEWER_EMAIL


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
            json={"email": "dup@example.com", "password": "password1"},
        )
        resp = await client.post(
            "/api/auth/register",
            json={"email": "dup@example.com", "password": "password2"},
        )
        assert resp.status_code == 409
        assert "already registered" in resp.json()["detail"]

    async def test_register_password_shorter_than_eight_returns_422(
        self, client, app
    ):
        resp = await client.post(
            "/api/auth/register",
            json={"email": "short@example.com", "password": "1234567"},
        )
        assert resp.status_code == 422
        assert await app.state.db.get_user_by_email("short@example.com") is None

    async def test_register_password_of_exactly_eight_is_accepted(self, client):
        resp = await client.post(
            "/api/auth/register",
            json={"email": "eight@example.com", "password": "12345678"},
        )
        assert resp.status_code == 200

    async def test_register_password_longer_than_128_returns_422(self, client):
        resp = await client.post(
            "/api/auth/register",
            json={"email": "long@example.com", "password": "a" * 129},
        )
        assert resp.status_code == 422

    async def test_login_has_no_length_rule_for_legacy_short_password(
        self, client, app
    ):
        # Accounts created before the minimum existed must still sign in.
        await app.state.auth_service.register_user("legacy@example.com", "abc")
        resp = await client.post(
            "/api/auth/login",
            json={"email": "legacy@example.com", "password": "abc"},
        )
        assert resp.status_code == 200

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
            json={"email": "user@example.com", "password": "mypass123"},
        )
        resp = await client.post(
            "/api/auth/login",
            json={"email": "user@example.com", "password": "mypass123"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["email"] == "user@example.com"
        assert body["role"] == "viewer"

    async def test_login_wrong_password_returns_401(self, client):
        await client.post(
            "/api/auth/register",
            json={"email": "user@example.com", "password": "correct-horse"},
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


class TestMeEndpoint:
    async def test_requires_a_bearer_token(self, client):
        resp = await client.get("/api/auth/me")
        assert resp.status_code == 401

    async def test_returns_the_profile_and_nothing_secret(
        self, client, viewer_user, api_auth_headers
    ):
        resp = await client.get("/api/auth/me", headers=api_auth_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body) == {
            "user_id",
            "email",
            "role",
            "created_at",
            "scheduled_deletion_at",
            "first_paired_at",
        }
        assert body["user_id"] == viewer_user["id"]
        assert body["email"] == TEST_VIEWER_EMAIL
        assert body["role"] == "viewer"
        assert body["created_at"] == viewer_user["created_at"]
        assert body["scheduled_deletion_at"] is None
        assert body["first_paired_at"] is None

    async def test_first_paired_at_mirrors_the_pairing_marker(
        self, client, app, viewer_user, api_auth_headers
    ):
        db = app.state.db
        assert await db.mark_user_first_paired(viewer_user["id"]) is True
        stamped = (await db.get_user_by_id(viewer_user["id"]))["first_paired_at"]

        resp = await client.get("/api/auth/me", headers=api_auth_headers)

        assert resp.status_code == 200, resp.text
        assert resp.json()["first_paired_at"] == stamped

    async def test_admin_tokens_are_accepted(self, client, admin_api_auth_headers):
        resp = await client.get("/api/auth/me", headers=admin_api_auth_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["role"] == "admin"
