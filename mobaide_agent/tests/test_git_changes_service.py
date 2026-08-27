"""Repo resolution, in-progress-operation detection and the status response
of the session git Changes service, against real temp repositories."""
from __future__ import annotations

import os

import pytest

from mobaide.agent.git import changes_service
from mobaide.agent.git.changes_service import (
    GitCommandError,
    GitTimeoutError,
    get_session_git_status,
    repo_state,
    resolve_repo,
    resolve_repo_dirs,
)
from mobaide.core import git_exec
from mobaide.core.git_exec import (
    CmdResult,
    GitCwdMissingError,
    GitUnavailableError,
    reset_caches,
)

from .git_repo_support import (
    add_submodule,
    commit_all,
    init_repo,
    make_conflict_branches,
    norm_path,
    repo,  # noqa: F401 - pytest fixture
    requires_git,
    run_git_sync,
    stub_git,
    write_file,
)

_DUBIOUS = CmdResult(
    rc=128, stdout=b"",
    stderr=b"fatal: detected dubious ownership in repository at '/srv/x'\n",
)


@pytest.fixture(autouse=True)
def _fresh_caches():
    reset_caches()
    yield
    reset_caches()


class TestResolveRepo:
    async def test_none_cwd_is_cwd_missing(self):
        probe = await resolve_repo(None)

        assert (probe.reason, probe.repo) == ("cwd_missing", None)

    async def test_vanished_cwd_is_cwd_missing(self, tmp_path):
        probe = await resolve_repo(str(tmp_path / "gone"))

        assert (probe.reason, probe.repo) == ("cwd_missing", None)

    @requires_git
    async def test_plain_directory_is_not_a_repo(self, tmp_path):
        probe = await resolve_repo(str(tmp_path))

        assert (probe.reason, probe.repo) == ("not_a_repo", None)

    @requires_git
    async def test_bare_repository(self, tmp_path):
        bare = str(tmp_path / "bare.git")
        run_git_sync(str(tmp_path), "init", "--bare", bare)

        probe = await resolve_repo(bare)

        assert (probe.reason, probe.repo) == ("bare_repository", None)

    @requires_git
    async def test_main_worktree_from_subdirectory(self, repo):
        sub = os.path.join(repo, "src", "pkg")
        os.makedirs(sub)

        probe = await resolve_repo(sub)

        assert probe.reason is None
        assert probe.repo.toplevel == norm_path(repo)
        assert probe.repo.git_dir == norm_path(repo) + "/.git"
        assert probe.repo.common_dir == probe.repo.git_dir
        assert probe.repo.is_linked_worktree is False

    @requires_git
    async def test_linked_worktree(self, repo, tmp_path):
        wt = str(tmp_path / "wt")
        run_git_sync(repo, "worktree", "add", "-b", "topic", wt)

        probe = await resolve_repo(wt)

        assert probe.repo.toplevel == norm_path(wt)
        assert probe.repo.common_dir == norm_path(repo) + "/.git"
        assert probe.repo.git_dir.startswith(probe.repo.common_dir + "/worktrees/")
        assert probe.repo.is_linked_worktree is True

    async def test_old_git_is_git_too_old(self, tmp_path, monkeypatch):
        stub_git(monkeypatch, CmdResult(rc=0, stdout=b"", stderr=b""), version=(2, 14, 9))

        probe = await resolve_repo(str(tmp_path))

        assert (probe.reason, probe.repo) == ("git_too_old", None)

    async def test_unparseable_version_raises(self, tmp_path, monkeypatch):
        stub_git(monkeypatch, CmdResult(rc=0, stdout=b"", stderr=b""), version=None)

        with pytest.raises(GitCommandError):
            await resolve_repo(str(tmp_path))

    async def test_dubious_ownership_is_unsafe_directory(self, tmp_path, monkeypatch):
        stub_git(monkeypatch, _DUBIOUS)

        probe = await resolve_repo(str(tmp_path))

        assert (probe.reason, probe.repo) == ("unsafe_directory", None)

    async def test_cwd_vanishing_mid_run_is_cwd_missing(self, tmp_path, monkeypatch):
        stub_git(monkeypatch, CmdResult(rc=0, stdout=b"", stderr=b""))

        async def vanish(args, **_kwargs):
            raise GitCwdMissingError(str(tmp_path))

        monkeypatch.setattr(changes_service, "run_git", vanish)

        probe = await resolve_repo(str(tmp_path))

        assert (probe.reason, probe.repo) == ("cwd_missing", None)

    async def test_unknown_failure_raises_command_error(self, tmp_path, monkeypatch):
        stub_git(monkeypatch, CmdResult(rc=128, stdout=b"", stderr=b"fatal: index corrupt"))

        with pytest.raises(GitCommandError, match="index corrupt"):
            await resolve_repo(str(tmp_path))


