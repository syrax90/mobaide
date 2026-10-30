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

    async def test_unverified_row_not_taken_over_when_verification_bypassed(
        self, auth: AuthService, db: Database
    ):
        await db.create_user(
            email="legacy@example.com", password_hash="h", email_verified=False
        )
        with pytest.raises(ValueError, match="already registered"):
            await auth.register_user("legacy@example.com", "other456")
        user = await db.get_user_by_email("legacy@example.com")
        assert user["password_hash"] == "h"


def _patch_google(monkeypatch, *, sub: str, email: str) -> None:
    async def _verify(self, token_str):
        return {"sub": sub, "email": email, "email_verified": True}

    monkeypatch.setattr(AuthService, "_verify_google_id_token", _verify)


class TestGoogleEmailLink:
    async def test_linking_unverified_account_drops_its_password(
        self, auth: AuthService, db: Database, monkeypatch
    ):
        user_id = await db.create_user(
            email="squatted@example.com", password_hash="h", email_verified=False
        )
        _patch_google(monkeypatch, sub="g-owner", email="squatted@example.com")

        linked_id, _role, _token, is_new = await auth.google_authenticate("tok")

        assert (linked_id, is_new) == (user_id, False)
        user = await db.get_user_by_id(user_id)
        assert user["password_hash"] is None
        assert user["google_sub"] == "g-owner"
        assert user["email_verified"] == 1

    async def test_linking_verified_account_keeps_its_password(
        self, auth: AuthService, db: Database, monkeypatch
    ):
        await auth.register_user("owner@example.com", "pass123")
        _patch_google(monkeypatch, sub="g-owner", email="owner@example.com")

        await auth.google_authenticate("tok")

        user_id, _role, _token, _scheduled = await auth.login_user(
            "owner@example.com", "pass123"
        )
        user = await db.get_user_by_id(user_id)
        assert user["google_sub"] == "g-owner"

    async def test_linking_unverified_account_clears_its_attribution_and_code(
        self, auth: AuthService, db: Database, monkeypatch
    ):
        user_id = await db.create_user(
            email="squatted@example.com",
            password_hash="h",
            email_verified=False,
            ad_click_id="Cj0K_squatter",
            ad_click_kind="gclid",
            ads_consent=True,
            ad_attribution_sig="sig-squatter",
        )
        await db.create_verification_token(user_id, "code-hash", 900)
        _patch_google(monkeypatch, sub="g-owner", email="squatted@example.com")

        await auth.google_authenticate("tok")

        user = await db.get_user_by_id(user_id)
        assert (
            user["ad_click_id"],
            user["ads_consent"],
            user["ad_attribution_sig"],
        ) == (None, 0, None)
        assert await db.consume_verification_token(user_id, "code-hash", 5) != "ok"


class TestGoogleSubVerification:
    async def test_verifying_own_unverified_row_drops_password_keeps_attribution(
        self, auth: AuthService, db: Database, monkeypatch
    ):
        user_id = await db.create_user(
            email="gsub@example.com",
            password_hash="h",
            google_sub="g-1",
            email_verified=False,
            ad_click_id="Cj0K_owner",
            ad_click_kind="gclid",
            ads_consent=True,
        )
        _patch_google(monkeypatch, sub="g-1", email="gsub@example.com")

        signed_in_id, _role, _token, is_new = await auth.google_authenticate("tok")

        assert (signed_in_id, is_new) == (user_id, False)
        user = await db.get_user_by_id(user_id)
        assert user["password_hash"] is None
        assert user["email_verified"] == 1
        assert (user["ad_click_id"], user["ads_consent"]) == ("Cj0K_owner", 1)


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

    @pytest.fixture
    def zero_ttl_auth(self, db: Database) -> AuthService:
        # The default TTLs are finite; 0 is the explicit dev-only opt-out.
        config = CentralServerConfig(
            jwt_secret_key=TEST_SECRET,
            email_verification_required=False,
            mobile_token_expire_minutes=0,
            mobile_refresh_expire_minutes=0,
            dashboard_token_expire_minutes=0,
        )
        return AuthService(config, db)

    async def test_mobile_token_with_zero_ttl_has_no_exp(
        self, zero_ttl_auth: AuthService, db: Database
    ):
        auth = zero_ttl_auth
        owner = await db.create_user(email="owner@test.local", password_hash="h")
        agent_id, _ = await db.register_agent("test", owner)
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
        self, zero_ttl_auth: AuthService
    ):
        auth = zero_ttl_auth
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

    async def test_mobile_token_type_rejects_non_mobile(
        self, zero_ttl_auth: AuthService
    ):
        """A dashboard token must not decode as a mobile access token, even
        when it has no `exp` claim (guarding the type check under the new
        missing-exp path).
        """
        auth = zero_ttl_auth
        result = await auth.register_user("x@example.com", "pass123")
        dashboard_token = result.access_token
        assert dashboard_token is not None
        with pytest.raises(jwt.InvalidTokenError, match="mobile_access"):
            auth.decode_mobile_token(dashboard_token)


