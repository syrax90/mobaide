"""Tests for worktree slug generation and WorktreeManager."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from claude_mobile.linear.worktree import (
    WorktreeManager,
    WorktreeResult,
    convert_to_worktree_slug,
)


# -- convert_to_worktree_slug --


class TestConvertToWorktreeSlug:
    def test_basic(self):
        assert (
            convert_to_worktree_slug("CLA-8", "Fix the cancel button")
            == "cla-8-fix-the-cancel-button"
        )

    def test_special_characters_replaced(self):
        assert (
            convert_to_worktree_slug("ENG-42", "Add user's profile (v2)")
            == "eng-42-add-user-s-profile-v2"
        )

    def test_long_title_truncated_to_10_tokens(self):
        title = "one two three four five six seven eight nine ten eleven twelve"
        slug = convert_to_worktree_slug("X-1", title)
        parts = slug.split("-")
        assert len(parts) <= 10

    def test_collapses_multiple_dashes(self):
        slug = convert_to_worktree_slug("CLA-1", "hello---world")
        assert "--" not in slug

    def test_trims_leading_trailing_dashes(self):
        slug = convert_to_worktree_slug("CLA-1", "  hello  ")
        assert not slug.startswith("-")
        assert not slug.endswith("-")

    def test_empty_title_falls_back_to_identifier(self):
        slug = convert_to_worktree_slug("CLA-99", "")
        assert slug == "cla-99"

    def test_all_special_chars_title(self):
        slug = convert_to_worktree_slug("CLA-5", "!@#$%")
        assert slug  # should not be empty
        assert "cla-5" in slug

    def test_lowercase(self):
        slug = convert_to_worktree_slug("CLA-8", "Server Linear Integration")
        assert slug == slug.lower()

    def test_exact_10_tokens(self):
        title = "a b c d e f g h i"  # identifier + 9 title words = 10 tokens
        slug = convert_to_worktree_slug("X-1", title)
        assert slug == "x-1-a-b-c-d-e-f-g-h"
        assert len(slug.split("-")) == 10


# -- WorktreeManager --


class TestWorktreeManager:
    @pytest.fixture
    def manager(self):
        return WorktreeManager(
            worktree_base="claude_worktree",
            branch_prefix="feature/claude",
            main_branch="main",
        )

    async def test_create_worktree_reuses_existing(self, manager, tmp_path):
        """If the worktree directory already exists, return it without running git."""
        project_root = str(tmp_path / "project")
        os.makedirs(project_root)
        wt_dir = os.path.join(project_root, "claude_worktree", "cla-8-test")
        os.makedirs(wt_dir)

        result = await manager.create_worktree("cla-8-test", project_root)
        assert result is not None
        assert result.path == wt_dir
        assert result.is_new is False

    @patch("claude_mobile.linear.worktree._run_git")
    async def test_create_worktree_new_branch(self, mock_git, manager, tmp_path):
        """Successful worktree creation with a new branch."""
        project_root = str(tmp_path / "project")
        os.makedirs(project_root)

        # config longpaths -> success, fetch -> success, worktree add -b -> success
        mock_git.side_effect = [
            (0, "", ""),  # git config
            (0, "", ""),  # git fetch
            (0, "", ""),  # git worktree add -b
        ]

        result = await manager.create_worktree("cla-8-test", project_root)
        assert result is not None
        assert result.is_new is True
        assert result.branch == "feature/claude/cla-8-test"
        assert mock_git.call_count == 3

        worktree_add_call = mock_git.call_args_list[2]
        assert "--no-track" in worktree_add_call.args
        assert "-b" in worktree_add_call.args

    @patch("claude_mobile.linear.worktree._run_git")
    async def test_create_worktree_existing_branch_fallback(
        self, mock_git, manager, tmp_path
    ):
        """Falls back to worktree add without -b when branch already exists."""
        project_root = str(tmp_path / "project")
        os.makedirs(project_root)

        mock_git.side_effect = [
            (0, "", ""),   # git config
            (0, "", ""),   # git fetch
            (128, "", "fatal: branch already exists"),  # git worktree add -b fails
            (0, "", ""),   # git worktree add (no -b) succeeds
        ]

        result = await manager.create_worktree("cla-8-test", project_root)
        assert result is not None
        assert result.is_new is True
        assert mock_git.call_count == 4

    @patch("claude_mobile.linear.worktree._run_git")
    async def test_create_worktree_total_failure(self, mock_git, manager, tmp_path):
        """Returns None when both attempts fail."""
        project_root = str(tmp_path / "project")
        os.makedirs(project_root)

        mock_git.side_effect = [
            (0, "", ""),   # git config
            (0, "", ""),   # git fetch
            (128, "", "fatal: error"),  # first attempt
            (128, "", "fatal: error"),  # second attempt
        ]

        result = await manager.create_worktree("cla-8-test", project_root)
        assert result is None

    @patch("claude_mobile.linear.worktree._run_git")
    async def test_cleanup_worktree(self, mock_git, manager, tmp_path):
        mock_git.return_value = (0, "", "")
        success = await manager.cleanup_worktree("/some/path", str(tmp_path))
        assert success is True
