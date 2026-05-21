"""Tests for the password-reset flow (forgot-password → email → reset-password).

Mirrors ``test_email_verification.py``: unit behaviour exercised against an
in-memory fake email sender, plus router integration tests via the FastAPI
ASGI transport. Stays hermetic — no SMTP, no Mailpit.
"""
from __future__ import annotations

import asyncio
import re

import pytest
from httpx import ASGITransport, AsyncClient

from central_server.agents.service import AgentService
from central_server.app import create_app
from central_server.auth.rate_limit import ResendRateLimiter
from central_server.auth.service import (
    AuthService,
    ExpiredResetCodeError,
    InvalidResetCodeError,
    NewPasswordSameAsOldError,
    ResetAttemptsExhaustedError,
)
from central_server.config import CentralServerConfig
from central_server.db.database import Database
from central_server.pairing.service import PairingService
from central_server.relay.routing import RoutingTable
from tests.conftest import TEST_JWT_SECRET


class _FakeEmailSender:
    """Collects reset + password-changed emails instead of sending them."""

    def __init__(self) -> None:
        self.reset_messages: list[tuple[str, str, int, str | None]] = []
        self.changed_messages: list[tuple[str, int]] = []

    async def send_verification_email(
        self, to: str, code: str, ttl_minutes: int
    ) -> None:
        # Required so the same fake can serve both flows (register issues
        # a verification email before our reset-flow tests can run).
        pass

    async def send_password_reset_email(
        self,
        to: str,
        code: str,
        ttl_minutes: int,
        client_ip: str | None = None,
    ) -> None:
        self.reset_messages.append((to, code, ttl_minutes, client_ip))

    async def send_password_changed_notification(
        self, to: str, revoked_session_count: int
    ) -> None:
        self.changed_messages.append((to, revoked_session_count))


@pytest.fixture
def reset_config(tmp_path) -> CentralServerConfig:
    return CentralServerConfig(
        jwt_secret_key=TEST_JWT_SECRET,
        db_path=str(tmp_path / "reset_test.db"),
        log_level="DEBUG",
        log_format="console",
        email_verification_required=True,
        email_verification_ttl_minutes=15,
        email_verification_max_attempts=5,
        email_verification_resend_cooldown_seconds=0,
        email_verification_resend_hourly_cap=100,
        password_reset_ttl_minutes=30,
        password_reset_max_attempts=5,
        password_reset_email_cooldown_seconds=60,
        password_reset_email_hourly_cap=3,
        password_reset_ip_hourly_cap=10,
    )


@pytest.fixture
async def reset_db(reset_config) -> Database:
    db = Database(reset_config.db_path)
    await db.connect()
    yield db
    await db.close()


@pytest.fixture
def fake_sender() -> _FakeEmailSender:
    return _FakeEmailSender()


@pytest.fixture
def verification_limiter(reset_config) -> ResendRateLimiter:
    return ResendRateLimiter(
        cooldown_seconds=reset_config.email_verification_resend_cooldown_seconds,
        hourly_cap=reset_config.email_verification_resend_hourly_cap,
    )


@pytest.fixture
def reset_email_limiter(reset_config) -> ResendRateLimiter:
    return ResendRateLimiter(
        cooldown_seconds=reset_config.password_reset_email_cooldown_seconds,
        hourly_cap=reset_config.password_reset_email_hourly_cap,
    )


@pytest.fixture
def reset_ip_limiter(reset_config) -> ResendRateLimiter:
    return ResendRateLimiter(
        cooldown_seconds=0,
        hourly_cap=reset_config.password_reset_ip_hourly_cap,
    )


@pytest.fixture
def reset_service(
    reset_config,
    reset_db,
    fake_sender,
    verification_limiter,
    reset_email_limiter,
) -> AuthService:
    return AuthService(
        reset_config,
        reset_db,
        email_sender=fake_sender,  # type: ignore[arg-type]
        resend_limiter=verification_limiter,
        reset_email_limiter=reset_email_limiter,
    )


async def _make_verified_user(
    db: Database,
    svc: AuthService,
    email: str,
    password: str = "hunter22",
) -> str:
    """Insert a user that is already verified (bypass OTP).

    Hashes the password through the service so login_user accepts it; the
    test never needs the verification OTP because the user lands as
    ``email_verified=1`` from the start.
    """
    pw_hash = await svc._hash_password(password)  # noqa: SLF001 — fixture helper
    user_id = await db.create_user(
        email=email,
        password_hash=pw_hash,
        role="viewer",
        email_verified=True,
    )
    return user_id


