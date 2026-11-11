"""Tests for worktree slug generation and WorktreeManager."""

from __future__ import annotations

import os
import shutil
import subprocess
from unittest.mock import AsyncMock, patch

import pytest

from mobaide.linear.worktree import (
    WorktreeCreationError,
    WorktreeManager,
    WorktreeResult,
    convert_to_worktree_slug,
    create_standard_worktree,
)

from .git_repo_support import init_repo, requires_git, run_git_sync


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

    def test_empty_title_falls_back_to_identifier(self):
        slug = convert_to_worktree_slug("CLA-99", "")
        assert slug == "cla-99"

    def test_all_special_chars_title(self):
        slug = convert_to_worktree_slug("CLA-5", "!@#$%")
        assert slug  # should not be empty
        assert "cla-5" in slug


# -- create_standard_worktree --


class TestCreateStandardWorktree:
    """The helper both ``ClaudeLauncher`` (Standard) and ``SessionManager``
    (Terminal) reuse: derive a slug + default a blank branch, then delegate to
    ``mgr.create_worktree(slug, project_root, branch_name=branch)``. The manager
    is mocked so no real ``git`` runs."""

    @staticmethod
    def _mock_mgr(result_path: str = "/repo/wt") -> AsyncMock:
        mgr = AsyncMock()
        mgr.create_worktree.return_value = WorktreeResult(
            path=result_path, is_new=True, branch="b",
        )
        return mgr

    async def test_explicit_branch_used_verbatim_and_slugged(self):
        mgr = self._mock_mgr()

        result = await create_standard_worktree(mgr, "/repo", "feature/x")

        mgr.create_worktree.assert_awaited_once()
        call = mgr.create_worktree.await_args
        assert call.args[0] == "feature-x"  # convert_to_worktree_slug("", "feature/x")
        assert call.args[1] == "/repo"
        assert call.kwargs["branch_name"] == "feature/x"
        assert result.path == "/repo/wt"

    async def test_blank_branch_defaults_to_prefixed_unique_branch(self):
        mgr = self._mock_mgr()

        await create_standard_worktree(mgr, "/repo", "", fallback_prefix="terminal-session")

        branch = mgr.create_worktree.await_args.kwargs["branch_name"]
        assert branch.startswith("terminal-session-")
        assert len(branch) > len("terminal-session-")  # a hex suffix was appended

    async def test_two_blank_branch_calls_get_distinct_branches(self):
        """Two worktree-on sessions with blank branches must not collide on the
        same branch/dir — the hex suffix is unique per call."""
        mgr = self._mock_mgr()

        await create_standard_worktree(mgr, "/repo", None)
        await create_standard_worktree(mgr, "/repo", None)

        branches = [c.kwargs["branch_name"] for c in mgr.create_worktree.await_args_list]
        assert branches[0] != branches[1]


# -- WorktreeManager --


