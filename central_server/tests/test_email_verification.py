"""Tests for the email-verification flow (register → email → verify).

Covers unit behaviour with an in-memory fake email sender, plus an optional
Mailpit REST integration test when ``MAILPIT_REST_URL`` is set. The Mailpit
integration is skipped by default so these tests remain hermetic.
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from central_server.agents.service import AgentService
from central_server.app import create_app
from central_server.auth.rate_limit import ResendRateLimiter
from central_server.auth.service import (
    AuthService,
    ExpiredVerificationCodeError,
    InvalidVerificationCodeError,
    ResendRateLimitedError,
    VerificationAttemptsExhaustedError,
    VerificationRequiredError,
)
from central_server.config import CentralServerConfig
from central_server.db.database import Database
from central_server.pairing.service import PairingService
from central_server.relay.routing import RoutingTable
from tests.conftest import TEST_JWT_SECRET


class _FakeEmailSender:
    """Collects emails instead of sending them."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str, int]] = []

    async def send_verification_email(
        self, to: str, code: str, ttl_minutes: int
    ) -> None:
        self.messages.append((to, code, ttl_minutes))


@pytest.fixture
def verify_config(tmp_path) -> CentralServerConfig:
    return CentralServerConfig(
        jwt_secret_key=TEST_JWT_SECRET,
        db_path=str(tmp_path / "verify_test.db"),
        log_level="DEBUG",
        log_format="console",
        email_verification_required=True,
        email_verification_ttl_minutes=15,
        email_verification_max_attempts=5,
        email_verification_resend_cooldown_seconds=60,
        email_verification_resend_hourly_cap=3,
    )


@pytest.fixture
async def verify_db(verify_config) -> Database:
    db = Database(verify_config.db_path)
    await db.connect()
    yield db
    await db.close()


@pytest.fixture
def fake_sender() -> _FakeEmailSender:
    return _FakeEmailSender()


@pytest.fixture
def limiter(verify_config) -> ResendRateLimiter:
    return ResendRateLimiter(
        cooldown_seconds=verify_config.email_verification_resend_cooldown_seconds,
        hourly_cap=verify_config.email_verification_resend_hourly_cap,
    )


@pytest.fixture
def verify_service(
    verify_config, verify_db, fake_sender, limiter
) -> AuthService:
    return AuthService(
        verify_config,
        verify_db,
        email_sender=fake_sender,  # type: ignore[arg-type]
        resend_limiter=limiter,
    )


# -- Unit tests (no real SMTP) -------------------------------------------------


class TestRegisterAndVerify:
    async def test_register_requires_verification(
        self, verify_service: AuthService, fake_sender: _FakeEmailSender
    ):
        result = await verify_service.register_user(
            "alice@example.com", "hunter22", None
        )
        await asyncio.sleep(0)  # let the asyncio.create_task fallback run
        assert result.status == "verification_required"
        assert result.access_token is None
        assert result.email == "alice@example.com"
        # BackgroundTasks doesn't run synchronously, so drive the send directly
        # for the assertion (this mirrors the test's intent; the live server
        # lets FastAPI run the task after returning the response).
        assert len(fake_sender.messages) == 1  # issued via asyncio fallback or direct
        sent_to, code, ttl = fake_sender.messages[0]
        assert sent_to == "alice@example.com"
        assert re.fullmatch(r"\d{6}", code)
        assert ttl == 15

    async def test_verify_with_correct_code_returns_jwt(
        self, verify_service: AuthService, fake_sender: _FakeEmailSender
    ):
        await verify_service.register_user(
            "bob@example.com", "hunter22", None
        )
        await asyncio.sleep(0)
        _, code, _ = fake_sender.messages[-1]

        user_id, role, token, _scheduled = await verify_service.verify_email(
            "bob@example.com", code
        )
        assert role == "viewer"
        assert len(token) > 20
        assert len(user_id) == 12

    async def test_login_before_verify_raises(
        self, verify_service: AuthService
    ):
        await verify_service.register_user(
            "carol@example.com", "hunter22", None
        )
        with pytest.raises(VerificationRequiredError) as exc:
            await verify_service.login_user("carol@example.com", "hunter22")
        assert exc.value.email == "carol@example.com"

    async def test_verify_for_already_verified_user_with_wrong_code_rejects(
        self, verify_service: AuthService, fake_sender: _FakeEmailSender
    ):
        """An already-verified user must not be able to get a JWT from
        /verify-email without proving knowledge of a fresh code. Handing
        one out would be an authentication bypass."""
        await verify_service.register_user(
            "kate@example.com", "hunter22", None
        )
        await asyncio.sleep(0)
        _, code, _ = fake_sender.messages[-1]
        await verify_service.verify_email("kate@example.com", code)  # legit verify

        # Now the user is verified. A second verify call with ANY code
        # must be rejected because the token is consumed.
        with pytest.raises(InvalidVerificationCodeError):
            await verify_service.verify_email("kate@example.com", "000000")
        with pytest.raises(InvalidVerificationCodeError):
            await verify_service.verify_email("kate@example.com", code)

    async def test_login_after_verify_succeeds(
        self, verify_service: AuthService, fake_sender: _FakeEmailSender
    ):
        await verify_service.register_user(
            "dave@example.com", "hunter22", None
        )
        await asyncio.sleep(0)
        _, code, _ = fake_sender.messages[-1]
        await verify_service.verify_email("dave@example.com", code)

        user_id, role, token, _scheduled = await verify_service.login_user(
            "dave@example.com", "hunter22"
        )
        assert role == "viewer"
        assert len(token) > 20
        assert len(user_id) == 12


