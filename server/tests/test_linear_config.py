"""Tests for AutoScanConfig.from_env — env-driven auto-scan config."""
from __future__ import annotations

import json

import pytest

from claude_mobile.linear.config import (
    _DEFAULT_AUTO_SCAN_PROMPT,
    AutoScanConfig,
)

_ENV_VARS = (
    "PROJECTS",
    "AUTO_SCAN_PROMPT",
    "SCAN_INTERVAL_SECONDS",
    "WORKTREE_BASE",
    "BRANCH_PREFIX",
    "MAIN_BRANCH",
    "LINEAR_ACTIVE_STATE",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# Dummy reference: the autouse fixture is invoked by pytest via name-based
# lookup, so Pyright's "not accessed" hint is a false positive.
_ = _clean_env


def _projects_json(**overrides):
    """Build a PROJECTS JSON blob with at least one key-carrying project."""
    base = {
        "KeyProject": {
            "path": "C:/workspace/key",
            "linear_api_key": "lin_api_xxx",
        }
    }
    base.update(overrides)
    return json.dumps(base)


class TestAutoScanConfigNoProjects:
    def test_returns_none_when_projects_env_unset(self):
        assert AutoScanConfig.from_env() is None

    def test_returns_none_when_no_project_has_linear_key(self, monkeypatch):
        monkeypatch.setenv(
            "PROJECTS",
            json.dumps(
                {
                    "A": {"path": "C:/a"},
                    "B": {"path": "C:/b", "conda_env": "ml"},
                }
            ),
        )

        assert AutoScanConfig.from_env() is None

    def test_returns_none_when_projects_env_empty_string(self, monkeypatch):
        monkeypatch.setenv("PROJECTS", "")

        assert AutoScanConfig.from_env() is None


class TestAutoScanConfigProjectFiltering:
    def test_filters_out_projects_without_linear_key(self, monkeypatch):
        monkeypatch.setenv(
            "PROJECTS",
            json.dumps(
                {
                    "WithKey": {
                        "path": "C:/with",
                        "linear_api_key": "lin_api_1",
                    },
                    "NoKey": {"path": "C:/without"},
                }
            ),
        )

        cfg = AutoScanConfig.from_env()

        assert cfg is not None
        assert "WithKey" in cfg.projects
        assert "NoKey" not in cfg.projects
        assert len(cfg.projects) == 1

    def test_keeps_all_key_carrying_projects(self, monkeypatch):
        monkeypatch.setenv(
            "PROJECTS",
            json.dumps(
                {
                    "A": {"path": "C:/a", "linear_api_key": "lin_a"},
                    "B": {"path": "C:/b", "linear_api_key": "lin_b"},
                }
            ),
        )

        cfg = AutoScanConfig.from_env()

        assert cfg is not None
        assert set(cfg.projects.keys()) == {"A", "B"}


class TestAutoScanConfigPrompt:
    def test_backslash_n_is_unescaped_to_real_newline(self, monkeypatch):
        monkeypatch.setenv("PROJECTS", _projects_json())
        # Literal characters: the two chars '\' and 'n', NOT a real newline.
        monkeypatch.setenv("AUTO_SCAN_PROMPT", "line1\\nline2")

        cfg = AutoScanConfig.from_env()

        assert cfg is not None
        assert cfg.auto_scan_prompt == "line1\nline2"
        assert "\\n" not in cfg.auto_scan_prompt

    def test_unset_uses_default_prompt(self, monkeypatch):
        monkeypatch.setenv("PROJECTS", _projects_json())

        cfg = AutoScanConfig.from_env()

        assert cfg is not None
        assert cfg.auto_scan_prompt == _DEFAULT_AUTO_SCAN_PROMPT

    def test_empty_string_uses_default_prompt(self, monkeypatch):
        monkeypatch.setenv("PROJECTS", _projects_json())
        monkeypatch.setenv("AUTO_SCAN_PROMPT", "")

        cfg = AutoScanConfig.from_env()

        assert cfg is not None
        assert cfg.auto_scan_prompt == _DEFAULT_AUTO_SCAN_PROMPT


class TestAutoScanConfigIntegerParsing:
    def test_scan_interval_seconds_parsed_as_int(self, monkeypatch):
        monkeypatch.setenv("PROJECTS", _projects_json())
        monkeypatch.setenv("SCAN_INTERVAL_SECONDS", "120")

        cfg = AutoScanConfig.from_env()

        assert cfg is not None
        assert cfg.scan_interval_seconds == 120
        assert isinstance(cfg.scan_interval_seconds, int)


class TestAutoScanConfigDefaults:
    def test_defaults_when_optional_env_unset(self, monkeypatch):
        monkeypatch.setenv("PROJECTS", _projects_json())

        cfg = AutoScanConfig.from_env()

        assert cfg is not None
        assert cfg.worktree_base == "claude_worktree"
        assert cfg.scan_interval_seconds == 300
        assert cfg.branch_prefix == ""
        assert cfg.main_branch == "main"
        assert cfg.linear_active_state == "In Progress"

    def test_env_overrides_applied(self, monkeypatch):
        monkeypatch.setenv("PROJECTS", _projects_json())
        monkeypatch.setenv("WORKTREE_BASE", "wt")
        monkeypatch.setenv("BRANCH_PREFIX", "feat/")
        monkeypatch.setenv("MAIN_BRANCH", "develop")
        monkeypatch.setenv("LINEAR_ACTIVE_STATE", "Doing")

        cfg = AutoScanConfig.from_env()

        assert cfg is not None
        assert cfg.worktree_base == "wt"
        assert cfg.branch_prefix == "feat/"
        assert cfg.main_branch == "develop"
        assert cfg.linear_active_state == "Doing"
