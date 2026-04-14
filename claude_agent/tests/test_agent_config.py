"""Tests for AgentConfig.from_env — pure env-driven construction."""
from __future__ import annotations

import json

import pytest

from claude_mobile.core.projects_config import ProjectsConfig
from claude_mobile.linear.config import AutoScanConfig
from claude_mobile.agent.config import AgentConfig

# All env vars AgentConfig.from_env consults plus the downstream
# PROJECTS / auto-scan vars, so that host-env values never leak into the
# unit under test.
_ENV_VARS = (
    "JWT_SECRET_KEY",
    "JWT_ALGORITHM",
    "JWT_ACCESS_TOKEN_EXPIRE_MINUTES",
    "JWT_REFRESH_TOKEN_EXPIRE_MINUTES",
    "USER_STORE_PATH",
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
    """Strip every env var AgentConfig / its dependencies read."""
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# Dummy reference: the autouse fixture is invoked by pytest via name-based
# lookup, so Pyright's "not accessed" hint is a false positive.
_ = _clean_env


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
        assert cfg.jwt_access_token_expire_minutes == 30
        assert cfg.jwt_refresh_token_expire_minutes == 1440
        assert cfg.user_store_path == "users.json"
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 8000
        assert cfg.max_sessions == 16
        assert cfg.session_disconnect_timeout == 300
        assert cfg.log_level == "INFO"
        assert cfg.log_format == "json"
        assert cfg.audit_log_path is None
        assert cfg.log_sample_ws is False
        assert cfg.auto_scan_config is None
        assert cfg.projects_config is None


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


class TestAgentConfigProjectsIntegration:
    def test_projects_and_auto_scan_populated_when_projects_env_has_key(
        self, monkeypatch
    ):
        projects_json = json.dumps(
            {
                "My Project": {
                    "path": "C:/workspace/my",
                    "linear_api_key": "lin_api_xxx",
                }
            }
        )
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("PROJECTS", projects_json)

        cfg = AgentConfig.from_env()

        assert isinstance(cfg.projects_config, ProjectsConfig)
        assert "My Project" in cfg.projects_config.projects
        assert isinstance(cfg.auto_scan_config, AutoScanConfig)
        assert "My Project" in cfg.auto_scan_config.projects

    def test_projects_populated_but_auto_scan_none_without_linear_key(
        self, monkeypatch
    ):
        projects_json = json.dumps(
            {"My Project": {"path": "C:/workspace/my"}}
        )
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("PROJECTS", projects_json)

        cfg = AgentConfig.from_env()

        assert isinstance(cfg.projects_config, ProjectsConfig)
        assert cfg.auto_scan_config is None

    def test_both_none_when_projects_env_unset(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")

        cfg = AgentConfig.from_env()

        assert cfg.projects_config is None
        assert cfg.auto_scan_config is None


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