class TestCodeFailureModes:
    async def test_wrong_code_is_invalid(
        self, verify_service: AuthService, fake_sender: _FakeEmailSender
    ):
        await verify_service.register_user(
            "eve@example.com", "hunter22", None
        )
        with pytest.raises(InvalidVerificationCodeError):
            await verify_service.verify_email("eve@example.com", "000000")

    async def test_exhausting_attempts_invalidates_token(
        self,
        verify_service: AuthService,
        fake_sender: _FakeEmailSender,
        verify_config: CentralServerConfig,
    ):
        await verify_service.register_user(
            "frank@example.com", "hunter22", None
        )
        await asyncio.sleep(0)
        for _ in range(verify_config.email_verification_max_attempts):
            with pytest.raises(InvalidVerificationCodeError):
                await verify_service.verify_email("frank@example.com", "111111")
        # Further attempts — including the correct code — are locked out.
        _, real_code, _ = fake_sender.messages[-1]
        with pytest.raises(VerificationAttemptsExhaustedError):
            await verify_service.verify_email("frank@example.com", real_code)

    async def test_expired_token_is_rejected(
        self,
        verify_config: CentralServerConfig,
        tmp_path,
        fake_sender: _FakeEmailSender,
        limiter: ResendRateLimiter,
    ):
        # Build a custom service with a 0-minute TTL so the token expires
        # immediately.
        fast_config = CentralServerConfig(
            jwt_secret_key=TEST_JWT_SECRET,
            db_path=str(tmp_path / "expire_test.db"),
            log_level="DEBUG",
            log_format="console",
            email_verification_required=True,
            email_verification_ttl_minutes=0,
        )
        db = Database(fast_config.db_path)
        await db.connect()
        try:
            svc = AuthService(
                fast_config,
                db,
                email_sender=fake_sender,  # type: ignore[arg-type]
                resend_limiter=limiter,
            )
            await svc.register_user("grace@example.com", "hunter22", None)
            await asyncio.sleep(0)
            _, code, _ = fake_sender.messages[-1]
            with pytest.raises(ExpiredVerificationCodeError):
                await svc.verify_email("grace@example.com", code)
        finally:
            await db.close()


class TestResendRateLimit:
    async def test_cooldown_enforced(
        self,
        verify_service: AuthService,
        fake_sender: _FakeEmailSender,
    ):
        await verify_service.register_user(
            "henry@example.com", "hunter22", None
        )
        # The register path already took the first rate-limit slot, so
        # an immediate resend is blocked by the cooldown.
        with pytest.raises(ResendRateLimitedError) as exc:
            await verify_service.resend_verification(
                "henry@example.com", None
            )
        assert exc.value.cooldown_seconds_remaining > 0

    async def test_resend_after_verify_is_noop(
        self,
        verify_service: AuthService,
        fake_sender: _FakeEmailSender,
    ):
        await verify_service.register_user(
            "ivan@example.com", "hunter22", None
        )
        await asyncio.sleep(0)
        _, code, _ = fake_sender.messages[-1]
        await verify_service.verify_email("ivan@example.com", code)

        # After verify, rate-limit is reset and resend is silently a no-op.
        before = len(fake_sender.messages)
        await verify_service.resend_verification(
            "ivan@example.com", None
        )
        # No new message — the user is already verified.
        assert len(fake_sender.messages) == before

    async def test_resend_for_unknown_email_silent(
        self,
        verify_service: AuthService,
        fake_sender: _FakeEmailSender,
    ):
        before = len(fake_sender.messages)
        # Must not raise, must not reveal whether the email exists.
        await verify_service.resend_verification(
            "nobody@example.com", None
        )
        assert len(fake_sender.messages) == before


class TestResendBypassesCooldownAfterVerify:
    async def test_hourly_cap_enforced(
        self,
        verify_service: AuthService,
        fake_sender: _FakeEmailSender,
        verify_config: CentralServerConfig,
    ):
        # Use a custom limiter with a near-zero cooldown so we can burn
        # through the hourly cap in a single test.
        limiter = ResendRateLimiter(
            cooldown_seconds=0,
            hourly_cap=verify_config.email_verification_resend_hourly_cap,
        )
        # Rebuild the service with the aggressive limiter.
        svc = AuthService(
            verify_config,
            verify_service._db,  # noqa: SLF001
            email_sender=fake_sender,  # type: ignore[arg-type]
            resend_limiter=limiter,
        )
        await svc.register_user("jude@example.com", "hunter22", None)
        # register already consumed 1 slot. Fill the remaining (hourly_cap - 1).
        for _ in range(verify_config.email_verification_resend_hourly_cap - 1):
            await svc.resend_verification(
                "jude@example.com", None
            )
        # Next resend must hit the cap.
        with pytest.raises(ResendRateLimitedError):
            await svc.resend_verification(
                "jude@example.com", None
            )