class TestWorktreeManager:
    @pytest.fixture
    def manager(self):
        return WorktreeManager(
            worktree_base="mobaide_worktree",
            branch_prefix="feature/mobaide",
            main_branch="main",
        )

    async def test_create_worktree_reuses_existing(self, manager, tmp_path):
        """If the worktree directory already exists, return it without running git."""
        project_root = str(tmp_path / "project")
        os.makedirs(project_root)
        wt_dir = os.path.join(project_root, "mobaide_worktree", "cla-8-test")
        os.makedirs(wt_dir)

        result = await manager.create_worktree("cla-8-test", project_root)
        assert result is not None
        assert result.path == wt_dir
        assert result.is_new is False

    @patch("mobaide.linear.worktree._run_git")
    async def test_create_worktree_new_branch(self, mock_git, manager, tmp_path):
        """Successful worktree creation with a new branch — origin/main resolved."""
        project_root = str(tmp_path / "project")
        os.makedirs(project_root)

        mock_git.side_effect = [
            (0, "", ""),              # git worktree list (no match)
            (0, "", ""),              # git config core.longpaths
            (0, "", ""),              # git fetch origin
            (1, "", ""),                    # symbolic-ref origin/HEAD (unset)
            (0, "", ""),              # git rev-parse origin/main  (exists)
            (0, "", ""),              # git worktree add --no-track -b
        ]

        result = await manager.create_worktree("cla-8-test", project_root)
        assert result.is_new is True
        assert result.branch == "feature/mobaide/cla-8-test"
        assert mock_git.call_count == 6

        worktree_add_call = mock_git.call_args_list[5]
        assert "--no-track" in worktree_add_call.args
        assert "-b" in worktree_add_call.args
        assert "origin/main" in worktree_add_call.args

    @patch("mobaide.linear.worktree._run_git")
    async def test_create_worktree_accepts_explicit_branch_name(
        self, mock_git, manager, tmp_path,
    ):
        """Standard-mode path: explicit branch_name is used verbatim — no prefix."""
        project_root = str(tmp_path / "project")
        os.makedirs(project_root)

        mock_git.side_effect = [
            (0, "", ""),              # git worktree list (no match)
            (0, "", ""),              # git config
            (0, "", ""),              # git fetch
            (1, "", ""),                    # symbolic-ref origin/HEAD (unset)
            (0, "", ""),              # git rev-parse origin/main
            (0, "", ""),              # git worktree add -b
        ]

        result = await manager.create_worktree(
            "my-dir", project_root, branch_name="feature/user-typed",
        )
        assert result.branch == "feature/user-typed"
        worktree_add_call = mock_git.call_args_list[5]
        assert "feature/user-typed" in worktree_add_call.args
        # The prefix must NOT be applied when branch_name is given explicitly.
        assert "feature/mobaide/my-dir" not in worktree_add_call.args

    @patch("mobaide.linear.worktree._run_git")
    async def test_create_worktree_rejects_dash_prefixed_branch(
        self, mock_git, manager, tmp_path,
    ):
        """A branch name starting with '-' is rejected before any git runs.

        Git refs cannot begin with '-'; the early guard stops a client-supplied
        name from being parsed as a ``git worktree add`` option (argument
        injection), surfacing as ``WorktreeCreationError`` (mapped to HTTP 400).
        """
        project_root = str(tmp_path / "project")
        os.makedirs(project_root)

        with pytest.raises(WorktreeCreationError):
            await manager.create_worktree(
                "my-dir", project_root, branch_name="--upload-pack=evil",
            )
        mock_git.assert_not_awaited()

    @patch("mobaide.linear.worktree._run_git")
    async def test_create_worktree_falls_back_to_local_main_when_no_origin(
        self, mock_git, manager, tmp_path,
    ):
        """If ``origin/main`` doesn't resolve but local ``main`` does, branch
        from the local ref so repos without a remote still work."""
        project_root = str(tmp_path / "project")
        os.makedirs(project_root)

        mock_git.side_effect = [
            (0, "", ""),                    # git worktree list (no match)
            (0, "", ""),                    # git config
            (128, "", "network error"),     # git fetch (ignored)
            (1, "", ""),                    # symbolic-ref origin/HEAD (unset)
            (128, "", "no origin/main"),    # rev-parse origin/main (missing)
            (0, "", ""),                    # rev-parse refs/heads/main (ok)
            (0, "", ""),                    # git worktree add -b
        ]

        result = await manager.create_worktree("cla-1", project_root)
        assert result.is_new is True
        worktree_add_call = mock_git.call_args_list[6]
        assert "main" in worktree_add_call.args
        assert "origin/main" not in worktree_add_call.args

    @patch("mobaide.linear.worktree._run_git")
    async def test_create_worktree_falls_back_to_head_when_no_main(
        self, mock_git, manager, tmp_path,
    ):
        """Final fallback: neither ``origin/main`` nor local ``main`` exists →
        branch from ``HEAD`` so the worktree still gets created."""
        project_root = str(tmp_path / "project")
        os.makedirs(project_root)

        mock_git.side_effect = [
            (0, "", ""),                   # git worktree list (no match)
            (0, "", ""),                   # git config
            (128, "", "no remote"),        # git fetch
            (1, "", ""),                    # symbolic-ref origin/HEAD (unset)
            (128, "", "no origin/main"),   # rev-parse origin/main
            (128, "", "no local main"),   # rev-parse refs/heads/main
            (0, "", ""),                   # git worktree add -b HEAD
        ]

        result = await manager.create_worktree("cla-1", project_root)
        assert result.is_new is True
        worktree_add_call = mock_git.call_args_list[6]
        assert "HEAD" in worktree_add_call.args

    @patch("mobaide.linear.worktree._run_git")
    async def test_create_worktree_existing_branch_fallback(
        self, mock_git, manager, tmp_path
    ):
        """Falls back to worktree add without -b when branch already exists."""
        project_root = str(tmp_path / "project")
        os.makedirs(project_root)

        mock_git.side_effect = [
            (0, "", ""),                                  # git worktree list (no match)
            (0, "", ""),                                  # git config
            (0, "", ""),                                  # git fetch
            (1, "", ""),                    # symbolic-ref origin/HEAD (unset)
            (0, "", ""),                                  # rev-parse origin/main
            (128, "", "fatal: branch already exists"),    # worktree add -b fails
            (0, "", ""),                                  # worktree add (no -b)
        ]

        result = await manager.create_worktree("cla-8-test", project_root)
        assert result.is_new is True
        assert mock_git.call_count == 7

    @patch("mobaide.linear.worktree._run_git")
    async def test_create_worktree_raises_on_total_failure(
        self, mock_git, manager, tmp_path,
    ):
        """Replaces the old ``returns None`` contract: hard failure surfaces
        as ``WorktreeCreationError`` so the session router can return HTTP 400
        with the git stderr instead of silently landing in the project root."""
        project_root = str(tmp_path / "project")
        os.makedirs(project_root)

        mock_git.side_effect = [
            (0, "", ""),                   # git worktree list (no match)
            (0, "", ""),                   # git config
            (0, "", ""),                   # git fetch
            (1, "", ""),                    # symbolic-ref origin/HEAD (unset)
            (0, "", ""),                   # rev-parse origin/main
            (128, "", "fatal: boom"),      # worktree add -b fails
            (128, "", "fatal: still no"), # worktree add fails too
        ]

        with pytest.raises(WorktreeCreationError) as exc_info:
            await manager.create_worktree("cla-8-test", project_root)
        assert exc_info.value.branch == "feature/mobaide/cla-8-test"
        assert "still no" in exc_info.value.stderr or "boom" in exc_info.value.stderr

    @patch("mobaide.linear.worktree._run_git")
    async def test_cleanup_worktree(self, mock_git, manager, tmp_path):
        mock_git.return_value = (0, "", "")
        success = await manager.cleanup_worktree("/some/path", str(tmp_path))
        assert success is True


