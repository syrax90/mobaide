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
