"""Stage / unstage / discard / commit of the session git Changes service
against real temp repositories: what each leaves in the index and working
tree, the ``409`` refusal vocabulary, and the ``git.changed`` echo every
successful mutation publishes."""
from __future__ import annotations

import os
import subprocess

import pytest

from mobaide.agent.git import changes_mutations, changes_service
from mobaide.agent.git.changes_models import MESSAGE_LIMIT, GitCommitRequest
from mobaide.agent.git.changes_mutations import (
    commit_session,
    discard_session_paths,
    get_session_commit_context,
    stage_session_paths,
    unstage_session_paths,
)
from mobaide.agent.git.changes_service import (
    GitBadRequestError,
    GitRefusedError,
    get_session_git_status,
    http_error_for,
)
from mobaide.core.git_exec import CmdResult, reset_caches

from .git_repo_support import (
    GIT_BIN,
    commit_all,
    init_repo,
    make_conflict_branches,
    norm_path,
    posix_only,
    repo,  # noqa: F401 - pytest fixture
    requires_git,
    run_git_sync,
    write_file,
)


@pytest.fixture(autouse=True)
def _fresh_caches():
    reset_caches()
    yield
    reset_caches()


class FakeEventBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    def publish(self, event_type: str, payload: dict) -> None:
        self.published.append((event_type, payload))


def _changed(repo_dir: str, project_name: str) -> tuple[str, dict]:
    return (
        "git.changed",
        {
            "project_name": project_name,
            "toplevel": norm_path(repo_dir),
            "common_dir": norm_path(repo_dir) + "/.git",
        },
    )


async def _entries(cwd: str) -> list[tuple[str, str]]:
    """``(group, path)`` pairs as the status route lists them."""
    resp = await get_session_git_status(session_id="s1", cwd=cwd, etag=None)
    return [(e.group, e.path) for e in resp.entries]


def _read(repo_dir: str, rel: str) -> bytes:
    with open(os.path.join(repo_dir, *rel.split("/")), "rb") as fh:
        return fh.read()


def _git_out(repo_dir: str, *args: str) -> str:
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = os.path.devnull
    env["GIT_CONFIG_SYSTEM"] = os.path.devnull
    return subprocess.run(
        [GIT_BIN, *args], cwd=repo_dir, check=True, env=env, capture_output=True
    ).stdout.decode().strip()


def _commit_count(repo_dir: str) -> int:
    return int(_git_out(repo_dir, "rev-list", "--count", "HEAD"))


def _forget_identity(repo_dir: str, monkeypatch, home: str) -> None:
    """No identity anywhere git looks: the runner scrubs ``GIT_AUTHOR_*``,
    ``HOME`` hides the developer's global config and ``useConfigOnly`` stops
    the user@hostname fallback."""
    run_git_sync(repo_dir, "config", "--unset", "user.name")
    run_git_sync(repo_dir, "config", "--unset", "user.email")
    run_git_sync(repo_dir, "config", "user.useConfigOnly", "true")
    monkeypatch.setenv("HOME", home)
    monkeypatch.setenv("XDG_CONFIG_HOME", os.path.join(home, "xdg"))


def _install_pre_commit(repo_dir: str, script: str) -> None:
    run_git_sync(repo_dir, "config", "core.hooksPath", ".git/hooks")
    hook = write_file(repo_dir, ".git/hooks/pre-commit", script.encode())
    os.chmod(hook, 0o755)


_REJECTING_HOOK = "#!/bin/sh\necho 'lint: nope' >&2\nexit 1\n"


def _commit_huge_body(repo_dir: str, tmp_path) -> str:
    """A generated-changelog commit: a body far past the 64 KiB message cap."""
    body = "x" * 100_000
    write_file(repo_dir, "a.txt", b"a\n")
    message = write_file(str(tmp_path), "message.txt", f"Big\n\n{body}\n".encode())
    run_git_sync(repo_dir, "add", "-A")
    run_git_sync(repo_dir, "commit", "-q", "-F", message)
    return body