class TestRequestPasswordReset:
    async def test_known_verified_user_gets_email(
        self,
        reset_service: AuthService,
        reset_db: Database,
        fake_sender: _FakeEmailSender,
    ):
        await _make_verified_user(reset_db, reset_service, "alice@example.com")

        await reset_service.request_password_reset(
            "alice@example.com", None, client_ip="203.0.113.7",
        )
        await asyncio.sleep(0)

        assert len(fake_sender.reset_messages) == 1
        to, code, ttl, ip = fake_sender.reset_messages[0]
        assert to == "alice@example.com"
        assert re.fullmatch(r"\d{6}", code)
        assert ttl == 30
        assert ip == "203.0.113.7"

    async def test_unknown_email_silent_success(
        self,
        reset_service: AuthService,
        fake_sender: _FakeEmailSender,
    ):
        # Must NOT raise and must NOT leak existence.
        await reset_service.request_password_reset(
            "nobody@example.com", None,
        )
        await asyncio.sleep(0)
        assert fake_sender.reset_messages == []

    async def test_unverified_user_silent_success(
        self,
        reset_service: AuthService,
        reset_db: Database,
        fake_sender: _FakeEmailSender,
    ):
        # Register without verifying — user.email_verified is False.
        await reset_service.register_user(
            "bob@example.com", "hunter22", None,
        )
        await asyncio.sleep(0)
        fake_sender.reset_messages.clear()

        await reset_service.request_password_reset("bob@example.com", None)
        await asyncio.sleep(0)

        # No reset email — unverified users are treated as unknown.
        assert fake_sender.reset_messages == []

    async def test_per_email_cooldown_silently_throttles(
        self,
        reset_service: AuthService,
        reset_db: Database,
        fake_sender: _FakeEmailSender,
    ):
        await _make_verified_user(reset_db, reset_service, "carol@example.com")

        await reset_service.request_password_reset("carol@example.com", None)
        # Second immediate call must be silently throttled (per-email cooldown=60s).
        await reset_service.request_password_reset("carol@example.com", None)
        await asyncio.sleep(0)

        assert len(fake_sender.reset_messages) == 1


class TestResetPasswordHappyPath:
    async def test_resets_password_revokes_sessions_sends_notification(
        self,
        reset_service: AuthService,
        reset_db: Database,
        fake_sender: _FakeEmailSender,
    ):
        user_id = await _make_verified_user(
            reset_db, reset_service, "dave@example.com", "oldpass99",
        )
        # Give the user an agent and an active mobile refresh token so we can
        # observe the bulk revoke.
        agent_id, _agent_token = await reset_db.register_agent(
            "dave-agent", user_id,
        )
        await reset_db.store_refresh_token(
            raw_token="refresh-token-secret",
            mobile_device_id="device-1",
            agent_id=agent_id,
            expire_minutes=60,
        )

        await reset_service.request_password_reset("dave@example.com", None)
        await asyncio.sleep(0)
        assert len(fake_sender.reset_messages) == 1
        _to, code, _ttl, _ip = fake_sender.reset_messages[0]

        await reset_service.reset_password(
            "dave@example.com", code, "newpass99", None,
        )
        await asyncio.sleep(0)

        # Old password no longer logs in; new one does.
        with pytest.raises(ValueError):
            await reset_service.login_user("dave@example.com", "oldpass99")
        new_id, role, token, _scheduled = await reset_service.login_user(
            "dave@example.com", "newpass99",
        )
        assert new_id == user_id
        assert role == "viewer"
        assert len(token) > 20

        # Mobile refresh token for that user's agent is now revoked.
        consumed = await reset_db.validate_and_consume_refresh(
            "refresh-token-secret"
        )
        assert consumed is None

        # Password-changed notification went out exactly once.
        assert len(fake_sender.changed_messages) == 1
        to, revoked_count = fake_sender.changed_messages[0]
        assert to == "dave@example.com"
        assert revoked_count == 1


