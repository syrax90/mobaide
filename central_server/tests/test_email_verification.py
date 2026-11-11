"""Tests for the email-verification flow (register → email → verify).

Covers unit behaviour with an in-memory fake email sender.
"""
from __future__ import annotations

import asyncio
import re

import pytest
from httpx import ASGITransport, AsyncClient

from central_server.ads.consent import network_fingerprint, sign_attribution
from central_server.agents.service import AgentService
from central_server.app import create_app
from central_server.auth.models import AdAttribution
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

    async def test_concurrent_guesses_cannot_exceed_attempt_cap(
        self,
        verify_service: AuthService,
        verify_db: Database,
        fake_sender: _FakeEmailSender,
        verify_config: CentralServerConfig,
    ):
        await verify_service.register_user("ivan@example.com", "hunter22", None)
        await asyncio.sleep(0)
        _, real_code, _ = fake_sender.messages[-1]
        wrong_codes = [f"{n:06d}" for n in range(30) if f"{n:06d}" != real_code]

        results = await asyncio.gather(
            *(
                verify_service.verify_email("ivan@example.com", code)
                for code in [*wrong_codes, real_code]
            ),
            return_exceptions=True,
        )

        invalid = [
            r for r in results if isinstance(r, InvalidVerificationCodeError)
        ]
        assert len(invalid) == verify_config.email_verification_max_attempts
        assert isinstance(results[-1], VerificationAttemptsExhaustedError)
        user = await verify_db.get_user_by_email("ivan@example.com")
        assert not user["email_verified"]

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


def _attribution(click_id: str, client_ip: str = "127.0.0.1") -> AdAttribution:
    """A banner-signed attribution minted on ``client_ip``'s network."""
    network = network_fingerprint(TEST_JWT_SECRET, client_ip)
    return AdAttribution(
        click_id=click_id,
        click_id_kind="gclid",
        clicked_at=1_756_800_000,
        network=network,
        signature=sign_attribution(
            TEST_JWT_SECRET,
            click_id=click_id,
            click_id_kind="gclid",
            clicked_at=1_756_800_000,
            network=network,
        ),
    )


async def _expire_codes(db: Database, user_id: str) -> None:
    await db.conn.execute(
        "UPDATE email_verification_tokens SET expires_at = ? WHERE user_id = ?",
        ("2000-01-01T00:00:00+00:00", user_id),
    )
    await db.conn.commit()


async def _abandon_signup(
    service: AuthService,
    db: Database,
    limiter: ResendRateLimiter,
    email: str,
    password: str,
) -> str:
    """Register ``email`` and leave it the way a user who never typed the
    code would: code window over, resend cooldown long gone."""
    result = await service.register_user(email, password, None)
    await asyncio.sleep(0)
    await _expire_codes(db, result.user_id)
    await limiter.reset(email)
    return result.user_id


