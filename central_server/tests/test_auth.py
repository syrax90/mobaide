"""Tests for AuthService."""
from __future__ import annotations

import time

import bcrypt
import jwt
import pytest

from central_server.auth.service import AuthService
from central_server.config import CentralServerConfig
from central_server.db.database import Database

TEST_SECRET = "test-secret-key-for-auth-tests"
TEST_DASH_USER = "admin"
TEST_DASH_PASS = "secret123"
TEST_DASH_HASH = bcrypt.hashpw(TEST_DASH_PASS.encode(), bcrypt.gensalt()).decode()


@pytest.fixture
def config() -> CentralServerConfig:
    return CentralServerConfig(
        jwt_secret_key=TEST_SECRET,
        dashboard_username=TEST_DASH_USER,
        dashboard_password_hash=TEST_DASH_HASH,
    )


@pytest.fixture
async def db(tmp_path) -> Database:
    database = Database(str(tmp_path / "auth_test.db"))
    await database.connect()
    yield database
    await database.close()


@pytest.fixture
def auth(config, db) -> AuthService:
    return AuthService(config, db)


class TestAgentAuth:
    async def test_verify_agent_token(self, auth: AuthService, db: Database):
        agent_id, raw_token = await db.register_agent("test")
        assert await auth.verify_agent_token(agent_id, raw_token) is True
        assert await auth.verify_agent_token(agent_id, "wrong") is False
        assert await auth.verify_agent_token("no-such", raw_token) is False


class TestMobileAuth:
    async def test_create_and_decode(self, auth: AuthService):
        access, refresh = await auth.create_mobile_tokens("device-1", "agent-1")
        payload = auth.decode_mobile_token(access)
        assert payload.sub == "device-1"
        assert payload.agent_id == "agent-1"
        assert payload.token_type == "mobile_access"

    async def test_refresh_rotation(self, auth: AuthService):
        _, refresh = await auth.create_mobile_tokens("device-1", "agent-1")
        result = await auth.refresh_mobile_tokens(refresh)
        assert result is not None
        new_access, new_refresh = result
        # Old refresh should no longer work
        assert await auth.refresh_mobile_tokens(refresh) is None
        # New refresh should work
        assert await auth.refresh_mobile_tokens(new_refresh) is not None

    async def test_invalid_token_raises(self, auth: AuthService):
        with pytest.raises(jwt.InvalidTokenError):
            auth.decode_mobile_token("bogus")


class TestDashboardAuth:
    def test_verify_credentials(self, auth: AuthService):
        assert auth.verify_dashboard_credentials(TEST_DASH_USER, TEST_DASH_PASS) is True
        assert auth.verify_dashboard_credentials(TEST_DASH_USER, "wrong") is False
        assert auth.verify_dashboard_credentials("wrong-user", TEST_DASH_PASS) is False

    def test_create_and_decode_token(self, auth: AuthService):
        token = auth.create_dashboard_token(TEST_DASH_USER)
        username = auth.decode_dashboard_token(token)
        assert username == TEST_DASH_USER

    def test_decode_invalid_raises(self, auth: AuthService):
        with pytest.raises(jwt.InvalidTokenError):
            auth.decode_dashboard_token("bogus")

    def test_mobile_token_rejected_as_dashboard(self, auth: AuthService):
        """A mobile access token should not be accepted as a dashboard token."""
        mobile_jwt = jwt.encode(
            {"sub": "device", "type": "mobile_access", "iat": int(time.time()), "exp": int(time.time()) + 3600, "agent_id": "a"},
            TEST_SECRET,
            algorithm="HS256",
        )
        with pytest.raises(jwt.InvalidTokenError, match="dashboard"):
            auth.decode_dashboard_token(mobile_jwt)