def _make_many_conflicts(repo_dir: str, count: int) -> None:
    """``main`` and ``other`` each add the same ``count`` files with different
    content, so merging stops with that many ``AA`` conflicts."""
    names = [f"conflicts/dir_{i:03d}/a_file_with_a_long_enough_name_{i:03d}.txt" for i in range(count)]
    run_git_sync(repo_dir, "checkout", "-q", "-b", "other")
    for name in names:
        write_file(repo_dir, name, b"other\n")
    commit_all(repo_dir, "other side")
    run_git_sync(repo_dir, "checkout", "-q", "main")
    for name in names:
        write_file(repo_dir, name, b"main\n")
    commit_all(repo_dir, "main side")
    run_git_sync(repo_dir, "merge", "other", check=False)


async def _stage(cwd, paths, project_name=None, event_bus=None):
    return await stage_session_paths(
        cwd=cwd, paths=paths, project_name=project_name, event_bus=event_bus
    )


async def _unstage(cwd, paths, project_name=None, event_bus=None):
    return await unstage_session_paths(
        cwd=cwd, paths=paths, project_name=project_name, event_bus=event_bus
    )


async def _discard(cwd, paths, scope, project_name=None, event_bus=None):
    return await discard_session_paths(
        cwd=cwd, paths=paths, scope=scope, project_name=project_name, event_bus=event_bus
    )


async def _commit(cwd, message, *, amend=False, no_verify=False, project_name=None,
                  event_bus=None):
    return await commit_session(
        cwd=cwd,
        request=GitCommitRequest(message=message, amend=amend, no_verify=no_verify),
        project_name=project_name,
        event_bus=event_bus,
    )


@requires_git
class TestStage:
    async def test_stages_modified_deleted_and_untracked_together(self, repo):
        write_file(repo, "keep.txt", b"one\nTWO\nthree\n")
        os.remove(os.path.join(repo, "README.md"))
        write_file(repo, "new.txt", b"n\n")

        resp = await _stage(repo, ["keep.txt", "README.md", "new.txt"])

        assert resp.model_dump() == {"ok": True}
        assert await _entries(repo) == [
            ("staged", "README.md"), ("staged", "keep.txt"), ("staged", "new.txt")]

    @pytest.mark.parametrize(
        ("project_name", "published_as"), [("proj", "proj"), (None, "")]
    )
    async def test_stages_only_the_named_paths_and_publishes_git_changed(
        self, repo, project_name, published_as
    ):
        write_file(repo, "a.txt", b"a\n")
        write_file(repo, "b.txt", b"b\n")
        bus = FakeEventBus()

        await _stage(repo, ["a.txt"], project_name=project_name, event_bus=bus)

        assert await _entries(repo) == [("staged", "a.txt"), ("untracked", "b.txt")]
        assert bus.published == [_changed(repo, published_as)]

    async def test_vanished_path_is_pathspec_mismatch_and_stages_nothing(self, repo):
        write_file(repo, "a.txt", b"a\n")

        with pytest.raises(GitRefusedError) as info:
            await _stage(repo, ["a.txt", "gone.txt"])

        assert info.value.code == "pathspec_mismatch"
        assert "gone.txt" in info.value.message
        assert await _entries(repo) == [("untracked", "a.txt")]

    async def test_escaping_path_is_rejected_before_git_runs(self, tmp_path):
        with pytest.raises(GitBadRequestError) as info:
            await _stage(str(tmp_path), ["../escape.txt"])

        assert info.value.code == "bad_path"


    async def test_git_below_the_mutation_floor_is_refused(self, repo, monkeypatch):
        async def old_git():
            return (2, 25, 9)

        monkeypatch.setattr(changes_service, "git_version", old_git)
        write_file(repo, "a.txt", b"a\n")

        with pytest.raises(GitRefusedError) as info:
            await _stage(repo, ["a.txt"])

        assert info.value.code == "git_too_old"
        assert info.value.message == "git 2.26.0 or newer is required"
        assert await _entries(repo) == [("untracked", "a.txt")]

    async def test_index_lock_held_by_another_git_is_refused(self, repo):
        write_file(repo, "a.txt", b"a\n")
        write_file(repo, ".git/index.lock", b"")

        with pytest.raises(GitRefusedError) as info:
            await _stage(repo, ["a.txt"])

        assert info.value.code == "index_locked"
        assert "index.lock" in info.value.message

    async def test_git_runs_while_the_repo_lock_is_held(self, repo, monkeypatch):
        real_run_git = changes_mutations.run_git
        lock = changes_mutations._lock_for(norm_path(repo))
        held: list[bool] = []

        async def observe(args, **kwargs):
            if args[0] == "add":
                held.append(lock.locked())
            return await real_run_git(args, **kwargs)

        monkeypatch.setattr(changes_mutations, "run_git", observe)
        write_file(repo, "a.txt", b"a\n")

        await _stage(repo, ["a.txt"])

        assert held == [True]
        assert lock.locked() is False


