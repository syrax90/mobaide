"""Repo resolution, in-progress-operation detection and the status response
of the session git Changes service, against real temp repositories."""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from mobaide.agent.git import changes_service
from mobaide.agent.git.changes_service import (
    GitCommandError,
    GitTimeoutError,
    get_session_git_status,
    repo_state,
    resolve_repo,
    resolve_repo_dirs,
    restorable_paths,
)
from mobaide.core import git_exec
from mobaide.core.git_exec import (
    CmdResult,
    GitCwdMissingError,
    GitUnavailableError,
    reset_caches,
)

from .git_repo_support import (
    GIT_BIN,
    add_submodule,
    commit_all,
    init_repo,
    make_conflict_branches,
    make_modify_delete_branches,
    make_submodule_conflict,
    norm_path,
    posix_only,
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

    @requires_git
    @posix_only
    async def test_trailing_whitespace_in_directory_name_is_kept(self, tmp_path):
        trail = init_repo(str(tmp_path / "trail "))

        probe = await resolve_repo(trail)

        assert probe.reason is None
        assert probe.repo.toplevel.endswith("trail ")
        assert probe.repo.toplevel == norm_path(trail)
        assert probe.repo.git_dir == norm_path(trail) + "/.git"

    @requires_git
    @posix_only
    @pytest.mark.parametrize(
        "name",
        ["vt\x0bb", "nel\u0085b", "cr\rb", "ls\u2028b"],
        ids=["vt", "nel", "cr", "ls"],
    )
    async def test_line_terminator_in_directory_name_is_kept(self, tmp_path, name):
        odd = init_repo(str(tmp_path / name))

        probe = await resolve_repo(odd)

        assert probe.reason is None
        assert probe.repo.toplevel == norm_path(odd)
        assert probe.repo.git_dir == norm_path(odd) + "/.git"

    @requires_git
    @posix_only
    async def test_newline_in_directory_name_fails_closed(self, tmp_path):
        odd = init_repo(str(tmp_path / "nl\nb"))

        with pytest.raises(GitCommandError, match="unexpected git rev-parse output"):
            await resolve_repo(odd)

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

    @posix_only
    async def test_linked_worktree_under_cr_directory_is_merging(self, tmp_path):
        odd = init_repo(str(tmp_path / "cr\rb"))
        make_conflict_branches(odd)
        wt = str(tmp_path / "wt")
        run_git_sync(odd, "worktree", "add", wt, "other")
        run_git_sync(wt, "merge", "main", check=False)

        assert await repo_state(norm_path(wt)) == "merging"

    async def test_extra_git_path_lines_raise(self, tmp_path, monkeypatch):
        stub_git(monkeypatch, CmdResult(
            rc=0, stderr=b"",
            stdout=b".git/MERGE_HEAD\n.git/BISECT_LOG\n.git/rebase-merge\n"
                   b"split\n.git/rebase-apply\n",
        ))

        with pytest.raises(GitCommandError, match="unexpected git rev-parse --git-path"):
            await repo_state(str(tmp_path))


def _rev(repo_dir: str, rev: str, *, short: bool = False) -> str:
    args = ["rev-parse", *(["--short"] if short else []), rev]
    return subprocess.run(
        [GIT_BIN, *args], cwd=repo_dir, check=True, capture_output=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull},
    ).stdout.decode().strip()


async def _rebase_status(cwd: str):
    return await get_session_git_status(session_id="s1", cwd=cwd, etag=None)


