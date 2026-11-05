"""Tests for the password sign-in throttle.

Covers the per-email and per-IP failure buckets, the account-deletion re-auth
that shares the email bucket, and the 429 mapping on the login routes. The
limiter's clock is pinned so every ``retry_after_seconds`` is exact.
"""
from __future__ import annotations

import asyncio
import types
from typing import Any

import pytest
from starlette.testclient import TestClient

from central_server.app import create_app
from central_server.auth.models import DeleteAccountRequest, UserTokenPayload
from central_server.auth.rate_limit import ResendRateLimiter
from central_server.auth.service import (
    AccountDeletionAuthError,
    AuthService,
    LoginRateLimitedError,
    retry_after_phrase,
)
from central_server.config import CentralServerConfig
from central_server.db.database import Database
from tests.conftest import TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD

EMAIL_CAP = 3
IP_CAP = 5
WINDOW = 900


class _FakeClock:
    """Monotonic stand-in we can tick forward deterministically."""

    def __init__(self, start: float = 10_000.0) -> None:
        self.now = start

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def __call__(self) -> float:
        return self.now


class _FakeEmailSender:
    """Keeps reset codes so a test can finish the reset flow."""

    def __init__(self) -> None:
        self.reset_codes: list[str] = []

    async def send_verification_email(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def send_password_reset_email(
        self, to: str, code: str, ttl_minutes: int, client_ip: str | None = None
    ) -> None:
        self.reset_codes.append(code)

    async def send_password_changed_notification(
        self, *args: Any, **kwargs: Any
    ) -> None:
        pass


@pytest.fixture
def clock(monkeypatch) -> _FakeClock:
    fake = _FakeClock()
    # Swap only the limiter module's ``time`` so the event loop keeps its
    # real clock.
    monkeypatch.setattr(
        "central_server.auth.rate_limit.time",
        types.SimpleNamespace(monotonic=fake),
    )
    return fake


@pytest.fixture
def email_limiter() -> ResendRateLimiter:
    return ResendRateLimiter(
        cooldown_seconds=0, hourly_cap=EMAIL_CAP, window_seconds=WINDOW
    )


@pytest.fixture
def ip_limiter() -> ResendRateLimiter:
    return ResendRateLimiter(
        cooldown_seconds=0, hourly_cap=IP_CAP, window_seconds=WINDOW
    )


@pytest.fixture
def fake_sender() -> _FakeEmailSender:
    return _FakeEmailSender()


@pytest.fixture
def throttled_service(
    config, db, clock, email_limiter, ip_limiter, fake_sender
) -> AuthService:
    return AuthService(
        config,
        db,
        email_sender=fake_sender,  # type: ignore[arg-type]
        login_email_limiter=email_limiter,
        login_ip_limiter=ip_limiter,
    )


async def _make_user(
    db: Database, svc: AuthService, email: str, password: str = "correct-horse"
) -> dict:
    pw_hash = await svc._hash_password(password)  # noqa: SLF001 — fixture helper
    user_id = await db.create_user(
        email=email, password_hash=pw_hash, role="viewer", email_verified=True
    )
    user = await db.get_user_by_id(user_id)
    assert user is not None
    return user


async def _fail_login(
    svc: AuthService, email: str, times: int, client_ip: str | None = "10.0.0.1"
) -> None:
    for _ in range(times):
        with pytest.raises(ValueError):
            await svc.login_user(email, "wrong-password", client_ip=client_ip)


class TestRetryAfterPhrase:
    @pytest.mark.parametrize(
        ("seconds", "phrase"),
        [
            (0, "1 minute"),
            (1, "1 minute"),
            (60, "1 minute"),
            (61, "2 minutes"),
            (900, "15 minutes"),
        ],
    )
    def test_rounds_up_to_whole_minutes(self, seconds: int, phrase: str):
        assert retry_after_phrase(seconds) == phrase


class TestEmailBucket:
    async def test_blocks_after_cap_wrong_passwords(
        self, throttled_service: AuthService, db: Database
    ):
        await _make_user(db, throttled_service, "a@example.com")
        await _fail_login(throttled_service, "a@example.com", EMAIL_CAP)

        with pytest.raises(LoginRateLimitedError) as exc:
            await throttled_service.login_user(
                "a@example.com", "wrong-password", client_ip="10.0.0.2"
            )
        assert exc.value.retry_after_seconds == WINDOW

    async def test_correct_password_is_refused_while_blocked(
        self, throttled_service: AuthService, db: Database
    ):
        await _make_user(db, throttled_service, "a@example.com")
        await _fail_login(throttled_service, "a@example.com", EMAIL_CAP)

        # A blocked caller must not learn whether a guess was right.
        with pytest.raises(LoginRateLimitedError):
            await throttled_service.login_user(
                "a@example.com", "correct-horse", client_ip="10.0.0.2"
            )

    async def test_correct_passwords_do_not_use_up_the_allowance(
        self, throttled_service: AuthService, db: Database
    ):
        await _make_user(db, throttled_service, "a@example.com")
        for _ in range(EMAIL_CAP + 2):
            await throttled_service.login_user(
                "a@example.com", "correct-horse", client_ip="10.0.0.1"
            )
        await _fail_login(throttled_service, "a@example.com", EMAIL_CAP)

    async def test_success_does_not_wipe_earlier_failures(
        self, throttled_service: AuthService, db: Database
    ):
        await _make_user(db, throttled_service, "a@example.com")
        await _fail_login(throttled_service, "a@example.com", EMAIL_CAP - 1)
        await throttled_service.login_user(
            "a@example.com", "correct-horse", client_ip="10.0.0.1"
        )
        await _fail_login(throttled_service, "a@example.com", 1)

        with pytest.raises(LoginRateLimitedError):
            await throttled_service.login_user(
                "a@example.com", "correct-horse", client_ip="10.0.0.1"
            )

    async def test_unknown_email_counts_as_a_failure(
        self, throttled_service: AuthService
    ):
        await _fail_login(throttled_service, "ghost@example.com", EMAIL_CAP)
        with pytest.raises(LoginRateLimitedError):
            await throttled_service.login_user(
                "ghost@example.com", "anything", client_ip="10.0.0.9"
            )

    async def test_email_case_shares_one_bucket(
        self, throttled_service: AuthService, db: Database
    ):
        await _make_user(db, throttled_service, "a@example.com")
        await _fail_login(throttled_service, "A@Example.com", EMAIL_CAP - 1)
        await _fail_login(throttled_service, "a@example.com", 1)

        with pytest.raises(LoginRateLimitedError):
            await throttled_service.login_user(
                "a@example.com", "correct-horse", client_ip="10.0.0.2"
            )

    async def test_window_elapsing_lets_the_owner_back_in(
        self, throttled_service: AuthService, db: Database, clock: _FakeClock
    ):
        user = await _make_user(db, throttled_service, "a@example.com")
        await _fail_login(throttled_service, "a@example.com", EMAIL_CAP)

        # The limiter keeps a timestamp that sits exactly on the window edge.
        clock.advance(WINDOW + 1)
        user_id, _role, _token, _sched = await throttled_service.login_user(
            "a@example.com", "correct-horse", client_ip="10.0.0.1"
        )
        assert user_id == user["id"]

    async def test_verify_error_does_not_charge_the_bucket(
        self, throttled_service: AuthService, db: Database, monkeypatch
    ):
        await _make_user(db, throttled_service, "a@example.com")

        real_verify = throttled_service._verify_password  # noqa: SLF001
        broken = True

        async def _flaky(password_hash: str, password: str) -> bool:
            if broken:
                raise RuntimeError("argon2 pool down")
            return await real_verify(password_hash, password)

        monkeypatch.setattr(throttled_service, "_verify_password", _flaky)
        for _ in range(EMAIL_CAP + 1):
            with pytest.raises(RuntimeError):
                await throttled_service.login_user(
                    "a@example.com", "correct-horse", client_ip="10.0.0.1"
                )
        broken = False

        await _fail_login(throttled_service, "a@example.com", EMAIL_CAP)


class TestIpBucket:
    async def test_failures_across_emails_block_the_ip(
        self, throttled_service: AuthService, db: Database
    ):
        await _make_user(db, throttled_service, "victim@example.com")
        for i in range(IP_CAP):
            await _fail_login(throttled_service, f"spray{i}@example.com", 1)

        with pytest.raises(LoginRateLimitedError) as exc:
            await throttled_service.login_user(
                "victim@example.com", "correct-horse", client_ip="10.0.0.1"
            )
        assert exc.value.retry_after_seconds == WINDOW

    async def test_other_ip_is_not_blocked(
        self, throttled_service: AuthService, db: Database
    ):
        user = await _make_user(db, throttled_service, "victim@example.com")
        for i in range(IP_CAP):
            await _fail_login(throttled_service, f"spray{i}@example.com", 1)

        user_id, *_ = await throttled_service.login_user(
            "victim@example.com", "correct-horse", client_ip="10.0.0.2"
        )
        assert user_id == user["id"]

    async def test_ip_block_does_not_charge_the_email_bucket(
        self, throttled_service: AuthService, db: Database
    ):
        await _make_user(db, throttled_service, "victim@example.com")
        for i in range(IP_CAP):
            await _fail_login(throttled_service, f"spray{i}@example.com", 1)
        for _ in range(EMAIL_CAP + 2):
            with pytest.raises(LoginRateLimitedError):
                await throttled_service.login_user(
                    "victim@example.com", "wrong-password", client_ip="10.0.0.1"
                )

        # The refused attempts never reached a verify, so the victim still
        # has the full allowance from a clean address.
        await _fail_login(
            throttled_service, "victim@example.com", EMAIL_CAP, client_ip="10.0.0.2"
        )

    async def test_missing_client_ip_skips_the_ip_bucket(
        self, throttled_service: AuthService
    ):
        for i in range(IP_CAP + 2):
            await _fail_login(
                throttled_service, f"spray{i}@example.com", 1, client_ip=None
            )


class TestUnthrottledService:
    async def test_service_without_limiters_never_blocks(
        self, config, db: Database
    ):
        svc = AuthService(config, db)
        await _make_user(db, svc, "a@example.com")
        await _fail_login(svc, "a@example.com", 20)
        user_id, *_ = await svc.login_user("a@example.com", "correct-horse")
        assert user_id


class TestPasswordResetClearsLoginBucket:
    async def test_successful_reset_lifts_the_lockout(
        self,
        throttled_service: AuthService,
        db: Database,
        fake_sender: _FakeEmailSender,
    ):
        user = await _make_user(db, throttled_service, "a@example.com")
        await _fail_login(throttled_service, "a@example.com", EMAIL_CAP)

        await throttled_service.request_password_reset("a@example.com")
        await asyncio.sleep(0)
        assert len(fake_sender.reset_codes) == 1
        await throttled_service.reset_password(
            "a@example.com", fake_sender.reset_codes[0], "brand-new-pass"
        )

        user_id, *_ = await throttled_service.login_user(
            "a@example.com", "brand-new-pass", client_ip="10.0.0.2"
        )
        assert user_id == user["id"]


def _payload(user: dict) -> UserTokenPayload:
    return UserTokenPayload(
        sub=user["id"],
        email=user["email"],
        role=user["role"],
        token_type="dashboard",
        exp=None,
        iat=0,
    )


class TestAccountDeletionReauth:
    async def _delete(self, svc: AuthService, user: dict, password: str) -> None:
        await svc.request_account_deletion(
            _payload(user),
            source="android",
            proof=DeleteAccountRequest(method="password", password=password),
        )

    async def test_wrong_passwords_lock_out_deletion_reauth(
        self, throttled_service: AuthService, db: Database
    ):
        user = await _make_user(db, throttled_service, "a@example.com")
        for _ in range(EMAIL_CAP):
            with pytest.raises(AccountDeletionAuthError, match="Incorrect password"):
                await self._delete(throttled_service, user, "wrong-password")

        with pytest.raises(AccountDeletionAuthError) as exc:
            await self._delete(throttled_service, user, "correct-horse")
        assert str(exc.value) == (
            "Too many incorrect passwords. Try again in 15 minutes."
        )
        refreshed = await db.get_user_by_id(user["id"])
        assert refreshed is not None
        assert refreshed["scheduled_deletion_at"] is None

    async def test_login_failures_share_the_bucket_with_deletion(
        self, throttled_service: AuthService, db: Database
    ):
        user = await _make_user(db, throttled_service, "a@example.com")
        await _fail_login(throttled_service, "a@example.com", EMAIL_CAP)

        with pytest.raises(AccountDeletionAuthError, match="Too many incorrect"):
            await self._delete(throttled_service, user, "correct-horse")

    async def test_deletion_reauth_does_not_charge_the_ip_bucket(
        self, throttled_service: AuthService, db: Database
    ):
        user = await _make_user(db, throttled_service, "a@example.com")
        for _ in range(EMAIL_CAP):
            with pytest.raises(AccountDeletionAuthError):
                await self._delete(throttled_service, user, "wrong-password")

        for i in range(IP_CAP):
            await _fail_login(throttled_service, f"other{i}@example.com", 1)


@pytest.fixture
async def throttled_app(app, config, db, clock, email_limiter, ip_limiter):
    app.state.auth_service = AuthService(
        config,
        db,
        routing=app.state.routing,
        login_email_limiter=email_limiter,
        login_ip_limiter=ip_limiter,
    )
    return app


class TestLoginEndpoint429:
    async def test_api_login_returns_429_with_retry_after(
        self, throttled_app, client
    ):
        for _ in range(EMAIL_CAP):
            resp = await client.post(
                "/api/auth/login",
                json={"email": "a@example.com", "password": "wrong-password"},
            )
            assert resp.status_code == 401

        resp = await client.post(
            "/api/auth/login",
            json={"email": "a@example.com", "password": "wrong-password"},
        )
        assert resp.status_code == 429
        assert resp.headers["Retry-After"] == str(WINDOW)
        assert resp.json() == {
            "detail": {
                "error": "login_rate_limited",
                "retry_after_seconds": WINDOW,
            }
        }

    async def test_api_login_ip_bucket_uses_request_client_host(
        self, throttled_app, client
    ):
        # ASGITransport reports one synthetic client host for every request.
        for i in range(IP_CAP):
            resp = await client.post(
                "/api/auth/login",
                json={"email": f"spray{i}@example.com", "password": "wrong-password"},
            )
            assert resp.status_code == 401

        resp = await client.post(
            "/api/auth/login",
            json={"email": TEST_ADMIN_EMAIL, "password": TEST_ADMIN_PASSWORD},
        )
        assert resp.status_code == 429

    async def test_dashboard_login_renders_429_with_minutes(
        self, throttled_app, client
    ):
        for _ in range(EMAIL_CAP):
            resp = await client.post(
                "/dashboard/login",
                data={"username": TEST_ADMIN_EMAIL, "password": "wrong-password"},
            )
            assert resp.status_code == 401

        resp = await client.post(
            "/dashboard/login",
            data={"username": TEST_ADMIN_EMAIL, "password": TEST_ADMIN_PASSWORD},
        )
        assert resp.status_code == 429
        assert resp.headers["Retry-After"] == str(WINDOW)
        assert "Too many sign-in attempts. Try again in 15 minutes." in resp.text
        assert "dashboard_token" not in resp.headers.get("set-cookie", "")


def _lifespan_config(tmp_path, **knobs: int) -> CentralServerConfig:
    return CentralServerConfig(
        jwt_secret_key="test-secret-key-for-login-rate-limit-wiring",
        db_path=str(tmp_path / "wiring.db"),
        manifest_store_path=str(tmp_path / "manifest.json"),
        email_verification_required=False,
        **knobs,
    )


class TestLifespanWiring:
    """The real lifespan builds the limiters from config."""

    def test_email_cap_from_config_throttles_login(self, tmp_path):
        config = _lifespan_config(
            tmp_path, login_email_failure_cap=2, login_ip_failure_cap=0
        )
        with TestClient(create_app(config=config)) as client:
            body = {"email": "a@example.com", "password": "wrong-password"}
            assert client.post("/api/auth/login", json=body).status_code == 401
            assert client.post("/api/auth/login", json=body).status_code == 401
            resp = client.post("/api/auth/login", json=body)
            assert resp.status_code == 429
            assert resp.json()["detail"]["retry_after_seconds"] <= 900

    def test_ip_cap_from_config_throttles_login(self, tmp_path):
        config = _lifespan_config(
            tmp_path, login_email_failure_cap=0, login_ip_failure_cap=2
        )
        with TestClient(create_app(config=config)) as client:
            for i in range(2):
                resp = client.post(
                    "/api/auth/login",
                    json={"email": f"s{i}@example.com", "password": "wrong-pass"},
                )
                assert resp.status_code == 401
            resp = client.post(
                "/api/auth/login",
                json={"email": "fresh@example.com", "password": "wrong-pass"},
            )
            assert resp.status_code == 429

    def test_zero_caps_disable_the_throttle(self, tmp_path):
        config = _lifespan_config(
            tmp_path, login_email_failure_cap=0, login_ip_failure_cap=0
        )
        with TestClient(create_app(config=config)) as client:
            body = {"email": "a@example.com", "password": "wrong-password"}
            for _ in range(12):
                assert client.post("/api/auth/login", json=body).status_code == 401