def _auth_with_ttls(db: Database, **ttls: int) -> AuthService:
    config = CentralServerConfig(
        jwt_secret_key=TEST_SECRET, email_verification_required=False, **ttls
    )
    return AuthService(config, db)


def _dashboard_token(**claims) -> str:
    return jwt.encode(
        {
            "sub": "user-1",
            "type": "dashboard",
            "email": "user@example.com",
            "role": "viewer",
            **claims,
        },
        TEST_SECRET,
        algorithm="HS256",
    )


class TestLegacyDashboardTokenAgeOut:
    """A dashboard JWT without `exp` was minted while the TTL was 0. Once the
    TTL is positive it expires `dashboard_token_expire_minutes` after `iat`.
    """

    async def test_token_older_than_the_ttl_is_expired(self, db: Database):
        auth = _auth_with_ttls(db, dashboard_token_expire_minutes=60)
        token = _dashboard_token(iat=int(time.time()) - 60 * 60 - 10)

        with pytest.raises(jwt.ExpiredSignatureError):
            auth.decode_dashboard_token(token)

    async def test_token_younger_than_the_ttl_still_decodes(self, db: Database):
        auth = _auth_with_ttls(db, dashboard_token_expire_minutes=60)
        token = _dashboard_token(iat=int(time.time()) - 50 * 60)

        payload = auth.decode_dashboard_token(token)

        assert payload.sub == "user-1"
        assert payload.exp is None

    async def test_zero_ttl_keeps_accepting_it(self, db: Database):
        auth = _auth_with_ttls(db, dashboard_token_expire_minutes=0)
        token = _dashboard_token(iat=int(time.time()) - 365 * 24 * 3600)

        assert auth.decode_dashboard_token(token).sub == "user-1"

    async def test_token_with_exp_is_judged_by_exp_alone(self, db: Database):
        """A token minted under an earlier, longer TTL lives to its own `exp`."""
        auth = _auth_with_ttls(db, dashboard_token_expire_minutes=60)
        now = int(time.time())
        token = _dashboard_token(iat=now - 2 * 3600, exp=now + 3600)

        assert auth.decode_dashboard_token(token).exp == now + 3600


class TestLegacyDashboardTokenAgeOutWithExplicitTtl:
    """A caller passes the TTL of the surface presenting the token (the admin
    dashboard cookie has its own); it replaces `dashboard_token_expire_minutes`.
    """

    async def test_token_older_than_the_explicit_ttl_is_expired(self, db: Database):
        auth = _auth_with_ttls(db, dashboard_token_expire_minutes=600)
        token = _dashboard_token(iat=int(time.time()) - 60 * 60)

        assert auth.decode_dashboard_token(token).sub == "user-1"
        with pytest.raises(jwt.ExpiredSignatureError):
            auth.decode_dashboard_token(token, ttl_minutes=30)

    async def test_explicit_zero_ttl_keeps_accepting_it(self, db: Database):
        auth = _auth_with_ttls(db, dashboard_token_expire_minutes=60)
        token = _dashboard_token(iat=int(time.time()) - 365 * 24 * 3600)

        assert auth.decode_dashboard_token(token, ttl_minutes=0).sub == "user-1"

    async def test_token_with_exp_ignores_the_explicit_ttl(self, db: Database):
        auth = _auth_with_ttls(db, dashboard_token_expire_minutes=600)
        now = int(time.time())
        token = _dashboard_token(iat=now - 2 * 3600, exp=now + 3600)

        payload = auth.decode_dashboard_token(token, ttl_minutes=30)

        assert payload.exp == now + 3600