@requires_git
class TestRebaseInfo:
    async def test_a_clean_repo_reports_no_rebase(self, repo):
        resp = await _rebase_status(repo)

        assert (resp.repo.state, resp.repo.rebase) == ("clean", None)

    async def test_stopped_merge_backend_rebase_reports_branch_onto_stop_and_step(self, repo):
        make_conflict_branches(repo)
        run_git_sync(repo, "checkout", "other")
        run_git_sync(repo, "rebase", "main", check=False)

        resp = await _rebase_status(repo)

        assert resp.repo.state == "rebasing"
        assert resp.repo.head.detached is True
        assert resp.repo.rebase.model_dump() == {
            "branch": "other",
            "onto_oid": _rev(repo, "main"),
            "onto_short_oid": _rev(repo, "main", short=True),
            "onto_ref": "main",
            "stopped_on": {
                "oid": _rev(repo, "other"),
                "short_oid": _rev(repo, "other", short=True),
                "subject": "other side",
            },
            "step": 1,
            "total": 1,
        }

    async def test_apply_backend_counts_steps_from_next_and_last(self, repo):
        make_conflict_branches(repo)
        run_git_sync(repo, "checkout", "other")
        write_file(repo, "later.txt", b"l\n")
        commit_all(repo, "later")
        run_git_sync(repo, "rebase", "--apply", "main", check=False)

        rebase = (await _rebase_status(repo)).repo.rebase

        assert (rebase.branch, rebase.onto_oid, rebase.onto_ref) == (
            "other", _rev(repo, "main"), "main")
        assert (rebase.step, rebase.total) == (1, 2)

    async def test_a_git_am_session_is_rebasing_without_rebase_details(self, repo, tmp_path):
        make_conflict_branches(repo)
        patch = subprocess.run(
            [GIT_BIN, "format-patch", "-1", "--stdout", "other"],
            cwd=repo, check=True, capture_output=True,
        ).stdout
        patch_file = write_file(str(tmp_path), "other.patch", patch)
        run_git_sync(repo, "am", "-3", patch_file, check=False)

        resp = await _rebase_status(repo)

        assert (resp.repo.state, resp.repo.rebase) == ("rebasing", None)

    async def test_a_detached_head_rebase_has_no_branch(self, repo):
        make_conflict_branches(repo)
        run_git_sync(repo, "checkout", "--detach", "other")
        run_git_sync(repo, "rebase", "main", check=False)

        rebase = (await _rebase_status(repo)).repo.rebase

        assert rebase.branch is None
        assert (rebase.onto_ref, rebase.stopped_on.subject) == ("main", "other side")

    async def test_onto_ref_prefers_a_local_branch_over_a_remote_tracking_one(self, repo):
        make_conflict_branches(repo)
        run_git_sync(repo, "update-ref", "refs/remotes/origin/main", "main")
        run_git_sync(repo, "checkout", "other")
        run_git_sync(repo, "rebase", "origin/main", check=False)

        assert (await _rebase_status(repo)).repo.rebase.onto_ref == "main"

    async def test_onto_ref_falls_back_to_a_remote_branch_and_skips_its_head_symref(
        self, repo
    ):
        # ``origin/HEAD`` sorts before ``origin/feature`` and points at the
        # same commit; naming the symref would read as a branch called HEAD.
        make_conflict_branches(repo)
        run_git_sync(repo, "update-ref", "refs/remotes/origin/feature", "main")
        run_git_sync(
            repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/feature"
        )
        onto = _rev(repo, "main")
        run_git_sync(repo, "commit", "--allow-empty", "-q", "-m", "main moves on")
        run_git_sync(repo, "checkout", "other")
        run_git_sync(repo, "rebase", "origin/feature", check=False)

        rebase = (await _rebase_status(repo)).repo.rebase

        assert (rebase.onto_oid, rebase.onto_ref) == (onto, "origin/feature")

    async def test_onto_with_no_branch_pointing_at_it_keeps_only_the_oid(self, repo):
        make_conflict_branches(repo)
        onto = _rev(repo, "main")
        run_git_sync(repo, "commit", "--allow-empty", "-q", "-m", "main moves on")
        run_git_sync(repo, "checkout", "other")
        run_git_sync(repo, "rebase", onto, check=False)

        rebase = (await _rebase_status(repo)).repo.rebase

        assert (rebase.onto_oid, rebase.onto_short_oid, rebase.onto_ref) == (
            onto, _rev(repo, onto, short=True), None)
        assert rebase.stopped_on.subject == "other side"

    async def test_unreadable_state_files_degrade_to_nulls(self, repo):
        make_conflict_branches(repo)
        run_git_sync(repo, "checkout", "other")
        run_git_sync(repo, "rebase", "main", check=False)
        state_dir = os.path.join(repo, ".git", "rebase-merge")
        for name in ("head-name", "onto", "end"):
            os.remove(os.path.join(state_dir, name))
        write_file(state_dir, "msgnum", b"not a number\n")

        resp = await _rebase_status(repo)

        assert resp.repo.state == "rebasing"
        assert resp.repo.rebase.model_dump() == {
            "branch": None,
            "onto_oid": None,
            "onto_short_oid": None,
            "onto_ref": None,
            "stopped_on": {
                "oid": _rev(repo, "other"),
                "short_oid": _rev(repo, "other", short=True),
                "subject": "other side",
            },
            "step": None,
            "total": None,
        }

    async def test_a_new_stop_point_alone_changes_the_etag(self, repo):
        # Porcelain and HEAD unchanged; only the rebase's progress moved.
        make_conflict_branches(repo)
        run_git_sync(repo, "checkout", "other")
        run_git_sync(repo, "rebase", "main", check=False)
        before = await _rebase_status(repo)
        write_file(repo, ".git/rebase-merge/msgnum", b"2\n")
        write_file(repo, ".git/rebase-merge/end", b"2\n")

        after = await get_session_git_status(session_id="s1", cwd=repo, etag=before.etag)

        assert after.unchanged is False
        assert (after.repo.rebase.step, after.repo.rebase.total) == (2, 2)
        assert after.entries == before.entries

    async def test_a_linked_worktree_rebase_is_reported_only_in_that_worktree(
        self, repo, tmp_path
    ):
        make_conflict_branches(repo)
        wt = str(tmp_path / "wt")
        run_git_sync(repo, "worktree", "add", wt, "other")
        run_git_sync(wt, "rebase", "main", check=False)

        linked = await _rebase_status(wt)
        root = await _rebase_status(repo)

        assert (linked.repo.rebase.branch, linked.repo.rebase.onto_ref) == ("other", "main")
        assert (root.repo.state, root.repo.rebase) == ("clean", None)


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

    @pytest.mark.parametrize(("ours_deletes", "code"), [(False, "UD"), (True, "DU")])
    async def test_modify_delete_conflict_is_a_conflict_entry(
        self, repo, ours_deletes, code
    ):
        make_modify_delete_branches(repo, ours_deletes=ours_deletes)
        run_git_sync(repo, "merge", "other", check=False)

        resp = await get_session_git_status(session_id="s1", cwd=repo, etag=None)

        assert resp.repo.state == "merging"
        assert [(e.group, e.path, e.code, e.index_status, e.worktree_status) for e in resp.entries] == [
            ("conflicts", "keep.txt", code, code[0], code[1])]
        assert resp.counts.conflicts == 1 and resp.counts.total == 1

    async def test_cwd_vanishing_after_resolve_is_cwd_missing(self, repo, monkeypatch):
        real_run_git = changes_service.run_git

        async def rmtree_after_resolve(args, **kwargs):
            if args[:2] != ["rev-parse", "--is-bare-repository"]:
                shutil.rmtree(repo, ignore_errors=True)
            return await real_run_git(args, **kwargs)

        monkeypatch.setattr(changes_service, "run_git", rmtree_after_resolve)

        first = await get_session_git_status(session_id="s1", cwd=repo, etag=None)
        cached = await get_session_git_status(session_id="s1", cwd=repo, etag=first.etag)

        assert (first.unavailable_reason, first.repo, first.entries, first.counts) == (
            "cwd_missing", None, [], None)
        assert first.unchanged is False and len(first.etag) == 40
        assert cached.unchanged is True and cached.etag == first.etag

    @posix_only
    async def test_backslash_in_directory_name_is_kept(self, tmp_path):
        odd = init_repo(str(tmp_path / "foo\\bar"))

        resp = await get_session_git_status(session_id="s1", cwd=odd, etag=None)

        assert resp.unavailable_reason is None
        assert resp.repo.toplevel.endswith("foo\\bar")
        assert resp.repo.head.branch == "main"

    async def test_cwd_inside_git_dir_is_not_a_repo(self, repo):
        inside = os.path.join(repo, ".git", "objects")

        resp = await get_session_git_status(session_id="s1", cwd=inside, etag=None)

        assert resp.unavailable_reason == "not_a_repo"
        assert resp.repo is None

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


