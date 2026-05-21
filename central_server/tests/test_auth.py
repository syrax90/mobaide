"""Tests for AuthService."""
from __future__ import annotations

import time

import jwt
import pytest

from central_server.auth.models import UserTokenPayload
from central_server.auth.service import AuthService
from central_server.config import CentralServerConfig
from central_server.db.database import Database

TEST_SECRET = "test-secret-key-for-auth-tests"
TEST_ADMIN_EMAIL = "admin@test.local"
TEST_ADMIN_PASSWORD = "secret123"


@pytest.fixture
def config() -> CentralServerConfig:
    return CentralServerConfig(
        jwt_secret_key=TEST_SECRET,
        admin_seed_email=TEST_ADMIN_EMAIL,
        admin_seed_password=TEST_ADMIN_PASSWORD,
        # Bypass OTP flow for these non-verification tests.
        email_verification_required=False,
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
        owner = await db.create_user(email="owner@test.local", password_hash="h")
        agent_id, raw_token = await db.register_agent("test", owner)
        assert await auth.verify_agent_token(agent_id, raw_token) is True
        assert await auth.verify_agent_token(agent_id, "wrong") is False
        assert await auth.verify_agent_token("no-such", raw_token) is False


class TestMobileAuth:
    async def test_create_and_decode(self, auth: AuthService, db: Database):
        owner = await db.create_user(email="owner@test.local", password_hash="h")
        agent_id, _ = await db.register_agent("test", owner)
        access, refresh = await auth.create_mobile_tokens("device-1", agent_id, owner)
        payload = auth.decode_mobile_token(access)
        assert payload.sub == "device-1"
        assert payload.agent_id == agent_id
        assert payload.user_id == owner
        assert payload.token_type == "mobile_access"

    async def test_refresh_rotation(self, auth: AuthService, db: Database):
        owner = await db.create_user(email="owner@test.local", password_hash="h")
        agent_id, _ = await db.register_agent("test", owner)
        _, refresh = await auth.create_mobile_tokens("device-1", agent_id, owner)
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


class TestUserRegistration:
    async def test_register_and_login(self, auth: AuthService):
        result = await auth.register_user("user@example.com", "pass123")
        assert result.status == "ok"
        assert result.access_token is not None
        assert len(result.user_id) == 12
        assert len(result.access_token) > 20

        login_id, role, login_token, _scheduled = await auth.login_user(
            "user@example.com", "pass123"
        )
        assert login_id == result.user_id
        assert role == "viewer"
        assert len(login_token) > 20

    async def test_duplicate_email_raises(self, auth: AuthService):
        await auth.register_user("dup@example.com", "pass123")
        with pytest.raises(ValueError, match="already registered"):
            await auth.register_user("dup@example.com", "other456")

    async def test_login_wrong_password(self, auth: AuthService):
        await auth.register_user("user@example.com", "correct")
        with pytest.raises(ValueError, match="Invalid"):
            await auth.login_user("user@example.com", "wrong")

    async def test_login_nonexistent_email(self, auth: AuthService):
        with pytest.raises(ValueError, match="Invalid"):
            await auth.login_user("nobody@example.com", "pass")

    async def test_login_google_only_user(self, auth: AuthService, db: Database):
        """Google-only users (no password) cannot login with email+password."""
        await db.create_user(email="google@example.com", google_sub="g123")
        with pytest.raises(ValueError, match="Invalid"):
            await auth.login_user("google@example.com", "anything")


class TestDashboardToken:
    async def test_create_and_decode_user_token(self, auth: AuthService):
        result = await auth.register_user("user@example.com", "pass123")
        assert result.access_token is not None
        payload = auth.decode_dashboard_token(result.access_token)
        assert isinstance(payload, UserTokenPayload)
        assert payload.sub == result.user_id
        assert payload.email == "user@example.com"
        assert payload.role == "viewer"
        assert payload.token_type == "dashboard"

    def test_decode_invalid_raises(self, auth: AuthService):
        with pytest.raises(jwt.InvalidTokenError):
            auth.decode_dashboard_token("bogus")

    def test_mobile_token_rejected_as_dashboard(self, auth: AuthService):
        """A mobile access token should not be accepted as a dashboard token."""
        mobile_jwt = jwt.encode(
            {
                "sub": "device",
                "type": "mobile_access",
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
                "agent_id": "a",
            },
            TEST_SECRET,
            algorithm="HS256",
        )
        with pytest.raises(jwt.InvalidTokenError, match="dashboard"):
            auth.decode_dashboard_token(mobile_jwt)

    def test_backward_compat_old_token(self, auth: AuthService):
        """Old-style dashboard tokens (sub=username, no email/role) should decode."""
        old_jwt = jwt.encode(
            {
                "sub": "admin",
                "type": "dashboard",
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
            },
            TEST_SECRET,
            algorithm="HS256",
        )
        payload = auth.decode_dashboard_token(old_jwt)
        assert payload.sub == "admin"
        assert payload.email == "admin"  # fallback: email = sub
        assert payload.role == "admin"  # fallback: role = "admin"


class TestAdminSeeding:
    async def test_seed_creates_admin(self, auth: AuthService, db: Database):
        await auth.seed_admin_user()
        user = await db.get_user_by_email(TEST_ADMIN_EMAIL)
        assert user is not None
        assert user["role"] == "admin"

    async def test_seed_is_idempotent(self, auth: AuthService, db: Database):
        await auth.seed_admin_user()
        await auth.seed_admin_user()  # should not raise
        user = await db.get_user_by_email(TEST_ADMIN_EMAIL)
        assert user is not None

    async def test_seed_skipped_without_config(self, db: Database):
        config = CentralServerConfig(jwt_secret_key=TEST_SECRET)
        auth = AuthService(config, db)
        await auth.seed_admin_user()
        assert await db.get_user_by_email("anything") is None

    async def test_seeded_admin_can_login(self, auth: AuthService):
        await auth.seed_admin_user()
        user_id, role, token, _scheduled = await auth.login_user(
            TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD
        )
        assert role == "admin"
        assert len(token) > 20

    async def test_seed_does_not_promote_existing_viewer(
        self, auth: AuthService, db: Database
    ):
        """If the seed email is already a viewer, seed must NOT flip them to
        admin — viewers may own agents, and admins never own agents.
        """
        # Pre-create the user as a viewer with the seed email.
        await auth.register_user(TEST_ADMIN_EMAIL, "somepassword")
        before = await db.get_user_by_email(TEST_ADMIN_EMAIL)
        assert before is not None
        assert before["role"] == "viewer"

        await auth.seed_admin_user()

        after = await db.get_user_by_email(TEST_ADMIN_EMAIL)
        assert after is not None
        assert after["role"] == "viewer"  # unchanged


class TestZeroTtlTokens:
    """Tokens issued with `*_expire_minutes == 0` omit the `exp` claim
    entirely and decode with `payload.exp is None`.
    """

    async def test_mobile_token_with_zero_ttl_has_no_exp(
        self, auth: AuthService, db: Database
    ):
        owner = await db.create_user(email="owner@test.local", password_hash="h")
        agent_id, _ = await db.register_agent("test", owner)
        # Default config has mobile_token_expire_minutes == 0.
        access, _refresh = await auth.create_mobile_tokens(
            "device-1", agent_id, owner
        )

        # Raw JWT must not contain an `exp` claim when TTL is 0.
        raw_claims = jwt.decode(access, options={"verify_signature": False})
        assert "exp" not in raw_claims
        assert "iat" in raw_claims  # iat is always emitted

        # Normal decode path exposes exp as None (not raising KeyError).
        payload = auth.decode_mobile_token(access)
        assert payload.exp is None
        assert isinstance(payload.iat, int)

    async def test_dashboard_token_with_zero_ttl_has_no_exp(
        self, auth: AuthService
    ):
        result = await auth.register_user("user@example.com", "pass123")
        token = result.access_token
        assert token is not None

        raw_claims = jwt.decode(token, options={"verify_signature": False})
        assert "exp" not in raw_claims
        assert "iat" in raw_claims

        payload = auth.decode_dashboard_token(token)
        assert payload.exp is None
        assert isinstance(payload.iat, int)

    async def test_nonzero_ttl_still_sets_exp(self, db: Database):
        """With a positive TTL, `exp` must be set to iat + ttl*60."""
        config = CentralServerConfig(
            jwt_secret_key=TEST_SECRET,
            mobile_token_expire_minutes=60,
            dashboard_token_expire_minutes=120,
        )
        auth = AuthService(config, db)
        owner = await db.create_user(email="owner2@test.local", password_hash="h")
        agent_id, _ = await db.register_agent("test", owner)
        access, _refresh = await auth.create_mobile_tokens(
            "device-1", agent_id, owner
        )

        raw_claims = jwt.decode(access, options={"verify_signature": False})
        assert "exp" in raw_claims
        assert raw_claims["exp"] == raw_claims["iat"] + 60 * 60

        payload = auth.decode_mobile_token(access)
        assert payload.exp == raw_claims["exp"]

    async def test_mobile_token_type_rejects_non_mobile(self, auth: AuthService):
        """A dashboard token must not decode as a mobile access token, even
        when it has no `exp` claim (guarding the type check under the new
        missing-exp path).
        """
        result = await auth.register_user("x@example.com", "pass123")
        dashboard_token = result.access_token
        assert dashboard_token is not None
        with pytest.raises(jwt.InvalidTokenError, match="mobile_access"):
            auth.decode_mobile_token(dashboard_token)
