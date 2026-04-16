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
