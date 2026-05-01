"""Tests for AgentConfig.from_env — pure env-driven construction."""
from __future__ import annotations

import pytest

from mobaide.agent.config import AgentConfig
from mobaide.linear.config import AutoScanConfig

# All env vars AgentConfig.from_env consults. Auto-scan-related settings
# live on ``AutoScanConfig`` but are listed here so host-env values never
# leak into the unit under test.
_ENV_VARS = (
    "JWT_SECRET_KEY",
    "JWT_ALGORITHM",
    "JWT_ACCESS_TOKEN_EXPIRE_MINUTES",
    "JWT_REFRESH_TOKEN_EXPIRE_MINUTES",
    "USER_STORE_PATH",
    "PROJECTS_STORE_PATH",
    "HOST",
    "PORT",
    "MAX_SESSIONS",
    "SESSION_DISCONNECT_TIMEOUT",
    "LOG_LEVEL",
    "LOG_FORMAT",
    "AUDIT_LOG_PATH",
    "LOG_SAMPLE_WS",
    "PROJECTS",
    "AUTO_SCAN_PROMPT",
    "SCAN_INTERVAL_SECONDS",
    "WORKTREE_BASE",
    "BRANCH_PREFIX",
    "MAIN_BRANCH",
    "LINEAR_ACTIVE_STATE",
    "RELAY_SERVER_URL",
    "RELAY_AGENT_ID",
    "RELAY_AGENT_TOKEN",
    "RELAY_HEARTBEAT_INTERVAL",
    "RELAY_RECONNECT_BASE",
    "RELAY_RECONNECT_MAX",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


_ = _clean_env  # silence pyright "not accessed" hint


class TestAgentConfigJwtSecretRequirement:
    def test_missing_jwt_secret_raises_runtime_error(self):
        with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
            AgentConfig.from_env()

    def test_empty_jwt_secret_raises_runtime_error(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "")
        with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
            AgentConfig.from_env()


class TestAgentConfigDefaults:
    def test_only_jwt_secret_set_uses_dataclass_defaults(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "supersecret")

        cfg = AgentConfig.from_env()

        assert cfg.jwt_secret_key == "supersecret"
        assert cfg.jwt_algorithm == "HS256"
        # Default is 0 = never expires (issue JWTs without an `exp` claim).
        assert cfg.jwt_access_token_expire_minutes == 0
        assert cfg.jwt_refresh_token_expire_minutes == 0
        assert cfg.user_store_path == "users.json"
        assert cfg.projects_store_path == "projects.json"
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 8000
        assert cfg.max_sessions == 16
        assert cfg.session_disconnect_timeout == 300
        assert cfg.log_level == "INFO"
        assert cfg.log_format == "json"
        assert cfg.audit_log_path is None
        assert cfg.log_sample_ws is False
        # Post-CLA-39: AutoScanConfig is always wired in (defaults applied).
        assert isinstance(cfg.auto_scan_config, AutoScanConfig)


class TestAgentConfigIntegerParsing:
    def test_port_parsed_as_int(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("PORT", "9090")

        cfg = AgentConfig.from_env()

        assert cfg.port == 9090
        assert isinstance(cfg.port, int)

    def test_max_sessions_parsed_as_int(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("MAX_SESSIONS", "32")

        cfg = AgentConfig.from_env()

        assert cfg.max_sessions == 32
        assert isinstance(cfg.max_sessions, int)

    def test_jwt_access_token_expire_parsed_as_int(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "15")

        cfg = AgentConfig.from_env()

        assert cfg.jwt_access_token_expire_minutes == 15
        assert isinstance(cfg.jwt_access_token_expire_minutes, int)

    def test_jwt_refresh_token_expire_parsed_as_int(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("JWT_REFRESH_TOKEN_EXPIRE_MINUTES", "2880")

        cfg = AgentConfig.from_env()

        assert cfg.jwt_refresh_token_expire_minutes == 2880
        assert isinstance(cfg.jwt_refresh_token_expire_minutes, int)

    def test_session_disconnect_timeout_parsed_as_int(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("SESSION_DISCONNECT_TIMEOUT", "600")

        cfg = AgentConfig.from_env()

        assert cfg.session_disconnect_timeout == 600
        assert isinstance(cfg.session_disconnect_timeout, int)


class TestAgentConfigLogSampleWs:
    @pytest.mark.parametrize("value", ["1", "true", "yes", "TRUE", "YES"])
    def test_truthy_variants_parse_to_true(self, monkeypatch, value):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("LOG_SAMPLE_WS", value)

        cfg = AgentConfig.from_env()

        assert cfg.log_sample_ws is True

    @pytest.mark.parametrize("value", ["0", "false", "no", ""])
    def test_falsy_variants_parse_to_false(self, monkeypatch, value):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("LOG_SAMPLE_WS", value)

        cfg = AgentConfig.from_env()

        assert cfg.log_sample_ws is False

    def test_unset_is_false(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")

        cfg = AgentConfig.from_env()

        assert cfg.log_sample_ws is False


class TestAgentConfigAuditLogPath:
    def test_empty_string_becomes_none(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("AUDIT_LOG_PATH", "")

        cfg = AgentConfig.from_env()

        assert cfg.audit_log_path is None

    def test_set_value_passed_through(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("AUDIT_LOG_PATH", "/var/log/audit.log")

        cfg = AgentConfig.from_env()

        assert cfg.audit_log_path == "/var/log/audit.log"

    def test_unset_is_none(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")

        cfg = AgentConfig.from_env()

        assert cfg.audit_log_path is None


class TestAgentConfigProjectsStorePath:
    def test_unset_default(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        cfg = AgentConfig.from_env()
        assert cfg.projects_store_path == "projects.json"

    def test_env_overrides_default(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("PROJECTS_STORE_PATH", "/var/agent/projects.json")
        cfg = AgentConfig.from_env()
        assert cfg.projects_store_path == "/var/agent/projects.json"


class TestAgentConfigStringPassThrough:
    def test_jwt_algorithm_from_env(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("JWT_ALGORITHM", "HS512")

        cfg = AgentConfig.from_env()

        assert cfg.jwt_algorithm == "HS512"

    def test_host_from_env(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("HOST", "127.0.0.1")

        cfg = AgentConfig.from_env()

        assert cfg.host == "127.0.0.1"

    def test_log_level_and_format_from_env(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("LOG_FORMAT", "console")

        cfg = AgentConfig.from_env()

        assert cfg.log_level == "DEBUG"
        assert cfg.log_format == "console"


class TestAgentConfigRelayIntegration:
    def test_relay_config_none_when_not_configured(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")

        cfg = AgentConfig.from_env()

        assert cfg.relay_config is None

    def test_relay_config_populated_when_env_vars_set(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("RELAY_SERVER_URL", "wss://relay.example.com")
        monkeypatch.setenv("RELAY_AGENT_ID", "agent-1")
        monkeypatch.setenv("RELAY_AGENT_TOKEN", "secret-tok")

        cfg = AgentConfig.from_env()

        assert cfg.relay_config is not None
        assert cfg.relay_config.server_url == "wss://relay.example.com"
        assert cfg.relay_config.agent_id == "agent-1"
        assert cfg.relay_config.agent_token == "secret-tok"

    def test_relay_config_raises_on_partial_env(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("RELAY_SERVER_URL", "wss://relay.example.com")
        # Missing RELAY_AGENT_ID and RELAY_AGENT_TOKEN

        with pytest.raises(RuntimeError, match="RELAY_AGENT_ID"):
            AgentConfig.from_env()
