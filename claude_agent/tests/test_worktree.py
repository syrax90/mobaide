"""Tests for worktree slug generation and WorktreeManager."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from claude_mobile.linear.worktree import (
    WorktreeCreationError,
    WorktreeManager,
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
        """Successful worktree creation with a new branch — origin/main resolved."""
        project_root = str(tmp_path / "project")
        os.makedirs(project_root)

        mock_git.side_effect = [
            (0, "", ""),              # git config core.longpaths
            (0, "", ""),              # git fetch origin
            (0, "", ""),              # git rev-parse origin/main  (exists)
            (0, "", ""),              # git worktree add --no-track -b
        ]

        result = await manager.create_worktree("cla-8-test", project_root)
        assert result.is_new is True
        assert result.branch == "feature/claude/cla-8-test"
        assert mock_git.call_count == 4

        worktree_add_call = mock_git.call_args_list[3]
        assert "--no-track" in worktree_add_call.args
        assert "-b" in worktree_add_call.args
        assert "origin/main" in worktree_add_call.args

    @patch("claude_mobile.linear.worktree._run_git")
    async def test_create_worktree_accepts_explicit_branch_name(
        self, mock_git, manager, tmp_path,
    ):
        """Standard-mode path: explicit branch_name is used verbatim — no prefix."""
        project_root = str(tmp_path / "project")
        os.makedirs(project_root)

        mock_git.side_effect = [
            (0, "", ""),              # git config
            (0, "", ""),              # git fetch
            (0, "", ""),              # git rev-parse origin/main
            (0, "", ""),              # git worktree add -b
        ]

        result = await manager.create_worktree(
            "my-dir", project_root, branch_name="feature/user-typed",
        )
        assert result.branch == "feature/user-typed"
        worktree_add_call = mock_git.call_args_list[3]
        assert "feature/user-typed" in worktree_add_call.args
        # The prefix must NOT be applied when branch_name is given explicitly.
        assert "feature/claude/my-dir" not in worktree_add_call.args

    @patch("claude_mobile.linear.worktree._run_git")
    async def test_create_worktree_falls_back_to_local_main_when_no_origin(
        self, mock_git, manager, tmp_path,
    ):
        """If ``origin/main`` doesn't resolve but local ``main`` does, branch
        from the local ref so repos without a remote still work."""
        project_root = str(tmp_path / "project")
        os.makedirs(project_root)

        mock_git.side_effect = [
            (0, "", ""),                    # git config
            (128, "", "network error"),     # git fetch (ignored)
            (128, "", "no origin/main"),    # rev-parse origin/main (missing)
            (0, "", ""),                    # rev-parse refs/heads/main (ok)
            (0, "", ""),                    # git worktree add -b
        ]

        result = await manager.create_worktree("cla-1", project_root)
        assert result.is_new is True
        worktree_add_call = mock_git.call_args_list[4]
        assert "main" in worktree_add_call.args
        assert "origin/main" not in worktree_add_call.args

    @patch("claude_mobile.linear.worktree._run_git")
    async def test_create_worktree_falls_back_to_head_when_no_main(
        self, mock_git, manager, tmp_path,
    ):
        """Final fallback: neither ``origin/main`` nor local ``main`` exists →
        branch from ``HEAD`` so the worktree still gets created."""
        project_root = str(tmp_path / "project")
        os.makedirs(project_root)

        mock_git.side_effect = [
            (0, "", ""),                   # git config
            (128, "", "no remote"),        # git fetch
            (128, "", "no origin/main"),   # rev-parse origin/main
            (128, "", "no local main"),   # rev-parse refs/heads/main
            (0, "", ""),                   # git worktree add -b HEAD
        ]

        result = await manager.create_worktree("cla-1", project_root)
        assert result.is_new is True
        worktree_add_call = mock_git.call_args_list[4]
        assert "HEAD" in worktree_add_call.args

    @patch("claude_mobile.linear.worktree._run_git")
    async def test_create_worktree_existing_branch_fallback(
        self, mock_git, manager, tmp_path
    ):
        """Falls back to worktree add without -b when branch already exists."""
        project_root = str(tmp_path / "project")
        os.makedirs(project_root)

        mock_git.side_effect = [
            (0, "", ""),                                  # git config
            (0, "", ""),                                  # git fetch
            (0, "", ""),                                  # rev-parse origin/main
            (128, "", "fatal: branch already exists"),    # worktree add -b fails
            (0, "", ""),                                  # worktree add (no -b)
        ]

        result = await manager.create_worktree("cla-8-test", project_root)
        assert result.is_new is True
        assert mock_git.call_count == 5

    @patch("claude_mobile.linear.worktree._run_git")
    async def test_create_worktree_raises_on_total_failure(
        self, mock_git, manager, tmp_path,
    ):
        """Replaces the old ``returns None`` contract: hard failure surfaces
        as ``WorktreeCreationError`` so the session router can return HTTP 400
        with the git stderr instead of silently landing in the project root."""
        project_root = str(tmp_path / "project")
        os.makedirs(project_root)

        mock_git.side_effect = [
            (0, "", ""),                   # git config
            (0, "", ""),                   # git fetch
            (0, "", ""),                   # rev-parse origin/main
            (128, "", "fatal: boom"),      # worktree add -b fails
            (128, "", "fatal: still no"), # worktree add fails too
        ]

        with pytest.raises(WorktreeCreationError) as exc_info:
            await manager.create_worktree("cla-8-test", project_root)
        assert exc_info.value.branch == "feature/claude/cla-8-test"
        assert "still no" in exc_info.value.stderr or "boom" in exc_info.value.stderr

    @patch("claude_mobile.linear.worktree._run_git")
    async def test_cleanup_worktree(self, mock_git, manager, tmp_path):
        mock_git.return_value = (0, "", "")
        success = await manager.cleanup_worktree("/some/path", str(tmp_path))
        assert success is True
