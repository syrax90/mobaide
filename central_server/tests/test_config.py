"""Tests for CentralServerConfig."""
from __future__ import annotations

import pytest

from central_server.config import CentralServerConfig


def test_from_env_with_required_vars(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DASHBOARD_USERNAME", "admin")
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", "$2b$12$fakehash")
    config = CentralServerConfig.from_env()
    assert config.jwt_secret_key == "test-secret"
    assert config.dashboard_username == "admin"
    assert config.port == 9000


def test_from_env_missing_secret(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        CentralServerConfig.from_env()


def test_from_env_missing_dashboard_username(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.delenv("DASHBOARD_USERNAME", raising=False)
    with pytest.raises(RuntimeError, match="DASHBOARD_USERNAME"):
        CentralServerConfig.from_env()


def test_from_env_custom_values(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "s")
    monkeypatch.setenv("DASHBOARD_USERNAME", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", "h")
    monkeypatch.setenv("PORT", "8888")
    monkeypatch.setenv("PAIRING_TOKEN_TTL_SECONDS", "600")
    config = CentralServerConfig.from_env()
    assert config.port == 8888
    assert config.pairing_token_ttl_seconds == 600


def test_frozen_config():
    config = CentralServerConfig(
        jwt_secret_key="s",
        dashboard_username="u",
        dashboard_password_hash="h",
    )
    with pytest.raises(AttributeError):
        config.port = 1234  # type: ignore[misc]