class TestResetPasswordFailures:
    async def test_invalid_code_for_known_user_raises(
        self,
        reset_service: AuthService,
        reset_db: Database,
        fake_sender: _FakeEmailSender,
    ):
        await _make_verified_user(reset_db, reset_service, "eve@example.com")
        await reset_service.request_password_reset("eve@example.com", None)
        await asyncio.sleep(0)

        with pytest.raises(InvalidResetCodeError):
            await reset_service.reset_password(
                "eve@example.com", "000000", "newpass99", None,
            )

    async def test_unknown_email_raises_invalid_with_timing_parity(
        self,
        reset_service: AuthService,
    ):
        # No user exists. Service must still pay one Argon2id hash for
        # timing parity before raising InvalidResetCodeError. We don't
        # stopwatch-compare; we just assert the failure type so the
        # anti-enumeration contract is exercised.
        with pytest.raises(InvalidResetCodeError):
            await reset_service.reset_password(
                "ghost@example.com", "123456", "newpass99", None,
            )

    async def test_exhausting_attempts_locks_out_correct_code(
        self,
        reset_service: AuthService,
        reset_db: Database,
        fake_sender: _FakeEmailSender,
        reset_config: CentralServerConfig,
    ):
        await _make_verified_user(reset_db, reset_service, "frank@example.com")
        await reset_service.request_password_reset("frank@example.com", None)
        await asyncio.sleep(0)
        _to, real_code, _ttl, _ip = fake_sender.reset_messages[-1]

        for _ in range(reset_config.password_reset_max_attempts):
            with pytest.raises(InvalidResetCodeError):
                await reset_service.reset_password(
                    "frank@example.com", "111111", "newpass99", None,
                )

        # Real code rejected — caller must request a fresh OTP.
        with pytest.raises(ResetAttemptsExhaustedError):
            await reset_service.reset_password(
                "frank@example.com", real_code, "newpass99", None,
            )

    async def test_expired_code_is_rejected(
        self,
        tmp_path,
        fake_sender: _FakeEmailSender,
        verification_limiter: ResendRateLimiter,
        reset_email_limiter: ResendRateLimiter,
    ):
        # 0-minute TTL so the token expires the instant it's stored.
        fast_config = CentralServerConfig(
            jwt_secret_key=TEST_JWT_SECRET,
            db_path=str(tmp_path / "expire.db"),
            log_level="DEBUG",
            log_format="console",
            email_verification_required=True,
            password_reset_ttl_minutes=0,
        )
        db = Database(fast_config.db_path)
        await db.connect()
        try:
            svc = AuthService(
                fast_config,
                db,
                email_sender=fake_sender,  # type: ignore[arg-type]
                resend_limiter=verification_limiter,
                reset_email_limiter=reset_email_limiter,
            )
            await _make_verified_user(db, svc, "grace@example.com")
            await svc.request_password_reset("grace@example.com", None)
            await asyncio.sleep(0)
            _to, code, _ttl, _ip = fake_sender.reset_messages[-1]

            with pytest.raises(ExpiredResetCodeError):
                await svc.reset_password(
                    "grace@example.com", code, "newpass99", None,
                )
        finally:
            await db.close()

    async def test_same_password_rejected(
        self,
        reset_service: AuthService,
        reset_db: Database,
        fake_sender: _FakeEmailSender,
    ):
        await _make_verified_user(
            reset_db, reset_service, "hank@example.com", "oldpass99",
        )
        await reset_service.request_password_reset("hank@example.com", None)
        await asyncio.sleep(0)
        _to, code, _ttl, _ip = fake_sender.reset_messages[-1]

        with pytest.raises(NewPasswordSameAsOldError):
            await reset_service.reset_password(
                "hank@example.com", code, "oldpass99", None,
            )


@pytest.fixture
async def reset_app(
    reset_config,
    reset_db,
    fake_sender,
    verification_limiter,
    reset_email_limiter,
    reset_ip_limiter,
):
    app = create_app(config=reset_config)
    app.state.db = reset_db
    app.state.routing = RoutingTable()
    app.state.email_sender = fake_sender
    app.state.resend_limiter = verification_limiter
    app.state.reset_email_limiter = reset_email_limiter
    app.state.reset_ip_limiter = reset_ip_limiter
    app.state.auth_service = AuthService(
        reset_config,
        reset_db,
        email_sender=fake_sender,  # type: ignore[arg-type]
        resend_limiter=verification_limiter,
        reset_email_limiter=reset_email_limiter,
    )
    app.state.pairing_service = PairingService(reset_db)
    app.state.agent_service = AgentService(reset_db, app.state.routing)
    yield app