@requires_git
class TestCreateWorktreeReopensBranchWorktree:
    """Real-git coverage: asking for a branch that is already checked out in
    another linked worktree reopens that worktree instead of failing git's
    one-checkout-per-branch rule."""

    @pytest.fixture
    def repo(self, tmp_path):
        return init_repo(str(tmp_path / "project"))

    @staticmethod
    def _manager(worktree_base: str = "claude_worktree") -> WorktreeManager:
        return WorktreeManager(
            worktree_base=worktree_base,
            branch_prefix="feature/mobaide",
            main_branch="main",
        )

    async def test_reuses_worktree_created_under_another_base(self, repo):
        first = await self._manager("mobaide_worktree").create_worktree(
            "claude-session-56a3f247", repo, branch_name="claude-session-56a3f247",
        )

        result = await self._manager("claude_worktree").create_worktree(
            "claude-session-56a3f247", repo, branch_name="claude-session-56a3f247",
        )

        assert result.is_new is False
        assert os.path.samefile(result.path, first.path)
        assert result.branch == "claude-session-56a3f247"
        assert not os.path.exists(
            os.path.join(repo, "claude_worktree", "claude-session-56a3f247")
        )

    async def test_prunes_deleted_worktree_and_checks_branch_out_again(self, repo):
        stale = os.path.join(repo, "old", "topic")
        run_git_sync(repo, "worktree", "add", "-q", "-b", "topic", stale)
        shutil.rmtree(stale)

        result = await self._manager().create_worktree(
            "topic", repo, branch_name="topic",
        )

        assert result.is_new is True
        assert result.path == os.path.join(repo, "claude_worktree", "topic")
        head = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            cwd=result.path, capture_output=True, text=True, check=True,
        )
        assert head.stdout.strip() == "topic"

    async def test_branch_checked_out_in_project_root_still_fails(self, repo):
        with pytest.raises(WorktreeCreationError, match="already used by worktree"):
            await self._manager().create_worktree("main", repo, branch_name="main")

    async def test_project_registered_at_linked_worktree_is_not_reused(self, repo):
        linked = os.path.join(repo, "linked")
        run_git_sync(repo, "worktree", "add", "-q", "-b", "topic", linked)

        with pytest.raises(WorktreeCreationError, match="already used by worktree"):
            await self._manager().create_worktree("topic", linked, branch_name="topic")
