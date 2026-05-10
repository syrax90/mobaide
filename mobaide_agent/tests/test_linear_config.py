"""Tests for AutoScanConfig.from_env — env-driven auto-scan settings.

Post-CLA-39: AutoScanConfig no longer owns a projects dict and
``from_env()`` always returns an instance. The set of scannable projects is
resolved live from ``mobaide.core.projects_store.ProjectsStore``.

Post-CLA-52: ``LINEAR_ACTIVE_STATE`` / ``LINEAR_DONE_STATE`` are no longer
on ``AutoScanConfig``. Per-project workflow-state names live on
``ProjectRecord.auto_scan_filter``.
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
    "AUTO_REMOVE_WORKTREES",
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
        assert cfg.auto_remove_worktrees is True
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
        monkeypatch.setenv("AGENT_STATE_DIR", "/tmp/my-state")

        cfg = AutoScanConfig.from_env()

        assert cfg.worktree_base == "wt"
        assert cfg.branch_prefix == "feat/"
        assert cfg.main_branch == "develop"
        assert cfg.state_dir == "/tmp/my-state"


class TestDeprecatedFieldsRemoved:
    """``LINEAR_ACTIVE_STATE`` / ``LINEAR_DONE_STATE`` have been removed.

    The pydantic-settings model is configured with ``extra="ignore"``, so a
    leftover env var from a previous deployment does not crash boot. These
    tests pin both halves of that contract: the field is gone, AND a
    leftover env var is silently ignored.
    """

    def test_linear_active_state_field_does_not_exist(self):
        cfg = AutoScanConfig.from_env()
        assert not hasattr(cfg, "linear_active_state")

    def test_linear_done_state_field_does_not_exist(self):
        cfg = AutoScanConfig.from_env()
        assert not hasattr(cfg, "linear_done_state")

    def test_leftover_env_vars_are_ignored(self, monkeypatch):
        monkeypatch.setenv("LINEAR_ACTIVE_STATE", "Doing")
        monkeypatch.setenv("LINEAR_DONE_STATE", "Completed")
        # Build should not raise; the values are silently ignored.
        cfg = AutoScanConfig.from_env()
        assert cfg is not None


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
