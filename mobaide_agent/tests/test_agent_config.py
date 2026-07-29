"""Tests for AgentConfig.from_env — pure env-driven construction."""
from __future__ import annotations

import pytest

from mobaide.agent.config import AgentConfig
from mobaide.agent.install_paths import default_prefix

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
    "MOBAIDE_ENV",
    "MOBAIDE_AGENT_PREFIX",
    "ENABLE_API_DOCS",
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
    "RELAY_SERVER_URL",
    "RELAY_AGENT_ID",
    "RELAY_AGENT_TOKEN",
    "RELAY_HEARTBEAT_INTERVAL",
    "RELAY_RECONNECT_BASE",
    "RELAY_RECONNECT_MAX",
    "RELAY_RECONNECT_STABILITY_THRESHOLD",
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


class TestAgentConfigLogSampleWs:
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
        # Writable install prefix → ``state_prefix()`` == ``default_prefix()``.
        monkeypatch.setattr(
            "mobaide.agent.install_paths._is_writable_dir", lambda *_: True
        )
        cfg = AgentConfig.from_env()
        assert cfg.projects_store_path == str(default_prefix() / "projects.json")

    def test_unset_default_unwritable_prefix_falls_back_to_user_data_dir(
        self, monkeypatch, tmp_path
    ):
        """The regression guard: an unwritable install prefix (source/dev run)
        anchors relative state to the per-user data dir instead of crashing the
        first store write with ``[Errno 13] Permission denied``."""
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        fallback = tmp_path / "userdata"
        monkeypatch.setattr(
            "mobaide.agent.install_paths._is_writable_dir", lambda *_: False
        )
        monkeypatch.setattr(
            "mobaide.agent.install_paths.user_data_dir", lambda: fallback
        )
        cfg = AgentConfig.from_env()
        assert cfg.projects_store_path == str(fallback / "projects.json")
        assert cfg.user_store_path == str(fallback / "users.json")

    def test_env_overrides_default(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("PROJECTS_STORE_PATH", "/var/agent/projects.json")
        cfg = AgentConfig.from_env()
        assert cfg.projects_store_path == "/var/agent/projects.json"

    def test_relative_override_anchored_to_prefix(self, monkeypatch, tmp_path):
        """A relative store-path override is anchored to the prefix, not CWD."""
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(tmp_path))
        monkeypatch.setenv("PROJECTS_STORE_PATH", "projects.json")
        monkeypatch.setenv("USER_STORE_PATH", "users.json")
        cfg = AgentConfig.from_env()
        assert cfg.projects_store_path == str(tmp_path / "projects.json")
        assert cfg.user_store_path == str(tmp_path / "users.json")

    def test_relative_audit_path_anchored_none_preserved(self, monkeypatch, tmp_path):
        """A relative audit path is anchored; ``None`` stays ``None``."""
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(tmp_path))
        monkeypatch.setenv("AUDIT_LOG_PATH", "audit.log")
        cfg = AgentConfig.from_env()
        assert cfg.audit_log_path == str(tmp_path / "audit.log")


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


class TestAgentConfigEnableApiDocs:
    """``ENABLE_API_DOCS`` binds by field name via ``env_prefix=""``."""

    def test_unset_is_false(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")

        assert AgentConfig.from_env().enable_api_docs is False

    @pytest.mark.parametrize(
        "raw,expected",
        [("1", True), ("true", True), ("0", False), ("false", False)],
    )
    def test_env_var_opts_in_explicitly(self, monkeypatch, raw, expected):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("ENABLE_API_DOCS", raw)

        assert AgentConfig.from_env().enable_api_docs is expected

    @pytest.mark.parametrize(
        "raw,expected", [("", False), ("maybe", False), ("True ", True)]
    )
    def test_unrecognised_value_does_not_block_startup(
        self, monkeypatch, raw, expected
    ):
        """Pydantic's own bool coercion raises ``ValidationError`` on these
        three, which takes the whole agent down at construction. The empty
        string is what an operator gets from uncommenting the
        ``# ENABLE_API_DOCS=1`` line in ``env.template``, so the strict field
        turned a documentation hint into a refusal to start."""
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("ENABLE_API_DOCS", raw)

        assert AgentConfig.from_env().enable_api_docs is expected

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("", False),
            ("maybe", False),
            ("True ", True),
            ("1", True),
            ("0", False),
            ("yes", True),
            ("on", True),
            ("FALSE", False),
        ],
    )
    def test_agrees_with_the_sibling_boolean(self, monkeypatch, raw, expected):
        """The invariant the shared ``_parse_env_bool`` validator exists to
        establish: whatever ``LOG_SAMPLE_WS`` makes of a raw value, every
        other boolean in the package — ``ENABLE_API_DOCS`` included — makes
        of it too."""
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("ENABLE_API_DOCS", raw)
        monkeypatch.setenv("LOG_SAMPLE_WS", raw)

        cfg = AgentConfig.from_env()

        assert (cfg.enable_api_docs, cfg.log_sample_ws) == (expected, expected)

    def test_explicit_bool_is_passed_through(self):
        """The validator normalises strings; a caller-supplied ``bool`` has
        to survive it, or programmatic opt-in silently stays off."""
        cfg = AgentConfig(jwt_secret_key="s", enable_api_docs=True)

        assert cfg.enable_api_docs is True


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

    def test_relay_config_pairing_pending_when_id_and_token_empty(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("RELAY_SERVER_URL", "wss://relay.example.com")

        cfg = AgentConfig.from_env()

        assert cfg.relay_config is not None
        assert cfg.relay_config.pairing_pending is True

    def test_relay_config_raises_on_partial_env(self, monkeypatch):
        # Genuine misconfig (token without id, or vice versa) still raises.
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("RELAY_SERVER_URL", "wss://relay.example.com")
        monkeypatch.setenv("RELAY_AGENT_TOKEN", "tok-without-id")

        with pytest.raises(RuntimeError, match="RELAY_AGENT_ID"):
            AgentConfig.from_env()


class TestAgentConfigProjectsField:
    """The ``projects`` field carries the JSON seed forward from `.env` /
    process env into the config instance, where ``app.create_app`` then
    passes it explicitly to ``ProjectsStore.load(seed_json=...)``.

    This decouples the projects store from ``os.environ`` — necessary
    because pydantic-settings reads `.env` into the settings instance but
    does NOT mutate ``os.environ``. On platforms whose supervisor doesn't
    pre-load the env file (Windows Service launcher.cmd), the legacy
    ``os.getenv("PROJECTS")`` path silently missed `.env`-defined values.
    """

    def test_unset_projects_field_is_none(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")

        cfg = AgentConfig.from_env()

        assert cfg.projects is None

    def test_projects_env_var_populates_field_as_string(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        seed = '{"X":{"path":"/tmp"}}'
        monkeypatch.setenv("PROJECTS", seed)

        cfg = AgentConfig.from_env()

        # The field carries the raw JSON string forward — parsing happens
        # downstream in ``ProjectsStore._seed_from_env``. Keeping the
        # parse off the settings layer avoids early failure on a
        # malformed PROJECTS that would block agent startup before the
        # projects store has a chance to surface a useful error.
        assert cfg.projects == seed
        assert isinstance(cfg.projects, str)
