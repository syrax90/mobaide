"""Tests for RelayConfig.from_env — env-var-driven relay configuration."""
from __future__ import annotations

import pytest

from mobaide.relay.config import RelayConfig

# Every env var that RelayConfig.from_env reads, so host-env values never leak.
_RELAY_ENV_VARS = (
    "RELAY_SERVER_URL",
    "RELAY_AGENT_ID",
    "RELAY_AGENT_TOKEN",
    "RELAY_HEARTBEAT_INTERVAL",
    "RELAY_RECONNECT_BASE",
    "RELAY_RECONNECT_MAX",
    "RELAY_RECONNECT_STABILITY_THRESHOLD",
)


@pytest.fixture(autouse=True)
def _clean_relay_env(monkeypatch):
    """Strip every env var RelayConfig reads."""
    for var in _RELAY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


_ = _clean_relay_env


class TestRelayConfigNotConfigured:
    def test_returns_none_when_url_not_set(self):
        result = RelayConfig.from_env()
        assert result is None

    def test_returns_none_when_url_is_empty_string(self, monkeypatch):
        monkeypatch.setenv("RELAY_SERVER_URL", "")
        result = RelayConfig.from_env()
        assert result is None


class TestRelayConfigPartialConfig:
    def test_raises_when_agent_id_missing(self, monkeypatch):
        monkeypatch.setenv("RELAY_SERVER_URL", "wss://relay.example.com")
        monkeypatch.setenv("RELAY_AGENT_TOKEN", "tok123")
        with pytest.raises(RuntimeError, match="RELAY_AGENT_ID"):
            RelayConfig.from_env()

    def test_id_set_token_blank_is_re_pair_pending(self, monkeypatch):
        # The agent_id-set + token-blank state is the documented re-pair-
        # pending sub-state. ``mobaide-agent re-pair`` writes exactly this
        # shape so the boot autorun can carry the prior ``current_agent_id``
        # into the next claim. The previous strict validator that raised
        # RuntimeError here is intentionally relaxed.
        monkeypatch.setenv("RELAY_SERVER_URL", "wss://relay.example.com")
        monkeypatch.setenv("RELAY_AGENT_ID", "agent-1")
        monkeypatch.setenv("RELAY_AGENT_TOKEN", "")
        cfg = RelayConfig.from_env()
        assert cfg is not None
        assert cfg.agent_id == "agent-1"
        assert cfg.agent_token == ""
        assert cfg.pairing_pending is True
        assert cfg.re_pair_pending is True

    def test_raises_when_agent_id_is_empty(self, monkeypatch):
        monkeypatch.setenv("RELAY_SERVER_URL", "wss://relay.example.com")
        monkeypatch.setenv("RELAY_AGENT_ID", "")
        monkeypatch.setenv("RELAY_AGENT_TOKEN", "tok")
        with pytest.raises(RuntimeError, match="RELAY_AGENT_ID"):
            RelayConfig.from_env()


class TestRelayConfigPairingPending:
    def test_url_only_returns_instance_with_pairing_pending(self, monkeypatch):
        monkeypatch.setenv("RELAY_SERVER_URL", "wss://relay.example.com")
        cfg = RelayConfig.from_env()
        assert cfg is not None
        assert cfg.configured is True
        assert cfg.pairing_pending is True
        assert cfg.agent_id == ""
        assert cfg.agent_token == ""

    def test_full_config_is_not_pairing_pending(self, monkeypatch):
        monkeypatch.setenv("RELAY_SERVER_URL", "wss://relay.example.com")
        monkeypatch.setenv("RELAY_AGENT_ID", "a")
        monkeypatch.setenv("RELAY_AGENT_TOKEN", "t")
        cfg = RelayConfig.from_env()
        assert cfg is not None
        assert cfg.pairing_pending is False


