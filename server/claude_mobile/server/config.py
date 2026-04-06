from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ServerConfig:
    """Server configuration loaded from environment variables."""

    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_minutes: int = 1440  # 24 hours
    user_store_path: str = "users.json"
    host: str = "0.0.0.0"
    port: int = 8000
    max_sessions: int = 16
    session_disconnect_timeout: int = 300  # 5 minutes
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> ServerConfig:
        """Build config from environment variables. Requires JWT_SECRET_KEY."""
        secret = os.getenv("JWT_SECRET_KEY")
        if not secret:
            raise RuntimeError("JWT_SECRET_KEY environment variable is required")
        return cls(
            jwt_secret_key=secret,
            jwt_algorithm=os.getenv("JWT_ALGORITHM", "HS256"),
            jwt_access_token_expire_minutes=int(
                os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30"),
            ),
            jwt_refresh_token_expire_minutes=int(
                os.getenv("JWT_REFRESH_TOKEN_EXPIRE_MINUTES", "1440"),
            ),
            user_store_path=os.getenv("USER_STORE_PATH", "users.json"),
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "8000")),
            max_sessions=int(os.getenv("MAX_SESSIONS", "16")),
            session_disconnect_timeout=int(
                os.getenv("SESSION_DISCONNECT_TIMEOUT", "300"),
            ),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )
