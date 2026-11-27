"""Tests for AgentConfig.from_env — pure env-driven construction."""
from __future__ import annotations

import pytest

from mobaide.agent._env_writer import parse_env_file
from mobaide.agent.config import AgentConfig, migrate_stored_host

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
    "MOBAIDE_VOICE_FAKE",
    "MOBAIDE_VOICE_DIR",
    "MOBAIDE_OPENCODE_BIN",
    "MOBAIDE_ACP_OPENCODE_COMMAND",
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


class TestAgentConfigAuditLogPath:
    def test_empty_string_becomes_none(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("AUDIT_LOG_PATH", "")

        cfg = AgentConfig.from_env()

        assert cfg.audit_log_path is None


class TestAgentConfigProjectsStorePath:

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
    passes it explicitly to ``WorkspacesStore.load(seed_json=...)``.

    This decouples the workspaces store from ``os.environ`` — necessary
    because pydantic-settings reads `.env` into the settings instance but
    does NOT mutate ``os.environ``. On platforms whose supervisor doesn't
    pre-load the env file (Windows Service launcher.cmd), the legacy
    ``os.getenv("PROJECTS")`` path silently missed `.env`-defined values.
    """


    def test_projects_env_var_populates_field_as_string(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        seed = '{"X":{"path":"/tmp"}}'
        monkeypatch.setenv("PROJECTS", seed)

        cfg = AgentConfig.from_env()

        # The field carries the raw JSON string forward — parsing happens
        # downstream in ``WorkspacesStore._seed_from_env``. Keeping the
        # parse off the settings layer avoids early failure on a
        # malformed PROJECTS that would block agent startup before the
        # workspaces store has a chance to surface a useful error.
        assert cfg.projects == seed
        assert isinstance(cfg.projects, str)


class TestAgentConfigVoice:

    @pytest.mark.parametrize(("raw", "expected"), [("1", True), ("true", True), ("0", False), ("", False)])
    def test_fake_switch_parses_env_booleans(self, monkeypatch, raw, expected):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("MOBAIDE_VOICE_FAKE", raw)

        cfg = AgentConfig.from_env()

        assert cfg.voice_fake is expected

    def test_empty_voice_dir_becomes_none(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("MOBAIDE_VOICE_DIR", "")

        cfg = AgentConfig.from_env()

        assert cfg.voice_dir is None


    def test_relative_voice_dir_anchored_to_prefix(self, monkeypatch, tmp_path):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(tmp_path))
        monkeypatch.setenv("MOBAIDE_VOICE_DIR", "my-voice")

        cfg = AgentConfig.from_env()

        assert cfg.voice_dir == str(tmp_path / "my-voice")


class TestAgentConfigOpenCode:
    def test_env_file_lines_reach_the_typed_fields(self, tmp_path):
        # Without a typed field with the right alias, ``extra="ignore"`` drops
        # a ``.env`` line silently: it never reaches ``os.environ`` either, so
        # the launcher's own env lookup would not see the operator's path.
        env_file = tmp_path / "test.env"
        env_file.write_text(
            "JWT_SECRET_KEY=s\n"
            "MOBAIDE_OPENCODE_BIN=/abs/path/to/opencode\n"
            "MOBAIDE_ACP_OPENCODE_COMMAND=opencode-dev acp\n",
            encoding="utf-8",
        )

        cfg = AgentConfig.from_env(env_file=env_file)

        assert cfg.opencode_bin == "/abs/path/to/opencode"
        assert cfg.acp_opencode_command == "opencode-dev acp"


class TestAgentConfigListenAddress:
    """Clients reach the agent through the relay tunnel, so the HTTP listener
    serves this computer only."""

    @pytest.mark.parametrize("stored", ["0.0.0.0", "192.168.1.20", "::", "agent.lan"])
    def test_stored_address_that_is_not_loopback_is_replaced(self, monkeypatch, stored):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("HOST", stored)

        cfg = AgentConfig.from_env()

        assert cfg.host == "127.0.0.1"

    @pytest.mark.parametrize("stored", ["127.0.0.1", "127.0.0.5", "localhost", "::1"])
    def test_loopback_address_is_kept(self, monkeypatch, stored):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("HOST", stored)

        cfg = AgentConfig.from_env()

        assert cfg.host == stored

    def test_test_stack_may_select_another_address(self, monkeypatch, tmp_path):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("MOBAIDE_ENV", "test")
        monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(tmp_path))
        monkeypatch.setenv("HOST", "0.0.0.0")

        cfg = AgentConfig.from_env()

        assert cfg.host == "0.0.0.0"


class TestMigrateStoredHost:
    """Installs made before the listener became loopback-only hold
    ``HOST=0.0.0.0`` in their ``.env``; the first start rewrites it."""

    def test_rewrites_only_the_host_line(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "# agent settings\n"
            "JWT_SECRET_KEY=keep-me\n"
            "HOST=0.0.0.0\n"
            "PORT=8123\n"
            "RELAY_AGENT_TOKEN=tok\n",
            encoding="utf-8",
        )

        assert migrate_stored_host(env_file) is True

        text = env_file.read_text(encoding="utf-8")
        assert parse_env_file(text) == {
            "JWT_SECRET_KEY": "keep-me",
            "HOST": "127.0.0.1",
            "PORT": "8123",
            "RELAY_AGENT_TOKEN": "tok",
        }
        assert "# agent settings" in text

    @pytest.mark.parametrize(
        "content",
        [
            "JWT_SECRET_KEY=s\nHOST=127.0.0.1\n",
            "JWT_SECRET_KEY=s\nHOST=localhost\n",
            "JWT_SECRET_KEY=s\n",
        ],
    )
    def test_leaves_a_file_without_a_non_loopback_host_untouched(
        self, tmp_path, content
    ):
        env_file = tmp_path / ".env"
        env_file.write_text(content, encoding="utf-8")

        assert migrate_stored_host(env_file) is False
        assert env_file.read_text(encoding="utf-8") == content

    def test_missing_file_is_not_an_error(self, tmp_path):
        assert migrate_stored_host(tmp_path / "absent.env") is False


class TestStoredHostMigrationAtStartup:
    def _start(self, monkeypatch, tmp_path, extra: str = ""):
        from mobaide.agent.app import create_app

        env_file = tmp_path / ".env"
        env_file.write_text(
            "JWT_SECRET_KEY=test-secret-for-the-listen-address-tests\n"
            "HOST=0.0.0.0\n" + extra,
            encoding="utf-8",
        )
        monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(tmp_path))
        monkeypatch.setattr("mobaide.agent.app._resolve_env_file", lambda: env_file)
        create_app()
        return parse_env_file(env_file)

    def test_agent_start_rewrites_the_stored_address(self, monkeypatch, tmp_path):
        assert self._start(monkeypatch, tmp_path)["HOST"] == "127.0.0.1"

    def test_test_stack_keeps_its_stored_address(self, monkeypatch, tmp_path):
        stored = self._start(monkeypatch, tmp_path, extra="MOBAIDE_ENV=test\n")

        assert stored["HOST"] == "0.0.0.0"
