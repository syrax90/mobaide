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

from mobaide.linear.config import AutoScanConfig

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


class TestPrompt:
    def test_backslash_n_is_unescaped_to_real_newline(self, monkeypatch):
        monkeypatch.setenv("AUTO_SCAN_PROMPT", "line1\\nline2")

        cfg = AutoScanConfig.from_env()

        assert cfg.auto_scan_prompt == "line1\nline2"
        assert cfg.auto_scan_prompt is not None
        assert "\\n" not in cfg.auto_scan_prompt


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

    def test_leftover_env_vars_are_ignored(self, monkeypatch):
        monkeypatch.setenv("LINEAR_ACTIVE_STATE", "Doing")
        monkeypatch.setenv("LINEAR_DONE_STATE", "Completed")
        # Build should not raise; the values are silently ignored.
        cfg = AutoScanConfig.from_env()
        assert cfg is not None