def _merge_conflict(repo_dir: str) -> None:
    make_conflict_branches(repo_dir)
    run_git_sync(repo_dir, "merge", "other", check=False)


@requires_git
class TestMarkResolved:
    async def test_a_conflicted_file_still_holding_markers_is_refused_and_nothing_staged(
        self, repo
    ):
        _merge_conflict(repo)
        write_file(repo, "a.txt", b"a\n")
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _stage(repo, ["a.txt", "keep.txt"], project_name="proj", event_bus=bus)

        assert (info.value.code, info.value.message) == ("conflict_markers", "keep.txt")
        assert await _entries(repo) == [("conflicts", "keep.txt"), ("untracked", "a.txt")]
        assert bus.published == []

    async def test_a_fixed_file_is_marked_resolved(self, repo):
        _merge_conflict(repo)
        write_file(repo, "keep.txt", b"one\nBOTH\nthree\n")
        bus = FakeEventBus()

        resp = await _stage(repo, ["keep.txt"], project_name="proj", event_bus=bus)

        assert resp.model_dump() == {"ok": True}
        assert await _entries(repo) == [("staged", "keep.txt")]
        assert bus.published == [_changed(repo, "proj")]

    async def test_deleting_the_conflicted_file_resolves_it_as_a_removal(self, repo):
        _merge_conflict(repo)
        os.remove(os.path.join(repo, "keep.txt"))

        await _stage(repo, ["keep.txt"])

        assert await _entries(repo) == [("staged", "keep.txt")]
        assert _git_out(repo, "ls-files", "-u") == ""

    async def test_marker_lines_in_a_file_that_is_not_unmerged_stage_as_usual(self, repo):
        _merge_conflict(repo)
        write_file(repo, "docs/merging.md", b"Git writes\n<<<<<<< HEAD\nlike this\n")

        await _stage(repo, ["docs/merging.md"])

        assert await _entries(repo) == [
            ("conflicts", "keep.txt"), ("staged", "docs/merging.md")]

    @pytest.mark.parametrize(
        ("content", "refused"),
        [
            (b"one\n<<<<<<< HEAD\nMAIN\n", True),
            (b"one\n>>>>>>> 1234567 (other side)\n", True),
            (b"one\n<<<<<<<\ntwo\n", True),
            (b"one\n<<<<<<<<<<\ntwo\n", True),
            (b"one\r\n<<<<<<< HEAD\r\ntwo\r\n", True),
            (b"one\n>>>>>>>", True),
            (b"one\n<<<<<<<x\ntwo\n", False),
            (b"one\n<<<<<< HEAD\ntwo\n", False),
            (b"one\n  <<<<<<< HEAD\ntwo\n", False),
            (b"one\n=======\ntwo\n", False),
            (b"one\n||||||| base\ntwo\n", False),
        ],
        ids=[
            "ours-marker", "theirs-marker", "bare-marker", "long-marker", "crlf-marker",
            "marker-at-eof", "marker-glued-to-text", "six-angles", "indented",
            "separator-only", "base-marker-only",
        ],
    )
    async def test_only_a_line_opening_with_seven_angle_brackets_counts_as_a_marker(
        self, repo, content, refused
    ):
        _merge_conflict(repo)
        write_file(repo, "keep.txt", content)

        if refused:
            with pytest.raises(GitRefusedError) as info:
                await _stage(repo, ["keep.txt"])
            assert info.value.code == "conflict_markers"
            assert await _entries(repo) == [("conflicts", "keep.txt")]
        else:
            await _stage(repo, ["keep.txt"])
            assert await _entries(repo) == [("staged", "keep.txt")]

    async def test_a_binary_file_is_not_scanned(self, repo):
        _merge_conflict(repo)
        write_file(repo, "keep.txt", b"\0binary\n<<<<<<< HEAD\n")

        await _stage(repo, ["keep.txt"])

        assert await _entries(repo) == [("staged", "keep.txt")]

    async def test_a_listing_cut_at_the_cap_presumes_every_path_unmerged(
        self, repo, monkeypatch
    ):
        real_run_git = changes_mutations.run_git

        async def cut_listing(args, **kwargs):
            if args[:2] == ["ls-files", "-u"]:
                return CmdResult(rc=-9, stdout=b"", stderr=b"", truncated=True)
            return await real_run_git(args, **kwargs)

        monkeypatch.setattr(changes_mutations, "run_git", cut_listing)
        write_file(repo, "clean.txt", b"c\n")
        write_file(repo, "marked.txt", b">>>>>>> theirs\n")

        await _stage(repo, ["clean.txt"])
        with pytest.raises(GitRefusedError) as info:
            await _stage(repo, ["marked.txt"])

        assert (info.value.code, info.value.message) == ("conflict_markers", "marked.txt")
        assert await _entries(repo) == [("staged", "clean.txt"), ("untracked", "marked.txt")]


