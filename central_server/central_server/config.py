from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class CentralServerConfig:
    """Central relay server configuration loaded from environment variables."""

    jwt_secret_key: str

    # Admin seeding (optional — only takes effect on first startup)
    admin_seed_email: str | None = None
    admin_seed_password: str | None = None

    # Google OAuth (optional)
    google_client_id: str | None = None

    jwt_algorithm: str = "HS256"
    # TTLs are in minutes. 0 = never expires. The zero sentinel is
    # translated in each consumer:
    #   * JWTs omit the `exp` claim (PyJWT only validates `exp` when
    #     present).
    #   * Refresh tokens are stored with a year-9999 sentinel in
    #     `mobile_refresh_tokens.expires_at`.
    #   * The dashboard session cookie is set without a `Max-Age`
    #     attribute, which per RFC 6265 §4.1.2 makes it a browser
    #     session cookie (cleared on browser close). Max-Age=0 would
    #     delete the cookie immediately (RFC 6265 §5.2.2).
    # Chosen as the default because the mobile app has to be re-paired
    # by hand whenever tokens expire.
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
    agent_disconnect_timeout: int = 30

    # SMTP (optional unless email_verification_required=True)
    smtp_host: str = "localhost"
    smtp_port: int = 1025  # Mailpit default
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = False
    smtp_starttls: bool = False
    smtp_from_address: str = "no-reply@claude-mobile.local"
    smtp_from_name: str = "Claude Mobile"
    smtp_from_domain: str = "claude-mobile.local"

    email_verification_required: bool = True
    email_verification_ttl_minutes: int = 15
    email_verification_max_attempts: int = 5
    email_verification_resend_cooldown_seconds: int = 60
    email_verification_resend_hourly_cap: int = 3

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
            smtp_host=os.getenv("SMTP_HOST", "localhost"),
            smtp_port=int(os.getenv("SMTP_PORT", "1025")),
            smtp_username=os.getenv("SMTP_USERNAME") or None,
            smtp_password=os.getenv("SMTP_PASSWORD") or None,
            smtp_use_tls=_env_bool("SMTP_USE_TLS", False),
            smtp_starttls=_env_bool("SMTP_STARTTLS", False),
            smtp_from_address=os.getenv(
                "SMTP_FROM_ADDRESS", "no-reply@claude-mobile.local"
            ),
            smtp_from_name=os.getenv("SMTP_FROM_NAME", "Claude Mobile"),
            smtp_from_domain=os.getenv("SMTP_FROM_DOMAIN", "claude-mobile.local"),
            email_verification_required=_env_bool(
                "EMAIL_VERIFICATION_REQUIRED", True
            ),
            email_verification_ttl_minutes=int(
                os.getenv("EMAIL_VERIFICATION_TTL_MINUTES", "15"),
            ),
            email_verification_max_attempts=int(
                os.getenv("EMAIL_VERIFICATION_MAX_ATTEMPTS", "5"),
            ),
            email_verification_resend_cooldown_seconds=int(
                os.getenv("EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS", "60"),
            ),
            email_verification_resend_hourly_cap=int(
                os.getenv("EMAIL_VERIFICATION_RESEND_HOURLY_CAP", "3"),
            ),
        )
