from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class CentralServerConfig:
    """Central relay server configuration loaded from environment variables."""

    jwt_secret_key: str

    # Admin seeding (optional — used on first startup to create admin user)
    admin_seed_email: str | None = None
    admin_seed_password: str | None = None

    # Google OAuth (optional — enables POST /api/auth/google)
    google_client_id: str | None = None

    jwt_algorithm: str = "HS256"
    # TTLs are in minutes. 0 = never expires (the token is issued without
    # an `exp` claim; refresh tokens are stored with a year-9999 sentinel
    # in `mobile_refresh_tokens.expires_at`). Chosen as the default because
    # the mobile app has to be re-paired by hand whenever tokens expire.
    mobile_token_expire_minutes: int = 0
    mobile_refresh_expire_minutes: int = 0
    dashboard_token_expire_minutes: int = 0
    db_path: str = "central_server.db"
    host: str = "0.0.0.0"
    port: int = 9000
    log_level: str = "INFO"
    log_format: str = "json"  # "json" | "console"
    pairing_token_ttl_seconds: int = 300  # 5 minutes
    ws_ping_interval: int = 20  # seconds, within 15-30s spec
    agent_disconnect_timeout: int = 30  # seconds

    @classmethod
    def from_env(cls) -> CentralServerConfig:
        """Build config from environment variables."""
        secret = os.getenv("JWT_SECRET_KEY")
        if not secret:
            raise RuntimeError("JWT_SECRET_KEY environment variable is required")
        return cls(
            jwt_secret_key=secret,
            admin_seed_email=os.getenv("ADMIN_SEED_EMAIL"),
            admin_seed_password=os.getenv("ADMIN_SEED_PASSWORD"),
            google_client_id=os.getenv("GOOGLE_CLIENT_ID"),
            jwt_algorithm=os.getenv("JWT_ALGORITHM", "HS256"),
            mobile_token_expire_minutes=int(
                os.getenv("MOBILE_TOKEN_EXPIRE_MINUTES", "0"),
            ),
            mobile_refresh_expire_minutes=int(
                os.getenv("MOBILE_REFRESH_EXPIRE_MINUTES", "0"),
            ),
            dashboard_token_expire_minutes=int(
                os.getenv("DASHBOARD_TOKEN_EXPIRE_MINUTES", "0"),
            ),
            db_path=os.getenv("DB_PATH", "central_server.db"),
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "9000")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            log_format=os.getenv("LOG_FORMAT", "json"),
            pairing_token_ttl_seconds=int(
                os.getenv("PAIRING_TOKEN_TTL_SECONDS", "300"),
            ),
            ws_ping_interval=int(os.getenv("WS_PING_INTERVAL", "20")),
            agent_disconnect_timeout=int(
                os.getenv("AGENT_DISCONNECT_TIMEOUT", "30"),
            ),
        )
