from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass

_DEFAULT_SITE_BASE_URL = "https://mobaide.com"
_DEFAULT_WEB_APP_BASE_URL = "https://app.mobaide.com"

# Browser origins allowed to open relay WebSockets. Native clients (Android,
# iOS, the agent's Python client) send no Origin header and are always
# admitted; this list only gates browsers, where the header is
# attacker-independent. Loopback origins are structurally allowed at the
# check site (ws_origin.py) so Vite dev/preview servers need no env config.
_DEFAULT_WS_ALLOWED_ORIGINS = ("https://app.mobaide.com",)

# Scheme + host (+ optional port) only, no path. An origin is concatenated
# straight into canonical/OG/JSON-LD URLs, the sitemap and robots.txt, so a
# malformed value would emit relative links or malformed XML rather than fail
# loudly. Rejecting the shape here is cheaper than escaping at every use site.
_ORIGIN_RE = re.compile(r"^https?://[A-Za-z0-9.-]+(?::(?P<port>\d{1,5}))?$")


def _env_origin(name: str, default: str) -> str:
    """Read an origin env var, falling back to ``default`` if it is malformed.

    A typo in a marketing URL should not take the relay down, so this degrades
    to the default rather than raising. The trailing-slash strip runs before the
    match, so ``https://host/`` is accepted and normalised rather than rejected.

    The port is range-checked as well as shape-checked: ``\\d{1,5}`` alone would
    admit ``:99999``, which is not a usable port, so the guard would pass a value
    that cannot resolve.
    """
    raw = (os.getenv(name) or "").strip().rstrip("/")
    match = _ORIGIN_RE.fullmatch(raw)
    if match is None:
        return default
    port = match.group("port")
    if port is not None and not 1 <= int(port) <= 65535:
        return default
    return raw


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


_DIGITS_RE = re.compile(r"^\d+$")


def _env_digits(name: str) -> str | None:
    """Read a numeric id, tolerating the dashed ``123-456-7890`` spelling.

    Anything but digits and dashes counts as unset: the consumer names the
    missing variable at startup, which beats refusing to boot over an ads
    knob (same degrade-don't-raise stance as ``_env_origin``).
    """
    raw = (os.getenv(name) or "").strip().replace("-", "")
    return raw if _DIGITS_RE.fullmatch(raw) else None


def _env_int_clamped(name: str, default: int, low: int, high: int) -> int:
    """An integer knob that degrades to ``default`` on garbage and is clamped
    into ``[low, high]``."""
    raw = (os.getenv(name) or "").strip()
    if not raw.isascii() or not raw.isdigit():
        return default
    return max(low, min(high, int(raw)))


def normalize_origin(raw: str) -> str | None:
    """Canonicalize an origin for equality comparison, or None if malformed.

    String-based rather than ``_ORIGIN_RE`` because the WS gate must also
    canonicalize bracketed IPv6 loopback origins (``http://[::1]:5173``),
    which the hostname character class deliberately rejects for the
    marketing-URL fields. Scheme and host are case-folded per RFC 6454 §4
    and a default port is dropped, so ``HTTPS://App.Mobaide.com:443``
    compares equal to ``https://app.mobaide.com``. ``"null"`` (the opaque
    origin browsers send from sandboxed contexts) and non-http(s) schemes
    normalize to None — callers treat that as not-allowed.
    """
    candidate = raw.strip().rstrip("/").lower()
    scheme, sep, host = candidate.partition("://")
    if not sep or scheme not in ("http", "https") or not host or "/" in host:
        return None
    head, colon, port = host.rpartition(":")
    # isascii() guards the int(): str.isdigit() alone admits non-decimal
    # Unicode digits like "²" (valid obs-text in an HTTP header, so it can
    # arrive in an unauthenticated Origin) that int() rejects — without the
    # guard a crafted header would raise instead of falling through to the
    # never-matches-the-allow-list path.
    if colon and port.isascii() and port.isdigit():
        if not 1 <= int(port) <= 65535:
            return None
        if (scheme, port) in (("https", "443"), ("http", "80")):
            host = head
    return f"{scheme}://{host}"


