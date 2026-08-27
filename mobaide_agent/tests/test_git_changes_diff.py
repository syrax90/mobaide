"""``get_session_git_diff`` against real temp repositories: request
validation, every diff kind git can produce, and the Python-synthesised
untracked diff."""
from __future__ import annotations

import os

import pytest

from mobaide.agent.git import changes_service
from mobaide.agent.git.changes_models import BYTE_LIMIT
from mobaide.agent.git.changes_service import (
    GitBadRequestError,
    GitTimeoutError,
    get_session_git_diff,
)
from mobaide.core.git_exec import CmdResult, reset_caches

from .git_repo_support import (
    add_submodule,
    commit_all,
    make_conflict_branches,
    posix_only,
    repo,  # noqa: F401 - pytest fixture
    requires_git,
    run_git_sync,
    stub_git,
    write_file,
)


@pytest.fixture(autouse=True)
def _fresh_caches():
    reset_caches()
    yield
    reset_caches()


async def _diff(cwd, path, scope, **kwargs):
    return await get_session_git_diff(cwd=cwd, path=path, scope=scope, **kwargs)


def _lines(resp):
    return [(l.t, l.old, l.new, l.text) for h in resp.hunks for l in h.lines]


class TestRequestValidation:
    @pytest.mark.parametrize("scope", [None, "all", "STAGED"])
    async def test_bad_scope(self, tmp_path, scope):
        with pytest.raises(GitBadRequestError) as info:
            await _diff(str(tmp_path), "a.txt", scope)
        assert info.value.code == "bad_scope"

    @pytest.mark.parametrize(("path", "orig"), [("../x", None), ("a.txt", "/abs")])
    async def test_bad_path_or_orig_path(self, tmp_path, path, orig):
        with pytest.raises(GitBadRequestError) as info:
            await _diff(str(tmp_path), path, "staged", orig_path=orig)
        assert info.value.code == "bad_path"

    @requires_git
    async def test_repo_conditions_become_bad_request(self, tmp_path):
        with pytest.raises(GitBadRequestError) as not_repo:
            await _diff(str(tmp_path), "a.txt", "unstaged")
        with pytest.raises(GitBadRequestError) as no_cwd:
            await _diff(None, "a.txt", "unstaged")

        assert not_repo.value.code == "not_a_repo"
        assert no_cwd.value.code == "cwd_missing"

    async def test_timeout_raises(self, tmp_path, monkeypatch):
        stub_git(monkeypatch, CmdResult(rc=-9, stdout=b"", stderr=b"", timed_out=True))

        with pytest.raises(GitTimeoutError):
            await _diff(str(tmp_path), "a.txt", "unstaged")


