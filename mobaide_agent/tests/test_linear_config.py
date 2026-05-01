"""Tests for AutoScanConfig.from_env — env-driven auto-scan settings.

Post-CLA-39: AutoScanConfig no longer owns a projects dict and
``from_env()`` always returns an instance. The set of scannable projects is
resolved live from ``mobaide.core.projects_store.ProjectsStore``.
"""
from __future__ import annotations

import pytest

from mobaide.linear.config import (
    _DEFAULT_AUTO_SCAN_PROMPT,
    AutoScanConfig,
)

_ENV_VARS = (
    "AUTO_SCAN_PROMPT",
    "SCAN_INTERVAL_SECONDS",
    "WORKTREE_BASE",
    "BRANCH_PREFIX",
    "MAIN_BRANCH",
    "LINEAR_ACTIVE_STATE",
    "AUTO_REMOVE_WORKTREES",
    "LINEAR_DONE_STATE",
    "AGENT_STATE_DIR",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


_ = _clean_env


class TestAlwaysReturnsInstance:
    def test_no_env_returns_defaults(self):
        cfg = AutoScanConfig.from_env()
        assert cfg is not None
        assert cfg.worktree_base == "claude_worktree"
        assert cfg.scan_interval_seconds == 300
        assert cfg.branch_prefix == ""
        assert cfg.main_branch == "main"
        assert cfg.linear_active_state == "In Progress"
        assert cfg.auto_remove_worktrees is True
        assert cfg.linear_done_state == "Done"
        assert cfg.state_dir == "state"
        assert cfg.auto_scan_prompt == _DEFAULT_AUTO_SCAN_PROMPT

    def test_defaults_are_deterministic(self):
        # Back-to-back calls with the same env produce equal configs.
        a = AutoScanConfig.from_env()
        b = AutoScanConfig.from_env()
        assert a == b


class TestPrompt:
    def test_backslash_n_is_unescaped_to_real_newline(self, monkeypatch):
        monkeypatch.setenv("AUTO_SCAN_PROMPT", "line1\\nline2")

        cfg = AutoScanConfig.from_env()

        assert cfg.auto_scan_prompt == "line1\nline2"
        assert cfg.auto_scan_prompt is not None
        assert "\\n" not in cfg.auto_scan_prompt

    def test_empty_string_uses_default(self, monkeypatch):
        monkeypatch.setenv("AUTO_SCAN_PROMPT", "")

        cfg = AutoScanConfig.from_env()

        assert cfg.auto_scan_prompt == _DEFAULT_AUTO_SCAN_PROMPT


class TestIntegerParsing:
    def test_scan_interval_seconds_parsed_as_int(self, monkeypatch):
        monkeypatch.setenv("SCAN_INTERVAL_SECONDS", "120")

        cfg = AutoScanConfig.from_env()

        assert cfg.scan_interval_seconds == 120
        assert isinstance(cfg.scan_interval_seconds, int)


class TestEnvOverrides:
    def test_all_string_overrides_applied(self, monkeypatch):
        monkeypatch.setenv("WORKTREE_BASE", "wt")
        monkeypatch.setenv("BRANCH_PREFIX", "feat/")
        monkeypatch.setenv("MAIN_BRANCH", "develop")
        monkeypatch.setenv("LINEAR_ACTIVE_STATE", "Doing")
        monkeypatch.setenv("LINEAR_DONE_STATE", "Completed")
        monkeypatch.setenv("AGENT_STATE_DIR", "/tmp/my-state")

        cfg = AutoScanConfig.from_env()

        assert cfg.worktree_base == "wt"
        assert cfg.branch_prefix == "feat/"
        assert cfg.main_branch == "develop"
        assert cfg.linear_active_state == "Doing"
        assert cfg.linear_done_state == "Completed"
        assert cfg.state_dir == "/tmp/my-state"


class TestWorktreeCleanup:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("true", True), ("TRUE", True), ("1", True),
            ("yes", True), ("on", True),
            ("false", False), ("0", False), ("no", False),
            ("off", False), ("", False), ("garbage", False),
        ],
    )
    def test_auto_remove_worktrees_boolean_parsing(
        self, monkeypatch, raw, expected,
    ):
        monkeypatch.setenv("AUTO_REMOVE_WORKTREES", raw)

        cfg = AutoScanConfig.from_env()

        assert cfg.auto_remove_worktrees is expected

    def test_auto_remove_worktrees_defaults_true_when_unset(self):
        cfg = AutoScanConfig.from_env()
        assert cfg.auto_remove_worktrees is True