class TestResolveRepoDirs:
    @requires_git
    async def test_repo_yields_toplevel_and_common_dir(self, repo):
        assert await resolve_repo_dirs(repo) == (norm_path(repo), norm_path(repo) + "/.git")

    @requires_git
    async def test_non_repo_yields_nones(self, tmp_path):
        assert await resolve_repo_dirs(str(tmp_path)) == (None, None)

    async def test_never_raises(self, tmp_path, monkeypatch):
        async def boom():
            raise GitUnavailableError("gone")

        monkeypatch.setattr(changes_service, "git_version", boom)

        assert await resolve_repo_dirs(str(tmp_path)) == (None, None)


@requires_git
class TestRepoState:
    async def test_clean(self, repo):
        assert await repo_state(norm_path(repo)) == "clean"

    async def test_merging(self, repo):
        make_conflict_branches(repo)
        run_git_sync(repo, "merge", "other", check=False)

        assert await repo_state(norm_path(repo)) == "merging"

    async def test_rebasing(self, repo):
        make_conflict_branches(repo)
        run_git_sync(repo, "checkout", "other")
        run_git_sync(repo, "rebase", "main", check=False)

        assert await repo_state(norm_path(repo)) == "rebasing"

    async def test_cherry_picking(self, repo):
        make_conflict_branches(repo)
        run_git_sync(repo, "cherry-pick", "other", check=False)

        assert await repo_state(norm_path(repo)) == "cherry_picking"

    async def test_reverting(self, repo):
        run_git_sync(repo, "revert", "--no-commit", "HEAD")

        assert await repo_state(norm_path(repo)) == "reverting"

    async def test_bisecting(self, repo):
        run_git_sync(repo, "bisect", "start")

        assert await repo_state(norm_path(repo)) == "bisecting"

    async def test_linked_worktree_state_is_per_worktree(self, repo, tmp_path):
        make_conflict_branches(repo)
        wt = str(tmp_path / "wt")
        run_git_sync(repo, "worktree", "add", wt, "other")
        run_git_sync(wt, "merge", "main", check=False)

        assert await repo_state(norm_path(wt)) == "merging"
        assert await repo_state(norm_path(repo)) == "clean"