class TestRelayConfigDefaults:
    def test_defaults_with_required_vars(self, monkeypatch):
        monkeypatch.setenv("RELAY_SERVER_URL", "wss://relay.example.com")
        monkeypatch.setenv("RELAY_AGENT_ID", "agent-1")
        monkeypatch.setenv("RELAY_AGENT_TOKEN", "secret")

        cfg = RelayConfig.from_env()

        assert cfg is not None
        assert cfg.server_url == "wss://relay.example.com"
        assert cfg.agent_id == "agent-1"
        assert cfg.agent_token == "secret"
        assert cfg.heartbeat_interval == 15
        assert cfg.reconnect_base_delay == 1.0
        assert cfg.reconnect_max_delay == 60.0
        assert cfg.reconnect_stability_threshold == 60.0


class TestRelayConfigOptionalOverrides:
    def test_heartbeat_interval_from_env(self, monkeypatch):
        monkeypatch.setenv("RELAY_SERVER_URL", "wss://r.io")
        monkeypatch.setenv("RELAY_AGENT_ID", "a")
        monkeypatch.setenv("RELAY_AGENT_TOKEN", "t")
        monkeypatch.setenv("RELAY_HEARTBEAT_INTERVAL", "30")

        cfg = RelayConfig.from_env()
        assert cfg is not None

        assert cfg.heartbeat_interval == 30
        assert isinstance(cfg.heartbeat_interval, int)

    def test_reconnect_base_delay_from_env(self, monkeypatch):
        monkeypatch.setenv("RELAY_SERVER_URL", "wss://r.io")
        monkeypatch.setenv("RELAY_AGENT_ID", "a")
        monkeypatch.setenv("RELAY_AGENT_TOKEN", "t")
        monkeypatch.setenv("RELAY_RECONNECT_BASE", "2.5")

        cfg = RelayConfig.from_env()
        assert cfg is not None

        assert cfg.reconnect_base_delay == 2.5
        assert isinstance(cfg.reconnect_base_delay, float)

    def test_reconnect_stability_threshold_from_env(self, monkeypatch):
        monkeypatch.setenv("RELAY_SERVER_URL", "wss://r.io")
        monkeypatch.setenv("RELAY_AGENT_ID", "a")
        monkeypatch.setenv("RELAY_AGENT_TOKEN", "t")
        monkeypatch.setenv("RELAY_RECONNECT_STABILITY_THRESHOLD", "120")

        cfg = RelayConfig.from_env()
        assert cfg is not None

        assert cfg.reconnect_stability_threshold == 120.0
        assert isinstance(cfg.reconnect_stability_threshold, float)

    def test_reconnect_max_delay_from_env(self, monkeypatch):
        monkeypatch.setenv("RELAY_SERVER_URL", "wss://r.io")
        monkeypatch.setenv("RELAY_AGENT_ID", "a")
        monkeypatch.setenv("RELAY_AGENT_TOKEN", "t")
        monkeypatch.setenv("RELAY_RECONNECT_MAX", "120.0")

        cfg = RelayConfig.from_env()
        assert cfg is not None

        assert cfg.reconnect_max_delay == 120.0
        assert isinstance(cfg.reconnect_max_delay, float)


class TestRelayConfigFrozen:
    def test_config_is_frozen(self, monkeypatch):
        monkeypatch.setenv("RELAY_SERVER_URL", "wss://r.io")
        monkeypatch.setenv("RELAY_AGENT_ID", "a")
        monkeypatch.setenv("RELAY_AGENT_TOKEN", "t")

        cfg = RelayConfig.from_env()
        assert cfg is not None

        # Pydantic v2 frozen models raise ValidationError on assignment;
        # the legacy dataclass raised AttributeError. Accept either so the
        # test pins the immutability invariant rather than the exception class.
        from pydantic import ValidationError

        with pytest.raises((AttributeError, ValidationError)):
            cfg.server_url = "wss://other.io"  # type: ignore[misc]