# -- Router integration -------------------------------------------------------


@pytest.fixture
async def verify_app(verify_config, verify_db, fake_sender, limiter):
    app = create_app(config=verify_config)
    app.state.db = verify_db
    app.state.routing = RoutingTable()
    app.state.email_sender = fake_sender
    app.state.resend_limiter = limiter
    app.state.auth_service = AuthService(
        verify_config,
        verify_db,
        email_sender=fake_sender,  # type: ignore[arg-type]
        resend_limiter=limiter,
    )
    app.state.pairing_service = PairingService(verify_db)
    app.state.agent_service = AgentService(verify_db, app.state.routing)
    yield app


@pytest.fixture
async def verify_client(verify_app):
    transport = ASGITransport(app=verify_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestRouterIntegration:
    async def test_register_returns_verification_required(
        self, verify_client, fake_sender: _FakeEmailSender
    ):
        resp = await verify_client.post(
            "/api/auth/register",
            json={"email": "k@example.com", "password": "hunter22"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "verification_required"
        assert body["access_token"] is None
        assert body["email"] == "k@example.com"

        # BackgroundTasks have run by this point in the ASGI transport.
        await asyncio.sleep(0)  # yield so any scheduled task can complete
        assert any(m[0] == "k@example.com" for m in fake_sender.messages)

    async def test_verify_endpoint(
        self, verify_client, fake_sender: _FakeEmailSender
    ):
        await verify_client.post(
            "/api/auth/register",
            json={"email": "l@example.com", "password": "hunter22"},
        )
        await asyncio.sleep(0)
        code = next(m[1] for m in fake_sender.messages if m[0] == "l@example.com")

        resp = await verify_client.post(
            "/api/auth/verify-email",
            json={"email": "l@example.com", "code": code},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"]
        assert body["role"] == "viewer"

    async def test_login_unverified_returns_403(self, verify_client):
        await verify_client.post(
            "/api/auth/register",
            json={"email": "m@example.com", "password": "hunter22"},
        )
        resp = await verify_client.post(
            "/api/auth/login",
            json={"email": "m@example.com", "password": "hunter22"},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["error"] == "verification_required"
        assert detail["email"] == "m@example.com"

    async def test_verify_invalid_code_returns_400(self, verify_client):
        await verify_client.post(
            "/api/auth/register",
            json={"email": "n@example.com", "password": "hunter22"},
        )
        resp = await verify_client.post(
            "/api/auth/verify-email",
            json={"email": "n@example.com", "code": "000000"},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "verification_code_invalid"

    async def test_resend_cooldown_returns_429(
        self, verify_client, fake_sender: _FakeEmailSender
    ):
        await verify_client.post(
            "/api/auth/register",
            json={"email": "o@example.com", "password": "hunter22"},
        )
        resp = await verify_client.post(
            "/api/auth/resend-verification",
            json={"email": "o@example.com"},
        )
        assert resp.status_code == 429
        assert resp.headers.get("Retry-After")
        body = resp.json()
        assert body["detail"]["error"] == "resend_rate_limited"
        assert body["detail"]["cooldown_seconds_remaining"] > 0


# -- Mailpit integration (opt-in) ---------------------------------------------

MAILPIT_URL = os.getenv("MAILPIT_REST_URL")


@pytest.mark.skipif(
    not MAILPIT_URL, reason="MAILPIT_REST_URL not set — skipping Mailpit test"
)
class TestMailpitIntegration:
    """End-to-end test against a live Mailpit instance.

    Set ``MAILPIT_REST_URL=http://localhost:8025`` and ensure both Mailpit
    and the central server are running (``docker compose --profile dev up``).
    """

    async def _latest_message(self, client: httpx.AsyncClient) -> dict[str, Any]:
        resp = await client.get(f"{MAILPIT_URL}/api/v1/messages?limit=1")
        resp.raise_for_status()
        messages = resp.json().get("messages", [])
        assert messages, "no messages in Mailpit"
        msg_id = messages[0]["ID"]
        detail = await client.get(f"{MAILPIT_URL}/api/v1/message/{msg_id}")
        detail.raise_for_status()
        return detail.json()

    async def test_full_flow(self, verify_client):
        email = "mailpit-integration@example.com"
        await verify_client.post(
            "/api/auth/register",
            json={"email": email, "password": "hunter22"},
        )
        async with httpx.AsyncClient() as http:
            detail = await self._latest_message(http)
        body = detail.get("Text") or detail.get("HTML") or ""
        match = re.search(r"\b(\d{6})\b", body)
        assert match, "no 6-digit code found in Mailpit message"
        code = match.group(1)

        resp = await verify_client.post(
            "/api/auth/verify-email",
            json={"email": email, "code": code},
        )
        assert resp.status_code == 200
        assert resp.json()["access_token"]
