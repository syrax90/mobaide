"""Authentication service for the Central Server.

Four distinct auth paths:
1. Agent tokens — long-lived, SHA-256 hashed in DB, owned by users
2. Mobile session tokens — JWT with user_id claim, refresh rotation
3. User accounts — email/password (Argon2id) or Google OAuth, DB-backed
4. Dashboard sessions — cookie-based JWT with user_id + role claims
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import TYPE_CHECKING

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from .models import MobileTokenPayload, UserTokenPayload

if TYPE_CHECKING:
    from ..config import CentralServerConfig
    from ..db.database import Database

logger = logging.getLogger(__name__)

_ph = PasswordHasher()  # Thread-safe singleton


class AuthService:
    def __init__(self, config: CentralServerConfig, db: Database) -> None:
        self._config = config
        self._db = db

    # -- Password hashing (Argon2id, non-blocking) ----------------------------

    async def _hash_password(self, password: str) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _ph.hash, password)

    async def _verify_password(self, hash: str, password: str) -> bool:
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, _ph.verify, hash, password)
        except VerifyMismatchError:
            return False

    # -- User registration & login --------------------------------------------

    async def register_user(self, email: str, password: str) -> tuple[str, str]:
        """Register a new user with email+password.

        Returns (user_id, jwt_token).
        Raises ValueError if email already taken.
        """
        existing = await self._db.get_user_by_email(email)
        if existing is not None:
            raise ValueError("Email already registered")

        password_hash = await self._hash_password(password)
        user_id = await self._db.create_user(
            email=email,
            password_hash=password_hash,
            role="viewer",
        )
        token = self._create_user_token(user_id, email, "viewer")
        return user_id, token

    async def login_user(self, email: str, password: str) -> tuple[str, str, str]:
        """Authenticate a user by email+password.

        Returns (user_id, role, jwt_token).
        Raises ValueError if credentials are invalid.
        """
        user = await self._db.get_user_by_email(email)
        if user is None or user["password_hash"] is None:
            raise ValueError("Invalid email or password")

        if not await self._verify_password(user["password_hash"], password):
            raise ValueError("Invalid email or password")

        token = self._create_user_token(user["id"], user["email"], user["role"])
        return user["id"], user["role"], token

    # -- Google OAuth ---------------------------------------------------------

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

        # 1. Check by google_sub first (exact match)
        user = await self._db.get_user_by_google_sub(google_sub)
        if user is not None:
            token = self._create_user_token(user["id"], user["email"], user["role"])
            return user["id"], user["role"], token, False

        # 2. Check by email (account linking) — only if email_verified
        if email_verified:
            user = await self._db.get_user_by_email(email)
            if user is not None:
                await self._db.link_google_to_user(user["id"], google_sub)
                token = self._create_user_token(
                    user["id"], user["email"], user["role"]
                )
                return user["id"], user["role"], token, False

        # 3. Check if email already taken (edge case: unverified Google email)
        existing = await self._db.get_user_by_email(email)
        if existing is not None:
            raise ValueError("Email already registered with a different account")

        # 4. New user
        user_id = await self._db.create_user(
            email=email,
            google_sub=google_sub,
            role="viewer",
        )
        token = self._create_user_token(user_id, email, "viewer")
        return user_id, "viewer", token, True

    # -- Admin seeding --------------------------------------------------------

    async def seed_admin_user(self) -> None:
        """Seed the admin user from config env vars if no admin exists.

        Called during app startup. Idempotent. If a user with the seed email
        already exists, we leave it untouched — we do not flip an existing
        viewer into admin because that viewer may own agents (admins must
        not own agents; see migration 0003).
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
        )
        logger.info(
            "admin_user.seeded email=%s", self._config.admin_seed_email
        )

    # -- Agent auth ------------------------------------------------------------

    async def verify_agent_token(self, agent_id: str, raw_token: str) -> bool:
        return await self._db.verify_agent_token(agent_id, raw_token)

    # -- Mobile JWT ------------------------------------------------------------

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
        # Look up the agent owner to include user_id in the refreshed token
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
            exp=payload["exp"],
            iat=payload["iat"],
        )

    # -- Dashboard auth --------------------------------------------------------

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
            exp=payload["exp"],
            iat=payload["iat"],
        )

    # -- Internal --------------------------------------------------------------

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
            "exp": now + expire_minutes * 60,
        }
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
