from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class CentralServerConfig:
    """Central relay server configuration loaded from environment variables."""

    jwt_secret_key: str
    dashboard_username: str
    dashboard_password_hash: str

    jwt_algorithm: str = "HS256"
    mobile_token_expire_minutes: int = 1440  # 24 hours
    mobile_refresh_expire_minutes: int = 10080  # 7 days
    dashboard_token_expire_minutes: int = 480  # 8 hours
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
        dashboard_user = os.getenv("DASHBOARD_USERNAME")
        if not dashboard_user:
            raise RuntimeError("DASHBOARD_USERNAME environment variable is required")
        dashboard_hash = os.getenv("DASHBOARD_PASSWORD_HASH")
        if not dashboard_hash:
            raise RuntimeError("DASHBOARD_PASSWORD_HASH environment variable is required")
        return cls(
            jwt_secret_key=secret,
            dashboard_username=dashboard_user,
            dashboard_password_hash=dashboard_hash,
            jwt_algorithm=os.getenv("JWT_ALGORITHM", "HS256"),
            mobile_token_expire_minutes=int(
                os.getenv("MOBILE_TOKEN_EXPIRE_MINUTES", "1440"),
            ),
            mobile_refresh_expire_minutes=int(
                os.getenv("MOBILE_REFRESH_EXPIRE_MINUTES", "10080"),
            ),
            dashboard_token_expire_minutes=int(
                os.getenv("DASHBOARD_TOKEN_EXPIRE_MINUTES", "480"),
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
