from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from dataclasses import dataclass

import jwt

logger = logging.getLogger(__name__)


@dataclass
class TokenPayload:
    sub: str
    token_type: str  # "access" or "refresh"
    exp: datetime
    iat: datetime


class JwtService:
    """JWT token creation and validation using PyJWT."""

    def __init__(
        self,
        secret_key: str,
        algorithm: str = "HS256",
        access_ttl_minutes: int = 30,
        refresh_ttl_minutes: int = 1440,
    ) -> None:
        self._secret = secret_key
        self._algorithm = algorithm
        self._access_ttl = timedelta(minutes=access_ttl_minutes)
        self._refresh_ttl = timedelta(minutes=refresh_ttl_minutes)

    def create_access_token(self, username: str) -> str:
        now = datetime.now(UTC)
        payload = {
            "sub": username,
            "type": "access",
            "iat": now,
            "exp": now + self._access_ttl,
        }
        return jwt.encode(payload, self._secret, algorithm=self._algorithm)

    def create_refresh_token(self, username: str) -> str:
        now = datetime.now(UTC)
        payload = {
            "sub": username,
            "type": "refresh",
            "iat": now,
            "exp": now + self._refresh_ttl,
        }
        return jwt.encode(payload, self._secret, algorithm=self._algorithm)

    def decode_token(self, token: str) -> TokenPayload:
        """Decode and validate a JWT token. Raises jwt.InvalidTokenError on failure."""
        data = jwt.decode(token, self._secret, algorithms=[self._algorithm])
        return TokenPayload(
            sub=data["sub"],
            token_type=data["type"],
            exp=datetime.fromtimestamp(data["exp"], tz=UTC),
            iat=datetime.fromtimestamp(data["iat"], tz=UTC),
        )