class TestAdminDashboardSession:
    async def test_admin_session_uses_the_admin_ttl(self, db: Database):
        auth = _auth_with_ttls(
            db,
            dashboard_token_expire_minutes=600,
            admin_dashboard_token_expire_minutes=30,
        )

        token = auth.issue_admin_dashboard_session(
            "user-1", "admin@example.com", "admin"
        )

        raw_claims = jwt.decode(token, options={"verify_signature": False})
        assert raw_claims["exp"] == raw_claims["iat"] + 30 * 60
        payload = auth.decode_dashboard_token(token)
        assert (payload.sub, payload.email, payload.role, payload.token_type) == (
            "user-1",
            "admin@example.com",
            "admin",
            "dashboard",
        )

    async def test_other_sessions_keep_the_dashboard_ttl(self, db: Database):
        auth = _auth_with_ttls(
            db,
            dashboard_token_expire_minutes=600,
            admin_dashboard_token_expire_minutes=30,
        )

        result = await auth.register_user("user@example.com", "pass123")

        assert result.access_token is not None
        raw_claims = jwt.decode(
            result.access_token, options={"verify_signature": False}
        )
        assert raw_claims["exp"] == raw_claims["iat"] + 600 * 60


class TestArgon2Hardening:
    async def test_old_param_hash_still_verifies(self, auth: AuthService):
        from argon2 import PasswordHasher

        # Cheap legacy-style parameters; per-hash encoding means the new
        # module-level hasher must still verify it.
        old = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash("pw")
        assert await auth._verify_password(old, "pw") is True
        assert await auth._verify_password(old, "wrong") is False

    async def test_login_rehashes_legacy_hash(
        self, auth: AuthService, db: Database
    ):
        import argon2
        from argon2 import PasswordHasher

        old = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(
            "pass123"
        )
        await db.create_user(email="legacy@test.local", password_hash=old)

        await auth.login_user("legacy@test.local", "pass123")

        user = await db.get_user_by_email("legacy@test.local")
        assert user["password_hash"] != old
        params = argon2.extract_parameters(user["password_hash"])
        assert (params.time_cost, params.memory_cost, params.parallelism) == (
            3,
            65536,
            1,
        )
        # The migrated hash must keep working.
        await auth.login_user("legacy@test.local", "pass123")

    async def test_login_with_current_hash_does_not_rewrite(
        self, auth: AuthService, db: Database
    ):
        await auth.register_user("current@test.local", "pass123")
        before = (await db.get_user_by_email("current@test.local"))[
            "password_hash"
        ]
        await auth.login_user("current@test.local", "pass123")
        after = (await db.get_user_by_email("current@test.local"))[
            "password_hash"
        ]
        assert after == before

    async def test_login_succeeds_when_rehash_persist_fails(
        self, auth: AuthService, db: Database
    ):
        from argon2 import PasswordHasher

        old = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(
            "pass123"
        )
        await db.create_user(email="fragile@test.local", password_hash=old)

        persist_attempts: list[str] = []

        async def _boom(user_id: str, password_hash: str) -> None:
            persist_attempts.append(user_id)
            raise RuntimeError("disk full")

        db.update_user_password = _boom  # instance attr shadows the method
        try:
            _user_id, _role, token, _scheduled = await auth.login_user(
                "fragile@test.local", "pass123"
            )
        finally:
            del db.update_user_password

        assert token
        # Guard against a vacuous pass: the rehash path must actually have
        # run and raised.
        assert persist_attempts
        # The failed migration left the stored legacy hash untouched.
        user = await db.get_user_by_email("fragile@test.local")
        assert user["password_hash"] == old
