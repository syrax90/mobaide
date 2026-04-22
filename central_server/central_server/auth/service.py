"""Authentication service for the Central Server.

Four distinct auth paths:
1. Agent tokens — long-lived, SHA-256 hashed in DB, owned by users
2. Mobile session tokens — JWT with user_id claim, refresh rotation
3. User accounts — email/password (Argon2id) or Google OAuth, DB-backed
4. Dashboard sessions — cookie-based JWT with user_id + role claims
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import BackgroundTasks

from .models import MobileTokenPayload, UserTokenPayload

if TYPE_CHECKING:
    from ..config import CentralServerConfig
    from ..db.database import Database
    from .email import EmailSender
    from .rate_limit import ResendRateLimiter

logger = logging.getLogger(__name__)

_ph = PasswordHasher()  # Thread-safe singleton

# Strong references to fire-and-forget email tasks. asyncio's internal task
# set only holds weak refs, so without this the GC can collect a task
# mid-send (see Ruff RUF006 / CPython issue python/cpython#91887).
_background_email_tasks: set[asyncio.Task] = set()


class VerificationRequiredError(Exception):
    """Raised by ``login_user`` when the account is unverified."""

    def __init__(self, email: str) -> None:
        super().__init__(f"Email verification required for {email}")
        self.email = email


class InvalidVerificationCodeError(Exception):
    """Supplied code did not match the active token."""


class ExpiredVerificationCodeError(Exception):
    """Active token has passed its TTL."""


class VerificationAttemptsExhaustedError(Exception):
    """Too many failed attempts against the current code; resend required."""


class ResendRateLimitedError(Exception):
    """Resend blocked by cooldown or hourly cap."""

    def __init__(self, cooldown_seconds_remaining: int) -> None:
        super().__init__(
            f"Resend blocked for {cooldown_seconds_remaining}s"
        )
        self.cooldown_seconds_remaining = cooldown_seconds_remaining


@dataclass
class RegisterResult:
    """Outcome of ``AuthService.register_user``.

    ``status == "ok"`` → ``access_token`` is a valid JWT.
    ``status == "verification_required"`` → ``access_token is None``.
    """

    user_id: str
    email: str
    role: str
    status: str
    access_token: str | None


class AuthService:
    def __init__(
        self,
        config: CentralServerConfig,
        db: Database,
        email_sender: "EmailSender | None" = None,
        resend_limiter: "ResendRateLimiter | None" = None,
    ) -> None:
        self._config = config
        self._db = db
        self._email_sender = email_sender
        self._resend_limiter = resend_limiter

    async def _hash_password(self, password: str) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _ph.hash, password)

    async def _verify_password(self, hash: str, password: str) -> bool:
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, _ph.verify, hash, password)
        except VerifyMismatchError:
            return False

    def _hash_code(self, user_id: str, code: str) -> str:
        """HMAC-SHA256 of ``code`` peppered with the server's JWT secret.

        The pepper defends against DB-only leaks: even an attacker with the
        full email_verification_tokens table cannot brute-force the 6-digit
        code without also compromising the secret.
        """
        return hmac.new(
            self._config.jwt_secret_key.encode(),
            f"{user_id}:{code}".encode(),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _generate_code() -> str:
        """Generate a cryptographically random 6-digit code."""
        return f"{secrets.randbelow(1_000_000):06d}"

    async def _issue_verification_code(
        self,
        user_id: str,
        email: str,
        background_tasks: BackgroundTasks | None,
        consume_rate_limit_slot: bool = False,
    ) -> None:
        """Create a fresh verification token and schedule the email send.

        When ``consume_rate_limit_slot`` is True the initial email consumes
        a resend-rate-limit slot so that follow-up resends honour the
        per-email cooldown. This is used by ``register_user``; resend
        callers acquire the slot themselves (and may be blocked by it).
        """
        if consume_rate_limit_slot and self._resend_limiter is not None:
            # Bump the counter even on fresh registration; if a previous
            # incomplete flow left state, the next explicit resend enforces
            # the cap.
            await self._resend_limiter.try_acquire(email)
        code = self._generate_code()
        code_hash = self._hash_code(user_id, code)
        ttl_seconds = self._config.email_verification_ttl_minutes * 60
        await self._db.create_verification_token(user_id, code_hash, ttl_seconds)
        if self._email_sender is None:
            logger.warning(
                "email_verification.no_sender_configured user_id=%s — "
                "code was generated but not emailed",
                user_id,
            )
            return
        sender = self._email_sender
        ttl_minutes = self._config.email_verification_ttl_minutes
        if background_tasks is not None:
            background_tasks.add_task(
                sender.send_verification_email, email, code, ttl_minutes
            )
        else:
            # No BackgroundTasks context — fire-and-forget with a strong ref
            # so the GC can't drop the task mid-send.
            task = asyncio.create_task(
                sender.send_verification_email(email, code, ttl_minutes)
            )
            _background_email_tasks.add(task)
            task.add_done_callback(_background_email_tasks.discard)

    async def register_user(
        self,
        email: str,
        password: str,
        background_tasks: BackgroundTasks | None = None,
    ) -> RegisterResult:
        """Register a new user with email+password.

        When ``email_verification_required`` is True, the returned
        ``RegisterResult`` has ``status="verification_required"`` and
        ``access_token=None``; the client must call ``verify_email`` to
        obtain a JWT. When False (CI bypass), the user is marked verified
        immediately and a JWT is returned.

        Raises ValueError if the email is already registered.
        """
        existing = await self._db.get_user_by_email(email)
        if existing is not None:
            raise ValueError("Email already registered")

        password_hash = await self._hash_password(password)

        if self._config.email_verification_required:
            user_id = await self._db.create_user(
                email=email,
                password_hash=password_hash,
                role="viewer",
                email_verified=False,
            )
            await self._issue_verification_code(
                user_id,
                email,
                background_tasks,
                consume_rate_limit_slot=True,
            )
            return RegisterResult(
                user_id=user_id,
                email=email,
                role="viewer",
                status="verification_required",
                access_token=None,
            )

        # Bypass path: mark verified and issue a JWT immediately.
        user_id = await self._db.create_user(
            email=email,
            password_hash=password_hash,
            role="viewer",
            email_verified=True,
        )
        token = self._create_user_token(user_id, email, "viewer")
        return RegisterResult(
            user_id=user_id,
            email=email,
            role="viewer",
            status="ok",
            access_token=token,
        )

    async def login_user(self, email: str, password: str) -> tuple[str, str, str]:
        """Authenticate a user by email+password.

        Returns (user_id, role, jwt_token).
        Raises ValueError if credentials are invalid.
        Raises VerificationRequiredError if the user is unverified.
        """
        user = await self._db.get_user_by_email(email)
        if user is None or user["password_hash"] is None:
            raise ValueError("Invalid email or password")

        if not await self._verify_password(user["password_hash"], password):
            raise ValueError("Invalid email or password")

        if self._config.email_verification_required and not user.get("email_verified"):
            raise VerificationRequiredError(user["email"])

        token = self._create_user_token(user["id"], user["email"], user["role"])
        return user["id"], user["role"], token

    async def verify_email(
        self, email: str, code: str
    ) -> tuple[str, str, str]:
        """Consume a verification code and mark the user verified.

        Returns (user_id, role, jwt_token) on success. Raises one of
        ``InvalidVerificationCodeError`` / ``ExpiredVerificationCodeError`` /
        ``VerificationAttemptsExhaustedError`` on failure.
        """
        user = await self._db.get_user_by_email(email)
        if user is None:
            # Do not disclose whether the email exists — treat as invalid.
            raise InvalidVerificationCodeError()

        # NB: we do NOT short-circuit for already-verified users. Handing out
        # a JWT to any caller who names a verified email would be an auth
        # bypass. A legitimate retry hits the normal consume path; since the
        # token is already consumed (or no active token exists), the CAS
        # naturally fails and we raise InvalidVerificationCodeError.

        code_hash = self._hash_code(user["id"], code)
        outcome = await self._db.consume_verification_token(
            user["id"],
            code_hash,
            self._config.email_verification_max_attempts,
        )
        if outcome == "ok":
            await self._db.mark_user_verified(user["id"])
            if self._resend_limiter is not None:
                await self._resend_limiter.reset(email)
            token = self._create_user_token(user["id"], user["email"], user["role"])
            logger.info("email_verification.verified user_id=%s", user["id"])
            return user["id"], user["role"], token
        if outcome == "expired":
            raise ExpiredVerificationCodeError()
        if outcome == "exhausted":
            raise VerificationAttemptsExhaustedError()
        raise InvalidVerificationCodeError()

    async def resend_verification(
        self,
        email: str,
        background_tasks: BackgroundTasks | None = None,
    ) -> None:
        """Issue a new verification code (rate-limited).

        Silently no-ops if the account is already verified or does not
        exist — this prevents account-enumeration via the resend endpoint.
        Raises ``ResendRateLimitedError`` when the per-email cooldown or
        hourly cap is hit.
        """
        if self._resend_limiter is not None:
            wait = await self._resend_limiter.try_acquire(email)
            if wait is not None:
                raise ResendRateLimitedError(wait)

        user = await self._db.get_user_by_email(email)
        if user is None or user.get("email_verified"):
            # Silent no-op: the caller already "paid" the rate-limit slot
            # above, which is fine because we still logged it.
            return

        await self._issue_verification_code(user["id"], user["email"], background_tasks)

    async def google_authenticate(
        self, token_str: str
    ) -> tuple[str, str, str, bool]:
        """Verify a Google ID token and return/create user.

        Returns (user_id, role, jwt_token, is_new_user).
        Raises ValueError if token is invalid or Google client ID not configured.
        """
        if not self._config.google_client_id:
            raise ValueError("Google authentication is not configured")

        # Defer import to avoid hard dependency when Google auth is unused
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token as google_id_token

        loop = asyncio.get_running_loop()
        try:
            idinfo = await loop.run_in_executor(
                None,
                lambda: google_id_token.verify_oauth2_token(
                    token_str,
                    google_requests.Request(),
                    self._config.google_client_id,
                ),
            )
        except Exception as exc:
            raise ValueError(f"Invalid Google ID token: {exc}")

        google_sub: str = idinfo["sub"]
        email: str = idinfo["email"]
        email_verified: bool = idinfo.get("email_verified", False)

        user = await self._db.get_user_by_google_sub(google_sub)
        if user is not None:
            if email_verified and not user.get("email_verified"):
                # Google vouches for the address → clear any pending OTP flow.
                await self._db.mark_user_verified(user["id"])
            token = self._create_user_token(user["id"], user["email"], user["role"])
            return user["id"], user["role"], token, False

        # Link an existing email/password account when Google confirms the address.
        if email_verified:
            user = await self._db.get_user_by_email(email)
            if user is not None:
                await self._db.link_google_to_user(user["id"], google_sub)
                if not user.get("email_verified"):
                    await self._db.mark_user_verified(user["id"])
                token = self._create_user_token(
                    user["id"], user["email"], user["role"]
                )
                return user["id"], user["role"], token, False

        # Guard: email taken by a different account (unverified Google email edge case).
        existing = await self._db.get_user_by_email(email)
        if existing is not None:
            raise ValueError("Email already registered with a different account")

        # New user — trust Google's email_verified claim.
        user_id = await self._db.create_user(
            email=email,
            google_sub=google_sub,
            role="viewer",
            email_verified=email_verified,
        )
        token = self._create_user_token(user_id, email, "viewer")
        return user_id, "viewer", token, True

    async def seed_admin_user(self) -> None:
        """Seed the admin user from config env vars if no admin exists.

        Called during app startup. Idempotent. If a user with the seed email
        already exists, we leave it untouched — we do not promote an existing
        viewer to admin because viewers may own agents (admins must not).
        """
        if not self._config.admin_seed_email or not self._config.admin_seed_password:
            return

        existing = await self._db.get_user_by_email(self._config.admin_seed_email)
        if existing is not None:
            if existing["role"] != "admin":
                logger.warning(
                    "admin_user.seed_skipped email=%s reason=existing_non_admin_user "
                    "(refusing to promote an existing account to admin)",
                    self._config.admin_seed_email,
                )
            return

        password_hash = await self._hash_password(self._config.admin_seed_password)
        await self._db.create_user(
            email=self._config.admin_seed_email,
            password_hash=password_hash,
            role="admin",
            email_verified=True,
        )
        logger.info(
            "admin_user.seeded email=%s", self._config.admin_seed_email
        )

    async def verify_agent_token(self, agent_id: str, raw_token: str) -> bool:
        return await self._db.verify_agent_token(agent_id, raw_token)

    async def create_mobile_tokens(
        self, mobile_device_id: str, agent_id: str, user_id: str
    ) -> tuple[str, str]:
        """Create access + refresh token pair for a mobile device."""
        access = self._create_jwt(
            sub=mobile_device_id,
            agent_id=agent_id,
            user_id=user_id,
            token_type="mobile_access",
            expire_minutes=self._config.mobile_token_expire_minutes,
        )
        refresh = secrets.token_urlsafe(32)
        await self._db.store_refresh_token(
            raw_token=refresh,
            mobile_device_id=mobile_device_id,
            agent_id=agent_id,
            expire_minutes=self._config.mobile_refresh_expire_minutes,
        )
        return access, refresh

    async def refresh_mobile_tokens(
        self, refresh_token: str
    ) -> tuple[str, str] | None:
        """Exchange a refresh token for a new access+refresh pair.

        Returns None if the refresh token is invalid/expired/revoked.
        Looks up the agent owner to include user_id in the new token.
        """
        result = await self._db.validate_and_consume_refresh(refresh_token)
        if result is None:
            return None
        mobile_device_id, agent_id = result
        agent = await self._db.get_agent(agent_id)
        user_id = agent["owner_user_id"] if agent else ""
        return await self.create_mobile_tokens(mobile_device_id, agent_id, user_id)

    def decode_mobile_token(self, token: str) -> MobileTokenPayload:
        """Decode and validate a mobile access JWT. Raises jwt.InvalidTokenError."""
        payload = jwt.decode(
            token,
            self._config.jwt_secret_key,
            algorithms=[self._config.jwt_algorithm],
        )
        if payload.get("type") != "mobile_access":
            raise jwt.InvalidTokenError("Expected mobile_access token")
        if "agent_id" not in payload:
            raise jwt.InvalidTokenError("Missing agent_id claim")
        return MobileTokenPayload(
            sub=payload["sub"],
            agent_id=payload["agent_id"],
            user_id=payload.get("user_id", ""),
            token_type=payload["type"],
            exp=payload.get("exp"),
            iat=payload["iat"],
        )

    def decode_dashboard_token(self, token: str) -> UserTokenPayload:
        """Decode a dashboard JWT. Returns UserTokenPayload. Raises jwt.InvalidTokenError."""
        payload = jwt.decode(
            token,
            self._config.jwt_secret_key,
            algorithms=[self._config.jwt_algorithm],
        )
        if payload.get("type") != "dashboard":
            raise jwt.InvalidTokenError("Expected dashboard token")
        return UserTokenPayload(
            sub=payload["sub"],
            email=payload.get("email", payload["sub"]),
            role=payload.get("role", "admin"),
            token_type=payload["type"],
            exp=payload.get("exp"),
            iat=payload["iat"],
        )

    def _create_user_token(self, user_id: str, email: str, role: str) -> str:
        """Create a dashboard JWT with user identity claims."""
        return self._create_jwt(
            sub=user_id,
            token_type="dashboard",
            expire_minutes=self._config.dashboard_token_expire_minutes,
            user_id=user_id,
            email=email,
            role=role,
        )

    def _create_jwt(
        self,
        *,
        sub: str,
        token_type: str,
        expire_minutes: int,
        agent_id: str | None = None,
        user_id: str | None = None,
        email: str | None = None,
        role: str | None = None,
    ) -> str:
        now = int(time.time())
        payload: dict = {
            "sub": sub,
            "type": token_type,
            "iat": now,
        }
        # expire_minutes == 0 means "never expires" — omit the `exp` claim
        # entirely. PyJWT only validates `exp` when the claim is present,
        # so a missing `exp` decodes as a non-expiring token.
        if expire_minutes > 0:
            payload["exp"] = now + expire_minutes * 60
        if agent_id is not None:
            payload["agent_id"] = agent_id
        if user_id is not None:
            payload["user_id"] = user_id
        if email is not None:
            payload["email"] = email
        if role is not None:
            payload["role"] = role
        return jwt.encode(
            payload,
            self._config.jwt_secret_key,
            algorithm=self._config.jwt_algorithm,
        )
