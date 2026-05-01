"""Tests for the CLA-46 git management additions to ``WorktreeManager``.

Covers the two parsers (``_parse_for_each_ref`` /
``_parse_worktree_porcelain``) and the two new mutating methods
(``delete_branch`` / ``remove_worktree``). Mutators are driven through
mocked ``_run_git`` to keep the suite fast and platform-independent.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from mobaide.linear.worktree import (
    BranchDeleteError,
    BranchInfo,
    WorktreeInfo,
    WorktreeManager,
    WorktreeRemoveError,
    _parse_for_each_ref,
    _parse_worktree_porcelain,
)


class TestParseForEachRef:
    def test_empty_input_returns_empty_list(self):
        assert _parse_for_each_ref("") == []

    def test_single_branch_with_no_upstream(self):
        # name\0upstream\0track\0sha\0head — empty upstream + no track + no HEAD marker
        line = "main\x00\x00\x00abc123\x00 \n"
        result = _parse_for_each_ref(line)
        assert len(result) == 1
        b = result[0]
        assert b.name == "main"
        assert b.upstream is None
        assert b.ahead == 0
        assert b.behind == 0
        assert b.gone is False
        assert b.is_current is False
        assert b.sha == "abc123"

    def test_current_branch_marker(self):
        # %(HEAD) is "*" for the current branch.
        line = "feature/x\x00origin/feature/x\x00\x00deadbeef\x00*\n"
        result = _parse_for_each_ref(line)
        assert len(result) == 1
        assert result[0].name == "feature/x"
        assert result[0].is_current is True
        assert result[0].upstream == "origin/feature/x"

    def test_gone_upstream_sets_gone_flag(self):
        # Track field surfaces "[gone]" when upstream was force-deleted on remote.
        line = "stale\x00origin/stale\x00[gone]\x00cafef00d\x00 \n"
        result = _parse_for_each_ref(line)
        assert len(result) == 1
        assert result[0].gone is True
        # ahead/behind must stay 0 when gone is true (no track counters meaningful).
        assert result[0].ahead == 0
        assert result[0].behind == 0

    def test_ahead_behind_parsing(self):
        # Track format is git's "[ahead 2, behind 1]".
        line = "topic\x00origin/topic\x00[ahead 2, behind 1]\x00sha\x00 \n"
        result = _parse_for_each_ref(line)
        assert result[0].ahead == 2
        assert result[0].behind == 1
        assert result[0].gone is False

    def test_ahead_only(self):
        line = "ahead-only\x00origin/ahead-only\x00[ahead 5]\x00sha\x00 \n"
        result = _parse_for_each_ref(line)
        assert result[0].ahead == 5
        assert result[0].behind == 0

    def test_behind_only(self):
        line = "behind-only\x00origin/behind-only\x00[behind 3]\x00sha\x00 \n"
        result = _parse_for_each_ref(line)
        assert result[0].ahead == 0
        assert result[0].behind == 3

    def test_multiple_branches_one_current(self):
        stdout = (
            "main\x00origin/main\x00\x00sha1\x00*\n"
            "topic\x00\x00\x00sha2\x00 \n"
            "stale\x00origin/stale\x00[gone]\x00sha3\x00 \n"
        )
        result = _parse_for_each_ref(stdout)
        assert [b.name for b in result] == ["main", "topic", "stale"]
        assert [b.is_current for b in result] == [True, False, False]
        assert result[2].gone is True

    def test_blank_lines_skipped(self):
        stdout = "\nmain\x00\x00\x00sha\x00*\n\n"
        result = _parse_for_each_ref(stdout)
        assert len(result) == 1
        assert result[0].name == "main"

    def test_short_line_skipped_silently(self):
        # Lines without all 5 fields must be ignored, not crash.
        stdout = "garbage-only-one-field\nmain\x00\x00\x00sha\x00*\n"
        result = _parse_for_each_ref(stdout)
        assert len(result) == 1
        assert result[0].name == "main"


class TestParseWorktreePorcelain:
    def test_empty_input_returns_empty_list(self):
        assert _parse_worktree_porcelain("") == []

    def test_single_worktree_with_branch(self):
        stdout = (
            "worktree /repo\n"
            "HEAD abc123\n"
            "branch refs/heads/main\n"
            "\n"
        )
        result = _parse_worktree_porcelain(stdout)
        assert len(result) == 1
        wt = result[0]
        assert wt.path == "/repo"
        assert wt.head == "abc123"
        assert wt.branch == "refs/heads/main"
        assert wt.detached is False
        assert wt.locked is False
        assert wt.prunable is False

    def test_detached_worktree(self):
        stdout = (
            "worktree /repo/wt\n"
            "HEAD deadbeef\n"
            "detached\n"
            "\n"
        )
        result = _parse_worktree_porcelain(stdout)
        assert len(result) == 1
        wt = result[0]
        assert wt.detached is True
        assert wt.branch is None
        assert wt.head == "deadbeef"

    def test_locked_worktree_with_reason(self):
        stdout = (
            "worktree /repo/wt\n"
            "HEAD sha\n"
            "branch refs/heads/topic\n"
            "locked manual lock for migration\n"
            "\n"
        )
        result = _parse_worktree_porcelain(stdout)
        assert len(result) == 1
        wt = result[0]
        assert wt.locked is True
        assert wt.locked_reason == "manual lock for migration"

    def test_locked_worktree_without_reason(self):
        # Plain "locked\n" with no value.
        stdout = (
            "worktree /repo/wt\n"
            "HEAD sha\n"
            "branch refs/heads/topic\n"
            "locked\n"
            "\n"
        )
        result = _parse_worktree_porcelain(stdout)
        assert result[0].locked is True
        assert result[0].locked_reason is None

    def test_prunable_worktree(self):
        stdout = (
            "worktree /repo/orphan\n"
            "HEAD abc\n"
            "branch refs/heads/orphan\n"
            "prunable gitdir file points to non-existent location\n"
            "\n"
        )
        result = _parse_worktree_porcelain(stdout)
        assert result[0].prunable is True
        assert "non-existent location" in (result[0].prunable_reason or "")

    def test_multi_stanza(self):
        # Three worktrees in one porcelain payload, including the main repo.
        stdout = (
            "worktree /repo\n"
            "HEAD sha1\n"
            "branch refs/heads/main\n"
            "\n"
            "worktree /repo/claude_worktree/cla-1\n"
            "HEAD sha2\n"
            "branch refs/heads/feature/claude/cla-1\n"
            "\n"
            "worktree /repo/claude_worktree/detached\n"
            "HEAD sha3\n"
            "detached\n"
            "\n"
        )
        result = _parse_worktree_porcelain(stdout)
        assert len(result) == 3
        assert [wt.path for wt in result] == [
            "/repo",
            "/repo/claude_worktree/cla-1",
            "/repo/claude_worktree/detached",
        ]
        assert result[2].detached is True
        assert result[1].branch == "refs/heads/feature/claude/cla-1"

    def test_carriage_returns_stripped(self):
        # Git-on-Windows can emit CRLF-terminated lines.
        stdout = (
            "worktree /repo\r\n"
            "HEAD sha\r\n"
            "branch refs/heads/main\r\n"
            "\r\n"
        )
        result = _parse_worktree_porcelain(stdout)
        assert len(result) == 1
        assert result[0].path == "/repo"

    def test_no_trailing_blank_still_flushes(self):
        # The parser must flush the final stanza even if the input doesn't end
        # with the documented blank line.
        stdout = (
            "worktree /repo\n"
            "HEAD sha\n"
            "branch refs/heads/main"
        )
        result = _parse_worktree_porcelain(stdout)
        assert len(result) == 1
        assert result[0].path == "/repo"


@pytest.fixture
def manager() -> WorktreeManager:
    return WorktreeManager(
        worktree_base="claude_worktree",
        branch_prefix="feature/claude",
        main_branch="main",
    )


class TestDeleteBranch:
    @patch("mobaide.linear.worktree._run_git")
    async def test_delete_branch_uses_lowercase_d_when_force_false(
        self, mock_git, manager, tmp_path,
    ):
        mock_git.return_value = (0, "", "")
        await manager.delete_branch(str(tmp_path), "feature/foo", force=False)
        args = mock_git.call_args.args
        assert "branch" in args
        assert "-d" in args
        assert "-D" not in args
        assert "feature/foo" in args

    @patch("mobaide.linear.worktree._run_git")
    async def test_delete_branch_uses_uppercase_d_when_force_true(
        self, mock_git, manager, tmp_path,
    ):
        mock_git.return_value = (0, "", "")
        await manager.delete_branch(str(tmp_path), "feature/foo", force=True)
        args = mock_git.call_args.args
        assert "-D" in args
        assert "-d" not in args

    @patch("mobaide.linear.worktree._run_git")
    async def test_delete_branch_unmerged_sets_unmerged_true(
        self, mock_git, manager, tmp_path,
    ):
        # git branch -d emits "is not fully merged" for unmerged branches.
        mock_git.return_value = (
            1, "",
            "error: The branch 'feature/x' is not fully merged.\n"
            "If you are sure you want to delete it, run 'git branch -D ...'.",
        )
        with pytest.raises(BranchDeleteError) as exc_info:
            await manager.delete_branch(str(tmp_path), "feature/x", force=False)
        assert exc_info.value.unmerged is True
        assert exc_info.value.name == "feature/x"
        assert "not fully merged" in exc_info.value.stderr

    @patch("mobaide.linear.worktree._run_git")
    async def test_delete_branch_generic_failure_unmerged_false(
        self, mock_git, manager, tmp_path,
    ):
        # Any other failure must NOT carry the unmerged flag.
        mock_git.return_value = (1, "", "error: branch 'nope' not found.")
        with pytest.raises(BranchDeleteError) as exc_info:
            await manager.delete_branch(str(tmp_path), "nope", force=False)
        assert exc_info.value.unmerged is False
        assert "not found" in exc_info.value.stderr

    @patch("mobaide.linear.worktree._run_git")
    async def test_delete_branch_force_clears_unmerged_flag(
        self, mock_git, manager, tmp_path,
    ):
        # Even if stderr coincidentally mentions "not fully merged", force=True
        # must clear unmerged so the API doesn't loop the user back to a force prompt.
        mock_git.return_value = (1, "", "error: not fully merged (race)")
        with pytest.raises(BranchDeleteError) as exc_info:
            await manager.delete_branch(str(tmp_path), "x", force=True)
        assert exc_info.value.unmerged is False

    @patch("mobaide.linear.worktree._run_git")
    async def test_delete_branch_happy_path_returns_none(
        self, mock_git, manager, tmp_path,
    ):
        mock_git.return_value = (0, "", "")
        # Must not raise; the contract is "return None on success".
        result = await manager.delete_branch(str(tmp_path), "feature/x", force=False)
        assert result is None


class TestRemoveWorktree:
    @patch("mobaide.linear.worktree._run_git")
    async def test_remove_worktree_happy_path_runs_remove_then_prune(
        self, mock_git, manager, tmp_path,
    ):
        mock_git.return_value = (0, "", "")
        await manager.remove_worktree(str(tmp_path), "/repo/wt/foo")
        # Two calls: the remove, then a prune. Order matters.
        assert mock_git.call_count == 2
        first_args = mock_git.call_args_list[0].args
        second_args = mock_git.call_args_list[1].args
        assert "remove" in first_args and "/repo/wt/foo" in first_args
        # Refuses to use --force per the v1 contract.
        assert "--force" not in first_args
        assert "prune" in second_args

    @patch("mobaide.linear.worktree._run_git")
    async def test_remove_worktree_failure_raises_and_skips_prune(
        self, mock_git, manager, tmp_path,
    ):
        mock_git.return_value = (1, "", "fatal: '/repo/wt/foo' is locked")
        with pytest.raises(WorktreeRemoveError) as exc_info:
            await manager.remove_worktree(str(tmp_path), "/repo/wt/foo")
        assert exc_info.value.path == "/repo/wt/foo"
        assert "locked" in exc_info.value.stderr
        # On failure, prune must NOT be called — only the failed remove.
        assert mock_git.call_count == 1


class TestListMethods:
    @patch("mobaide.linear.worktree._run_git")
    async def test_list_branches_invokes_for_each_ref_with_format(
        self, mock_git, manager, tmp_path,
    ):
        mock_git.return_value = (
            0,
            "main\x00origin/main\x00\x00sha1\x00*\n"
            "topic\x00\x00\x00sha2\x00 \n",
            "",
        )
        result = await manager.list_branches(str(tmp_path))
        assert [b.name for b in result] == ["main", "topic"]
        # The git invocation must be `for-each-ref` against `refs/heads/`.
        args = mock_git.call_args.args
        assert "for-each-ref" in args
        assert "refs/heads/" in args
        # Format string must be NUL-delimited (uses the documented %00 marker).
        fmt_arg = next(a for a in args if a.startswith("--format="))
        assert "%00" in fmt_arg

    @patch("mobaide.linear.worktree._run_git")
    async def test_list_branches_propagates_failure(
        self, mock_git, manager, tmp_path,
    ):
        mock_git.return_value = (128, "", "fatal: not a git repository")
        with pytest.raises(RuntimeError, match="for-each-ref failed"):
            await manager.list_branches(str(tmp_path))

    @patch("mobaide.linear.worktree._run_git")
    async def test_list_worktrees_invokes_porcelain(
        self, mock_git, manager, tmp_path,
    ):
        mock_git.return_value = (
            0,
            "worktree /repo\nHEAD sha\nbranch refs/heads/main\n\n",
            "",
        )
        result = await manager.list_worktrees(str(tmp_path))
        assert len(result) == 1
        assert isinstance(result[0], WorktreeInfo)
        args = mock_git.call_args.args
        assert "worktree" in args
        assert "list" in args
        assert "--porcelain" in args

    @patch("mobaide.linear.worktree._run_git")
    async def test_list_worktrees_propagates_failure(
        self, mock_git, manager, tmp_path,
    ):
        mock_git.return_value = (128, "", "fatal: boom")
        with pytest.raises(RuntimeError, match="worktree list failed"):
            await manager.list_worktrees(str(tmp_path))

    @patch("mobaide.linear.worktree._run_git")
    async def test_list_branches_returns_branchinfo_instances(
        self, mock_git, manager, tmp_path,
    ):
        mock_git.return_value = (0, "main\x00\x00\x00sha\x00*\n", "")
        result = await manager.list_branches(str(tmp_path))
        assert all(isinstance(b, BranchInfo) for b in result)
