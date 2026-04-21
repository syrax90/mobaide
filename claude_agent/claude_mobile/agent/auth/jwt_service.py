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
    iat: datetime
    exp: datetime | None  # None when the token has no `exp` claim


class JwtService:
    """JWT token creation and validation using PyJWT.

    TTL semantics: 0 minutes means "never expires" — the `exp` claim is
    omitted entirely. PyJWT only validates `exp` when the claim is present,
    so this yields a non-expiring token without needing decode-side flags.
    """

    def __init__(
        self,
        secret_key: str,
        algorithm: str = "HS256",
        access_ttl_minutes: int = 0,
        refresh_ttl_minutes: int = 0,
    ) -> None:
        self._secret = secret_key
        self._algorithm = algorithm
        self._access_ttl_minutes = access_ttl_minutes
        self._refresh_ttl_minutes = refresh_ttl_minutes

    def create_access_token(self, username: str) -> str:
        return self._create(username, "access", self._access_ttl_minutes)

    def create_refresh_token(self, username: str) -> str:
        return self._create(username, "refresh", self._refresh_ttl_minutes)

    def _create(self, username: str, token_type: str, ttl_minutes: int) -> str:
        now = datetime.now(UTC)
        payload: dict = {
            "sub": username,
            "type": token_type,
            "iat": now,
        }
        if ttl_minutes > 0:
            payload["exp"] = now + timedelta(minutes=ttl_minutes)
        return jwt.encode(payload, self._secret, algorithm=self._algorithm)

    def decode_token(self, token: str) -> TokenPayload:
        """Decode and validate a JWT token. Raises jwt.InvalidTokenError on failure."""
        data = jwt.decode(token, self._secret, algorithms=[self._algorithm])
        exp_claim = data.get("exp")
        return TokenPayload(
            sub=data["sub"],
            token_type=data["type"],
            iat=datetime.fromtimestamp(data["iat"], tz=UTC),
            exp=(
                datetime.fromtimestamp(exp_claim, tz=UTC)
                if exp_claim is not None
                else None
            ),
        )