def _resolve_undo_record(
    path: str, stages: dict[int, str], *, gitlink: tuple[int, ...] = ()
) -> bytes:
    """The record git keeps per stage; the stages in ``gitlink`` carry a
    submodule's mode instead of a file's."""
    return b"".join(
        f"{'160000' if stage in gitlink else '100644'} {oid} {stage}\t{path}\0".encode()
        for stage, oid in stages.items()
    )


@requires_git
class TestResolvedPaths:
    async def test_a_path_resolved_during_the_operation_is_listed(self, repo):
        make_conflict_branches(repo)
        run_git_sync(repo, "merge", "other", check=False)
        before = await get_session_git_status(session_id="s1", cwd=repo, etag=None)
        write_file(repo, "keep.txt", b"one\nRESOLVED\nthree\n")
        run_git_sync(repo, "add", "keep.txt")

        resp = await get_session_git_status(session_id="s1", cwd=repo, etag=None)

        assert before.resolved_paths == []
        assert resp.resolved_paths == ["keep.txt"]
        assert [(e.group, e.path) for e in resp.entries] == [("staged", "keep.txt")]

    async def test_a_resolution_equal_to_head_is_listed_without_any_entry(self, repo):
        make_conflict_branches(repo)
        run_git_sync(repo, "merge", "other", check=False)
        run_git_sync(repo, "checkout", "--ours", "--", "keep.txt")
        run_git_sync(repo, "add", "keep.txt")

        resp = await get_session_git_status(session_id="s1", cwd=repo, etag=None)

        assert (resp.entries, resp.resolved_paths) == ([], ["keep.txt"])

    async def test_a_record_left_by_a_finished_operation_is_not_listed(self, repo):
        make_conflict_branches(repo)
        run_git_sync(repo, "merge", "other", check=False)
        run_git_sync(repo, "checkout", "--ours", "--", "keep.txt")
        run_git_sync(repo, "add", "keep.txt")
        run_git_sync(repo, "commit", "-q", "--no-edit")
        records = subprocess.run(
            [GIT_BIN, "ls-files", "--resolve-undo"], cwd=repo, check=True, capture_output=True
        ).stdout

        resp = await get_session_git_status(session_id="s1", cwd=repo, etag=None)

        assert b"keep.txt" in records
        assert (resp.repo.state, resp.resolved_paths) == ("clean", [])

    async def test_a_vanished_record_changes_the_etag(self, repo):
        make_conflict_branches(repo)
        run_git_sync(repo, "merge", "other", check=False)
        write_file(repo, "keep.txt", b"one\nRESOLVED\nthree\n")
        run_git_sync(repo, "add", "keep.txt")
        first = await get_session_git_status(session_id="s1", cwd=repo, etag=None)
        run_git_sync(repo, "update-index", "--clear-resolve-undo")

        resp = await get_session_git_status(session_id="s1", cwd=repo, etag=first.etag)

        assert first.resolved_paths == ["keep.txt"]
        assert (resp.unchanged, resp.resolved_paths) == (False, [])
        assert [(e.group, e.path) for e in resp.entries] == [("staged", "keep.txt")]

    @pytest.mark.parametrize(
        "answer",
        [
            CmdResult(rc=129, stdout=b"", stderr=b"error: unknown option\n"),
            CmdResult(rc=-9, stdout=b"", stderr=b"", timed_out=True),
            OSError("boom"),
        ],
    )
    async def test_status_survives_git_not_answering_for_the_records(
        self, repo, monkeypatch, answer
    ):
        make_conflict_branches(repo)
        run_git_sync(repo, "merge", "other", check=False)
        write_file(repo, "keep.txt", b"one\nRESOLVED\nthree\n")
        run_git_sync(repo, "add", "keep.txt")
        real_run_git = changes_service.run_git

        async def no_resolve_undo(args, **kwargs):
            if "--resolve-undo" not in args:
                return await real_run_git(args, **kwargs)
            if isinstance(answer, Exception):
                raise answer
            return answer

        monkeypatch.setattr(changes_service, "run_git", no_resolve_undo)

        resp = await get_session_git_status(session_id="s1", cwd=repo, etag=None)

        assert resp.repo.state == "merging"
        assert resp.resolved_paths == []
        assert [(e.group, e.path) for e in resp.entries] == [("staged", "keep.txt")]

    async def test_a_listing_cut_at_the_output_cap_keeps_its_complete_records(
        self, repo, monkeypatch
    ):
        make_conflict_branches(repo)
        run_git_sync(repo, "merge", "other", check=False)
        write_file(repo, "keep.txt", b"one\nRESOLVED\nthree\n")
        run_git_sync(repo, "add", "keep.txt")
        # The cap fell inside ``README.md.orig``: read as a whole record, the
        # tail would pair with README.md's stage 2 and list a second path.
        cut_tail = (
            _resolve_undo_record("README.md", {2: _rev(repo, "HEAD:README.md")})
            + f"100644 {'1' * 40} 3\tREADME.md".encode()
        )
        real_run_git = changes_service.run_git

        async def cut_listing(args, **kwargs):
            result = await real_run_git(args, **kwargs)
            if "--resolve-undo" not in args:
                return result
            return CmdResult(
                rc=-9, stdout=result.stdout + cut_tail, stderr=b"", truncated=True
            )

        monkeypatch.setattr(changes_service, "run_git", cut_listing)

        resp = await get_session_git_status(session_id="s1", cwd=repo, etag=None)

        assert resp.resolved_paths == ["keep.txt"]

    async def test_heads_blobs_are_asked_for_in_chunks_and_merged(self, repo, monkeypatch):
        names = [f"{name}.txt" for name in ("a", "b", "c", "d", "e")]
        for name in names:
            write_file(repo, name, f"{name}\n".encode())
        commit_all(repo, "five files")
        raw = b"".join(
            _resolve_undo_record(name, {2: _rev(repo, f"HEAD:{name}"), 3: "1" * 40})
            for name in names
        )
        asked: list[list[str]] = []
        real_run_git = changes_service.run_git

        async def record_ls_tree(args, **kwargs):
            if args[0] == "ls-tree":
                asked.append(list(args[args.index("--") + 1 :]))
            return await real_run_git(args, **kwargs)

        monkeypatch.setattr(changes_service, "run_git", record_ls_tree)
        monkeypatch.setattr(changes_service, "_LS_TREE_CHUNK", 2)

        listed = await restorable_paths(repo, raw, unmerged=set())

        assert listed == names
        assert asked == [["a.txt", "b.txt"], ["c.txt", "d.txt"], ["e.txt"]]

    @pytest.mark.parametrize(
        "answer",
        [
            CmdResult(rc=128, stdout=b"", stderr=b"fatal: not a tree object\n"),
            CmdResult(rc=-9, stdout=b"", stderr=b"", timed_out=True),
            CmdResult(rc=-9, stdout=b"", stderr=b"", truncated=True),
            OSError("boom"),
        ],
    )
    async def test_a_chunk_git_cannot_answer_drops_only_its_own_paths(
        self, repo, monkeypatch, answer
    ):
        names = [f"{name}.txt" for name in ("a", "b", "c")]
        for name in names:
            write_file(repo, name, f"{name}\n".encode())
        commit_all(repo, "three files")
        raw = b"".join(
            _resolve_undo_record(name, {2: _rev(repo, f"HEAD:{name}"), 3: "1" * 40})
            for name in names
        )
        real_run_git = changes_service.run_git

        async def first_chunk_fails(args, **kwargs):
            if args[0] != "ls-tree" or "a.txt" not in args:
                return await real_run_git(args, **kwargs)
            if isinstance(answer, Exception):
                raise answer
            return answer

        monkeypatch.setattr(changes_service, "run_git", first_chunk_fails)
        monkeypatch.setattr(changes_service, "_LS_TREE_CHUNK", 2)

        listed = await restorable_paths(repo, raw, unmerged=set())

        assert listed == ["c.txt"]

    async def test_status_survives_git_not_answering_for_heads_blobs(
        self, repo, monkeypatch
    ):
        make_conflict_branches(repo)
        run_git_sync(repo, "merge", "other", check=False)
        write_file(repo, "keep.txt", b"one\nRESOLVED\nthree\n")
        run_git_sync(repo, "add", "keep.txt")
        real_run_git = changes_service.run_git

        async def no_ls_tree(args, **kwargs):
            if args[0] == "ls-tree":
                raise OSError("boom")
            return await real_run_git(args, **kwargs)

        monkeypatch.setattr(changes_service, "run_git", no_ls_tree)

        resp = await get_session_git_status(session_id="s1", cwd=repo, etag=None)

        assert resp.repo.state == "merging"
        assert resp.resolved_paths == []
        assert [(e.group, e.path) for e in resp.entries] == [("staged", "keep.txt")]

    async def test_only_a_two_sided_record_whose_ours_is_still_head_is_restorable(self, repo):
        write_file(repo, "b.txt", b"b\n")
        write_file(repo, "c.txt", b"c\n")
        commit_all(repo, "more files")
        head = {p: _rev(repo, f"HEAD:{p}") for p in ("keep.txt", "README.md", "b.txt", "c.txt")}
        other = "1" * 40
        raw = (
            _resolve_undo_record("keep.txt", {1: other, 2: head["keep.txt"], 3: other})
            + _resolve_undo_record("added.txt", {2: other, 3: other})
            + _resolve_undo_record("README.md", {1: other, 2: other, 3: other})
            + _resolve_undo_record("b.txt", {1: other, 3: other})
            + _resolve_undo_record("c.txt", {2: head["c.txt"], 3: other})
            + b"garbage without a tab\0"
        )

        listed = await restorable_paths(repo, raw, unmerged={"c.txt"})

        assert listed == ["keep.txt"]

    @pytest.mark.parametrize(
        ("gitlink", "listed"),
        [
            ((), ["keep.txt"]),
            ((1,), []),
            ((2,), []),
            ((3,), []),
            ((1, 2, 3), []),
        ],
    )
    async def test_a_record_with_a_submodule_on_any_stage_is_never_restorable(
        self, repo, gitlink, listed
    ):
        # Stage 2 equals HEAD's entry in every case, so only the mode decides.
        other = "1" * 40
        raw = _resolve_undo_record(
            "keep.txt",
            {1: other, 2: _rev(repo, "HEAD:keep.txt"), 3: other},
            gitlink=gitlink,
        )

        assert await restorable_paths(repo, raw, unmerged=set()) == listed

    async def test_a_resolved_submodule_conflict_is_not_listed(self, repo, tmp_path):
        # ``git add sub`` with the submodule on main's commit resolves as
        # "ours": the record's stage 2 is HEAD's gitlink, and ``checkout
        # --merge`` on it dies with "unable to read blob object".
        make_submodule_conflict(repo, str(tmp_path))
        before = await get_session_git_status(session_id="s1", cwd=repo, etag=None)
        run_git_sync(repo, "add", "sub")
        records = subprocess.run(
            [GIT_BIN, "ls-files", "--resolve-undo"], cwd=repo, check=True, capture_output=True
        ).stdout.decode()

        resp = await get_session_git_status(session_id="s1", cwd=repo, etag=None)

        assert [(e.group, e.path) for e in before.entries] == [("conflicts", "sub")]
        assert f"160000 {_rev(repo, 'HEAD:sub')} 2\tsub" in records
        assert resp.repo.state == "merging"
        assert [e.path for e in resp.entries if e.group == "conflicts"] == []
        assert resp.resolved_paths == []

    async def test_the_list_is_capped_in_path_order(self, repo, monkeypatch):
        for name in ("c", "a", "b"):
            write_file(repo, f"{name}.txt", b"x\n")
        commit_all(repo, "three files")
        blob = _rev(repo, "HEAD:a.txt")
        raw = b"".join(
            _resolve_undo_record(f"{name}.txt", {2: blob, 3: "1" * 40})
            for name in ("c", "a", "b")
        )
        monkeypatch.setattr(changes_service, "RESOLVED_LIMIT", 2)

        listed = await restorable_paths(repo, raw, unmerged=set())

        assert listed == ["a.txt", "b.txt"]

    async def test_no_records_never_asks_git(self, tmp_path):
        assert await restorable_paths(str(tmp_path / "gone"), b"", unmerged=set()) == []
