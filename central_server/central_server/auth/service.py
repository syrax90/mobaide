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
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Mapping

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import BackgroundTasks

from ..relay.routing import WS_CLOSE_ADMIN_REVOKED
from .models import DeleteAccountRequest, MobileTokenPayload, UserTokenPayload

if TYPE_CHECKING:
    from ..config import CentralServerConfig
    from ..db.database import Database
    from ..relay.routing import RoutingTable
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


class InvalidResetCodeError(Exception):
    """Supplied reset code did not match the active token."""


class ExpiredResetCodeError(Exception):
    """Active reset token has passed its TTL."""


class ResetAttemptsExhaustedError(Exception):
    """Too many failed attempts against the current reset code."""


class NewPasswordSameAsOldError(Exception):
    """New password matches the current password — reset rejected."""


class AccountDeletionAuthError(Exception):
    """Re-auth proof failed for an account-deletion request.

    Distinct from a generic ``ValueError`` so the router can return 403 (proof
    rejected) rather than 400 (malformed) or 401 (no session).
    """


class AccountDeletionForbiddenError(Exception):
    """Account deletion refused for policy reasons (e.g., seed admin)."""


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
        reset_email_limiter: "ResendRateLimiter | None" = None,
        routing: "RoutingTable | None" = None,
    ) -> None:
        self._config = config
        self._db = db
        self._email_sender = email_sender
        self._resend_limiter = resend_limiter
        self._reset_email_limiter = reset_email_limiter
        self._routing = routing

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

    async def login_user(
        self, email: str, password: str
    ) -> tuple[str, str, str, str | None]:
        """Authenticate a user by email+password.

        Returns (user_id, role, jwt_token, scheduled_deletion_at).
        ``scheduled_deletion_at`` is the ISO timestamp when the account is
        scheduled for soft-delete purge, or ``None`` if not scheduled.
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
        return (
            user["id"],
            user["role"],
            token,
            user.get("scheduled_deletion_at"),
        )

    async def verify_email(
        self, email: str, code: str
    ) -> tuple[str, str, str, str | None]:
        """Consume a verification code and mark the user verified.

        Returns (user_id, role, jwt_token, scheduled_deletion_at) on success.
        ``scheduled_deletion_at`` is rarely non-null on this path (the user
        is registering / just verified email) but threaded for shape parity
        with :func:`login_user`. Raises one of ``InvalidVerificationCodeError``
        / ``ExpiredVerificationCodeError`` / ``VerificationAttemptsExhaustedError``
        on failure.
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
            return (
                user["id"],
                user["role"],
                token,
                user.get("scheduled_deletion_at"),
            )
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

    async def request_password_reset(
        self,
        email: str,
        background_tasks: BackgroundTasks | None = None,
        *,
        client_ip: str | None = None,
    ) -> None:
        """Issue a password-reset OTP. Enumeration-resistant: always silent success.

        Unknown email / unverified user → no DB write, no email. We deliberately
        do NOT run a dummy Argon2id hash here: this endpoint's happy path is
        HMAC + a single SQL INSERT (microsecond-millisecond range), so an
        Argon2id (~50-100ms) on the unknown branch would INVERT timing parity
        and turn the silent-success contract into a measurable enumeration
        oracle. The residual differential (HMAC + insert vs lookup-only) is
        well inside network jitter. The same dummy-hash anti-pattern is
        intentionally preserved in ``reset_password``, where the happy path
        actually does run Argon2id (verify_old + hash_new).
        """
        user = await self._db.get_user_by_email(email)
        if user is None or not user.get("email_verified"):
            return

        if self._reset_email_limiter is not None:
            wait = await self._reset_email_limiter.try_acquire(email)
            if wait is not None:
                return

        code = self._generate_code()
        code_hash = self._hash_code(user["id"], code)
        ttl_seconds = self._config.password_reset_ttl_minutes * 60
        await self._db.create_password_reset_token(user["id"], code_hash, ttl_seconds)

        if self._email_sender is None:
            logger.warning(
                "password_reset.no_sender_configured user_id=%s — "
                "code was generated but not emailed",
                user["id"],
            )
            return

        sender = self._email_sender
        ttl_minutes = self._config.password_reset_ttl_minutes
        if background_tasks is not None:
            background_tasks.add_task(
                sender.send_password_reset_email,
                user["email"], code, ttl_minutes, client_ip,
            )
        else:
            task = asyncio.create_task(
                sender.send_password_reset_email(
                    user["email"], code, ttl_minutes, client_ip,
                )
            )
            _background_email_tasks.add(task)
            task.add_done_callback(_background_email_tasks.discard)

    async def reset_password(
        self,
        email: str,
        code: str,
        new_password: str,
        background_tasks: BackgroundTasks | None = None,
    ) -> None:
        """Consume the reset OTP, replace the password, and revoke mobile sessions.

        Raises one of ``InvalidResetCodeError`` / ``ExpiredResetCodeError`` /
        ``ResetAttemptsExhaustedError`` on a code failure, or
        ``NewPasswordSameAsOldError`` if the new password matches the current
        one. On success, persists the new Argon2id hash, logically revokes
        every active mobile refresh token for agents this user owns, clears
        the per-email rate-limit slot, and schedules a "password changed"
        notification email (best-effort, never raises).
        """
        user = await self._db.get_user_by_email(email)
        if user is None:
            # Timing parity: still pay one Argon2id hash before failing.
            await self._hash_password("timing-parity-dummy")
            raise InvalidResetCodeError()

        code_hash = self._hash_code(user["id"], code)
        outcome = await self._db.consume_password_reset_token(
            user["id"],
            code_hash,
            self._config.password_reset_max_attempts,
        )
        if outcome == "expired":
            raise ExpiredResetCodeError()
        if outcome == "exhausted":
            raise ResetAttemptsExhaustedError()
        if outcome != "ok":
            raise InvalidResetCodeError()

        if user["password_hash"] is not None and await self._verify_password(
            user["password_hash"], new_password
        ):
            raise NewPasswordSameAsOldError()

        new_hash = await self._hash_password(new_password)
        # Revoke first, then update the password. If a concurrent refresh
        # request slots in between the two commits, the worst case is that
        # the user still has the OLD password live for a few ms after their
        # sessions die — harmless, because the old password is what they're
        # rotating away from. The reverse order would leave a tiny window
        # where the new password is durable but stolen refresh tokens still
        # work.
        revoked = await self._db.revoke_all_refresh_tokens_for_user(user["id"])
        await self._db.update_user_password(user["id"], new_hash)

        if self._reset_email_limiter is not None:
            await self._reset_email_limiter.reset(email)

        if user["role"] == "admin":
            logger.info(
                "password_reset.admin_completed user_id=%s revoked_sessions=%d",
                user["id"], revoked,
            )
        else:
            logger.info(
                "password_reset.completed user_id=%s revoked_sessions=%d",
                user["id"], revoked,
            )

        if self._email_sender is None:
            return

        sender = self._email_sender
        if background_tasks is not None:
            background_tasks.add_task(
                sender.send_password_changed_notification,
                user["email"], revoked,
            )
        else:
            task = asyncio.create_task(
                sender.send_password_changed_notification(user["email"], revoked)
            )
            _background_email_tasks.add(task)
            task.add_done_callback(_background_email_tasks.discard)

    async def _verify_google_id_token(self, token_str: str) -> Mapping[str, Any]:
        """Verify a Google ID token's signature + audience and return idinfo.

        Raises ``ValueError`` if the configured client ID is missing or the
        token does not validate. Callers layer additional claim checks
        (``nonce``, ``iat``, ``sub``) on top via ``_verify_google_token_claims``.
        """
        if not self._config.google_client_id:
            raise ValueError("Google authentication is not configured")

        # Defer import to avoid hard dependency when Google auth is unused
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token as google_id_token

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                None,
                lambda: google_id_token.verify_oauth2_token(
                    token_str,
                    google_requests.Request(),
                    self._config.google_client_id,
                ),
            )
        except Exception as exc:
            raise ValueError(f"Invalid Google ID token: {exc}")

    def _verify_google_token_claims(
        self,
        idinfo: Mapping[str, Any],
        *,
        expected_nonce: str | None = None,
        expected_sub: str | None = None,
    ) -> None:
        """Apply re-auth/freshness checks to an already-validated idinfo dict.

        Raises ``ValueError`` with a stable message on any failure. Callers
        translate to their own exception type as needed (the portal Google
        login path lets ``ValueError`` bubble; the delete re-auth path wraps
        it in ``AccountDeletionAuthError``).
        """
        if expected_sub is not None and idinfo.get("sub") != expected_sub:
            raise ValueError("Google identity does not match this account")
        if expected_nonce is not None:
            id_nonce = idinfo.get("nonce")
            if not id_nonce or not secrets.compare_digest(id_nonce, expected_nonce):
                raise ValueError("Nonce mismatch")
            iat = idinfo.get("iat")
            if not isinstance(iat, int):
                raise ValueError("ID token missing iat claim")
            if int(time.time()) - iat > self._GOOGLE_REAUTH_FRESHNESS_SECONDS:
                raise ValueError("Google ID token is too old — re-authenticate")

    async def google_authenticate(
        self,
        token_str: str,
        *,
        expected_nonce: str | None = None,
    ) -> tuple[str, str, str, bool]:
        """Verify a Google ID token and return/create user.

        Returns (user_id, role, jwt_token, is_new_user).
        Raises ValueError if token is invalid or Google client ID not configured.

        When ``expected_nonce`` is non-None, the nonce inside the ID token must
        match and the token must be fresh (``iat`` within
        ``_GOOGLE_REAUTH_FRESHNESS_SECONDS``). Portal flows pass a
        server-generated nonce; mobile Credential Manager sign-in leaves it
        ``None`` (the mobile nonce is verified at re-auth time, not at initial
        login).
        """
        idinfo = await self._verify_google_id_token(token_str)
        self._verify_google_token_claims(idinfo, expected_nonce=expected_nonce)

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

    # Re-auth freshness window for self-delete via Google ID token. Auth0
    # guidance recommends 5 minutes for high-risk actions.
    _GOOGLE_REAUTH_FRESHNESS_SECONDS = 5 * 60

    async def _verify_fresh_credentials(
        self, user: dict, proof: DeleteAccountRequest
    ) -> None:
        """Re-verify a user's identity before destructive action.

        Raises ``AccountDeletionAuthError`` on rejection. The session JWT
        alone is never sufficient — the caller must present either:

          * ``method="password"``: current password, verified via Argon2id.
          * ``method="google"``: a fresh ID token whose signature, audience,
            ``iat`` (≤5 min old), ``nonce`` (matches the client-supplied
            nonce), and ``sub`` (matches the stored ``google_sub``) all
            validate.
        """
        if proof.method == "password":
            if user["password_hash"] is None:
                raise AccountDeletionAuthError(
                    "This account does not use a password — re-authenticate "
                    "with Google instead."
                )
            assert proof.password is not None
            if not await self._verify_password(user["password_hash"], proof.password):
                raise AccountDeletionAuthError("Incorrect password")
            return

        if proof.method == "google":
            assert proof.id_token is not None
            assert proof.nonce is not None
            stored_sub = user.get("google_sub")
            if stored_sub is None:
                raise AccountDeletionAuthError(
                    "This account is not linked to a Google identity"
                )
            try:
                idinfo = await self._verify_google_id_token(proof.id_token)
                self._verify_google_token_claims(
                    idinfo,
                    expected_nonce=proof.nonce,
                    expected_sub=stored_sub,
                )
            except ValueError as exc:
                raise AccountDeletionAuthError(str(exc)) from exc
            return

        raise AccountDeletionAuthError(f"Unknown re-auth method: {proof.method}")

    async def request_account_deletion(
        self,
        user_payload: UserTokenPayload,
        *,
        source: str,
        proof: DeleteAccountRequest,
        background_tasks: BackgroundTasks | None = None,
    ) -> str | None:
        """Process a user-initiated account deletion.

        ``proof.mode`` controls behavior:

        * ``"scheduled"`` (default): soft-delete with a 14-day grace window.
          Returns the ISO ``scheduled_deletion_at`` timestamp.
        * ``"immediate"``: cascade-purge the user immediately. No grace, no
          cancel token. Returns ``None``.

        Scheduled-path order (mirrors admin ``DELETE /dashboard/users/{user_id}``
        for the teardown sequence, but adds the soft-delete state + audit row
        before the WS close — so a racing reconnect sees the schedule and
        surfaces the cancel UI):

          1. Reject the seed-admin.
          2. Verify fresh credential proof.
          3. Set ``users.scheduled_deletion_at`` + ``deletion_requested_at``.
          4. Write a ``deletion_audit`` row in ``pending`` state.
          5. Revoke every active mobile refresh token for the user.
          6. Close the user's WS connections + each owned-agent's WS with
             ``4010``. The routing table snapshot is taken before any DB
             row that invalidates the lookup keys.
          7. Issue a single-use cancel-deletion token + send the confirmation
             email (best-effort; failure does not undo the schedule).

        Immediate-path order (no grace, no cancel token):

          1-2. Same seed-admin + credential checks.
          3. Write a ``deletion_audit`` row in ``pending`` state with
             ``mode='immediate'`` and ``scheduled_for=now`` (forensic trail
             before any destructive step).
          4. Revoke refresh tokens + close WS-4010 (mirrors scheduled order
             so live sessions cannot race the purge).
          5. Cascade ``delete_user``.
          6. Stamp audit ``completed``; email the final-purge confirmation.
        """
        user_id = user_payload.sub
        user = await self._db.get_user_by_id(user_id)
        if user is None:
            raise AccountDeletionAuthError("User not found")

        seed_email = self._config.admin_seed_email
        if seed_email is not None and user["email"] == seed_email:
            raise AccountDeletionForbiddenError(
                "The seed admin account cannot be self-deleted"
            )

        await self._verify_fresh_credentials(user, proof)

        if proof.mode == "immediate":
            await self._execute_immediate_deletion(user, source=source)
            return None

        now = datetime.now(timezone.utc)
        scheduled_for = now + timedelta(
            days=self._config.account_deletion_grace_days
        )
        now_iso = now.isoformat()
        scheduled_iso = scheduled_for.isoformat()

        await self._db.set_scheduled_deletion(
            user_id,
            scheduled_for=scheduled_iso,
            requested_at=now_iso,
        )
        await self._db.record_deletion_audit(
            user_id=user_id,
            email_at_request=user["email"],
            requested_at=now_iso,
            scheduled_for=scheduled_iso,
            source=source,
            mode="scheduled",
        )
        revoked = await self._db.revoke_all_refresh_tokens_for_user(user_id)

        if self._routing is not None:
            agent_ids = await self._db.list_agents_for_user(user_id)
            for agent in agent_ids:
                await self._routing.disconnect_agent(
                    agent["agent_id"], WS_CLOSE_ADMIN_REVOKED, "user_deleted"
                )
            await self._routing.disconnect_user(
                user_id, WS_CLOSE_ADMIN_REVOKED, "user_deleted"
            )

        cancel_ttl_seconds = self._config.account_deletion_cancel_token_ttl_hours * 3600
        cancel_token = await self._db.create_deletion_cancel_token(
            user_id, cancel_ttl_seconds
        )

        if self._email_sender is not None:
            sender = self._email_sender
            email = user["email"]
            grace_days = self._config.account_deletion_grace_days
            cancel_url = self._build_cancel_url(cancel_token)
            if background_tasks is not None:
                background_tasks.add_task(
                    sender.send_account_deletion_requested,
                    email, scheduled_iso, grace_days, cancel_url,
                )
            else:
                task = asyncio.create_task(
                    sender.send_account_deletion_requested(
                        email, scheduled_iso, grace_days, cancel_url,
                    )
                )
                _background_email_tasks.add(task)
                task.add_done_callback(_background_email_tasks.discard)

        logger.info(
            "account_deletion.requested user_id=%s source=%s "
            "scheduled_for=%s revoked_sessions=%d",
            user_id, source, scheduled_iso, revoked,
        )
        return scheduled_iso

    async def _execute_immediate_deletion(
        self,
        user: dict,
        *,
        source: str,
    ) -> None:
        """Cascade-purge a user without the soft-delete grace window.

        Pre-flight checks (seed-admin, fresh credentials) must already have
        passed. Writes the audit row BEFORE any destructive step so a crash
        mid-purge leaves a forensic trail.
        """
        user_id = user["id"]
        email = user["email"]

        now_iso = datetime.now(timezone.utc).isoformat()
        audit_id = await self._db.record_deletion_audit(
            user_id=user_id,
            email_at_request=email,
            requested_at=now_iso,
            scheduled_for=now_iso,
            source=source,
            mode="immediate",
        )
        revoked = await self._db.revoke_all_refresh_tokens_for_user(user_id)

        if self._routing is not None:
            agent_ids = await self._db.list_agents_for_user(user_id)
            for agent in agent_ids:
                await self._routing.disconnect_agent(
                    agent["agent_id"], WS_CLOSE_ADMIN_REVOKED, "user_deleted"
                )
            await self._routing.disconnect_user(
                user_id, WS_CLOSE_ADMIN_REVOKED, "user_deleted"
            )

        await self._db.delete_user(user_id)
        await self._db.update_deletion_audit_status(
            audit_id, "completed", completed_at=now_iso
        )

        if self._email_sender is not None:
            task = asyncio.create_task(
                self._email_sender.send_account_deletion_completed(email)
            )
            _background_email_tasks.add(task)
            task.add_done_callback(_background_email_tasks.discard)

        logger.info(
            "account_deletion.immediate user_id=%s source=%s "
            "revoked_sessions=%d",
            user_id, source, revoked,
        )

    def _build_cancel_url(self, raw_token: str) -> str:
        """Construct the public URL emailed for one-click deletion cancel."""
        base = (
            self._config.portal_base_url
            or self._config.public_base_url
            or ""
        ).rstrip("/")
        return f"{base}/portal/cancel-deletion?token={raw_token}"

    async def cancel_account_deletion(
        self,
        user_id: str,
        *,
        cancellation_reason: str = "user_initiated",
    ) -> str | None:
        """Restore an account from the soft-delete grace window. Idempotent.

        Returns the previous ``scheduled_deletion_at`` (or None if the user
        was not scheduled — caller can surface "no-op" UX accordingly).
        """
        user = await self._db.get_user_by_id(user_id)
        if user is None:
            return None
        previous = user.get("scheduled_deletion_at")
        if previous is None:
            return None
        await self._db.clear_scheduled_deletion(user_id)
        audit = await self._db.find_pending_audit_for_user(user_id)
        if audit is not None:
            await self._db.update_deletion_audit_status(
                audit["id"],
                "cancelled",
                cancellation_reason=cancellation_reason,
            )
        logger.info(
            "account_deletion.cancelled user_id=%s reason=%s previous=%s",
            user_id, cancellation_reason, previous,
        )
        return previous

    async def cancel_account_deletion_by_token(
        self, raw_token: str
    ) -> str | None:
        """Consume an emailed cancel-deletion token. Returns ``user_id`` on
        success, ``None`` on invalid / expired / already-consumed token."""
        user_id = await self._db.consume_deletion_cancel_token(raw_token)
        if user_id is None:
            return None
        await self.cancel_account_deletion(
            user_id, cancellation_reason="email_link"
        )
        return user_id

    async def run_deletion_sweep(self) -> int:
        """Process every user past their grace window. Returns purge count.

        Safety: each candidate is re-fetched + re-checked inside the loop so
        a cancel that lands between scan and delete does not race-purge an
        account that was just restored. Per-user errors are logged + the
        audit row is stamped ``failed``; the loop never raises.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        candidates = await self._db.list_users_due_for_purge(now_iso)
        purged = 0
        for candidate in candidates:
            user_id = candidate["id"]
            try:
                fresh = await self._db.get_user_by_id(user_id)
                if fresh is None:
                    continue
                if fresh.get("scheduled_deletion_at") is None:
                    continue
                if fresh["scheduled_deletion_at"] > datetime.now(timezone.utc).isoformat():
                    continue
                await self._purge_scheduled_user(fresh)
                purged += 1
            except Exception as exc:
                logger.exception(
                    "deletion_sweeper.user_failed user_id=%s error=%s",
                    user_id, exc,
                )
                audit = await self._db.find_pending_audit_for_user(user_id)
                if audit is not None:
                    await self._db.update_deletion_audit_status(
                        audit["id"],
                        "failed",
                        cancellation_reason=f"sweeper_error: {exc!r}"[:200],
                    )
        if purged:
            logger.info("deletion_sweeper.completed purged=%d", purged)
        return purged

    async def _purge_scheduled_user(self, user: dict) -> None:
        """Cascade-delete a user whose grace window has elapsed.

        Closes any live WS sockets first (mirrors the admin path), runs
        ``delete_user``, stamps the audit row ``completed``, and fires the
        final-purge confirmation email (best-effort).
        """
        user_id = user["id"]
        email = user["email"]
        audit = await self._db.find_pending_audit_for_user(user_id)
        if self._routing is not None:
            agent_ids = await self._db.list_agents_for_user(user_id)
            for agent in agent_ids:
                await self._routing.disconnect_agent(
                    agent["agent_id"], WS_CLOSE_ADMIN_REVOKED, "user_purged"
                )
            await self._routing.disconnect_user(
                user_id, WS_CLOSE_ADMIN_REVOKED, "user_purged"
            )
        await self._db.delete_user(user_id)
        completed_at = datetime.now(timezone.utc).isoformat()
        if audit is not None:
            await self._db.update_deletion_audit_status(
                audit["id"], "completed", completed_at=completed_at
            )
        if self._email_sender is not None:
            task = asyncio.create_task(
                self._email_sender.send_account_deletion_completed(email)
            )
            _background_email_tasks.add(task)
            task.add_done_callback(_background_email_tasks.discard)
        logger.info(
            "account_deletion.completed user_id=%s email=%s",
            user_id, email,
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
