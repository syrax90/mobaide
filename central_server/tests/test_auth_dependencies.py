"""Tests for auth/dependencies.py — FastAPI dependency functions.

Covers the auth dependency paths:
- get_dashboard_user: cookie-based JWT for dashboard (returns UserTokenPayload)
- get_api_user: Bearer token for user API (returns UserTokenPayload)
- get_mobile_user: Bearer token for mobile REST
- require_role: RBAC dependency factory

(ws_authenticate_mobile requires a real WebSocket and is tested via integration.)
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import jwt
import pytest
from fastapi import HTTPException

from central_server.auth.dependencies import (
    get_api_user,
    get_dashboard_user,
    get_mobile_user,
    get_non_admin_api_user,
    require_role,
)
from central_server.auth.models import UserTokenPayload
from central_server.auth.service import AuthService
from central_server.config import CentralServerConfig
from central_server.db.database import Database
from tests.conftest import TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD, TEST_JWT_SECRET

TEST_USER_EMAIL = "user@test.local"
TEST_USER_PASSWORD = "userpass123"


@pytest.fixture
def auth_config() -> CentralServerConfig:
    return CentralServerConfig(
        jwt_secret_key=TEST_JWT_SECRET,
        admin_seed_email=TEST_ADMIN_EMAIL,
        admin_seed_password=TEST_ADMIN_PASSWORD,
        # Bypass OTP flow for auth-dependency tests (JWT focus, not OTP).
        email_verification_required=False,
    )


@pytest.fixture
async def auth_db(tmp_path) -> Database:
    database = Database(str(tmp_path / "auth_dep_test.db"))
    await database.connect()
    yield database
    await database.close()


@pytest.fixture
async def auth_service(auth_config, auth_db) -> AuthService:
    svc = AuthService(auth_config, auth_db)
    await svc.seed_admin_user()
    return svc


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
        _user_id, _role, token = await auth_service.login_user(
            TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD
        )
        fake_app = _FakeApp(auth_service)
        request = _FakeRequest(
            cookies={"dashboard_token": token}, app=fake_app
        )
        user = await get_dashboard_user(request)
        assert isinstance(user, UserTokenPayload)
        assert user.email == TEST_ADMIN_EMAIL
        assert user.role == "admin"

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
                "sub": "user-id",
                "type": "dashboard",
                "email": "admin@test.local",
                "role": "admin",
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


class TestRequireRole:
    async def test_admin_allowed(self, auth_service):
        _user_id, _role, token = await auth_service.login_user(
            TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD
        )
        fake_app = _FakeApp(auth_service)
        request = _FakeRequest(
            cookies={"dashboard_token": token}, app=fake_app
        )
        check = require_role("admin")
        user = await check(user=await get_dashboard_user(request))
        assert user.role == "admin"

    async def test_viewer_blocked_from_admin(self, auth_service):
        result = await auth_service.register_user(
            TEST_USER_EMAIL, TEST_USER_PASSWORD
        )
        token = result.access_token
        fake_app = _FakeApp(auth_service)
        request = _FakeRequest(
            cookies={"dashboard_token": token}, app=fake_app
        )
        check = require_role("admin")
        user_payload = await get_dashboard_user(request)
        with pytest.raises(HTTPException) as exc_info:
            await check(user=user_payload)
        assert exc_info.value.status_code == 403
        assert "admin" in exc_info.value.detail

    async def test_multiple_roles_allowed(self, auth_service):
        result = await auth_service.register_user(
            TEST_USER_EMAIL, TEST_USER_PASSWORD
        )
        token = result.access_token
        fake_app = _FakeApp(auth_service)
        request = _FakeRequest(
            cookies={"dashboard_token": token}, app=fake_app
        )
        check = require_role("admin", "viewer")
        user = await check(user=await get_dashboard_user(request))
        assert user.role == "viewer"


class TestGetApiUser:
    async def test_valid_bearer(self, auth_service):
        _user_id, _role, token = await auth_service.login_user(
            TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD
        )
        fake_app = _FakeApp(auth_service)
        request = _FakeRequest(
            headers={"Authorization": f"Bearer {token}"}, app=fake_app
        )
        user = await get_api_user(request)
        assert isinstance(user, UserTokenPayload)
        assert user.email == TEST_ADMIN_EMAIL
        assert user.role == "admin"

    async def test_missing_auth_header(self, auth_service):
        fake_app = _FakeApp(auth_service)
        request = _FakeRequest(headers={}, app=fake_app)
        with pytest.raises(HTTPException) as exc_info:
            await get_api_user(request)
        assert exc_info.value.status_code == 401

    async def test_invalid_jwt(self, auth_service):
        fake_app = _FakeApp(auth_service)
        request = _FakeRequest(
            headers={"Authorization": "Bearer not-a-valid-jwt"},
            app=fake_app,
        )
        with pytest.raises(HTTPException) as exc_info:
            await get_api_user(request)
        assert exc_info.value.status_code == 401
        # Detail is a fixed, implementation-agnostic string — the PyJWT
        # exception message (which might say "Signature has expired" or
        # "Not enough segments" etc.) is logged for operators but never
        # leaked to the client.
        assert exc_info.value.detail == "Invalid token"

    async def test_expired_dashboard_token(self, auth_service):
        """Regression for the mobile-app 'Invalid token: Signature has expired' bug.

        A dashboard JWT with ``exp`` in the past must yield a clean 401
        with detail=='Invalid token' — no PyJWT internals leaked into
        the response body. The mobile client keys off the 401 status to
        invalidate the local session and route back to login.
        """
        expired_token = jwt.encode(
            {
                "sub": "user-id",
                "type": "dashboard",
                "email": "admin@test.local",
                "role": "admin",
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
            await get_api_user(request)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Invalid token"
        assert "Signature" not in str(exc_info.value.detail)
        assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}

    async def test_mobile_token_rejected_for_api_user(self, auth_service):
        """A mobile_access token should not pass get_api_user (wrong type)."""
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
            headers={"Authorization": f"Bearer {mobile_token}"},
            app=fake_app,
        )
        with pytest.raises(HTTPException) as exc_info:
            await get_api_user(request)
        assert exc_info.value.status_code == 401


class TestGetNonAdminApiUser:
    """Agent-ownership endpoints reject admin tokens with 403."""

    async def test_admin_blocked(self, auth_service):
        _user_id, _role, token = await auth_service.login_user(
            TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD
        )
        fake_app = _FakeApp(auth_service)
        request = _FakeRequest(
            headers={"Authorization": f"Bearer {token}"}, app=fake_app
        )
        with pytest.raises(HTTPException) as exc_info:
            await get_non_admin_api_user(user=await get_api_user(request))
        assert exc_info.value.status_code == 403
        assert "admin" in exc_info.value.detail.lower()

    async def test_viewer_allowed(self, auth_service):
        result = await auth_service.register_user(
            TEST_USER_EMAIL, TEST_USER_PASSWORD
        )
        token = result.access_token
        fake_app = _FakeApp(auth_service)
        request = _FakeRequest(
            headers={"Authorization": f"Bearer {token}"}, app=fake_app
        )
        user = await get_non_admin_api_user(user=await get_api_user(request))
        assert user.role == "viewer"
        assert user.email == TEST_USER_EMAIL


class TestGetMobileUser:
    async def test_valid_bearer(self, auth_service, auth_db):
        owner = await auth_db.create_user(email="owner@test.local", password_hash="h")
        agent_id, _ = await auth_db.register_agent("test-agent", owner)
        access, _ = await auth_service.create_mobile_tokens("dev-1", agent_id, owner)
        fake_app = _FakeApp(auth_service)
        request = _FakeRequest(
            headers={"Authorization": f"Bearer {access}"}, app=fake_app
        )
        payload = await get_mobile_user(request)
        assert payload.sub == "dev-1"
        assert payload.agent_id == agent_id
        assert payload.user_id == owner
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
        assert exc_info.value.detail == "Invalid token"

    async def test_dashboard_token_rejected_for_mobile(self, auth_service):
        """A dashboard token should not pass mobile auth (wrong type)."""
        _user_id, _role, dashboard_token = await auth_service.login_user(
            TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD
        )
        fake_app = _FakeApp(auth_service)
        request = _FakeRequest(
            headers={"Authorization": f"Bearer {dashboard_token}"},
            app=fake_app,
        )
        with pytest.raises(HTTPException) as exc_info:
            await get_mobile_user(request)
        assert exc_info.value.status_code == 401
        # Generic detail — type-mismatch and expired tokens share a
        # single opaque 401 so the client treats all auth failures
        # identically. The specific reason is logged for operators.
        assert exc_info.value.detail == "Invalid token"

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
