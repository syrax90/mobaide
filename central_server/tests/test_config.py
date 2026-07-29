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
    monkeypatch.setenv("PAIRING_TOKEN_TTL_SECONDS", "600")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "my-client-id.apps.googleusercontent.com")
    config = CentralServerConfig.from_env()
    assert config.port == 8888
    assert config.pairing_token_ttl_seconds == 600
    assert config.google_client_id == "my-client-id.apps.googleusercontent.com"


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