class TestReregisterAbandonedSignup:
    async def test_expired_signup_is_taken_over_with_the_new_password(
        self,
        verify_service: AuthService,
        verify_db: Database,
        limiter: ResendRateLimiter,
        fake_sender: _FakeEmailSender,
    ):
        user_id = await _abandon_signup(
            verify_service, verify_db, limiter, "pat@example.com", "oldpass11"
        )

        result = await verify_service.register_user(
            "pat@example.com", "newpass22", None
        )
        await asyncio.sleep(0)
        assert result.status == "verification_required"
        assert result.access_token is None
        assert result.user_id == user_id
        assert len(fake_sender.messages) == 2

        _, code, _ = fake_sender.messages[-1]
        await verify_service.verify_email("pat@example.com", code)
        login_id, _role, _token, _scheduled = await verify_service.login_user(
            "pat@example.com", "newpass22"
        )
        assert login_id == user_id
        with pytest.raises(ValueError, match="Invalid"):
            await verify_service.login_user("pat@example.com", "oldpass11")

    async def test_live_code_blocks_takeover(
        self,
        verify_service: AuthService,
        limiter: ResendRateLimiter,
        fake_sender: _FakeEmailSender,
    ):
        await verify_service.register_user("quinn@example.com", "oldpass11", None)
        await asyncio.sleep(0)
        await limiter.reset("quinn@example.com")

        with pytest.raises(ValueError, match="already registered"):
            await verify_service.register_user(
                "quinn@example.com", "newpass22", None
            )

        # The owner's code and password are untouched.
        _, code, _ = fake_sender.messages[-1]
        await verify_service.verify_email("quinn@example.com", code)
        await verify_service.login_user("quinn@example.com", "oldpass11")

    async def test_exhausted_attempts_do_not_end_the_code_window(
        self,
        verify_service: AuthService,
        limiter: ResendRateLimiter,
        verify_config: CentralServerConfig,
    ):
        await verify_service.register_user("rita@example.com", "oldpass11", None)
        for _ in range(verify_config.email_verification_max_attempts):
            with pytest.raises(InvalidVerificationCodeError):
                await verify_service.verify_email("rita@example.com", "111111")
        await limiter.reset("rita@example.com")

        with pytest.raises(ValueError, match="already registered"):
            await verify_service.register_user(
                "rita@example.com", "newpass22", None
            )

    async def test_verified_account_is_not_taken_over(
        self,
        verify_service: AuthService,
        verify_db: Database,
        fake_sender: _FakeEmailSender,
    ):
        result = await verify_service.register_user(
            "sam@example.com", "oldpass11", None
        )
        await asyncio.sleep(0)
        _, code, _ = fake_sender.messages[-1]
        await verify_service.verify_email("sam@example.com", code)
        await _expire_codes(verify_db, result.user_id)

        with pytest.raises(ValueError, match="already registered"):
            await verify_service.register_user(
                "sam@example.com", "newpass22", None
            )
        await verify_service.login_user("sam@example.com", "oldpass11")

    @pytest.mark.parametrize("provider_column", ["google_sub", "apple_sub"])
    async def test_provider_linked_unverified_account_is_not_taken_over(
        self,
        verify_service: AuthService,
        verify_db: Database,
        fake_sender: _FakeEmailSender,
        provider_column: str,
    ):
        await verify_db.create_user(
            email="tess@example.com",
            password_hash="h",
            email_verified=False,
            **{provider_column: "provider-sub-1"},
        )

        with pytest.raises(ValueError, match="already registered"):
            await verify_service.register_user(
                "tess@example.com", "newpass22", None
            )
        await asyncio.sleep(0)
        assert fake_sender.messages == []
        user = await verify_db.get_user_by_email("tess@example.com")
        assert user["password_hash"] == "h"

    async def test_rate_limited_takeover_keeps_the_old_password(
        self,
        verify_service: AuthService,
        verify_db: Database,
        fake_sender: _FakeEmailSender,
    ):
        # Register already took the cooldown slot; only the code expires.
        result = await verify_service.register_user(
            "uma@example.com", "oldpass11", None
        )
        await asyncio.sleep(0)
        await _expire_codes(verify_db, result.user_id)

        with pytest.raises(ResendRateLimitedError) as exc:
            await verify_service.register_user(
                "uma@example.com", "newpass22", None
            )
        assert exc.value.cooldown_seconds_remaining > 0
        await asyncio.sleep(0)
        assert len(fake_sender.messages) == 1
        # The old password still matches (gets as far as the verify gate).
        with pytest.raises(VerificationRequiredError):
            await verify_service.login_user("uma@example.com", "oldpass11")


    async def test_takeover_without_attribution_clears_the_first_registrants(
        self,
        verify_service: AuthService,
        verify_db: Database,
        limiter: ResendRateLimiter,
    ):
        first = await verify_service.register_user(
            "vera@example.com",
            "oldpass11",
            None,
            ad_attribution=_attribution("Cj0K_squatter"),
            client_ip="127.0.0.1",
        )
        await asyncio.sleep(0)
        row = await verify_db.get_user_by_id(first.user_id)
        assert (row["ad_click_id"], row["ads_consent"]) == ("Cj0K_squatter", 1)
        await _expire_codes(verify_db, first.user_id)
        await limiter.reset("vera@example.com")

        await verify_service.register_user("vera@example.com", "newpass22", None)

        row = await verify_db.get_user_by_id(first.user_id)
        assert (
            row["ad_click_id"],
            row["ad_click_kind"],
            row["ad_click_at"],
            row["ads_consent"],
            row["ad_attribution_sig"],
        ) == (None, None, None, 0, None)


    async def test_signup_verified_mid_takeover_is_not_overwritten(
        self,
        verify_service: AuthService,
        verify_db: Database,
        limiter: ResendRateLimiter,
        monkeypatch,
    ):
        await _abandon_signup(
            verify_service, verify_db, limiter, "xavi@example.com", "oldpass11"
        )
        real_check = verify_db.has_live_verification_token

        async def verified_meanwhile(user_id: str) -> bool:
            # The owner finishes verification between the takeover's
            # eligibility check and its write.
            await verify_db.mark_user_verified(user_id)
            return await real_check(user_id)

        monkeypatch.setattr(
            verify_db, "has_live_verification_token", verified_meanwhile
        )

        with pytest.raises(ValueError, match="already registered"):
            await verify_service.register_user(
                "xavi@example.com", "newpass22", None
            )
        await verify_service.login_user("xavi@example.com", "oldpass11")

    async def test_takeover_during_a_verify_in_progress_is_refused(
        self,
        verify_service: AuthService,
        verify_db: Database,
        limiter: ResendRateLimiter,
        fake_sender: _FakeEmailSender,
        monkeypatch,
    ):
        await verify_service.register_user("yara@example.com", "ownerpass1", None)
        await asyncio.sleep(0)
        _, code, _ = fake_sender.messages[-1]
        real_mark = verify_db.mark_user_verified

        async def takeover_between_consume_and_mark(user_id, password_hash=None):
            await limiter.reset("yara@example.com")
            with pytest.raises(ValueError, match="already registered"):
                await verify_service.register_user(
                    "yara@example.com", "squatpass2", None
                )
            await real_mark(user_id, password_hash)

        monkeypatch.setattr(
            verify_db, "mark_user_verified", takeover_between_consume_and_mark
        )

        # No typed password: the stored hash must still be the owner's.
        await verify_service.verify_email("yara@example.com", code)

        await verify_service.login_user("yara@example.com", "ownerpass1")


