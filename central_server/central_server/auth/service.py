"""Authentication service for the Central Server.

Three distinct auth paths:
1. Agent tokens — long-lived, bcrypt-hashed in DB
2. Mobile session tokens — JWT issued after QR pairing, with refresh rotation
3. Dashboard credentials — owner account (bcrypt hash in config)
"""
from __future__ import annotations

import secrets
import time
from typing import TYPE_CHECKING

import bcrypt
import jwt

from .models import MobileTokenPayload

if TYPE_CHECKING:
    from ..config import CentralServerConfig
    from ..db.database import Database


class AuthService:
    def __init__(self, config: CentralServerConfig, db: Database) -> None:
        self._config = config
        self._db = db

    # -- Agent auth ------------------------------------------------------------

    async def verify_agent_token(self, agent_id: str, raw_token: str) -> bool:
        return await self._db.verify_agent_token(agent_id, raw_token)

    # -- Mobile JWT ------------------------------------------------------------

    async def create_mobile_tokens(
        self, mobile_device_id: str, agent_id: str
    ) -> tuple[str, str]:
        """Create access + refresh token pair for a mobile device."""
        access = self._create_jwt(
            sub=mobile_device_id,
            agent_id=agent_id,
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
        """
        result = await self._db.validate_and_consume_refresh(refresh_token)
        if result is None:
            return None
        mobile_device_id, agent_id = result
        return await self.create_mobile_tokens(mobile_device_id, agent_id)

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
            token_type=payload["type"],
            exp=payload["exp"],
            iat=payload["iat"],
        )

    # -- Dashboard auth --------------------------------------------------------

    def verify_dashboard_credentials(
        self, username: str, password: str
    ) -> bool:
        if username != self._config.dashboard_username:
            return False
        return bcrypt.checkpw(
            password.encode(), self._config.dashboard_password_hash.encode()
        )

    def create_dashboard_token(self, username: str) -> str:
        return self._create_jwt(
            sub=username,
            token_type="dashboard",
            expire_minutes=self._config.dashboard_token_expire_minutes,
        )

    def decode_dashboard_token(self, token: str) -> str:
        """Decode a dashboard JWT. Returns username. Raises jwt.InvalidTokenError."""
        payload = jwt.decode(
            token,
            self._config.jwt_secret_key,
            algorithms=[self._config.jwt_algorithm],
        )
        if payload.get("type") != "dashboard":
            raise jwt.InvalidTokenError("Expected dashboard token")
        return payload["sub"]

    # -- Internal --------------------------------------------------------------

    def _create_jwt(
        self,
        *,
        sub: str,
        token_type: str,
        expire_minutes: int,
        agent_id: str | None = None,
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
        return jwt.encode(
            payload,
            self._config.jwt_secret_key,
            algorithm=self._config.jwt_algorithm,
        )
