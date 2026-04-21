from __future__ import annotations

import os
from dataclasses import dataclass

from claude_mobile.linear.config import AutoScanConfig
from claude_mobile.relay.config import RelayConfig


@dataclass(frozen=True)
class AgentConfig:
    """Agent configuration loaded from environment variables."""

    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    # TTLs are in minutes. 0 = never expires (the JWT is issued without an
    # `exp` claim). The default is 0 because re-entering credentials on a
    # phone is painful; set a positive value to opt back into expiration.
    jwt_access_token_expire_minutes: int = 0
    jwt_refresh_token_expire_minutes: int = 0
    user_store_path: str = "users.json"
    projects_store_path: str = "projects.json"
    host: str = "0.0.0.0"
    port: int = 8000
    max_sessions: int = 16
    session_disconnect_timeout: int = 300  # 5 minutes
    log_level: str = "INFO"
    log_format: str = "json"  # "json" | "console"
    audit_log_path: str | None = None
    log_sample_ws: bool = False
    auto_scan_config: AutoScanConfig | None = None
    relay_config: RelayConfig | None = None

    @classmethod
    def from_env(cls) -> AgentConfig:
        """Build config from environment variables. Requires JWT_SECRET_KEY."""
        secret = os.getenv("JWT_SECRET_KEY")
        if not secret:
            raise RuntimeError("JWT_SECRET_KEY environment variable is required")
        return cls(
            jwt_secret_key=secret,
            jwt_algorithm=os.getenv("JWT_ALGORITHM", "HS256"),
            jwt_access_token_expire_minutes=int(
                os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "0"),
            ),
            jwt_refresh_token_expire_minutes=int(
                os.getenv("JWT_REFRESH_TOKEN_EXPIRE_MINUTES", "0"),
            ),
            user_store_path=os.getenv("USER_STORE_PATH", "users.json"),
            projects_store_path=os.getenv("PROJECTS_STORE_PATH", "projects.json"),
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "8000")),
            max_sessions=int(os.getenv("MAX_SESSIONS", "16")),
            session_disconnect_timeout=int(
                os.getenv("SESSION_DISCONNECT_TIMEOUT", "300"),
            ),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            log_format=os.getenv("LOG_FORMAT", "json"),
            audit_log_path=os.getenv("AUDIT_LOG_PATH") or None,
            log_sample_ws=os.getenv("LOG_SAMPLE_WS", "").lower() in ("1", "true", "yes"),
            auto_scan_config=AutoScanConfig.from_env(),
            relay_config=RelayConfig.from_env(),
        )