@requires_git
class TestUnstage:
    async def test_moves_a_staged_change_back_to_the_working_tree(self, repo):
        write_file(repo, "keep.txt", b"one\nTWO\nthree\n")
        run_git_sync(repo, "add", "keep.txt")
        bus = FakeEventBus()

        await _unstage(repo, ["keep.txt"], project_name="proj", event_bus=bus)

        assert await _entries(repo) == [("unstaged", "keep.txt")]
        assert _read(repo, "keep.txt") == b"one\nTWO\nthree\n"
        assert bus.published == [_changed(repo, "proj")]

    async def test_works_on_a_branch_with_no_commits(self, tmp_path):
        unborn = init_repo(str(tmp_path / "unborn"), initial_commit=False)
        write_file(unborn, "a.txt", b"a\n")
        run_git_sync(unborn, "add", "a.txt")

        await _unstage(unborn, ["a.txt"])

        assert await _entries(unborn) == [("untracked", "a.txt")]

    async def test_rename_with_both_paths_undoes_the_whole_move(self, repo):
        run_git_sync(repo, "mv", "keep.txt", "moved.txt")

        await _unstage(repo, ["moved.txt", "keep.txt"])

        assert await _entries(repo) == [("unstaged", "keep.txt"), ("untracked", "moved.txt")]

    async def test_rename_with_the_new_path_alone_leaves_the_deletion_staged(self, repo):
        run_git_sync(repo, "mv", "keep.txt", "moved.txt")

        await _unstage(repo, ["moved.txt"])

        assert await _entries(repo) == [("staged", "keep.txt"), ("untracked", "moved.txt")]

    async def test_a_path_that_matches_nothing_is_skipped_and_the_rest_unstaged(self, repo):
        # Unlike ``add``, ``reset`` ignores a pathspec that matches nothing:
        # a row that vanished between the poll and the tap costs nothing.
        write_file(repo, "keep.txt", b"one\nTWO\nthree\n")
        run_git_sync(repo, "add", "keep.txt")

        resp = await _unstage(repo, ["keep.txt", "gone.txt"])

        assert resp.model_dump() == {"ok": True}
        assert await _entries(repo) == [("unstaged", "keep.txt")]