@requires_git
class TestTrackedDiff:
    async def test_unstaged_modification(self, repo):
        write_file(repo, "keep.txt", b"one\nTWO\nthree\n")

        resp = await _diff(repo, "keep.txt", "unstaged")

        assert (resp.path, resp.orig_path, resp.scope, resp.kind, resp.change) == (
            "keep.txt", None, "unstaged", "text", "modified")
        assert (resp.old_mode, resp.new_mode) == ("100644", "100644")
        assert (resp.additions, resp.deletions, resp.total_lines) == (1, 1, 4)
        assert _lines(resp) == [
            ("ctx", 1, 1, "one"), ("del", 2, None, "two"),
            ("add", None, 2, "TWO"), ("ctx", 3, 3, "three"),
        ]
        assert resp.truncated is False

    async def test_scope_follows_the_index(self, repo):
        write_file(repo, "keep.txt", b"one\nTWO\nthree\n")
        run_git_sync(repo, "add", "keep.txt")

        staged = await _diff(repo, "keep.txt", "staged")
        unstaged = await _diff(repo, "keep.txt", "unstaged")

        assert staged.kind == "text" and staged.additions == 1
        assert unstaged.kind == "missing" and unstaged.hunks == []

    async def test_context_is_clamped(self, repo):
        write_file(repo, "keep.txt", b"one\nTWO\nthree\n")

        resp = await _diff(repo, "keep.txt", "unstaged", context="-4")

        [hunk] = resp.hunks
        assert (hunk.old_start, hunk.old_lines, hunk.new_start, hunk.new_lines) == (2, 1, 2, 1)
        assert [l.t for l in hunk.lines] == ["del", "add"]

    async def test_crlf_content_is_preserved(self, repo):
        write_file(repo, "dos.txt", b"one\r\ntwo\r\n")
        commit_all(repo, "dos")
        write_file(repo, "dos.txt", b"one\r\nTWO\r\n")

        resp = await _diff(repo, "dos.txt", "unstaged")

        assert [(l.t, l.text) for l in resp.hunks[0].lines] == [
            ("ctx", "one\r"), ("del", "two\r"), ("add", "TWO\r")]

    async def test_rename_with_orig_path(self, repo):
        run_git_sync(repo, "mv", "keep.txt", "moved.txt")

        pure = await _diff(repo, "moved.txt", "staged", orig_path="keep.txt")
        write_file(repo, "moved.txt", b"one\ntwo\nthree\nfour\n")
        run_git_sync(repo, "add", "moved.txt")
        edited = await _diff(repo, "moved.txt", "staged", orig_path="keep.txt")

        assert (pure.change, pure.orig_path, pure.additions, pure.hunks) == (
            "renamed", "keep.txt", 0, [])
        assert (edited.change, edited.additions) == ("renamed", 1)
        assert _lines(edited)[-1] == ("add", None, 4, "four")

    async def test_deleted_file(self, repo):
        os.remove(os.path.join(repo, "keep.txt"))

        resp = await _diff(repo, "keep.txt", "unstaged")

        assert (resp.change, resp.old_mode, resp.new_mode) == ("deleted", "100644", None)
        assert (resp.additions, resp.deletions) == (0, 3)
        assert [l.t for l in resp.hunks[0].lines] == ["del"] * 3

    async def test_unknown_path_is_missing(self, repo):
        resp = await _diff(repo, "nope.txt", "unstaged")

        assert (resp.kind, resp.change, resp.hunks) == ("missing", None, [])

    async def test_conflicted_file_diffs_against_head(self, repo):
        make_conflict_branches(repo)
        run_git_sync(repo, "merge", "other", check=False)

        unstaged = await _diff(repo, "keep.txt", "unstaged")
        staged = await _diff(repo, "keep.txt", "staged")

        assert (unstaged.kind, unstaged.change) == ("text", "modified")
        assert (unstaged.additions, unstaged.deletions) == (4, 0)
        assert any(l.text.startswith("<<<<<<<") for l in unstaged.hunks[0].lines)
        assert staged.kind == "missing"

    async def test_binary_file(self, repo):
        write_file(repo, "blob.bin", b"\x00\x01\x02\xff")
        run_git_sync(repo, "add", "blob.bin")

        resp = await _diff(repo, "blob.bin", "staged")

        assert (resp.kind, resp.change, resp.hunks) == ("binary", "added", [])

    async def test_submodule(self, repo, tmp_path):
        add_submodule(repo, str(tmp_path))

        resp = await _diff(repo, "sub", "staged")

        assert (resp.kind, resp.change, resp.new_mode) == ("submodule", "added", "160000")
        [(kind, _old, _new, text)] = _lines(resp)
        assert kind == "add" and text.startswith("Subproject commit ")

    @posix_only
    async def test_type_change_merges_both_blocks(self, repo):
        os.remove(os.path.join(repo, "keep.txt"))
        os.symlink("README.md", os.path.join(repo, "keep.txt"))
        run_git_sync(repo, "add", "-A")

        resp = await _diff(repo, "keep.txt", "staged")

        assert (resp.change, resp.old_mode, resp.new_mode) == ("type_changed", "100644", "120000")
        assert [len(h.lines) for h in resp.hunks] == [3, 1]
        assert _lines(resp)[-1] == ("add", None, 1, "README.md")

    async def test_truncated_patch_is_reported_not_failed(self, repo, monkeypatch):
        write_file(repo, "keep.txt", b"one\nTWO\nthree\n")
        real_run_git = changes_service.run_git

        async def cut_patch(args, **kwargs):
            result = await real_run_git(args, **kwargs)
            if "--no-color" not in args:
                return result
            return CmdResult(rc=-9, stdout=result.stdout[:-6], stderr=b"", truncated=True)

        monkeypatch.setattr(changes_service, "run_git", cut_patch)

        resp = await _diff(repo, "keep.txt", "unstaged")

        assert resp.truncated is True
        assert resp.kind == "text"
        assert [l.text for l in resp.hunks[0].lines] == ["one", "two", "TWO"]


