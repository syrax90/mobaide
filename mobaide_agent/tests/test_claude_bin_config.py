"""Tests for the ``AgentConfig.claude_bin`` field.

Pinned contract: ``MOBAIDE_CLAUDE_BIN`` lines in ``.env`` must surface
on the typed ``AgentConfig.claude_bin`` field. Without a typed field
declared explicitly with ``validation_alias="MOBAIDE_CLAUDE_BIN"``,
``extra="ignore"`` (config.py:80) silently drops the line on the floor —
the gotcha already documented for the ``projects`` / ``PROJECTS`` pair.

The Windows installer writes ``MOBAIDE_CLAUDE_BIN=<abs-path>`` into
``<prefix>/mobaide_agent/.env`` so the agent always finds ``claude.cmd``
even when PATH is non-standard (relocated npm prefix, chocolatey, etc.);
if this field regresses the entire install flow's auto-detection
becomes dead code.
"""
from __future__ import annotations

import pytest

from mobaide.agent.config import AgentConfig


# Match ``test_agent_config.py`` — strip every env var AgentConfig
# consults so host-env values cannot leak into the unit under test.
_ENV_VARS = (
    "JWT_SECRET_KEY",
    "MOBAIDE_CLAUDE_BIN",
    "RELAY_SERVER_URL",
    "RELAY_AGENT_ID",
    "RELAY_AGENT_TOKEN",
    "PROJECTS",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


_ = _clean_env  # silence pyright "not accessed" hint


def _write_env_file(tmp_path, body: str):
    env_file = tmp_path / "test.env"
    env_file.write_text(body, encoding="utf-8")
    return env_file


class TestAgentConfigClaudeBinFromEnvFile:
    def test_env_file_populates_claude_bin(self, tmp_path):
        env_file = _write_env_file(
            tmp_path,
            "JWT_SECRET_KEY=s\nMOBAIDE_CLAUDE_BIN=/abs/path/to/claude\n",
        )

        cfg = AgentConfig.from_env(env_file=env_file)

        assert cfg.claude_bin == "/abs/path/to/claude"

    def test_env_file_windows_style_path_preserved_literally(self, tmp_path):
        # The installer writes a fully-expanded absolute Windows path —
        # the field must carry it through verbatim, no normalisation, so
        # the resolver downstream can pass it to ``shutil`` / ``os.path``
        # exactly as the operator (or installer) wrote it.
        env_file = _write_env_file(
            tmp_path,
            "JWT_SECRET_KEY=s\n"
            r"MOBAIDE_CLAUDE_BIN=C:\Users\alice\AppData\Roaming\npm\claude.cmd"
            "\n",
        )

        cfg = AgentConfig.from_env(env_file=env_file)

        assert cfg.claude_bin == (
            r"C:\Users\alice\AppData\Roaming\npm\claude.cmd"
        )


class TestAgentConfigClaudeBinFromProcessEnv:
    def test_process_env_populates_claude_bin(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "s")
        monkeypatch.setenv("MOBAIDE_CLAUDE_BIN", "/abs/path/from/env")

        cfg = AgentConfig.from_env()

        assert cfg.claude_bin == "/abs/path/from/env"
