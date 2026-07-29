from __future__ import annotations

import os
import tempfile
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

    # Deployment environment selector (MOBAIDE_ENV). "production" (the
    # default) keeps every other field at its production default. "test"
    # opts a disposable instance into the in-process email sink (no SMTP) and
    # temp-dir storage; see from_env() and auth.email.build_email_sender().
    env: str = "production"

    # OpenAPI exposure (ENABLE_API_DOCS). Default-deny: the schema enumerates
    # every route, parameter and model — including the admin dashboard and the
    # release-pipeline endpoint — so it is a recon map, not a public asset.
    # False leaves /docs, /redoc and /openapi.json unregistered (hard 404).
    # True registers them behind an admin session; see app._register_api_docs.
    enable_api_docs: bool = False

    # Admin seeding (optional — only takes effect on first startup)
    admin_seed_email: str | None = None
    admin_seed_password: str | None = None

    # Google OAuth (optional)
    google_client_id: str | None = None

    # Sign in with Apple (optional) — the iOS app's bundle id, used as the
    # expected `aud` of Apple identity tokens on /api/auth/apple.
    apple_client_id: str | None = None

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
    # NIST SP 800-63B sweet-spot for short-lived on-screen secrets: 60s is
    # too tight for scan latency over an SSH session; 300s is too generous
    # for an authenticator-bound code rendered to a terminal.
    pairing_qr_token_ttl_seconds: int = 120
    ws_ping_interval: int = 20  # seconds, within 15-30s spec
    agent_disconnect_timeout: int = 30
    # How often in-memory agent heartbeats are batch-flushed to the DB in a
    # single transaction. Bounds `agents.last_seen_at` staleness for ONLINE
    # agents; offline timestamps stay exact (flushed on disconnect).
    heartbeat_flush_interval_seconds: int = 60

    # SMTP (optional unless email_verification_required=True)
    smtp_host: str = "localhost"
    smtp_port: int = 1025  # Mailpit default
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = False
    smtp_starttls: bool = False
    smtp_from_address: str = "no-reply@mobaide.local"
    smtp_from_name: str = "Mobaide"
    smtp_from_domain: str = "mobaide.local"

    email_verification_required: bool = True
    email_verification_ttl_minutes: int = 15
    email_verification_max_attempts: int = 5
    email_verification_resend_cooldown_seconds: int = 60
    email_verification_resend_hourly_cap: int = 3

    # Password reset (OTP-in-app flow; see docs/password-reset-workflow.md).
    # TTL chosen at 30 min per NIST SP 800-63-4 guidance (link/OTP ≤60 min).
    # Per-email cooldown matches verification cooldown for UX consistency.
    # Per-IP cap is generous (10/hr) because legitimate operators sharing an
    # office NAT could otherwise stomp each other.
    password_reset_ttl_minutes: int = 30
    password_reset_max_attempts: int = 5
    password_reset_email_cooldown_seconds: int = 60
    password_reset_email_hourly_cap: int = 3
    password_reset_ip_hourly_cap: int = 10

    # Account self-deletion. Industry-norm soft-delete grace period
    # (X 30d, Spotify 7d, Pinterest 14d). Sweeper interval is a balance
    # between purge-latency SLA and steady-state load — 1h is plenty given
    # the 14-day grace window.
    account_deletion_grace_days: int = 14
    deletion_sweeper_interval_seconds: int = 3600
    # TTL on the single-use cancel-deletion link emailed to the user. Capped
    # at the grace window — a link is useless past final purge.
    account_deletion_cancel_token_ttl_hours: int = 24

    # End-user web portal (sibling of the admin dashboard at /portal/*).
    # Separate cookie name from `dashboard_token` to avoid namespace collision
    # (non-admin must never gain a cookie in the admin namespace).
    portal_token_expire_minutes: int = 0

    # CSRF for cookie-authenticated portal endpoints (HTMX). Falls back to
    # a key derived from `jwt_secret_key` when unset so single-secret
    # deployments work out of the box; supply CSRF_SECRET_KEY explicitly to
    # rotate independently.
    csrf_secret_key: str | None = None

    # Public base URL of the portal — used in outbound emails for the
    # cancel-deletion link. Falls back to `public_base_url` when unset.
    portal_base_url: str | None = None

    # Release / manifest publishing
    release_auth_token: str | None = None
    manifest_signing_public_key_b64: str | None = None
    # Optional Ed25519 private key paired with ``manifest_signing_public_key_b64``.
    # Required for the QR pairing flow: the central server signs the
    # WS-push payload that delivers ``agent_id``/``agent_token`` to the agent,
    # and the agent verifies with its pinned public key. When this is unset,
    # ``POST /api/pairing/initiate`` returns 503 — the trust-key endpoint at
    # ``/api/v1/agent/trust_key`` follows the same fail-closed pattern.
    # Manifest publishing itself remains driven by the external release
    # pipeline and does NOT consume this private key.
    manifest_signing_private_key_b64: str | None = None
    manifest_store_path: str = "data/agent_manifest.json"
    # Public base URL the agent and mobile use to talk to this server.
    # When set, ``POST /api/pairing/initiate`` embeds it in the QR payload.
    # When unset, falls back to the Host header from the initiate request.
    public_base_url: str | None = None

    # APNs push. All optional: when any of the four is unset the
    # push dispatcher is disabled and attention delivery falls back to the
    # user-events WebSocket only. `apns_use_sandbox` picks the APNs host for
    # device rows that predate per-device environments (push_environment
    # NULL); devices registered via the push-token endpoint carry their own.
    apns_key_path: str | None = None
    apns_key_id: str | None = None
    apns_team_id: str | None = None
    apns_topic: str | None = None
    apns_use_sandbox: bool = False

    @property
    def is_test_mode(self) -> bool:
        """True when MOBAIDE_ENV=test selects the disposable test profile."""
        return self.env == "test"

    @classmethod
    def from_env(cls) -> CentralServerConfig:
        """Build config from environment variables."""
        secret = os.getenv("JWT_SECRET_KEY")
        if not secret:
            raise RuntimeError("JWT_SECRET_KEY environment variable is required")

        env = os.getenv("MOBAIDE_ENV", "production")
        # In test mode, default DB / manifest storage to a throwaway temp dir
        # so a disposable MOBAIDE_ENV=test instance never writes
        # central_server.db* / data/ into the CWD. An explicit DB_PATH /
        # MANIFEST_STORE_PATH still wins (the E2E harness supplies them), and
        # production behaviour is unchanged.
        test_storage: list[str] = []

        def _storage_path(env_var: str, test_name: str, prod_default: str) -> str:
            explicit = os.getenv(env_var)
            if explicit is not None:
                return explicit
            if env != "test":
                return prod_default
            if not test_storage:
                test_storage.append(tempfile.mkdtemp(prefix="mobaide-test-"))
            return os.path.join(test_storage[0], test_name)

        return cls(
            jwt_secret_key=secret,
            env=env,
            enable_api_docs=_env_bool("ENABLE_API_DOCS", False),
            admin_seed_email=os.getenv("ADMIN_SEED_EMAIL"),
            admin_seed_password=os.getenv("ADMIN_SEED_PASSWORD"),
            google_client_id=os.getenv("GOOGLE_CLIENT_ID"),
            apple_client_id=os.getenv("APPLE_CLIENT_ID"),
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
            db_path=_storage_path(
                "DB_PATH", "central_server.db", "central_server.db",
            ),
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "9000")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            log_format=os.getenv("LOG_FORMAT", "json"),
            pairing_token_ttl_seconds=int(
                os.getenv("PAIRING_TOKEN_TTL_SECONDS", "300"),
            ),
            pairing_qr_token_ttl_seconds=int(
                os.getenv("PAIRING_QR_TOKEN_TTL_SECONDS", "120"),
            ),
            ws_ping_interval=int(os.getenv("WS_PING_INTERVAL", "20")),
            agent_disconnect_timeout=int(
                os.getenv("AGENT_DISCONNECT_TIMEOUT", "30"),
            ),
            heartbeat_flush_interval_seconds=int(
                os.getenv("HEARTBEAT_FLUSH_INTERVAL_SECONDS", "60"),
            ),
            smtp_host=os.getenv("SMTP_HOST", "localhost"),
            smtp_port=int(os.getenv("SMTP_PORT", "1025")),
            smtp_username=os.getenv("SMTP_USERNAME") or None,
            smtp_password=os.getenv("SMTP_PASSWORD") or None,
            smtp_use_tls=_env_bool("SMTP_USE_TLS", False),
            smtp_starttls=_env_bool("SMTP_STARTTLS", False),
            smtp_from_address=os.getenv(
                "SMTP_FROM_ADDRESS", "no-reply@mobaide.local"
            ),
            smtp_from_name=os.getenv("SMTP_FROM_NAME", "Mobaide"),
            smtp_from_domain=os.getenv("SMTP_FROM_DOMAIN", "mobaide.local"),
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
            password_reset_ttl_minutes=int(
                os.getenv("PASSWORD_RESET_TTL_MINUTES", "30"),
            ),
            password_reset_max_attempts=int(
                os.getenv("PASSWORD_RESET_MAX_ATTEMPTS", "5"),
            ),
            password_reset_email_cooldown_seconds=int(
                os.getenv("PASSWORD_RESET_EMAIL_COOLDOWN_SECONDS", "60"),
            ),
            password_reset_email_hourly_cap=int(
                os.getenv("PASSWORD_RESET_EMAIL_HOURLY_CAP", "3"),
            ),
            password_reset_ip_hourly_cap=int(
                os.getenv("PASSWORD_RESET_IP_HOURLY_CAP", "10"),
            ),
            account_deletion_grace_days=int(
                os.getenv("ACCOUNT_DELETION_GRACE_DAYS", "14"),
            ),
            deletion_sweeper_interval_seconds=int(
                os.getenv("DELETION_SWEEPER_INTERVAL_SECONDS", "3600"),
            ),
            account_deletion_cancel_token_ttl_hours=int(
                os.getenv("ACCOUNT_DELETION_CANCEL_TOKEN_TTL_HOURS", "24"),
            ),
            portal_token_expire_minutes=int(
                os.getenv("PORTAL_TOKEN_EXPIRE_MINUTES", "0"),
            ),
            csrf_secret_key=os.getenv("CSRF_SECRET_KEY") or None,
            portal_base_url=os.getenv("PORTAL_BASE_URL") or None,
            release_auth_token=os.getenv("RELEASE_AUTH_TOKEN") or None,
            manifest_signing_public_key_b64=(
                os.getenv("MANIFEST_SIGNING_PUBLIC_KEY") or None
            ),
            manifest_signing_private_key_b64=(
                os.getenv("MANIFEST_SIGNING_PRIVATE_KEY") or None
            ),
            manifest_store_path=_storage_path(
                "MANIFEST_STORE_PATH",
                "agent_manifest.json",
                "data/agent_manifest.json",
            ),
            public_base_url=os.getenv("PUBLIC_BASE_URL") or None,
            apns_key_path=os.getenv("APNS_KEY_PATH") or None,
            apns_key_id=os.getenv("APNS_KEY_ID") or None,
            apns_team_id=os.getenv("APNS_TEAM_ID") or None,
            apns_topic=os.getenv("APNS_TOPIC") or None,
            apns_use_sandbox=_env_bool("APNS_USE_SANDBOX", False),
        )