@requires_git
class TestDiscard:
    async def test_unstaged_scope_restores_the_index_version_and_keeps_the_staged_part(
        self, repo
    ):
        write_file(repo, "keep.txt", b"one\nSTAGED\nthree\n")
        run_git_sync(repo, "add", "keep.txt")
        write_file(repo, "keep.txt", b"one\nSTAGED\nWORKTREE\n")
        bus = FakeEventBus()

        await _discard(repo, ["keep.txt"], "unstaged", project_name="proj", event_bus=bus)

        assert _read(repo, "keep.txt") == b"one\nSTAGED\nthree\n"
        assert await _entries(repo) == [("staged", "keep.txt")]
        assert bus.published == [_changed(repo, "proj")]

    async def test_unstaged_scope_brings_back_a_deleted_file(self, repo):
        os.remove(os.path.join(repo, "README.md"))

        await _discard(repo, ["README.md"], "unstaged")

        assert _read(repo, "README.md") == b"hello\n"
        assert await _entries(repo) == []

    async def test_untracked_scope_deletes_in_chunks_and_spares_tracked_files(
        self, repo, monkeypatch
    ):
        monkeypatch.setattr(changes_mutations, "_CLEAN_CHUNK", 2)
        names = [f"u{i}.txt" for i in range(5)]
        for name in names:
            write_file(repo, name, b"x\n")
        write_file(repo, "keep.txt", b"one\nTWO\nthree\n")

        await _discard(repo, [*names, "keep.txt"], "untracked")

        assert [os.path.exists(os.path.join(repo, n)) for n in names] == [False] * 5
        assert _read(repo, "keep.txt") == b"one\nTWO\nthree\n"
        assert await _entries(repo) == [("unstaged", "keep.txt")]

    async def test_untracked_scope_skips_a_path_that_matches_nothing_and_deletes_nothing(
        self, repo
    ):
        write_file(repo, "u.txt", b"x\n")

        resp = await _discard(repo, ["gone.txt"], "untracked")

        assert resp.model_dump() == {"ok": True}
        assert _read(repo, "u.txt") == b"x\n"
        assert await _entries(repo) == [("untracked", "u.txt")]

    async def test_unstaged_scope_refuses_a_vanished_path_and_restores_nothing(self, repo):
        write_file(repo, "keep.txt", b"one\nTWO\nthree\n")

        with pytest.raises(GitRefusedError) as info:
            await _discard(repo, ["keep.txt", "gone.txt"], "unstaged")

        assert info.value.code == "pathspec_mismatch"
        assert _read(repo, "keep.txt") == b"one\nTWO\nthree\n"
        assert await _entries(repo) == [("unstaged", "keep.txt")]


@requires_git
class TestEmptyPathList:
    @pytest.mark.parametrize(
        "call",
        [
            lambda cwd: _stage(cwd, []),
            lambda cwd: _unstage(cwd, []),
            lambda cwd: _discard(cwd, [], "untracked"),
        ],
        ids=["stage", "unstage", "discard"],
    )
    async def test_an_empty_list_is_a_bad_request_and_git_never_runs(
        self, repo, monkeypatch, call
    ):
        # The transports' ``min_length=1`` is not the only guard: an empty
        # pathspec file means "no pathspec" to git, which would turn ``add -A``
        # and ``reset`` into whole-tree operations.
        async def never(args, **kwargs):
            raise AssertionError(f"git {args[0]} ran on an empty path list")

        monkeypatch.setattr(changes_mutations, "run_git", never)
        write_file(repo, "keep.txt", b"one\nTWO\nthree\n")
        write_file(repo, "a.txt", b"a\n")
        run_git_sync(repo, "add", "a.txt")

        with pytest.raises(GitBadRequestError) as info:
            await call(repo)

        assert info.value.code == "bad_path"
        assert await _entries(repo) == [("staged", "a.txt"), ("unstaged", "keep.txt")]