@requires_git
class TestUntrackedDiff:
    async def test_text_file_is_one_add_hunk(self, repo):
        write_file(repo, "new/a.txt", b"first\nsecond\n")

        resp = await _diff(repo, "new/a.txt", "untracked")

        assert (resp.kind, resp.change, resp.new_mode, resp.old_mode) == (
            "text", "added", "100644", None)
        assert (resp.additions, resp.deletions, resp.total_lines) == (2, 0, 2)
        [hunk] = resp.hunks
        assert (hunk.old_start, hunk.old_lines, hunk.new_start, hunk.new_lines) == (0, 0, 1, 2)
        assert [(l.t, l.old, l.new, l.text, l.no_newline) for l in hunk.lines] == [
            ("add", None, 1, "first", False), ("add", None, 2, "second", False)]

    async def test_missing_trailing_newline_and_crlf(self, repo):
        write_file(repo, "a.txt", b"one\r\ntwo")

        resp = await _diff(repo, "a.txt", "untracked")

        assert [(l.text, l.no_newline) for l in resp.hunks[0].lines] == [
            ("one\r", False), ("two", True)]

    async def test_empty_file_has_no_hunks(self, repo):
        write_file(repo, "empty.txt", b"")

        resp = await _diff(repo, "empty.txt", "untracked")

        assert (resp.kind, resp.hunks, resp.total_lines) == ("text", [], 0)

    async def test_binary_and_too_large(self, repo):
        write_file(repo, "blob.bin", b"text\x00more")
        write_file(repo, "huge.txt", b"x" * (BYTE_LIMIT + 1))

        binary = await _diff(repo, "blob.bin", "untracked")
        huge = await _diff(repo, "huge.txt", "untracked")

        assert (binary.kind, binary.change, binary.hunks) == ("binary", "added", [])
        assert (huge.kind, huge.change, huge.hunks) == ("too_large", "added", [])

    async def test_line_cap_marks_truncated(self, repo, monkeypatch):
        write_file(repo, "a.txt", b"1\n2\n3\n")
        monkeypatch.setattr(changes_service, "LINE_LIMIT", 2)

        resp = await _diff(repo, "a.txt", "untracked")

        assert (resp.truncated, resp.total_lines, resp.additions) == (True, 3, 3)
        assert resp.hunks[0].new_lines == 3
        assert [l.text for l in resp.hunks[0].lines] == ["1", "2"]

    async def test_tracked_absent_and_directory_paths_are_missing(self, repo):
        os.makedirs(os.path.join(repo, "adir"))

        tracked = await _diff(repo, "keep.txt", "untracked")
        absent = await _diff(repo, "nope.txt", "untracked")
        directory = await _diff(repo, "adir", "untracked")

        assert (tracked.kind, absent.kind, directory.kind) == ("missing",) * 3

    @posix_only
    async def test_symlink_and_executable_modes(self, repo):
        os.symlink("../outside", os.path.join(repo, "link"))
        script = write_file(repo, "run.sh", b"#!/bin/sh\n")
        os.chmod(script, 0o755)

        link = await _diff(repo, "link", "untracked")
        exe = await _diff(repo, "run.sh", "untracked")

        assert link.new_mode == "120000"
        assert [(l.text, l.no_newline) for l in link.hunks[0].lines] == [("../outside", True)]
        assert exe.new_mode == "100755"

    @posix_only
    async def test_symlinked_directory_escape_is_bad_path(self, repo, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_bytes(b"secret\n")
        os.symlink(str(outside), os.path.join(repo, "portal"))

        with pytest.raises(GitBadRequestError) as info:
            await _diff(repo, "portal/secret.txt", "untracked")

        assert info.value.code == "bad_path"
