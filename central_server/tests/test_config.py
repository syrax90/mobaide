"""Tests for CentralServerConfig."""
from __future__ import annotations

import pytest

from central_server.config import CentralServerConfig


def test_from_env_with_required_vars(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    config = CentralServerConfig.from_env()
    assert config.jwt_secret_key == "test-secret"
    assert config.admin_seed_email is None
    assert config.admin_seed_password is None
    assert config.google_client_id is None
    assert config.port == 9000


def test_from_env_missing_secret(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        CentralServerConfig.from_env()


def test_from_env_with_admin_seed(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ADMIN_SEED_EMAIL", "admin@example.com")
    monkeypatch.setenv("ADMIN_SEED_PASSWORD", "secretpass")
    config = CentralServerConfig.from_env()
    assert config.admin_seed_email == "admin@example.com"
    assert config.admin_seed_password == "secretpass"


def test_from_env_custom_values(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "s")
    monkeypatch.setenv("PORT", "8888")
    monkeypatch.setenv("PAIRING_QR_TOKEN_TTL_SECONDS", "600")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "my-client-id.apps.googleusercontent.com")
    config = CentralServerConfig.from_env()
    assert config.port == 8888
    assert config.pairing_qr_token_ttl_seconds == 600
    assert config.google_client_id == "my-client-id.apps.googleusercontent.com"


def test_the_two_apple_ids_are_read_from_their_own_env_vars(monkeypatch):
    """Distinct values on purpose: the bundle id and the Services ID are
    different Apple identifiers, and one ``os.getenv`` copied without editing
    the key would make ``_apple_audiences()`` offer the same id twice — a
    server that still rejects every browser token."""
    monkeypatch.setenv("JWT_SECRET_KEY", "s")
    monkeypatch.setenv("APPLE_CLIENT_ID", "com.mobaide.app")
    monkeypatch.setenv("APPLE_WEB_CLIENT_ID", "com.mobaide.app.web")
    config = CentralServerConfig.from_env()
    assert config.apple_client_id == "com.mobaide.app"
    assert config.apple_web_client_id == "com.mobaide.app.web"


def test_apple_web_client_id_is_absent_by_default(monkeypatch):
    """Unset hides the button on both surfaces, which is the right local
    behaviour — Apple rejects ``localhost`` Return URLs outright."""
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.delenv("APPLE_WEB_CLIENT_ID", raising=False)
    assert CentralServerConfig.from_env().apple_web_client_id is None


def test_heartbeat_flush_interval_default_and_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.delenv("HEARTBEAT_FLUSH_INTERVAL_SECONDS", raising=False)
    assert CentralServerConfig.from_env().heartbeat_flush_interval_seconds == 60
    monkeypatch.setenv("HEARTBEAT_FLUSH_INTERVAL_SECONDS", "15")
    assert CentralServerConfig.from_env().heartbeat_flush_interval_seconds == 15


def test_ttl_env_vars_parse_positive_integers(monkeypatch):
    """Explicit positive TTLs override the 0 default and are parsed as ints."""
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("MOBILE_TOKEN_EXPIRE_MINUTES", "60")
    monkeypatch.setenv("MOBILE_REFRESH_EXPIRE_MINUTES", "10080")
    monkeypatch.setenv("DASHBOARD_TOKEN_EXPIRE_MINUTES", "480")
    config = CentralServerConfig.from_env()
    assert config.mobile_token_expire_minutes == 60
    assert config.mobile_refresh_expire_minutes == 10080
    assert config.dashboard_token_expire_minutes == 480


def test_apns_defaults_to_unconfigured(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    for var in (
        "APNS_KEY_PATH", "APNS_KEY_ID", "APNS_TEAM_ID", "APNS_TOPIC",
        "APNS_USE_SANDBOX",
    ):
        monkeypatch.delenv(var, raising=False)
    config = CentralServerConfig.from_env()
    assert config.apns_key_path is None
    assert config.apns_key_id is None
    assert config.apns_team_id is None
    assert config.apns_topic is None
    assert config.apns_use_sandbox is False


def test_apns_env_vars_parse(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("APNS_KEY_PATH", "/secrets/AuthKey_ABC.p8")
    monkeypatch.setenv("APNS_KEY_ID", "ABC123DEFG")
    monkeypatch.setenv("APNS_TEAM_ID", "TEAM123456")
    monkeypatch.setenv("APNS_TOPIC", "com.mobaide.app")
    monkeypatch.setenv("APNS_USE_SANDBOX", "true")
    config = CentralServerConfig.from_env()
    assert config.apns_key_path == "/secrets/AuthKey_ABC.p8"
    assert config.apns_key_id == "ABC123DEFG"
    assert config.apns_team_id == "TEAM123456"
    assert config.apns_topic == "com.mobaide.app"
    assert config.apns_use_sandbox is True


def test_api_docs_are_disabled_unless_enabled(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.delenv("ENABLE_API_DOCS", raising=False)
    assert CentralServerConfig.from_env().enable_api_docs is False


@pytest.mark.parametrize(
    "raw,expected",
    [("1", True), ("true", True), ("0", False), ("false", False), ("", False)],
)
def test_enable_api_docs_parses_as_a_boolean(monkeypatch, raw, expected):
    """Opting in must be explicit: a plain truthiness check on the raw string
    would read "0"/"false" as enabled and silently republish the schema."""
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ENABLE_API_DOCS", raw)
    assert CentralServerConfig.from_env().enable_api_docs is expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://staging.example.com", "https://staging.example.com"),
        # A trailing slash is normalised, not rejected — the strip runs before
        # the shape match.
        ("https://staging.example.com/", "https://staging.example.com"),
        ("http://127.0.0.1:9000", "http://127.0.0.1:9000"),
        ("https://mobaide.com:65535", "https://mobaide.com:65535"),
        # Everything below is malformed and must fall back rather than publish a
        # relative or broken canonical/OG/sitemap URL.
        ("", "https://mobaide.com"),
        ("not-a-url", "https://mobaide.com"),
        ("staging.example.com", "https://mobaide.com"),
        ("ftp://mobaide.com", "https://mobaide.com"),
        ("https://mobaide.com/path", "https://mobaide.com"),
        ('https://x.com" onload="a', "https://mobaide.com"),
        ("https://a.com\nDisallow: /", "https://mobaide.com"),
        # `\d{1,5}` alone would admit these, but neither is a usable port.
        ("https://mobaide.com:99999", "https://mobaide.com"),
        ("https://mobaide.com:0", "https://mobaide.com"),
    ],
)
def test_site_base_url_falls_back_when_malformed(monkeypatch, raw, expected):
    """The origin is concatenated straight into canonical/OG/JSON-LD URLs, the
    sitemap and robots.txt, so a malformed value must never reach them — but a
    typo in a marketing URL must not take the relay down either."""
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("SITE_BASE_URL", raw)
    assert CentralServerConfig.from_env().site_base_url == expected


def test_site_base_url_defaults_to_the_apex_when_unset(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.delenv("SITE_BASE_URL", raising=False)
    assert CentralServerConfig.from_env().site_base_url == "https://mobaide.com"


def test_monitoring_interval_default_and_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.delenv("MONITORING_INTERVAL_SECONDS", raising=False)
    assert CentralServerConfig.from_env().monitoring_interval_seconds == 60
    monkeypatch.setenv("MONITORING_INTERVAL_SECONDS", "5")
    assert CentralServerConfig.from_env().monitoring_interval_seconds == 5


def test_ws_allowed_origins_defaults_when_unset(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.delenv("WS_ALLOWED_ORIGINS", raising=False)
    assert CentralServerConfig.from_env().ws_allowed_origins == (
        "https://app.mobaide.com",
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Per-element whitespace and a trailing slash are tolerated.
        (
            " https://a.example , https://b.example:8443/ ",
            ("https://a.example", "https://b.example:8443"),
        ),
        # Malformed elements are dropped, valid ones kept.
        (
            "https://good.example,not-a-url,ftp://x.example,https://x.example/path",
            ("https://good.example",),
        ),
        # Entries are stored pre-normalized (host case-fold, default-port
        # strip), so equivalent spellings dedupe to one entry.
        (
            "https://App.Mobaide.com:443,https://app.mobaide.com",
            ("https://app.mobaide.com",),
        ),
        # Order preserved, duplicates removed.
        (
            "https://b.example,https://a.example,https://b.example",
            ("https://b.example", "https://a.example"),
        ),
        # Operator input keeps _env_origin's hostname-only shape: bracketed
        # IPv6 is rejected here (loopback is structurally allowed at the WS
        # gate instead), so an all-IPv6 list falls back to the default.
        ("http://[::1]:5173", ("https://app.mobaide.com",)),
        # All-invalid list falls back to the default rather than locking
        # every browser out.
        ("not-a-url, ,", ("https://app.mobaide.com",)),
        # Blank var behaves like unset.
        ("   ", ("https://app.mobaide.com",)),
    ],
)
def test_ws_allowed_origins_parses_csv(monkeypatch, raw, expected):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("WS_ALLOWED_ORIGINS", raw)
    assert CentralServerConfig.from_env().ws_allowed_origins == expected