@requires_git
class TestCommitContext:
    async def test_reports_the_repo_identity_and_head(self, repo):
        write_file(repo, "a.txt", b"a\n")
        commit_all(repo, "Add a\n\nWhy a matters.\nSecond line.\n")

        ctx = await get_session_commit_context(cwd=repo)

        assert ctx.author.model_dump() == {"name": "Test", "email": "test@example.com"}
        assert ctx.head.oid == _git_out(repo, "rev-parse", "HEAD")
        assert ctx.head.oid.startswith(ctx.head.short_oid) and len(ctx.head.short_oid) >= 7
        assert (ctx.head.subject, ctx.head.body) == ("Add a", "Why a matters.\nSecond line.")


    async def test_a_huge_body_is_read_whole_and_clipped_to_the_message_cap(
        self, repo, tmp_path
    ):
        # Past the runner's small-output cap ``git log`` used to be killed
        # (a 500); the prefill needs no more than the sheet could send back.
        body = _commit_huge_body(repo, tmp_path)

        ctx = await get_session_commit_context(cwd=repo)

        assert ctx.head.subject == "Big"
        assert len(ctx.head.body) == MESSAGE_LIMIT
        assert ctx.head.body == body[:MESSAGE_LIMIT]

    async def test_unborn_branch_has_no_head_but_still_an_author(self, tmp_path):
        unborn = init_repo(str(tmp_path / "unborn"), initial_commit=False)

        ctx = await get_session_commit_context(cwd=unborn)

        assert ctx.head is None
        assert ctx.author.name == "Test"

    async def test_unknown_identity_is_null(self, repo, tmp_path, monkeypatch):
        _forget_identity(repo, monkeypatch, str(tmp_path / "home"))

        ctx = await get_session_commit_context(cwd=repo)

        assert ctx.author is None
        assert ctx.head.subject == "initial"