@requires_git
class TestSessionGitStatus:
    async def test_groups_sorts_and_counts_entries(self, repo):
        write_file(repo, "old name.txt", b"moved\n")
        commit_all(repo, "add old name")
        run_git_sync(repo, "mv", "old name.txt", "renamed.txt")
        write_file(repo, "keep.txt", b"one\nTWO\nthree\n")
        run_git_sync(repo, "add", "keep.txt")
        write_file(repo, "keep.txt", b"one\nTWO\nTHREE\n")
        write_file(repo, "new dir/日本 [x].txt", b"?\n")

        resp = await get_session_git_status(session_id="s1", cwd=repo, etag=None)

        assert (resp.session_id, resp.cwd, resp.unchanged, resp.unavailable_reason) == (
            "s1", repo, False, None)
        assert [(e.group, e.path, e.code, e.orig_path, e.rename_score) for e in resp.entries] == [
            ("staged", "keep.txt", "M", None, None),
            ("staged", "renamed.txt", "R", "old name.txt", 100),
            ("unstaged", "keep.txt", "M", None, None),
            ("untracked", "new dir/日本 [x].txt", "?", None, None),
        ]
        assert resp.counts.model_dump() == {
            "conflicts": 0, "staged": 2, "unstaged": 1, "untracked": 1, "total": 4}
        assert resp.repo.toplevel == norm_path(repo)
        assert resp.repo.is_linked_worktree is False
        assert resp.repo.state == "clean"
        assert resp.repo.upstream is None
        assert resp.repo.head.branch == "main"
        assert len(resp.repo.head.oid) == 40
        assert resp.repo.head.short_oid == resp.repo.head.oid[:7]
        assert (resp.repo.head.detached, resp.repo.head.initial) == (False, False)
        assert len(resp.etag) == 40 and resp.truncated is False

    async def test_matching_etag_short_circuits_until_tree_changes(self, repo):
        write_file(repo, "a.txt", b"a\n")
        first = await get_session_git_status(session_id="s1", cwd=repo, etag=None)

        cached = await get_session_git_status(session_id="s1", cwd=repo, etag=first.etag)

        assert cached.unchanged is True and cached.etag == first.etag
        assert (cached.repo, cached.entries, cached.counts) == (None, [], None)

        write_file(repo, "b.txt", b"b\n")
        changed = await get_session_git_status(session_id="s1", cwd=repo, etag=first.etag)

        assert changed.unchanged is False
        assert changed.etag != first.etag
        assert [e.path for e in changed.entries] == ["a.txt", "b.txt"]

    async def test_state_change_alone_changes_etag(self, repo):
        before = await get_session_git_status(session_id="s1", cwd=repo, etag=None)
        run_git_sync(repo, "bisect", "start")

        after = await get_session_git_status(session_id="s1", cwd=repo, etag=before.etag)

        assert after.unchanged is False
        assert after.repo.state == "bisecting"

    async def test_unavailable_reason_has_its_own_etag(self, tmp_path):
        plain = str(tmp_path / "plain")
        os.makedirs(plain)

        first = await get_session_git_status(session_id="s2", cwd=plain, etag=None)
        cached = await get_session_git_status(session_id="s2", cwd=plain, etag=first.etag)

        assert (first.unavailable_reason, first.repo, first.counts) == ("not_a_repo", None, None)
        assert cached.unchanged is True and cached.etag == first.etag

    async def test_detached_and_initial_heads(self, repo, tmp_path):
        run_git_sync(repo, "checkout", "--detach")
        fresh = init_repo(str(tmp_path / "fresh"), initial_commit=False)

        detached = await get_session_git_status(session_id="s1", cwd=repo, etag=None)
        initial = await get_session_git_status(session_id="s1", cwd=fresh, etag=None)

        assert (detached.repo.head.detached, detached.repo.head.branch) == (True, None)
        assert len(detached.repo.head.oid) == 40
        assert (initial.repo.head.initial, initial.repo.head.oid, initial.repo.head.short_oid) == (
            True, None, None)
        assert initial.repo.head.branch == "main"

    async def test_upstream_counts_then_gone(self, repo):
        run_git_sync(repo, "remote", "add", "origin", repo)
        run_git_sync(repo, "fetch", "origin")
        run_git_sync(repo, "branch", "--set-upstream-to=origin/main")

        tracked = await get_session_git_status(session_id="s1", cwd=repo, etag=None)
        run_git_sync(repo, "update-ref", "-d", "refs/remotes/origin/main")
        gone = await get_session_git_status(session_id="s1", cwd=repo, etag=None)

        assert tracked.repo.upstream.model_dump() == {"ref": "origin/main", "ahead": 0, "behind": 0}
        assert gone.repo.upstream.model_dump() == {"ref": "origin/main", "ahead": None, "behind": None}

    async def test_merge_conflict_lists_conflicts_group(self, repo):
        make_conflict_branches(repo)
        run_git_sync(repo, "merge", "other", check=False)

        resp = await get_session_git_status(session_id="s1", cwd=repo, etag=None)

        assert resp.repo.state == "merging"
        assert [(e.group, e.path, e.code, e.index_status, e.worktree_status) for e in resp.entries] == [
            ("conflicts", "keep.txt", "UU", "U", "U")]
        assert resp.counts.conflicts == 1 and resp.counts.total == 1

    async def test_submodule_and_linked_worktree_flags(self, repo, tmp_path):
        add_submodule(repo, str(tmp_path))
        wt = str(tmp_path / "wt")
        run_git_sync(repo, "worktree", "add", "-b", "topic", wt)

        main = await get_session_git_status(session_id="s1", cwd=repo, etag=None)
        linked = await get_session_git_status(session_id="s1", cwd=wt, etag=None)

        by_path = {e.path: e for e in main.entries}
        assert by_path["sub"].submodule is True and by_path["sub"].code == "A"
        assert by_path[".gitmodules"].submodule is False
        assert linked.repo.is_linked_worktree is True
        assert linked.repo.toplevel == norm_path(wt)
        assert linked.repo.common_dir == main.repo.common_dir
        assert linked.cwd == wt

    async def test_entry_cap_marks_truncated_but_counts_everything(self, repo, monkeypatch):
        for name in ("a", "b", "c"):
            write_file(repo, f"{name}.txt", b"x\n")
        monkeypatch.setattr(changes_service, "ENTRY_LIMIT", 2)

        resp = await get_session_git_status(session_id="s1", cwd=repo, etag=None)

        assert [e.path for e in resp.entries] == ["a.txt", "b.txt"]
        assert resp.truncated is True
        assert resp.counts.total == 3

    async def test_truncated_status_output_is_reported_not_failed(self, repo, monkeypatch):
        write_file(repo, "a.txt", b"x\n")
        write_file(repo, "b.txt", b"x\n")
        real_run_git = changes_service.run_git

        async def cut_status(args, **kwargs):
            result = await real_run_git(args, **kwargs)
            if args[0] != "status":
                return result
            partial = result.stdout[: result.stdout.rfind(b"\0")]
            return CmdResult(rc=-9, stdout=partial, stderr=b"", truncated=True)

        monkeypatch.setattr(changes_service, "run_git", cut_status)

        resp = await get_session_git_status(session_id="s1", cwd=repo, etag=None)

        assert resp.truncated is True
        assert [e.path for e in resp.entries] == ["a.txt"]

    async def test_timeout_raises_git_timeout(self, tmp_path, monkeypatch):
        stub_git(monkeypatch, CmdResult(rc=-9, stdout=b"", stderr=b"", timed_out=True))

        with pytest.raises(GitTimeoutError):
            await get_session_git_status(session_id="s1", cwd=str(tmp_path), etag=None)

    async def test_missing_git_raises_unavailable(self, repo, monkeypatch):
        monkeypatch.setattr(git_exec.shutil, "which", lambda _name: None)
        reset_caches()

        with pytest.raises(GitUnavailableError):
            await get_session_git_status(session_id="s1", cwd=repo, etag=None)
