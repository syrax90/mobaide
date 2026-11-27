"""Tests for CentralServerConfig."""
from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from central_server.config import CentralServerConfig


def test_from_env_missing_secret(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        CentralServerConfig.from_env()


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


def test_token_ttls_are_finite_by_default(monkeypatch):
    """from_env falls back to the same finite TTLs as the dataclass."""
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    for var in (
        "MOBILE_TOKEN_EXPIRE_MINUTES",
        "MOBILE_REFRESH_EXPIRE_MINUTES",
        "DASHBOARD_TOKEN_EXPIRE_MINUTES",
        "ADMIN_DASHBOARD_TOKEN_EXPIRE_MINUTES",
    ):
        monkeypatch.delenv(var, raising=False)

    for config in (
        CentralServerConfig.from_env(),
        CentralServerConfig(jwt_secret_key="test-secret"),
    ):
        assert (
            config.mobile_token_expire_minutes,
            config.mobile_refresh_expire_minutes,
            config.dashboard_token_expire_minutes,
            config.admin_dashboard_token_expire_minutes,
        ) == (60, 43200, 129600, 10080)


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


_GOOGLE_ADS_VARS = (
    "GOOGLE_ADS_CUSTOMER_ID",
    "GOOGLE_ADS_LOGIN_CUSTOMER_ID",
    "GOOGLE_ADS_OAUTH_CLIENT_ID",
    "GOOGLE_ADS_OAUTH_CLIENT_SECRET",
    "GOOGLE_ADS_OAUTH_REFRESH_TOKEN",
    "GOOGLE_ADS_CONVERSION_ACTION_ID",
)


def _set_all_google_ads(monkeypatch, **overrides):
    values = {
        "GOOGLE_ADS_CUSTOMER_ID": "123-456-7890",
        "GOOGLE_ADS_LOGIN_CUSTOMER_ID": "987-654-3210",
        "GOOGLE_ADS_OAUTH_CLIENT_ID": "id.apps.googleusercontent.com",
        "GOOGLE_ADS_OAUTH_CLIENT_SECRET": "GOCSPX-x",
        "GOOGLE_ADS_OAUTH_REFRESH_TOKEN": "1//refresh",
        "GOOGLE_ADS_CONVERSION_ACTION_ID": "987654321",
    }
    values.update(overrides)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_google_ads_env_vars_parse(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    _set_all_google_ads(monkeypatch)
    config = CentralServerConfig.from_env()
    assert config.google_ads_customer_id == "1234567890"
    assert config.google_ads_login_customer_id == "9876543210"
    assert config.google_ads_conversion_action_id == "987654321"
    assert config.google_ads_missing == ()
    assert config.google_ads_enabled is True


def test_google_ads_partial_set_reports_missing(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    for var in _GOOGLE_ADS_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GOOGLE_ADS_DEVELOPER_TOKEN", "dev-token")  # retired, ignored
    monkeypatch.setenv("GOOGLE_ADS_OAUTH_CLIENT_ID", "id")
    config = CentralServerConfig.from_env()
    assert config.google_ads_missing == (
        "GOOGLE_ADS_CUSTOMER_ID",
        "GOOGLE_ADS_OAUTH_CLIENT_SECRET",
        "GOOGLE_ADS_OAUTH_REFRESH_TOKEN",
        "GOOGLE_ADS_CONVERSION_ACTION_ID",
    )
    assert config.google_ads_enabled is False


@pytest.mark.parametrize("raw", ["12ab", "customers/123", " ", "1 2"])
def test_google_ads_customer_id_malformed_counts_as_unset(monkeypatch, raw):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    _set_all_google_ads(monkeypatch, GOOGLE_ADS_CUSTOMER_ID=raw)
    config = CentralServerConfig.from_env()
    assert config.google_ads_customer_id is None
    assert "GOOGLE_ADS_CUSTOMER_ID" in config.google_ads_missing
    assert config.google_ads_enabled is False


@pytest.mark.parametrize(
    "raw, expected",
    [(None, 90), ("30", 30), ("90", 90), ("200", 90), ("0", 1), ("abc", 90), ("", 90), ("-5", 90)],
)
def test_google_ads_click_window_days_parses_and_clamps(monkeypatch, raw, expected):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    if raw is None:
        monkeypatch.delenv("GOOGLE_ADS_CLICK_WINDOW_DAYS", raising=False)
    else:
        monkeypatch.setenv("GOOGLE_ADS_CLICK_WINDOW_DAYS", raw)
    assert CentralServerConfig.from_env().google_ads_click_window_days == expected


_CLOUD_VOICE_VARS = (
    "CLOUD_VOICE_URL",
    "CLOUD_VOICE_API_KEY",
    "CLOUD_VOICE_MODEL",
    "CLOUD_VOICE_ENABLED",
    "CLOUD_VOICE_PROMPT",
    "CLOUD_VOICE_USER_DAILY_SECONDS",
    "CLOUD_VOICE_GLOBAL_DAILY_SECONDS",
    "CLOUD_VOICE_MAX_CONCURRENCY",
    "CLOUD_VOICE_ALLOWED_EMAILS",
)
_CLOUD_VOICE_REQUIRED = _CLOUD_VOICE_VARS[:3]


def _set_cloud_voice(monkeypatch, **overrides):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    for var in _CLOUD_VOICE_VARS:
        monkeypatch.delenv(var, raising=False)
    values = {
        "CLOUD_VOICE_URL": "https://speech.test",
        "CLOUD_VOICE_API_KEY": "test-speech-key",
        "CLOUD_VOICE_MODEL": "test-speech-model",
    }
    values.update(overrides)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_cloud_voice_has_no_built_in_service(monkeypatch):
    """The speech service's address, key and model come from the environment
    only: with none of them set, neither ``from_env`` nor the dataclass
    may name one."""
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    for var in _CLOUD_VOICE_VARS:
        monkeypatch.delenv(var, raising=False)

    for config in (
        CentralServerConfig.from_env(),
        CentralServerConfig(jwt_secret_key="test-secret"),
    ):
        assert (
            config.cloud_voice_url,
            config.cloud_voice_api_key,
            config.cloud_voice_model,
        ) == (None, None, None)
        assert config.cloud_voice_missing == _CLOUD_VOICE_REQUIRED


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("https://speech.test", "https://speech.test"),
        (" https://speech.test/v1/ ", "https://speech.test/v1"),
        ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
        ("http://localhost:8000/v1", "http://localhost:8000/v1"),
        ("https://speech.test/gateway/v1", "https://speech.test/gateway/v1"),
    ],
)
def test_cloud_voice_env_vars_parse(monkeypatch, raw, expected):
    _set_cloud_voice(
        monkeypatch,
        CLOUD_VOICE_URL=raw,
        CLOUD_VOICE_API_KEY=" test-speech-key ",
        CLOUD_VOICE_ALLOWED_EMAILS=" First@Test.Local , ,second@test.local",
    )
    config = CentralServerConfig.from_env()
    assert config.cloud_voice_url == expected
    assert config.cloud_voice_api_key == "test-speech-key"
    assert config.cloud_voice_model == "test-speech-model"
    assert config.cloud_voice_missing == ()
    assert config.cloud_voice_enabled is True
    assert config.cloud_voice_allowed_emails == ("first@test.local", "second@test.local")


@pytest.mark.parametrize("name", _CLOUD_VOICE_REQUIRED)
def test_cloud_voice_partial_set_reports_missing(monkeypatch, name):
    _set_cloud_voice(monkeypatch, **{name: " "})
    assert CentralServerConfig.from_env().cloud_voice_missing == (name,)


@pytest.mark.parametrize(
    "raw",
    [
        "speech.test",
        "ftp://speech.test",
        "https://user:secret@speech.test",
        "https://speech.test/v1?key=secret",
        "https://speech.test/v1#key",
        "https://speech.test/a b",
        "http://speech.test",
        "http://10.0.0.5:8000/v1",
        "http://127.0.0.1.speech.test:8000",
        "http://localhost.speech.test",
    ],
)
def test_cloud_voice_url_malformed_counts_as_unset(monkeypatch, raw):
    """A URL that could carry a secret (credentials, a query) is refused,
    not sent requests: the key belongs in ``CLOUD_VOICE_API_KEY``. So is
    plain ``http`` to any host but this machine: the key and the audio
    would cross the network in clear text."""
    _set_cloud_voice(monkeypatch, CLOUD_VOICE_URL=raw)
    config = CentralServerConfig.from_env()
    assert config.cloud_voice_url is None
    assert config.cloud_voice_missing == ("CLOUD_VOICE_URL",)


@pytest.mark.parametrize(
    "raw",
    [
        "ключ-0123456789",
        "test speech key",
        "test-speech-key\nX-Injected: 1",
        "test-speech-key\x7f",
    ],
    ids=["non_ascii", "inner_space", "line_break", "control_character"],
)
def test_cloud_voice_api_key_that_cannot_be_a_header_counts_as_unset(monkeypatch, raw):
    """The key travels in an HTTP header. A value that cannot (a paste with
    a line break, a non-ASCII character) turns the feature off, as a missing
    key does, instead of stopping the server at startup."""
    _set_cloud_voice(monkeypatch, CLOUD_VOICE_API_KEY=raw)
    config = CentralServerConfig.from_env()
    assert config.cloud_voice_api_key is None
    assert config.cloud_voice_missing == ("CLOUD_VOICE_API_KEY",)


def _relay_pairing_keypair_b64() -> tuple[str, str]:
    """A private/public pair made for one test run."""
    private = Ed25519PrivateKey.generate()
    return (
        base64.b64encode(private.private_bytes_raw()).decode(),
        base64.b64encode(private.public_key().public_bytes_raw()).decode(),
    )


def test_relay_pairing_key_is_read_and_gives_its_public_half(monkeypatch):
    private_b64, public_b64 = _relay_pairing_keypair_b64()
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("RELAY_PAIRING_PRIVATE_KEY", f"  {private_b64}\n")

    config = CentralServerConfig.from_env()

    assert config.relay_pairing_private_key_b64 == private_b64
    assert config.relay_pairing_public_key_b64 == public_b64


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_relay_pairing_key_is_optional(monkeypatch, raw):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    if raw is None:
        monkeypatch.delenv("RELAY_PAIRING_PRIVATE_KEY", raising=False)
    else:
        monkeypatch.setenv("RELAY_PAIRING_PRIVATE_KEY", raw)

    config = CentralServerConfig.from_env()

    assert config.relay_pairing_private_key_b64 is None
    assert config.relay_pairing_public_key_b64 is None


@pytest.mark.parametrize(
    "raw",
    [
        "not-base64-!!",
        # Valid base64, but an Ed25519 private key is exactly 32 raw bytes.
        base64.b64encode(b"\x07" * 16).decode(),
        base64.b64encode(b"\x07" * 64).decode(),
    ],
)
def test_malformed_relay_pairing_key_stops_the_start(monkeypatch, raw):
    """A relay that starts without the key it was given would fail later
    with no clear cause. The error names the setting and not its value."""
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("RELAY_PAIRING_PRIVATE_KEY", raw)

    with pytest.raises(ValueError, match="RELAY_PAIRING_PRIVATE_KEY") as failure:
        CentralServerConfig.from_env()

    assert raw not in str(failure.value)