@pytest.fixture
async def reset_client(reset_app):
    transport = ASGITransport(app=reset_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestRouterIntegration:
    async def test_forgot_password_always_returns_200(
        self,
        reset_client,
        reset_app,
        reset_db: Database,
        fake_sender: _FakeEmailSender,
    ):
        # Unknown email — 200 with status:ok, no email sent.
        resp = await reset_client.post(
            "/api/auth/forgot-password",
            json={"email": "nobody@example.com"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        await asyncio.sleep(0)
        assert fake_sender.reset_messages == []

        # Known verified user — also 200, email IS dispatched.
        await _make_verified_user(
            reset_db, reset_app.state.auth_service, "ian@example.com",
        )
        resp = await reset_client.post(
            "/api/auth/forgot-password",
            json={"email": "ian@example.com"},
        )
        assert resp.status_code == 200
        await asyncio.sleep(0)
        assert any(m[0] == "ian@example.com" for m in fake_sender.reset_messages)

    async def test_reset_password_happy_path_returns_200(
        self,
        reset_client,
        reset_app,
        reset_db: Database,
        fake_sender: _FakeEmailSender,
    ):
        await _make_verified_user(
            reset_db, reset_app.state.auth_service, "jane@example.com",
            password="oldpass99",
        )
        await reset_client.post(
            "/api/auth/forgot-password",
            json={"email": "jane@example.com"},
        )
        await asyncio.sleep(0)
        code = next(
            m[1] for m in fake_sender.reset_messages
            if m[0] == "jane@example.com"
        )

        resp = await reset_client.post(
            "/api/auth/reset-password",
            json={
                "email": "jane@example.com",
                "code": code,
                "new_password": "newpass99",
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    async def test_reset_password_invalid_code_returns_400(
        self,
        reset_client,
        reset_app,
        reset_db: Database,
    ):
        await _make_verified_user(
            reset_db, reset_app.state.auth_service, "kate@example.com",
        )
        await reset_client.post(
            "/api/auth/forgot-password",
            json={"email": "kate@example.com"},
        )
        resp = await reset_client.post(
            "/api/auth/reset-password",
            json={
                "email": "kate@example.com",
                "code": "000000",
                "new_password": "newpass99",
            },
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "reset_code_invalid"

    async def test_reset_password_same_as_old_returns_400(
        self,
        reset_client,
        reset_app,
        reset_db: Database,
        fake_sender: _FakeEmailSender,
    ):
        await _make_verified_user(
            reset_db, reset_app.state.auth_service, "leo@example.com",
            password="oldpass99",
        )
        await reset_client.post(
            "/api/auth/forgot-password",
            json={"email": "leo@example.com"},
        )
        await asyncio.sleep(0)
        code = next(
            m[1] for m in fake_sender.reset_messages
            if m[0] == "leo@example.com"
        )

        resp = await reset_client.post(
            "/api/auth/reset-password",
            json={
                "email": "leo@example.com",
                "code": code,
                "new_password": "oldpass99",
            },
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "new_password_same_as_old"

    async def test_reset_password_attempts_exhausted_returns_429(
        self,
        reset_client,
        reset_app,
        reset_db: Database,
        reset_config: CentralServerConfig,
        fake_sender: _FakeEmailSender,
    ):
        await _make_verified_user(
            reset_db, reset_app.state.auth_service, "mona@example.com",
        )
        await reset_client.post(
            "/api/auth/forgot-password",
            json={"email": "mona@example.com"},
        )
        await asyncio.sleep(0)
        code = next(
            m[1] for m in fake_sender.reset_messages
            if m[0] == "mona@example.com"
        )

        # Burn through max_attempts with wrong codes.
        for _ in range(reset_config.password_reset_max_attempts):
            resp = await reset_client.post(
                "/api/auth/reset-password",
                json={
                    "email": "mona@example.com",
                    "code": "111111",
                    "new_password": "newpass99",
                },
            )
            assert resp.status_code == 400

        # Correct code now locked out with 429.
        resp = await reset_client.post(
            "/api/auth/reset-password",
            json={
                "email": "mona@example.com",
                "code": code,
                "new_password": "newpass99",
            },
        )
        assert resp.status_code == 429
        assert resp.json()["detail"]["error"] == "reset_attempts_exhausted"

    async def test_per_ip_cap_returns_429_with_retry_after(
        self,
        reset_client,
        reset_app,
        reset_config: CentralServerConfig,
    ):
        # Fill the per-IP hourly cap. ASGITransport reports a synthetic
        # request.client.host so all calls share the same IP key.
        for _ in range(reset_config.password_reset_ip_hourly_cap):
            resp = await reset_client.post(
                "/api/auth/forgot-password",
                json={"email": "noise@example.com"},
            )
            assert resp.status_code == 200

        resp = await reset_client.post(
            "/api/auth/forgot-password",
            json={"email": "noise@example.com"},
        )
        assert resp.status_code == 429
        assert resp.headers.get("Retry-After")
        detail = resp.json()["detail"]
        assert detail["error"] == "reset_ip_rate_limited"
        assert detail["retry_after_seconds"] > 0

    async def test_reset_password_short_code_returns_422(
        self,
        reset_client,
    ):
        # Pydantic field-level validation rejects non-6-digit codes upfront.
        resp = await reset_client.post(
            "/api/auth/reset-password",
            json={
                "email": "anyone@example.com",
                "code": "12",
                "new_password": "newpass99",
            },
        )
        assert resp.status_code == 422