@requires_git
class TestCommit:
    async def test_commits_only_the_staged_set(self, repo):
        write_file(repo, "a.txt", b"a\n")
        write_file(repo, "keep.txt", b"one\nTWO\nthree\n")
        run_git_sync(repo, "add", "a.txt")
        bus = FakeEventBus()

        resp = await _commit(repo, "Add a\n\nBecause.", project_name="proj", event_bus=bus)

        head = (await get_session_commit_context(cwd=repo)).head
        assert (resp.oid, resp.short_oid, resp.subject) == (head.oid, head.short_oid, "Add a")
        assert head.body == "Because."
        assert _git_out(repo, "log", "-1", "--format=%an <%ae>") == "Test <test@example.com>"
        assert await _entries(repo) == [("unstaged", "keep.txt")]
        assert _commit_count(repo) == 2
        assert bus.published == [_changed(repo, "proj")]

    async def test_leading_hash_in_the_message_survives(self, repo):
        write_file(repo, "a.txt", b"a\n")
        run_git_sync(repo, "add", "a.txt")

        resp = await _commit(repo, "#123 fix the thing")

        assert resp.subject == "#123 fix the thing"

    async def test_first_commit_on_an_unborn_branch(self, tmp_path):
        unborn = init_repo(str(tmp_path / "unborn"), initial_commit=False)
        write_file(unborn, "a.txt", b"a\n")
        run_git_sync(unborn, "add", "a.txt")

        resp = await _commit(unborn, "first")

        assert resp.subject == "first"
        assert _commit_count(unborn) == 1
        assert await _entries(unborn) == []

    async def test_blank_message_is_a_bad_request_before_git_runs(self, tmp_path):
        with pytest.raises(GitBadRequestError) as info:
            await _commit(str(tmp_path), "  \n")

        assert info.value.code == "empty_message"

    async def test_nothing_staged_is_refused_and_the_working_tree_is_left_alone(self, repo):
        write_file(repo, "keep.txt", b"one\nTWO\nthree\n")

        with pytest.raises(GitRefusedError) as info:
            await _commit(repo, "nothing")

        assert info.value.code == "nothing_to_commit"
        assert await _entries(repo) == [("unstaged", "keep.txt")]
        assert _commit_count(repo) == 1

    async def test_amend_rewrites_head_even_with_nothing_staged(self, repo):
        before = (await get_session_commit_context(cwd=repo)).head

        resp = await _commit(repo, "initial, reworded", amend=True)

        assert resp.oid != before.oid
        assert resp.subject == "initial, reworded"
        assert _commit_count(repo) == 1

    async def test_amend_over_a_huge_body_still_commits(self, repo, tmp_path):
        _commit_huge_body(repo, tmp_path)

        resp = await _commit(repo, "Big, reworded", amend=True)

        assert resp.subject == "Big, reworded"
        assert _commit_count(repo) == 2
        assert (await get_session_commit_context(cwd=repo)).head.body == ""

    async def test_amend_on_an_unborn_branch_is_refused(self, tmp_path):
        unborn = init_repo(str(tmp_path / "unborn"), initial_commit=False)
        write_file(unborn, "a.txt", b"a\n")
        run_git_sync(unborn, "add", "a.txt")

        with pytest.raises(GitRefusedError) as info:
            await _commit(unborn, "x", amend=True)

        assert info.value.code == "no_head"
        assert await _entries(unborn) == [("staged", "a.txt")]

    async def test_unmerged_index_is_refused(self, repo):
        make_conflict_branches(repo)
        run_git_sync(repo, "merge", "other", check=False)

        with pytest.raises(GitRefusedError) as info:
            await _commit(repo, "merge")

        assert info.value.code == "unmerged"

    async def test_hundreds_of_unmerged_paths_are_still_refused_as_unmerged(self, repo):
        # ``ls-files -u`` past 64 KiB used to be killed by the small-output
        # cap, turning the pre-check into a 500.
        _make_many_conflicts(repo, 400)
        assert len(_git_out(repo, "ls-files", "-u")) > 64 * 1024

        with pytest.raises(GitRefusedError) as info:
            await _commit(repo, "merge")

        assert info.value.code == "unmerged"
        assert _commit_count(repo) == 2

    async def test_unknown_identity_is_refused_before_anything_is_written(
        self, repo, tmp_path, monkeypatch
    ):
        write_file(repo, "a.txt", b"a\n")
        run_git_sync(repo, "add", "a.txt")
        _forget_identity(repo, monkeypatch, str(tmp_path / "home"))

        with pytest.raises(GitRefusedError) as info:
            await _commit(repo, "who am i")

        assert info.value.code == "identity_required"
        assert _commit_count(repo) == 1
        assert await _entries(repo) == [("staged", "a.txt")]

    @posix_only
    async def test_rejecting_hook_is_refused_with_its_output(self, repo):
        _install_pre_commit(repo, _REJECTING_HOOK)
        write_file(repo, "a.txt", b"a\n")
        run_git_sync(repo, "add", "a.txt")
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _commit(repo, "hooked", event_bus=bus)

        assert info.value.code == "hook_failed"
        assert "lint: nope" in info.value.message
        assert _commit_count(repo) == 1
        assert await _entries(repo) == [("staged", "a.txt")]
        assert bus.published == []

    @posix_only
    async def test_no_verify_skips_the_hook(self, repo):
        _install_pre_commit(repo, _REJECTING_HOOK)
        write_file(repo, "a.txt", b"a\n")
        run_git_sync(repo, "add", "a.txt")

        resp = await _commit(repo, "unhooked", no_verify=True)

        assert resp.subject == "unhooked"
        assert _commit_count(repo) == 2

    async def test_index_lock_held_by_another_git_is_refused(self, repo):
        write_file(repo, "a.txt", b"a\n")
        run_git_sync(repo, "add", "a.txt")
        write_file(repo, ".git/index.lock", b"")

        with pytest.raises(GitRefusedError) as info:
            await _commit(repo, "locked")

        assert info.value.code == "index_locked"
        assert _commit_count(repo) == 1


class TestRefusalMapping:
    def test_refusal_is_409_with_its_code_and_trimmed_message(self):
        assert http_error_for(GitRefusedError("hook_failed", "x" * 600)) == (
            409, {"detail": {"code": "hook_failed", "message": "x" * 500}})
