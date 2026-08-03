"""Tests for the opt-in refreshable user session.

The browser SPA asks for a short-lived access token plus a rotating refresh
token by sending ``refreshable: true`` on any session-minting auth call; the
mobile clients omit the flag and must keep receiving exactly today's response.
Covers that opt-in, the new unauthenticated ``POST /api/auth/refresh``
rotation endpoint, and the revocation of user-session refresh tokens at the
call sites that already drop the mobile axis's tokens.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from central_server.agents.service import AgentService
from central_server.app import create_app
from central_server.auth.service import AuthService
from central_server.config import CentralServerConfig
from central_server.db.database import Database
from central_server.manifest.store import ManifestStore
from central_server.pairing.service import PairingService
from central_server.relay.routing import RoutingTable
from tests.conftest import (
    TEST_JWT_SECRET,
    TEST_VIEWER_EMAIL,
    TEST_VIEWER_PASSWORD,
)


class _FakeEmailSender:
    """Captures the OTPs instead of sending them."""

    def __init__(self) -> None:
        self.verification: list[tuple[str, str]] = []
        self.reset: list[tuple[str, str]] = []

    async def send_verification_email(
        self, to: str, code: str, ttl_minutes: int
    ) -> None:
        self.verification.append((to, code))

    async def send_password_reset_email(
        self, to: str, code: str, ttl_minutes: int, client_ip: str | None = None
    ) -> None:
        self.reset.append((to, code))

    async def send_password_changed_notification(
        self, *args: Any, **kwargs: Any
    ) -> None:
        pass


async def _login(client, *, refreshable: bool) -> dict:
    resp = await client.post(
        "/api/auth/login",
        json={
            "email": TEST_VIEWER_EMAIL,
            "password": TEST_VIEWER_PASSWORD,
            "refreshable": refreshable,
        },
    )
    assert resp.status_code == 200
    return resp.json()


def _claims(token: str) -> dict:
    return jwt.decode(token, options={"verify_signature": False})


class TestRefreshableOptIn:
    async def test_omitting_the_flag_leaves_the_response_unchanged(
        self, client, viewer_user
    ) -> None:
        """Mobile never sends ``refreshable``; its session must not change."""
        del viewer_user
        resp = await client.post(
            "/api/auth/login",
            json={"email": TEST_VIEWER_EMAIL, "password": TEST_VIEWER_PASSWORD},
        )

        body = resp.json()
        assert resp.status_code == 200
        assert body["access_token"]
        assert body["refresh_token"] is None
        assert body["expires_in"] is None
        # dashboard_token_expire_minutes is 0 by default, so no expiry claim.
        assert "exp" not in _claims(body["access_token"])

    async def test_explicit_false_is_the_same_as_omitting_it(
        self, client, viewer_user
    ) -> None:
        del viewer_user
        body = await _login(client, refreshable=False)

        assert body["refresh_token"] is None
        assert body["expires_in"] is None

    async def test_opting_in_returns_a_rotating_short_lived_session(
        self, client, config, viewer_user
    ) -> None:
        del viewer_user
        body = await _login(client, refreshable=True)

        assert body["refresh_token"]
        assert body["expires_in"] == config.user_session_access_expire_minutes * 60
        assert "exp" in _claims(body["access_token"])

    async def test_opted_in_token_keeps_the_claims_dependencies_read(
        self, client, viewer_user
    ) -> None:
        """The upgrade re-mints the JWT, so its identity claims must survive."""
        body = await _login(client, refreshable=True)
        claims = _claims(body["access_token"])

        assert claims["user_id"] == viewer_user["id"]
        assert claims["email"] == TEST_VIEWER_EMAIL
        assert claims["role"] == "viewer"

    async def test_google_and_apple_bodies_accept_the_flag(self, client) -> None:
        """The flag is additive on every session-minting model, not just login.

        Both endpoints reject the fake token later in the handler; a 422 here
        would mean the request model never grew the field.
        """
        google = await client.post(
            "/api/auth/google", json={"id_token": "not-a-token", "refreshable": True}
        )
        apple = await client.post(
            "/api/auth/apple",
            json={"id_token": "not-a-token", "nonce": "n", "refreshable": True},
        )

        assert google.status_code != 422
        assert apple.status_code != 422


class TestRefreshEndpoint:
    async def test_rotates_the_token_and_rejects_the_replay(
        self, client, viewer_user
    ) -> None:
        first = await _login(client, refreshable=True)

        resp = await client.post(
            "/api/auth/refresh", json={"refresh_token": first["refresh_token"]}
        )

        body = resp.json()
        assert resp.status_code == 200
        assert body["refresh_token"] != first["refresh_token"]
        assert body["user_id"] == viewer_user["id"]
        assert body["email"] == TEST_VIEWER_EMAIL
        assert body["role"] == "viewer"
        assert body["expires_in"] == first["expires_in"]
        # The rotated access token is a usable bearer.
        devices = await client.get(
            "/api/devices",
            headers={"Authorization": f"Bearer {body['access_token']}"},
        )
        assert devices.status_code == 200
        # Rotate-on-use: presenting the consumed token again is terminal.
        replay = await client.post(
            "/api/auth/refresh", json={"refresh_token": first["refresh_token"]}
        )
        assert replay.status_code == 401

    async def test_the_rotated_token_can_itself_be_rotated(
        self, client, viewer_user
    ) -> None:
        del viewer_user
        first = await _login(client, refreshable=True)
        second = await client.post(
            "/api/auth/refresh", json={"refresh_token": first["refresh_token"]}
        )

        third = await client.post(
            "/api/auth/refresh",
            json={"refresh_token": second.json()["refresh_token"]},
        )

        assert third.status_code == 200
        assert third.json()["refresh_token"] not in {
            first["refresh_token"],
            second.json()["refresh_token"],
        }

    async def test_unknown_token_returns_401(self, client) -> None:
        resp = await client.post(
            "/api/auth/refresh", json={"refresh_token": "never-issued"}
        )

        assert resp.status_code == 401

    async def test_empty_token_is_rejected_by_validation(self, client) -> None:
        resp = await client.post("/api/auth/refresh", json={"refresh_token": ""})

        assert resp.status_code == 422

    async def test_needs_no_bearer_of_its_own(self, client, viewer_user) -> None:
        """The refresh token is the credential — the access token it replaces
        has usually expired already."""
        del viewer_user
        first = await _login(client, refreshable=True)

        resp = await client.post(
            "/api/auth/refresh",
            json={"refresh_token": first["refresh_token"]},
            headers={"Authorization": "Bearer garbage"},
        )

        assert resp.status_code == 200

    async def test_surfaces_a_deletion_schedule_taken_after_sign_in(
        self, client, app, viewer_user
    ) -> None:
        """The user row is re-read on rotation, so the grace banner stays
        accurate without a separate profile endpoint."""
        first = await _login(client, refreshable=True)
        scheduled_for = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
        await app.state.db.set_scheduled_deletion(
            viewer_user["id"],
            scheduled_for=scheduled_for,
            requested_at=datetime.now(timezone.utc).isoformat(),
        )

        resp = await client.post(
            "/api/auth/refresh", json={"refresh_token": first["refresh_token"]}
        )

        assert resp.json()["scheduled_deletion_at"] == scheduled_for


class TestRevocation:
    async def test_account_deletion_ends_browser_sessions(
        self, client, viewer_user, api_auth_headers
    ) -> None:
        del viewer_user
        session = await _login(client, refreshable=True)

        deleted = await client.post(
            "/api/auth/me/delete",
            json={"method": "password", "password": TEST_VIEWER_PASSWORD},
            headers=api_auth_headers,
        )

        assert deleted.status_code == 204
        replay = await client.post(
            "/api/auth/refresh", json={"refresh_token": session["refresh_token"]}
        )
        assert replay.status_code == 401

    async def test_password_reset_ends_browser_sessions(
        self, config, db, viewer_user
    ) -> None:
        sender = _FakeEmailSender()
        service = AuthService(config, db, email_sender=sender)  # type: ignore[arg-type]
        try:
            _access, refresh, _expires = await service.issue_refreshable_session(
                viewer_user["id"], TEST_VIEWER_EMAIL, "viewer"
            )
            await service.request_password_reset(TEST_VIEWER_EMAIL)
            await asyncio.sleep(0)
            _to, code = sender.reset[-1]

            await service.reset_password(TEST_VIEWER_EMAIL, code, "a-brand-new-pass")

            assert await db.validate_and_consume_user_refresh(refresh) is None
        finally:
            service.close()


class TestVerificationRequiredNeverGetsARefreshToken:
    """A ``/register`` that returns no session must not hand out a refresh
    token either — it would let an unverified account mint access tokens."""

    @pytest.fixture
    def verify_config(self, tmp_path) -> CentralServerConfig:
        return CentralServerConfig(
            jwt_secret_key=TEST_JWT_SECRET,
            db_path=str(tmp_path / "verify_refresh.db"),
            log_level="DEBUG",
            log_format="console",
            email_verification_required=True,
            manifest_store_path=str(tmp_path / "manifest.json"),
        )

    @pytest.fixture
    async def verify_db(self, verify_config) -> Database:
        database = Database(verify_config.db_path)
        await database.connect()
        yield database
        await database.close()

    @pytest.fixture
    async def verify_app(self, verify_config, verify_db):
        application = create_app(config=verify_config)
        application.state.db = verify_db
        application.state.routing = RoutingTable()
        application.state.manifest_store = ManifestStore(
            verify_config.manifest_store_path
        )
        application.state.sender = _FakeEmailSender()
        application.state.auth_service = AuthService(
            verify_config,
            verify_db,
            email_sender=application.state.sender,  # type: ignore[arg-type]
            routing=application.state.routing,
        )
        application.state.pairing_service = PairingService(verify_db)
        application.state.agent_service = AgentService(
            verify_db, application.state.routing
        )
        yield application
        application.state.auth_service.close()

    @pytest.fixture
    async def verify_client(self, verify_app):
        transport = ASGITransport(app=verify_app)
        async with AsyncClient(transport=transport, base_url="https://test") as c:
            yield c

    async def test_register_returns_no_session_and_no_refresh_token(
        self, verify_client
    ) -> None:
        resp = await verify_client.post(
            "/api/auth/register",
            json={
                "email": "pending@example.com",
                "password": "hunter22",
                "refreshable": True,
            },
        )

        body = resp.json()
        assert resp.status_code == 200
        assert body["status"] == "verification_required"
        assert body["access_token"] is None
        assert body["refresh_token"] is None
        assert body["expires_in"] is None

    async def test_verifying_the_code_completes_the_refreshable_session(
        self, verify_client, verify_app
    ) -> None:
        await verify_client.post(
            "/api/auth/register",
            json={
                "email": "pending2@example.com",
                "password": "hunter22",
                "refreshable": True,
            },
        )
        await asyncio.sleep(0)
        _to, code = verify_app.state.sender.verification[-1]

        resp = await verify_client.post(
            "/api/auth/verify-email",
            json={"email": "pending2@example.com", "code": code, "refreshable": True},
        )

        body = resp.json()
        assert resp.status_code == 200
        assert body["access_token"]
        assert body["refresh_token"]
        assert body["expires_in"] > 0