def _env_origin_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Read a comma-separated origin list, dropping malformed elements.

    Follows ``_env_origin``'s degrade-don't-raise philosophy per element; an
    unset/blank var or an all-invalid list falls back to ``default``. Entries
    are stored pre-normalized so the per-handshake check is a plain tuple
    membership test.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    out: list[str] = []
    for part in raw.split(","):
        candidate = part.strip().rstrip("/")
        # _ORIGIN_RE keeps operator input to the same shape _env_origin
        # accepts (hostname origins only); runtime headers go through the
        # more permissive normalize_origin at the check site instead.
        if _ORIGIN_RE.fullmatch(candidate) is None:
            continue
        normalized = normalize_origin(candidate)
        if normalized is not None:
            out.append(normalized)
    return tuple(dict.fromkeys(out)) or default


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

    # CSP enforcement mode (CSP_REPORT_ONLY). False emits
    # `Content-Security-Policy`; True emits `Content-Security-Policy-Report-Only`,
    # which reports violations to the browser console without blocking anything.
    # Enforcing is the default on purpose — a report-only default would mean the
    # policy silently never protects anyone if nobody remembers to flip it. The
    # soak is one deploy with this set; see docs/deployment.md.
    csp_report_only: bool = False

    # Admin seeding (optional — only takes effect on first startup)
    admin_seed_email: str | None = None
    admin_seed_password: str | None = None

    # Google OAuth (optional)
    google_client_id: str | None = None

    # Sign in with Apple (optional) — the iOS app's bundle id, used as the
    # expected `aud` of Apple identity tokens on /api/auth/apple.
    apple_client_id: str | None = None

    # Sign in with Apple on the web (optional) — the Apple *Services ID*.
    # Browser tokens minted by Sign in with Apple JS carry the Services ID as
    # `aud`, never the native bundle id, so /api/auth/apple accepts both. The
    # Services ID must be grouped under `apple_client_id` as its primary App
    # ID in the Apple developer portal; that grouping is what makes Apple
    # issue the same `sub` on web as on iOS, so one person is one account.
    apple_web_client_id: str | None = None

    jwt_algorithm: str = "HS256"
    # TTLs are in minutes. The mobile pair rotates on every refresh, so an
    # active device never reaches the refresh TTL; one idle past it taps
    # the agent once to re-mint. The user ("dashboard") JWT is a hard
    # session lifetime on Android and iOS, which hold it without a refresh
    # path — hence 90 days rather than the web session's 15 minutes.
    #
    # 0 = never expires, an explicit opt-out for local development only
    # (the app logs a warning at startup). The zero sentinel is
    # translated in each consumer:
    #   * JWTs omit the `exp` claim (PyJWT only validates `exp` when
    #     present).
    #   * Refresh tokens are stored with a year-9999 sentinel in
    #     `mobile_refresh_tokens.expires_at`.
    #   * The dashboard session cookie is set without a `Max-Age`
    #     attribute, which per RFC 6265 §4.1.2 makes it a browser
    #     session cookie (cleared on browser close). Max-Age=0 would
    #     delete the cookie immediately (RFC 6265 §5.2.2).
    # User JWTs and refresh tokens minted while their TTL was 0 are aged
    # out once it is positive. Mobile access JWTs without `exp` are not.
    mobile_token_expire_minutes: int = 60
    mobile_refresh_expire_minutes: int = 43200  # 30 days
    dashboard_token_expire_minutes: int = 129600  # 90 days
    admin_dashboard_token_expire_minutes: int = 10080  # 7 days
    # TTLs for a *refreshable* user session — the opt-in a client requests
    # with `refreshable: true` on an auth call (see RefreshableSessionRequest).
    # These deliberately do not honour the 0-means-never sentinel above: a
    # session whose whole point is that the access token expires and rotates
    # would be pointless with an eternal token, so both are clamped to a
    # positive minimum by `from_env`.
    user_session_access_expire_minutes: int = 15
    user_session_refresh_expire_minutes: int = 43200  # 30 days
    # Whether the browser session cookies carry `Secure` — and therefore the
    # `__Host-` name prefix, which RFC 6265bis makes conditional on it. The two
    # travel together: a browser silently drops a whole `__Host-` cookie that
    # arrives without `Secure`, so `session_cookie.py` derives the names from
    # this flag rather than letting them drift apart. False exists only for
    # Safari, which refuses `Secure` cookies on `http://localhost` where
    # Chrome and Firefox accept them. Deliberately not derived from `env`:
    # MOBAIDE_ENV defaults to "production", so a deployment that sets it to
    # "development" would silently lose the prefix on a real host.
    user_session_cookie_secure: bool = True
    db_path: str = "central_server.db"
    host: str = "0.0.0.0"
    port: int = 9000
    log_level: str = "INFO"
    log_format: str = "json"  # "json" | "console"
    # NIST SP 800-63B sweet-spot for short-lived on-screen secrets: 60s is
    # too tight for scan latency over an SSH session; 300s is too generous
    # for an authenticator-bound code rendered to a terminal. Production
    # raises this to 600 via env (bootstrap .env): the web pairing link must
    # survive the human hop from a terminal QR to a browser on another
    # device, and the nonce is single-use so the wider window adds no
    # replay exposure.
    pairing_qr_token_ttl_seconds: int = 120
    # Initiate is anonymous and inserts a row per call. One unpaired agent
    # re-initiates once per TTL (6/h at the production 600 s), so 120/h
    # still fits ~20 agents behind one NAT while capping an agent stuck in
    # a retry loop. 0 disables the cap.
    pairing_initiate_ip_hourly_cap: int = 120
    # Reversed (device-code) pairing. Two windows, because the human step
    # sits between them: the code has to survive the walk from a browser to
    # a terminal, while the confirm prompt is answered by someone already
    # looking at the screen. The second window is also the TTL of the nonce
    # minted at redeem, so it bounds how long a declined pairing leaves the
    # agent waiting on its WebSocket.
    pairing_device_code_ttl_seconds: int = 600
    pairing_device_code_confirm_ttl_seconds: int = 300
    # Guessing defence for the ~34.6-bit code. Per-IP degrades to a single
    # shared bucket behind a proxy that does not forward the client address
    # (see FORWARDED_ALLOW_IPS in docs/deployment.md), so the global cap is
    # the one a distributed guesser cannot dodge.
    pairing_device_code_redeem_ip_hourly_cap: int = 30
    pairing_device_code_redeem_global_hourly_cap: int = 600
    pairing_device_code_redeem_ip_cooldown_seconds: int = 1
    # Minting has a cooldown but deliberately no hourly cap: a new code
    # supersedes the account's previous one, so a user who reloads the
    # pairing screen re-mints, and a cap would lock them out of pairing
    # entirely for an hour.
    pairing_device_code_mint_cooldown_seconds: int = 2
    # Refuse a claim whose relay signing key differs from the one the
    # account first paired against. Set false only to recover a deployment
    # that has legitimately rotated MANIFEST_SIGNING_* — see the trust-key
    # rotation runbook in docs/qr-pairing.md.
    trust_key_pin_enforced: bool = True
    ws_ping_interval: int = 20  # seconds, within 15-30s spec
    ws_allowed_origins: tuple[str, ...] = _DEFAULT_WS_ALLOWED_ORIGINS
    agent_disconnect_timeout: int = 30
    # How often in-memory agent heartbeats are batch-flushed to the DB in a
    # single transaction. Bounds `agents.last_seen_at` staleness for ONLINE
    # agents; offline timestamps stay exact (flushed on disconnect).
    heartbeat_flush_interval_seconds: int = 60
    # Cadence of the monitoring.sample capacity log line (loop lag, cgroup
    # RSS vs cap, fds, routing gauges, send-queue drop counters).
    monitoring_interval_seconds: int = 60

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
    # /register and /resend-verification are anonymous and mail a caller-chosen
    # address, so a per-email cap alone lets one client mail any number of
    # strangers. Shared by both endpoints; 0 disables the cap.
    email_verification_ip_hourly_cap: int = 20

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

    # CSRF for the form POST on `/portal/cancel-deletion`. Falls back to a key
    # derived from `jwt_secret_key` when unset so single-secret deployments work
    # out of the box; supply CSRF_SECRET_KEY explicitly to rotate independently.
    csrf_secret_key: str | None = None

    # Public base URL of the surviving `/portal/*` pages — used in outbound
    # emails for the cancel-deletion link. Falls back to `public_base_url`.
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

    # Origin the marketing site advertises in its canonical, Open Graph, and
    # JSON-LD URLs. Deliberately separate from `public_base_url`: that one is
    # the relay origin the clients dial (production points it at
    # api.mobaide.com), so reusing it here would publish the API host as the
    # site's canonical URL. Defaults to the apex the site is served from; a
    # malformed SITE_BASE_URL falls back to that default (see `_env_origin`).
    site_base_url: str = _DEFAULT_SITE_BASE_URL

    # Origin of the browser client the marketing site hands users to. Every
    # "Sign in" affordance on `mobaide.com` and every cross-link on the two
    # surviving `/portal/*` pages is built from it, so a local checkout can
    # point the whole site at a Vite dev server with one env var.
    web_app_base_url: str = _DEFAULT_WEB_APP_BASE_URL

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

    # Google Ads "Agent paired" conversion upload through the Data Manager
    # API. All optional: the uploader is enabled only when the five
    # credentials/ids are set, and a partial set logs `ads.misconfigured` at
    # startup and stays disabled. Customer and action ids are stored
    # digits-only; a value with anything but digits and dashes is treated as
    # unset (see `_env_digits`). `login_customer_id` is the manager (MCC)
    # account, needed only when the OAuth user reaches the Ads account
    # through one. No developer token: the Data Manager API has none.
    google_ads_customer_id: str | None = None
    google_ads_login_customer_id: str | None = None
    google_ads_oauth_client_id: str | None = None
    google_ads_oauth_client_secret: str | None = None
    google_ads_oauth_refresh_token: str | None = None
    google_ads_conversion_action_id: str | None = None
    # The conversion action's click-through window in days: a click older
    # than this is not attributable, so the uploader tries the hashed-email
    # fallback instead. Clamped to the 90-day retention the policy promises.
    google_ads_click_window_days: int = 90

    @property
    def google_ads_missing(self) -> tuple[str, ...]:
        """Env names of the required Google Ads settings that are unset."""
        pairs = (
            ("GOOGLE_ADS_CUSTOMER_ID", self.google_ads_customer_id),
            ("GOOGLE_ADS_OAUTH_CLIENT_ID", self.google_ads_oauth_client_id),
            ("GOOGLE_ADS_OAUTH_CLIENT_SECRET", self.google_ads_oauth_client_secret),
            ("GOOGLE_ADS_OAUTH_REFRESH_TOKEN", self.google_ads_oauth_refresh_token),
            ("GOOGLE_ADS_CONVERSION_ACTION_ID", self.google_ads_conversion_action_id),
        )
        return tuple(name for name, value in pairs if not value)

    @property
    def google_ads_enabled(self) -> bool:
        return not self.google_ads_missing

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
            csp_report_only=_env_bool("CSP_REPORT_ONLY", False),
            admin_seed_email=os.getenv("ADMIN_SEED_EMAIL"),
            admin_seed_password=os.getenv("ADMIN_SEED_PASSWORD"),
            google_client_id=os.getenv("GOOGLE_CLIENT_ID"),
            apple_client_id=os.getenv("APPLE_CLIENT_ID"),
            apple_web_client_id=os.getenv("APPLE_WEB_CLIENT_ID"),
            jwt_algorithm=os.getenv("JWT_ALGORITHM", "HS256"),
            mobile_token_expire_minutes=int(
                os.getenv("MOBILE_TOKEN_EXPIRE_MINUTES", "60"),
            ),
            mobile_refresh_expire_minutes=int(
                os.getenv("MOBILE_REFRESH_EXPIRE_MINUTES", "43200"),
            ),
            dashboard_token_expire_minutes=int(
                os.getenv("DASHBOARD_TOKEN_EXPIRE_MINUTES", "129600"),
            ),
            admin_dashboard_token_expire_minutes=int(
                os.getenv("ADMIN_DASHBOARD_TOKEN_EXPIRE_MINUTES", "10080"),
            ),
            user_session_access_expire_minutes=max(
                1,
                int(os.getenv("USER_SESSION_ACCESS_EXPIRE_MINUTES", "15")),
            ),
            user_session_refresh_expire_minutes=max(
                1,
                int(os.getenv("USER_SESSION_REFRESH_EXPIRE_MINUTES", "43200")),
            ),
            user_session_cookie_secure=_env_bool(
                "USER_SESSION_COOKIE_SECURE", True,
            ),
            db_path=_storage_path(
                "DB_PATH", "central_server.db", "central_server.db",
            ),
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "9000")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            log_format=os.getenv("LOG_FORMAT", "json"),
            pairing_qr_token_ttl_seconds=int(
                os.getenv("PAIRING_QR_TOKEN_TTL_SECONDS", "120"),
            ),
            pairing_initiate_ip_hourly_cap=int(
                os.getenv("PAIRING_INITIATE_IP_HOURLY_CAP", "120"),
            ),
            pairing_device_code_ttl_seconds=int(
                os.getenv("PAIRING_DEVICE_CODE_TTL_SECONDS", "600"),
            ),
            pairing_device_code_confirm_ttl_seconds=int(
                os.getenv("PAIRING_DEVICE_CODE_CONFIRM_TTL_SECONDS", "300"),
            ),
            pairing_device_code_redeem_ip_hourly_cap=int(
                os.getenv("PAIRING_DEVICE_CODE_REDEEM_IP_HOURLY_CAP", "30"),
            ),
            pairing_device_code_redeem_global_hourly_cap=int(
                os.getenv(
                    "PAIRING_DEVICE_CODE_REDEEM_GLOBAL_HOURLY_CAP", "600"
                ),
            ),
            pairing_device_code_redeem_ip_cooldown_seconds=int(
                os.getenv(
                    "PAIRING_DEVICE_CODE_REDEEM_IP_COOLDOWN_SECONDS", "1"
                ),
            ),
            pairing_device_code_mint_cooldown_seconds=int(
                os.getenv("PAIRING_DEVICE_CODE_MINT_COOLDOWN_SECONDS", "2"),
            ),
            trust_key_pin_enforced=_env_bool("TRUST_KEY_PIN_ENFORCED", True),
            ws_ping_interval=int(os.getenv("WS_PING_INTERVAL", "20")),
            ws_allowed_origins=_env_origin_list(
                "WS_ALLOWED_ORIGINS", _DEFAULT_WS_ALLOWED_ORIGINS,
            ),
            agent_disconnect_timeout=int(
                os.getenv("AGENT_DISCONNECT_TIMEOUT", "30"),
            ),
            heartbeat_flush_interval_seconds=int(
                os.getenv("HEARTBEAT_FLUSH_INTERVAL_SECONDS", "60"),
            ),
            monitoring_interval_seconds=int(
                os.getenv("MONITORING_INTERVAL_SECONDS", "60"),
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
            email_verification_ip_hourly_cap=int(
                os.getenv("EMAIL_VERIFICATION_IP_HOURLY_CAP", "20"),
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
            site_base_url=_env_origin("SITE_BASE_URL", _DEFAULT_SITE_BASE_URL),
            web_app_base_url=_env_origin(
                "WEB_APP_BASE_URL", _DEFAULT_WEB_APP_BASE_URL
            ),
            apns_key_path=os.getenv("APNS_KEY_PATH") or None,
            apns_key_id=os.getenv("APNS_KEY_ID") or None,
            apns_team_id=os.getenv("APNS_TEAM_ID") or None,
            apns_topic=os.getenv("APNS_TOPIC") or None,
            apns_use_sandbox=_env_bool("APNS_USE_SANDBOX", False),
            google_ads_customer_id=_env_digits("GOOGLE_ADS_CUSTOMER_ID"),
            google_ads_login_customer_id=_env_digits("GOOGLE_ADS_LOGIN_CUSTOMER_ID"),
            google_ads_oauth_client_id=os.getenv("GOOGLE_ADS_OAUTH_CLIENT_ID") or None,
            google_ads_oauth_client_secret=(
                os.getenv("GOOGLE_ADS_OAUTH_CLIENT_SECRET") or None
            ),
            google_ads_oauth_refresh_token=(
                os.getenv("GOOGLE_ADS_OAUTH_REFRESH_TOKEN") or None
            ),
            google_ads_conversion_action_id=_env_digits(
                "GOOGLE_ADS_CONVERSION_ACTION_ID"
            ),
            google_ads_click_window_days=_env_int_clamped(
                "GOOGLE_ADS_CLICK_WINDOW_DAYS", 90, 1, 90
            ),
        )