class TestVerifyWithTypedPassword:
    async def test_owner_password_replaces_a_takeovers_on_verify(
        self,
        verify_service: AuthService,
        verify_db: Database,
        limiter: ResendRateLimiter,
        fake_sender: _FakeEmailSender,
    ):
        user_id = await _abandon_signup(
            verify_service, verify_db, limiter, "olga@example.com", "ownerpass1"
        )
        await verify_service.register_user("olga@example.com", "squatpass2", None)
        await asyncio.sleep(0)
        _, code, _ = fake_sender.messages[-1]

        verified_id, _role, token, _scheduled = await verify_service.verify_email(
            "olga@example.com", code, "ownerpass1"
        )

        assert verified_id == user_id
        assert token
        await verify_service.login_user("olga@example.com", "ownerpass1")
        with pytest.raises(ValueError, match="Invalid"):
            await verify_service.login_user("olga@example.com", "squatpass2")


    async def test_wrong_code_does_not_change_the_password(
        self,
        verify_service: AuthService,
        verify_db: Database,
        fake_sender: _FakeEmailSender,
    ):
        await verify_service.register_user("ruth@example.com", "ownerpass1", None)
        await asyncio.sleep(0)
        before = (await verify_db.get_user_by_email("ruth@example.com"))[
            "password_hash"
        ]
        _, code, _ = fake_sender.messages[-1]
        wrong = "000000" if code != "000000" else "111111"

        with pytest.raises(InvalidVerificationCodeError):
            await verify_service.verify_email("ruth@example.com", wrong, "squatpass2")

        after = await verify_db.get_user_by_email("ruth@example.com")
        assert after["password_hash"] == before
        assert not after["email_verified"]

    async def test_typed_password_is_installed_on_a_row_without_one(
        self,
        verify_service: AuthService,
        verify_db: Database,
        fake_sender: _FakeEmailSender,
    ):
        await verify_db.create_user(email="sid@example.com", email_verified=False)
        await verify_service.resend_verification("sid@example.com", None)
        await asyncio.sleep(0)
        _, code, _ = fake_sender.messages[-1]

        await verify_service.verify_email("sid@example.com", code, "ownerpass1")

        await verify_service.login_user("sid@example.com", "ownerpass1")

    async def test_typed_password_wins_over_a_hash_written_after_the_read(
        self,
        verify_service: AuthService,
        verify_db: Database,
        fake_sender: _FakeEmailSender,
        monkeypatch,
    ):
        await verify_service.register_user("tina@example.com", "ownerpass1", None)
        await asyncio.sleep(0)
        _, code, _ = fake_sender.messages[-1]
        real_consume = verify_db.consume_verification_token

        async def foreign_hash_after_consume(user_id, code_hash, max_attempts):
            outcome = await real_consume(user_id, code_hash, max_attempts)
            await verify_db.update_user_password(user_id, "foreign-hash")
            return outcome

        monkeypatch.setattr(
            verify_db, "consume_verification_token", foreign_hash_after_consume
        )

        await verify_service.verify_email("tina@example.com", code, "ownerpass1")

        user = await verify_db.get_user_by_email("tina@example.com")
        assert user["password_hash"] != "foreign-hash"
        await verify_service.login_user("tina@example.com", "ownerpass1")

    @pytest.mark.parametrize("provider_column", ["google_sub", "apple_sub"])
    async def test_provider_linked_row_keeps_its_password_on_verify(
        self,
        verify_service: AuthService,
        verify_db: Database,
        fake_sender: _FakeEmailSender,
        provider_column: str,
    ):
        await verify_db.create_user(
            email="ursa@example.com",
            password_hash="h",
            email_verified=False,
            **{provider_column: "provider-sub-1"},
        )
        await verify_service.resend_verification("ursa@example.com", None)
        await asyncio.sleep(0)
        _, code, _ = fake_sender.messages[-1]

        await verify_service.verify_email("ursa@example.com", code, "typedpass1")

        user = await verify_db.get_user_by_email("ursa@example.com")
        assert (user["password_hash"], user["email_verified"]) == ("h", 1)

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


    async def test_verify_endpoint_installs_the_typed_password(
        self, verify_client, fake_sender: _FakeEmailSender
    ):
        await verify_client.post(
            "/api/auth/register",
            json={"email": "q@example.com", "password": "firstpass1"},
        )
        await asyncio.sleep(0)
        code = next(m[1] for m in fake_sender.messages if m[0] == "q@example.com")

        resp = await verify_client.post(
            "/api/auth/verify-email",
            json={"email": "q@example.com", "code": code, "password": "typedpass2"},
        )
        assert resp.status_code == 200

        typed = await verify_client.post(
            "/api/auth/login",
            json={"email": "q@example.com", "password": "typedpass2"},
        )
        assert typed.status_code == 200
        replaced = await verify_client.post(
            "/api/auth/login",
            json={"email": "q@example.com", "password": "firstpass1"},
        )
        assert replaced.status_code == 401

    @pytest.mark.parametrize(
        "email",
        [
            "r1@example.com, r2@example.com",
            "Alice <r1@example.com>",
            "r1@example.com\r\nBcc: r2@example.com",
        ],
    )
    async def test_register_refuses_more_than_one_mailbox(
        self,
        verify_client,
        verify_db: Database,
        fake_sender: _FakeEmailSender,
        email: str,
    ):
        resp = await verify_client.post(
            "/api/auth/register",
            json={"email": email, "password": "hunter22"},
        )
        assert resp.status_code == 422
        assert "Enter a single valid email address" in resp.text

        await asyncio.sleep(0)
        assert fake_sender.messages == []
        assert await verify_db.get_user_by_email(email) is None
        assert await verify_db.get_user_by_email("r1@example.com") is None

    async def test_ip_cap_is_shared_by_register_and_resend(
        self,
        verify_app,
        verify_client,
        verify_db: Database,
        fake_sender: _FakeEmailSender,
    ):
        # ASGITransport reports one synthetic client host, so every call
        # below lands in the same per-IP bucket.
        verify_app.state.email_verification_ip_limiter = ResendRateLimiter(
            cooldown_seconds=0, hourly_cap=2,
        )
        first = await verify_client.post(
            "/api/auth/register",
            json={"email": "s1@example.com", "password": "hunter22"},
        )
        assert first.status_code == 200
        resend = await verify_client.post(
            "/api/auth/resend-verification",
            json={"email": "nobody@example.com"},
        )
        assert resend.status_code == 200

        blocked = await verify_client.post(
            "/api/auth/register",
            json={"email": "s2@example.com", "password": "hunter22"},
        )
        assert blocked.status_code == 429
        detail = blocked.json()["detail"]
        assert detail["error"] == "resend_rate_limited"
        assert detail["cooldown_seconds_remaining"] > 0
        assert blocked.headers.get("Retry-After") == str(
            detail["cooldown_seconds_remaining"]
        )
        await asyncio.sleep(0)
        assert await verify_db.get_user_by_email("s2@example.com") is None
        assert [m[0] for m in fake_sender.messages] == ["s1@example.com"]

        blocked_resend = await verify_client.post(
            "/api/auth/resend-verification",
            json={"email": "s1@example.com"},
        )
        assert blocked_resend.status_code == 429
        assert blocked_resend.json()["detail"]["error"] == "resend_rate_limited"
