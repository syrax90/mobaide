"""Tests for auth/dependencies.py — FastAPI dependency functions.

Covers the three auth dependency paths:
- get_dashboard_user: cookie-based JWT for dashboard
- get_mobile_user: Bearer token for mobile REST

(ws_authenticate_mobile requires a real WebSocket and is tested via integration.)
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import jwt
import pytest
from fastapi import HTTPException

from central_server.auth.dependencies import get_dashboard_user, get_mobile_user
from central_server.auth.service import AuthService
from central_server.config import CentralServerConfig
from central_server.db.database import Database
from tests.conftest import (
    TEST_DASHBOARD_PASSWORD,
    TEST_DASHBOARD_USER,
    TEST_JWT_SECRET,
)

# Build a stable hash for tests (avoids the per-test bcrypt cost of conftest).
import bcrypt

_DASH_HASH = bcrypt.hashpw(TEST_DASHBOARD_PASSWORD.encode(), bcrypt.gensalt()).decode()


@pytest.fixture
def auth_config() -> CentralServerConfig:
    return CentralServerConfig(
        jwt_secret_key=TEST_JWT_SECRET,
        dashboard_username=TEST_DASHBOARD_USER,
        dashboard_password_hash=_DASH_HASH,
    )


@pytest.fixture
async def auth_db(tmp_path) -> Database:
    database = Database(str(tmp_path / "auth_dep_test.db"))
    await database.connect()
    yield database
    await database.close()


@pytest.fixture
def auth_service(auth_config, auth_db) -> AuthService:
    return AuthService(auth_config, auth_db)


class _FakeApp:
    """Minimal app stub whose .state.auth_service is an AuthService."""

    def __init__(self, auth_service: AuthService):
        self.state = SimpleNamespace(auth_service=auth_service)


class _FakeRequest:
    """Minimal Request stub for dependency testing."""

    def __init__(
        self,
        *,
        cookies: dict | None = None,
        headers: dict | None = None,
        app=None,
    ):
        self.cookies = cookies or {}
        self.headers = headers or {}
        self.app = app


class TestGetDashboardUser:
    async def test_valid_cookie(self, auth_service):
        token = auth_service.create_dashboard_token(TEST_DASHBOARD_USER)
        fake_app = _FakeApp(auth_service)
        request = _FakeRequest(
            cookies={"dashboard_token": token}, app=fake_app
        )
        username = await get_dashboard_user(request)
        assert username == TEST_DASHBOARD_USER

    async def test_missing_cookie_raises_redirect(self, auth_service):
        fake_app = _FakeApp(auth_service)
        request = _FakeRequest(cookies={}, app=fake_app)
        with pytest.raises(HTTPException) as exc_info:
            await get_dashboard_user(request)
        assert exc_info.value.status_code == 303
        assert exc_info.value.headers["Location"] == "/dashboard/login"

    async def test_invalid_cookie_raises_redirect(self, auth_service):
        fake_app = _FakeApp(auth_service)
        request = _FakeRequest(
            cookies={"dashboard_token": "bogus-jwt"}, app=fake_app
        )
        with pytest.raises(HTTPException) as exc_info:
            await get_dashboard_user(request)
        assert exc_info.value.status_code == 303

    async def test_expired_cookie_raises_redirect(self, auth_service):
        """An expired JWT in the cookie should redirect to login."""
        expired_token = jwt.encode(
            {
                "sub": TEST_DASHBOARD_USER,
                "type": "dashboard",
                "iat": int(time.time()) - 7200,
                "exp": int(time.time()) - 3600,
            },
            TEST_JWT_SECRET,
            algorithm="HS256",
        )
        fake_app = _FakeApp(auth_service)
        request = _FakeRequest(
            cookies={"dashboard_token": expired_token}, app=fake_app
        )
        with pytest.raises(HTTPException) as exc_info:
            await get_dashboard_user(request)
        assert exc_info.value.status_code == 303

    async def test_mobile_token_rejected_for_dashboard(self, auth_service):
        """A mobile_access token should not be accepted as dashboard auth."""
        mobile_token = jwt.encode(
            {
                "sub": "device-1",
                "type": "mobile_access",
                "agent_id": "agent-1",
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
            },
            TEST_JWT_SECRET,
            algorithm="HS256",
        )
        fake_app = _FakeApp(auth_service)
        request = _FakeRequest(
            cookies={"dashboard_token": mobile_token}, app=fake_app
        )
        with pytest.raises(HTTPException) as exc_info:
            await get_dashboard_user(request)
        assert exc_info.value.status_code == 303


class TestGetMobileUser:
    async def test_valid_bearer(self, auth_service, auth_db):
        agent_id, _ = await auth_db.register_agent("test-agent")
        access, _ = await auth_service.create_mobile_tokens("dev-1", agent_id)
        fake_app = _FakeApp(auth_service)
        request = _FakeRequest(
            headers={"Authorization": f"Bearer {access}"}, app=fake_app
        )
        payload = await get_mobile_user(request)
        assert payload.sub == "dev-1"
        assert payload.agent_id == agent_id
        assert payload.token_type == "mobile_access"

    async def test_missing_auth_header(self, auth_service):
        fake_app = _FakeApp(auth_service)
        request = _FakeRequest(headers={}, app=fake_app)
        with pytest.raises(HTTPException) as exc_info:
            await get_mobile_user(request)
        assert exc_info.value.status_code == 401
        assert "Missing" in exc_info.value.detail

    async def test_non_bearer_scheme(self, auth_service):
        fake_app = _FakeApp(auth_service)
        request = _FakeRequest(
            headers={"Authorization": "Basic abc123"}, app=fake_app
        )
        with pytest.raises(HTTPException) as exc_info:
            await get_mobile_user(request)
        assert exc_info.value.status_code == 401

    async def test_invalid_jwt(self, auth_service):
        fake_app = _FakeApp(auth_service)
        request = _FakeRequest(
            headers={"Authorization": "Bearer not-a-valid-jwt"},
            app=fake_app,
        )
        with pytest.raises(HTTPException) as exc_info:
            await get_mobile_user(request)
        assert exc_info.value.status_code == 401
        assert "Invalid token" in exc_info.value.detail

    async def test_dashboard_token_rejected_for_mobile(self, auth_service):
        """A dashboard token should not pass mobile auth (wrong type)."""
        dashboard_token = auth_service.create_dashboard_token(TEST_DASHBOARD_USER)
        fake_app = _FakeApp(auth_service)
        request = _FakeRequest(
            headers={"Authorization": f"Bearer {dashboard_token}"},
            app=fake_app,
        )
        with pytest.raises(HTTPException) as exc_info:
            await get_mobile_user(request)
        assert exc_info.value.status_code == 401
        assert "mobile_access" in exc_info.value.detail

    async def test_expired_bearer(self, auth_service):
        expired_token = jwt.encode(
            {
                "sub": "dev-1",
                "type": "mobile_access",
                "agent_id": "agent-1",
                "iat": int(time.time()) - 7200,
                "exp": int(time.time()) - 3600,
            },
            TEST_JWT_SECRET,
            algorithm="HS256",
        )
        fake_app = _FakeApp(auth_service)
        request = _FakeRequest(
            headers={"Authorization": f"Bearer {expired_token}"},
            app=fake_app,
        )
        with pytest.raises(HTTPException) as exc_info:
            await get_mobile_user(request)
        assert exc_info.value.status_code == 401
