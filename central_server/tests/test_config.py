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


def test_frozen_config():
    config = CentralServerConfig(jwt_secret_key="s")
    with pytest.raises(AttributeError):
        config.port = 1234  # type: ignore[misc]


def test_ttl_env_vars_default_to_zero(monkeypatch):
    """All three user-facing TTLs default to 0 (never expires) when unset."""
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.delenv("MOBILE_TOKEN_EXPIRE_MINUTES", raising=False)
    monkeypatch.delenv("MOBILE_REFRESH_EXPIRE_MINUTES", raising=False)
    monkeypatch.delenv("DASHBOARD_TOKEN_EXPIRE_MINUTES", raising=False)
    config = CentralServerConfig.from_env()
    assert config.mobile_token_expire_minutes == 0
    assert config.mobile_refresh_expire_minutes == 0
    assert config.dashboard_token_expire_minutes == 0


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


def test_dataclass_defaults_are_zero():
    """The dataclass itself defaults all three TTLs to 0 (no env lookup path)."""
    config = CentralServerConfig(jwt_secret_key="s")
    assert config.mobile_token_expire_minutes == 0
    assert config.mobile_refresh_expire_minutes == 0
    assert config.dashboard_token_expire_minutes == 0
