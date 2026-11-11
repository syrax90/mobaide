"""Start / continue / skip / abort of the session rebase against real temp
repositories: the ``stopped`` / ``rebased`` / ``up_to_date`` / ``aborted``
outcomes, the guards that refuse before git runs, git's own refusals in the
``409`` vocabulary, and the ``git.changed`` echo published whenever git ran."""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import threading

import pytest

from mobaide.agent.git import changes_rebase
from mobaide.agent.git.changes_jobs import job_registry
from mobaide.agent.git.changes_rebase import (
    abort_session_rebase,
    continue_session_rebase,
    skip_session_rebase,
    start_session_rebase,
)
from mobaide.agent.git.changes_service import (
    GitRefusedError,
    GitTimeoutError,
    get_session_git_status,
)
from mobaide.agent.git.service import list_git_items
from mobaide.core.git_exec import CmdResult, reset_caches
from mobaide.linear.worktree import WorktreeManager

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


@pytest.fixture(autouse=True)
async def _fresh_registry():
    job_registry.reset()
    yield
    await job_registry.shutdown()
    job_registry.reset()


class FakeEventBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    def publish(self, event_type: str, payload: dict) -> None:
        self.published.append((event_type, payload))


def _changed(toplevel: str, project_name: str, common_repo: str | None = None) -> tuple[str, dict]:
    return (
        "git.changed",
        {
            "project_name": project_name,
            "toplevel": norm_path(toplevel),
            "common_dir": norm_path(common_repo or toplevel) + "/.git",
        },
    )


def _git_out(repo_dir: str, *args: str) -> str:
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = os.path.devnull
    env["GIT_CONFIG_SYSTEM"] = os.path.devnull
    return subprocess.run(
        [GIT_BIN, *args], cwd=repo_dir, check=True, env=env, capture_output=True
    ).stdout.decode().strip()


_TODO = ".git/rebase-merge/git-rebase-todo"


def _read(repo_dir: str, rel: str) -> bytes:
    with open(os.path.join(repo_dir, *rel.split("/")), "rb") as fh:
        return fh.read()


def _oid(repo_dir: str, rev: str = "HEAD") -> str:
    return _git_out(repo_dir, "rev-parse", rev)


def _subjects(repo_dir: str) -> list[str]:
    return _git_out(repo_dir, "log", "--format=%s").splitlines()


async def _status(cwd: str):
    return await get_session_git_status(session_id="s1", cwd=cwd, etag=None)


async def _start(cwd, onto, *, project_name="proj", event_bus=None):
    return await start_session_rebase(
        cwd=cwd, onto=onto, project_name=project_name, event_bus=event_bus
    )


async def _continue(cwd, *, project_name="proj", event_bus=None):
    return await continue_session_rebase(
        cwd=cwd, project_name=project_name, event_bus=event_bus
    )


async def _skip(cwd, *, project_name="proj", event_bus=None):
    return await skip_session_rebase(cwd=cwd, project_name=project_name, event_bus=event_bus)


async def _abort(cwd, *, project_name="proj", event_bus=None):
    return await abort_session_rebase(cwd=cwd, project_name=project_name, event_bus=event_bus)


def _topic_behind_main(repo_dir: str) -> None:
    """``topic`` adds ``topic.txt`` off the initial commit while ``main``
    moves on with ``main.txt``: a rebase with nothing to conflict on."""
    run_git_sync(repo_dir, "checkout", "-q", "-b", "topic")
    write_file(repo_dir, "topic.txt", b"t\n")
    commit_all(repo_dir, "topic work")
    run_git_sync(repo_dir, "checkout", "-q", "main")
    write_file(repo_dir, "main.txt", b"m\n")
    commit_all(repo_dir, "main work")
    run_git_sync(repo_dir, "checkout", "-q", "topic")


def _stop_on_conflict(repo_dir: str, *, second_conflict: bool = False) -> None:
    """``other`` (checked out) conflicts with ``main`` on line 2 of
    ``keep.txt``; with ``second_conflict`` it rewrites that line once more in
    a second commit, which conflicts again whatever the first stop became."""
    make_conflict_branches(repo_dir)
    run_git_sync(repo_dir, "checkout", "-q", "other")
    if second_conflict:
        write_file(repo_dir, "keep.txt", b"one\nOTHER AGAIN\nthree\n")
        commit_all(repo_dir, "other again")


def _resolve(repo_dir: str, content: bytes = b"one\nRESOLVED\nthree\n") -> None:
    write_file(repo_dir, "keep.txt", content)
    run_git_sync(repo_dir, "add", "keep.txt")


def _picks_onto_main(repo_dir: str, picks: list[tuple[str, str, bytes]]) -> None:
    """``topic`` (checked out) makes one commit per ``(subject, path,
    content)`` off the initial commit while ``main`` rewrites line 2 of
    ``keep.txt``, so every pick that rewrites that line stops on a conflict."""
    run_git_sync(repo_dir, "checkout", "-q", "-b", "topic")
    for subject, path, content in picks:
        write_file(repo_dir, path, content)
        commit_all(repo_dir, subject)
    run_git_sync(repo_dir, "checkout", "-q", "main")
    write_file(repo_dir, "keep.txt", b"one\nMAIN\nthree\n")
    commit_all(repo_dir, "main side")
    run_git_sync(repo_dir, "checkout", "-q", "topic")


def _stop_before_a_pick_that_adds_b(repo_dir: str) -> None:
    """Stopped on a ``keep.txt`` conflict at step 1 of 2, with ``b.txt``
    untracked in the worktree where the second pick adds it."""
    _picks_onto_main(repo_dir, [
        ("topic side", "keep.txt", b"one\nTOPIC\nthree\n"),
        ("adds b", "b.txt", b"b\n"),
    ])
    run_git_sync(repo_dir, "rebase", "--no-autostash", "main", check=False)
    write_file(repo_dir, "b.txt", b"mine\n")


def _stop_before_a_conflicting_pick_that_adds_b(
    repo_dir: str, subject: str, later: tuple[tuple[str, str, bytes], ...] = ()
) -> None:
    """Resolved at step 1, with ``b.txt`` untracked where the second pick,
    ``subject``, adds it while rewriting line 2 of ``keep.txt`` again, and the
    ``later`` picks after it: a continue commits the resolution and git
    reschedules that pick, whose retry conflicts once the blocker is gone."""
    _picks_onto_main(repo_dir, [("topic side", "keep.txt", b"one\nTOPIC\nthree\n")])
    write_file(repo_dir, "b.txt", b"b\n")
    write_file(repo_dir, "keep.txt", b"one\nTOPIC AGAIN\nthree\n")
    commit_all(repo_dir, subject)
    for later_subject, path, content in later:
        write_file(repo_dir, path, content)
        commit_all(repo_dir, later_subject)
    run_git_sync(repo_dir, "rebase", "--no-autostash", "main", check=False)
    _resolve(repo_dir)
    write_file(repo_dir, "b.txt", b"mine\n")


def _plant_stopped_sha(repo_dir: str) -> None:
    """The ``stopped-sha`` git before 2.43 writes at a rescheduled pick."""
    write_file(
        repo_dir, ".git/rebase-merge/stopped-sha", f"{_oid(repo_dir, 'REBASE_HEAD')}\n".encode()
    )


def _pin_git_version(monkeypatch, version: tuple[int, int, int]) -> None:
    async def pinned():
        return version

    monkeypatch.setattr(changes_rebase, "git_version", pinned)


_CLT_GIT_DIR = "/Library/Developer/CommandLineTools/usr/bin"


def _clt_git_before_2_43() -> bool:
    binary = os.path.join(_CLT_GIT_DIR, "git")
    if not os.path.isfile(binary):
        return False
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    match = re.search(rb"(\d+)\.(\d+)", out)
    return match is not None and (int(match[1]), int(match[2])) < (2, 43)


@requires_git
class TestStart:
    async def test_replays_the_branch_onto_the_target_and_publishes(self, repo):
        _topic_behind_main(repo)
        main_oid = _oid(repo, "main")
        bus = FakeEventBus()

        resp = await _start(repo, "main", event_bus=bus)

        assert resp.model_dump() == {"outcome": "rebased", "rebase": None, "message": None}
        assert _oid(repo, "HEAD~1") == main_oid
        assert _subjects(repo) == ["topic work", "main work", "initial"]
        assert (await _status(repo)).repo.state == "clean"
        assert bus.published == [_changed(repo, "proj")]

    async def test_a_branch_already_on_the_target_is_up_to_date(self, repo):
        run_git_sync(repo, "checkout", "-q", "-b", "topic")
        write_file(repo, "topic.txt", b"t\n")
        commit_all(repo, "topic work")
        before = _oid(repo)
        bus = FakeEventBus()

        resp = await _start(repo, "main", project_name=None, event_bus=bus)

        assert resp.model_dump() == {"outcome": "up_to_date", "rebase": None, "message": None}
        assert _oid(repo) == before
        assert bus.published == [_changed(repo, "")]

    async def test_a_conflict_stops_with_the_progress_the_banner_needs(self, repo):
        _stop_on_conflict(repo)
        main_oid = _oid(repo, "main")
        other_oid = _oid(repo, "other")
        bus = FakeEventBus()

        resp = await _start(repo, "main", event_bus=bus)

        assert (resp.outcome, resp.message) == ("stopped", None)
        assert resp.rebase.model_dump() == {
            "branch": "other",
            "onto_oid": main_oid,
            "onto_short_oid": _git_out(repo, "rev-parse", "--short", "main"),
            "onto_ref": "main",
            "stopped_on": {
                "oid": other_oid,
                "short_oid": _git_out(repo, "rev-parse", "--short", "other"),
                "subject": "other side",
            },
            "step": 1,
            "total": 1,
        }
        status = await _status(repo)
        assert status.repo.state == "rebasing"
        assert status.repo.rebase == resp.rebase
        assert [(e.group, e.path) for e in status.entries] == [("conflicts", "keep.txt")]
        assert bus.published == [_changed(repo, "proj")]

    async def test_an_untracked_file_in_a_later_pick_stops_with_gits_message(self, repo):
        # git began (HEAD is detached onto main), then declined one pick: the
        # rebase is in progress, so it is a stop the banner can abort, not a 409.
        run_git_sync(repo, "checkout", "-q", "-b", "topic")
        write_file(repo, "a.txt", b"a\n")
        commit_all(repo, "adds a")
        write_file(repo, "b.txt", b"b\n")
        commit_all(repo, "adds b")
        run_git_sync(repo, "rm", "-q", "b.txt")
        commit_all(repo, "removes b")
        run_git_sync(repo, "checkout", "-q", "main")
        write_file(repo, "main.txt", b"m\n")
        commit_all(repo, "main work")
        run_git_sync(repo, "checkout", "-q", "topic")
        write_file(repo, "b.txt", b"mine\n")

        resp = await _start(repo, "main")

        assert resp.outcome == "stopped"
        assert "would be overwritten by merge" in resp.message
        assert resp.rebase.branch == "topic"
        assert (await _status(repo)).repo.state == "rebasing"
        assert _read(repo, "b.txt") == b"mine\n"

    @pytest.mark.parametrize("onto", ["-i", "--exec=touch pwned", "main\0x"])
    async def test_an_option_like_or_nul_ref_is_unknown_before_the_repo_is_resolved(
        self, tmp_path, onto
    ):
        # A vanished cwd would be cwd_missing: the ref is refused first.
        with pytest.raises(GitRefusedError) as info:
            await _start(str(tmp_path / "gone"), onto)

        assert (info.value.code, info.value.message) == ("unknown_ref", onto)

    async def test_a_ref_that_names_no_commit_is_unknown_and_git_rebase_never_runs(
        self, repo
    ):
        bus = FakeEventBus()
        run_git_sync(repo, "tag", "tree-tag", "HEAD^{tree}")

        refused = []
        for onto in ("no-such-branch", "tree-tag"):
            with pytest.raises(GitRefusedError) as info:
                await _start(repo, onto, event_bus=bus)
            refused.append((info.value.code, info.value.message))

        assert refused == [("unknown_ref", "no-such-branch"), ("unknown_ref", "tree-tag")]
        assert bus.published == []

    async def test_an_operation_in_progress_is_refused_with_its_state(self, repo):
        make_conflict_branches(repo)
        run_git_sync(repo, "merge", "other", check=False)
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _start(repo, "other", event_bus=bus)

        assert (info.value.code, info.value.message) == ("operation_in_progress", "merging")
        assert (await _status(repo)).repo.state == "merging"
        assert bus.published == []


    async def test_a_branch_with_no_commits_is_no_head(self, tmp_path):
        unborn = init_repo(str(tmp_path / "unborn"), initial_commit=False)
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _start(unborn, "main", event_bus=bus)

        assert info.value.code == "no_head"
        assert bus.published == []

    @pytest.mark.parametrize("staged", [False, True], ids=["unstaged", "staged"])
    async def test_tracked_changes_are_dirty_worktree_even_with_autostash_configured(
        self, repo, staged
    ):
        _topic_behind_main(repo)
        run_git_sync(repo, "config", "rebase.autoStash", "true")
        before = _oid(repo)
        write_file(repo, "keep.txt", b"one\nMINE\nthree\n")
        if staged:
            run_git_sync(repo, "add", "keep.txt")
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _start(repo, "main", event_bus=bus)

        assert info.value.code == "dirty_worktree"
        assert _oid(repo) == before
        assert _read(repo, "keep.txt") == b"one\nMINE\nthree\n"
        assert _git_out(repo, "stash", "list") == ""
        assert (await _status(repo)).repo.state == "clean"
        assert bus.published == [_changed(repo, "proj")]

    async def test_untracked_files_alone_do_not_block(self, repo):
        _topic_behind_main(repo)
        write_file(repo, "scratch.txt", b"s\n")

        resp = await _start(repo, "main")

        assert resp.outcome == "rebased"
        assert _read(repo, "scratch.txt") == b"s\n"

    @posix_only
    async def test_a_refusing_pre_rebase_hook_is_hook_failed(self, repo):
        _topic_behind_main(repo)
        run_git_sync(repo, "config", "core.hooksPath", ".git/hooks")
        hook = write_file(repo, ".git/hooks/pre-rebase", b"#!/bin/sh\nexit 1\n")
        os.chmod(hook, 0o755)
        before = _oid(repo)

        with pytest.raises(GitRefusedError) as info:
            await _start(repo, "main")

        assert info.value.code == "hook_failed"
        assert "pre-rebase hook refused" in info.value.message
        assert _oid(repo) == before


    async def test_a_listed_branch_shadowed_by_a_tag_rebases_onto_the_branch_not_the_tag(
        self, repo
    ):
        # A bare `release` resolves to the tag first; the pickers send
        # `refs/heads/` + the branch name the project listing reports.
        _topic_behind_main(repo)
        run_git_sync(repo, "branch", "release", "main")
        run_git_sync(repo, "tag", "release", "main~1")
        assert _oid(repo, "release") != _oid(repo, "refs/heads/release")
        items = await list_git_items(
            repo, WorktreeManager(worktree_base=".worktrees", branch_prefix="auto"), remote=True
        )
        [listed] = [i.branch for i in items if i.branch not in ("main", "topic")]

        resp = await _start(repo, "refs/heads/" + listed)

        assert resp.outcome == "rebased"
        assert _oid(repo, "HEAD~1") == _oid(repo, "refs/heads/release")
        assert _subjects(repo) == ["topic work", "main work", "initial"]

    async def test_a_timeout_is_504_and_still_publishes(self, repo, monkeypatch):
        _topic_behind_main(repo)
        real_run_git = changes_rebase.run_git

        async def stall_rebase(args, **kwargs):
            if args[0] == "rebase":
                return CmdResult(rc=-9, stdout=b"", stderr=b"", timed_out=True)
            return await real_run_git(args, **kwargs)

        monkeypatch.setattr(changes_rebase, "run_git", stall_rebase)
        bus = FakeEventBus()

        with pytest.raises(GitTimeoutError):
            await _start(repo, "main", event_bus=bus)

        assert bus.published == [_changed(repo, "proj")]

    async def test_git_rebase_runs_under_the_repo_lock_with_no_editor(
        self, repo, monkeypatch
    ):
        _topic_behind_main(repo)
        real_run_git = changes_rebase.run_git
        lock = changes_rebase._lock_for(norm_path(repo))
        seen: list[tuple[list[str], bool, dict, float]] = []

        async def observe(args, **kwargs):
            if args[0] == "rebase":
                seen.append((list(args), lock.locked(), kwargs.get("env_extra"),
                             kwargs["timeout"]))
            return await real_run_git(args, **kwargs)

        monkeypatch.setattr(changes_rebase, "run_git", observe)

        await _start(repo, "main")

        assert seen == [(
            ["rebase", "--no-autostash", "--end-of-options", "main"],
            True,
            {"GIT_EDITOR": ":", "GIT_SEQUENCE_EDITOR": ":"},
            25.0,
        )]
        assert lock.locked() is False


@requires_git
class TestContinue:
    async def test_a_resolved_stop_finishes_with_the_original_message(self, repo):
        _stop_on_conflict(repo)
        await _start(repo, "main")
        _resolve(repo)
        bus = FakeEventBus()

        resp = await _continue(repo, event_bus=bus)

        assert resp.model_dump() == {"outcome": "rebased", "rebase": None, "message": None}
        assert _subjects(repo) == ["other side", "main side", "initial"]
        assert _read(repo, "keep.txt") == b"one\nRESOLVED\nthree\n"
        assert _git_out(repo, "symbolic-ref", "--short", "HEAD") == "other"
        assert (await _status(repo)).repo.state == "clean"
        assert bus.published == [_changed(repo, "proj")]

    @posix_only
    async def test_the_configured_editor_is_never_launched(self, repo, tmp_path):
        marker = tmp_path / "editor-ran"
        editor = write_file(
            str(tmp_path), "editor.sh", f"#!/bin/sh\ntouch '{marker}'\nexit 1\n".encode()
        )
        os.chmod(editor, 0o755)
        run_git_sync(repo, "config", "core.editor", editor)
        run_git_sync(repo, "config", "sequence.editor", editor)
        _stop_on_conflict(repo)
        await _start(repo, "main")
        _resolve(repo)

        resp = await _continue(repo)

        assert resp.outcome == "rebased"
        assert not marker.exists()

    async def test_a_second_conflict_stops_again_on_the_next_commit(self, repo):
        _stop_on_conflict(repo, second_conflict=True)
        first = await _start(repo, "main")
        _resolve(repo)

        resp = await _continue(repo)

        assert (first.rebase.stopped_on.subject, first.rebase.step, first.rebase.total) == (
            "other side", 1, 2)
        assert (resp.outcome, resp.message) == ("stopped", None)
        assert (resp.rebase.stopped_on.subject, resp.rebase.step, resp.rebase.total) == (
            "other again", 2, 2)
        assert resp.rebase.stopped_on.oid == _oid(repo, "other")
        assert (await _status(repo)).repo.rebase == resp.rebase

    async def test_subjects_worded_like_a_network_failure_still_stop_on_their_conflicts(
        self, repo
    ):
        # git quotes both subjects in its output, where the failure classifier
        # reads "unable to access" as `network`.
        _picks_onto_main(repo, [
            ("fix: unable to access the cache", "keep.txt", b"one\nTOPIC\nthree\n"),
            ("retry: unable to access it again", "keep.txt", b"one\nTOPIC AGAIN\nthree\n"),
        ])
        first = await _start(repo, "main")
        _resolve(repo)

        resp = await _continue(repo)

        assert (first.outcome, first.message, first.rebase.step) == ("stopped", None, 1)
        assert (resp.outcome, resp.message) == ("stopped", None)
        assert (resp.rebase.stopped_on.subject, resp.rebase.step, resp.rebase.total) == (
            "retry: unable to access it again", 2, 2)
        assert _subjects(repo)[0] == "fix: unable to access the cache"
        assert (await _status(repo)).repo.rebase == resp.rebase

    async def test_committing_the_resolution_into_an_untracked_file_stops_with_gits_message(
        self, repo
    ):
        _stop_before_a_pick_that_adds_b(repo)
        _resolve(repo)
        bus = FakeEventBus()

        resp = await _continue(repo, event_bus=bus)

        assert resp.outcome == "stopped"
        assert "would be overwritten by merge" in resp.message
        assert (resp.rebase.branch, resp.rebase.step, resp.rebase.total) == ("topic", 2, 2)
        assert _subjects(repo) == ["topic side", "main side", "initial"]
        assert _read(repo, "b.txt") == b"mine\n"
        assert (await _status(repo)).repo.state == "rebasing"
        assert bus.published == [_changed(repo, "proj")]

    async def test_a_held_index_lock_is_index_locked_and_leaves_the_stop_as_it_was(
        self, repo
    ):
        _stop_on_conflict(repo)
        await _start(repo, "main")
        _resolve(repo)
        head = _oid(repo)
        write_file(repo, ".git/index.lock", b"")
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _continue(repo, event_bus=bus)

        assert info.value.code == "index_locked"
        assert _oid(repo) == head
        os.remove(os.path.join(repo, ".git", "index.lock"))
        status = await _status(repo)
        assert (status.repo.state, status.repo.rebase.step) == ("rebasing", 1)
        assert bus.published == [_changed(repo, "proj")]

    async def test_a_rescheduled_pick_that_conflicts_on_retry_stops_on_it(self, repo):
        # The retried pick leaves HEAD and REBASE_HEAD where the blocked one
        # left them: only the conflicts it adds show that git moved on.
        _stop_before_a_conflicting_pick_that_adds_b(repo, "adds b and rewrites line two")
        blocked = await _continue(repo)
        os.remove(os.path.join(repo, "b.txt"))
        head = _oid(repo)
        bus = FakeEventBus()

        resp = await _continue(repo, event_bus=bus)

        assert "would be overwritten by merge" in blocked.message
        assert (resp.outcome, resp.message) == ("stopped", None)
        assert resp.rebase.stopped_on.subject == "adds b and rewrites line two"
        assert _oid(repo) == head
        status = await _status(repo)
        assert status.repo.rebase == resp.rebase
        assert [(e.group, e.path) for e in status.entries] == [
            ("conflicts", "keep.txt"), ("staged", "b.txt")]
        assert bus.published == [_changed(repo, "proj")]

    async def test_a_rescheduled_pick_still_blocked_is_dirty_worktree_whatever_its_subject(
        self, repo
    ):
        # git's advice for a rescheduled pick quotes its subject, where the
        # failure classifier reads "Permission to " as `auth_required`.
        subject = "Permission to read: unable to access b"
        _stop_before_a_conflicting_pick_that_adds_b(repo, subject)
        blocked = await _continue(repo)
        head = _oid(repo)
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _continue(repo, event_bus=bus)

        assert (blocked.outcome, blocked.rebase.stopped_on.subject) == ("stopped", subject)
        assert info.value.code == "dirty_worktree"
        assert "would be overwritten by merge" in info.value.message
        assert _oid(repo) == head
        assert _read(repo, "b.txt") == b"mine\n"
        status = await _status(repo)
        assert (status.repo.state, status.repo.rebase.stopped_on.subject) == ("rebasing", subject)
        assert bus.published == [_changed(repo, "proj")]

    async def test_unmerged_paths_are_refused_before_git_runs(self, repo, monkeypatch):
        _stop_on_conflict(repo)
        await _start(repo, "main")
        real_run_git = changes_rebase.run_git
        rebases: list[list[str]] = []

        async def record(args, **kwargs):
            if args[0] == "rebase":
                rebases.append(list(args))
            return await real_run_git(args, **kwargs)

        monkeypatch.setattr(changes_rebase, "run_git", record)
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _continue(repo, event_bus=bus)

        assert info.value.code == "unmerged"
        assert rebases == []
        assert bus.published == []
        assert (await _status(repo)).repo.state == "rebasing"

    async def test_unstaged_tracked_changes_are_dirty_worktree_not_git_error(self, repo):
        _stop_on_conflict(repo)
        await _start(repo, "main")
        _resolve(repo)
        write_file(repo, "README.md", b"edited\n")
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _continue(repo, event_bus=bus)

        assert (info.value.code, info.value.message) == (
            "dirty_worktree", "unstaged changes to tracked files")
        status = await _status(repo)
        assert status.repo.state == "rebasing"
        assert status.repo.rebase.stopped_on.subject == "other side"
        assert bus.published == [_changed(repo, "proj")]

    async def test_without_a_rebase_it_is_no_rebase_with_the_state(self, repo):
        make_conflict_branches(repo)
        run_git_sync(repo, "merge", "other", check=False)

        with pytest.raises(GitRefusedError) as info:
            await _continue(repo)

        assert (info.value.code, info.value.message) == ("no_rebase", "merging")


@requires_git
class TestSkip:
    async def test_skipping_the_only_pick_finishes_without_it(self, repo):
        _stop_on_conflict(repo)
        await _start(repo, "main")
        bus = FakeEventBus()

        resp = await _skip(repo, event_bus=bus)

        assert resp.model_dump() == {"outcome": "rebased", "rebase": None, "message": None}
        assert _oid(repo) == _oid(repo, "main")
        assert _git_out(repo, "symbolic-ref", "--short", "HEAD") == "other"
        assert bus.published == [_changed(repo, "proj")]

    async def test_skipping_into_the_next_conflict_stops_there_and_changes_the_etag(
        self, repo
    ):
        _stop_on_conflict(repo, second_conflict=True)
        await _start(repo, "main")
        before = await _status(repo)

        resp = await _skip(repo)
        after = await get_session_git_status(session_id="s1", cwd=repo, etag=before.etag)

        assert resp.outcome == "stopped"
        assert (resp.rebase.stopped_on.subject, resp.rebase.step) == ("other again", 2)
        assert after.unchanged is False
        assert after.repo.rebase == resp.rebase

    async def test_skip_is_allowed_while_conflicts_remain(self, repo):
        _stop_on_conflict(repo)
        await _start(repo, "main")

        resp = await _skip(repo)

        assert resp.outcome == "rebased"
        assert (await _status(repo)).entries == []

    async def test_skipping_into_an_untracked_file_in_the_next_pick_stops_with_gits_message(
        self, repo
    ):
        _stop_before_a_pick_that_adds_b(repo)
        head = _oid(repo)

        resp = await _skip(repo)

        assert resp.outcome == "stopped"
        assert "would be overwritten by merge" in resp.message
        assert (resp.rebase.step, resp.rebase.total) == (2, 2)
        assert _oid(repo) == head
        assert _read(repo, "b.txt") == b"mine\n"
        assert (await _status(repo)).repo.rebase == resp.rebase


    async def test_a_rescheduled_pick_still_blocked_is_rebased_whatever_its_subject(
        self, repo
    ):
        # git's advice for a rescheduled pick quotes its subject, where the
        # failure classifier reads "Permission to " as `auth_required`; the
        # drop must not be steered by it.
        subject = "Permission to read: unable to access b"
        _stop_before_a_conflicting_pick_that_adds_b(repo, subject)
        await _continue(repo)
        bus = FakeEventBus()

        resp = await _skip(repo, event_bus=bus)

        assert resp.model_dump() == {"outcome": "rebased", "rebase": None, "message": None}
        assert _subjects(repo) == ["topic side", "main side", "initial"]
        assert _read(repo, "b.txt") == b"mine\n"
        status = await _status(repo)
        assert (status.repo.state, [(e.group, e.path) for e in status.entries]) == (
            "clean", [("untracked", "b.txt")])
        assert bus.published == [_changed(repo, "proj")]

    async def test_skipping_a_rescheduled_pick_drops_it_and_applies_the_later_picks(
        self, repo
    ):
        _stop_before_a_conflicting_pick_that_adds_b(
            repo, "adds b and rewrites line two", later=(("adds c", "c.txt", b"c\n"),)
        )
        await _continue(repo)
        os.remove(os.path.join(repo, "b.txt"))
        bus = FakeEventBus()

        resp = await _skip(repo, event_bus=bus)

        assert resp.model_dump() == {"outcome": "rebased", "rebase": None, "message": None}
        assert _subjects(repo) == ["adds c", "topic side", "main side", "initial"]
        assert not os.path.exists(os.path.join(repo, "b.txt"))
        assert (_read(repo, "keep.txt"), _read(repo, "c.txt")) == (
            b"one\nRESOLVED\nthree\n", b"c\n")
        assert _git_out(repo, "symbolic-ref", "--short", "HEAD") == "topic"
        assert (await _status(repo)).repo.state == "clean"
        assert bus.published == [_changed(repo, "proj")]

    async def test_a_held_todo_lock_is_index_locked_before_git_runs(self, repo):
        _stop_before_a_conflicting_pick_that_adds_b(repo, "adds b and rewrites line two")
        blocked = await _continue(repo)
        todo = _read(repo, _TODO)
        head = _oid(repo)
        write_file(repo, _TODO + ".lock", b"")
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _skip(repo, event_bus=bus)

        assert (info.value.code, info.value.message) == ("index_locked", "git-rebase-todo.lock")
        assert (_read(repo, _TODO), _read(repo, _TODO + ".lock")) == (todo, b"")
        assert _oid(repo) == head
        assert (await _status(repo)).repo.rebase == blocked.rebase
        assert bus.published == []

    async def test_a_skip_git_refuses_puts_the_rescheduled_pick_back_for_a_retry(
        self, repo
    ):
        subject = "adds b and rewrites line two"
        _stop_before_a_conflicting_pick_that_adds_b(repo, subject)
        await _continue(repo)
        todo = _read(repo, _TODO)
        head = _oid(repo)
        write_file(repo, ".git/index.lock", b"")
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _skip(repo, event_bus=bus)

        assert info.value.code == "index_locked"
        assert _read(repo, _TODO) == todo
        assert _oid(repo) == head
        assert bus.published == [_changed(repo, "proj")]
        os.remove(os.path.join(repo, ".git", "index.lock"))
        os.remove(os.path.join(repo, "b.txt"))
        retried = await _continue(repo)
        assert (retried.outcome, retried.rebase.stopped_on.subject) == ("stopped", subject)
        status = await _status(repo)
        assert ("conflicts", "keep.txt") in [(e.group, e.path) for e in status.entries]

    async def test_a_timeout_after_git_moved_on_keeps_the_todo_git_wrote(
        self, repo, monkeypatch
    ):
        _stop_before_a_conflicting_pick_that_adds_b(
            repo,
            "adds b and rewrites line two",
            later=(("rewrites line two again", "keep.txt", b"one\nLATER\nthree\n"),),
        )
        await _continue(repo)
        os.remove(os.path.join(repo, "b.txt"))
        dropped = _oid(repo, "topic~1")
        real_run_git = changes_rebase.run_git

        async def run_then_time_out(args, **kwargs):
            result = await real_run_git(args, **kwargs)
            if args[0] == "rebase":
                return CmdResult(rc=-9, stdout=b"", stderr=b"", timed_out=True)
            return result

        monkeypatch.setattr(changes_rebase, "run_git", run_then_time_out)

        with pytest.raises(GitTimeoutError):
            await _skip(repo)

        assert dropped.encode() not in _read(repo, _TODO)
        rebase = (await _status(repo)).repo.rebase
        assert rebase.stopped_on.subject == "rewrites line two again"

    async def test_a_conflict_stop_whose_todo_repeats_the_stopped_commit_keeps_the_repeat(
        self, repo, monkeypatch
    ):
        # Only the missing `stopped-sha` tells a rescheduled pick from a todo
        # edited to pick the stopped commit again; pin the version so the
        # premise holds regardless of the git on PATH.
        _pin_git_version(monkeypatch, (2, 43, 0))
        _stop_on_conflict(repo)
        await _start(repo, "main")
        write_file(
            repo, _TODO, f"pick {_oid(repo, 'other')} other side\n".encode() + _read(repo, _TODO)
        )
        _resolve(repo)

        resp = await _skip(repo)

        assert (resp.outcome, resp.rebase.stopped_on.subject) == ("stopped", "other side")
        assert _oid(repo) == _oid(repo, "main")
        status = await _status(repo)
        assert [(e.group, e.path) for e in status.entries] == [("conflicts", "keep.txt")]

    async def test_before_git_2_43_a_rescheduled_pick_with_a_stopped_sha_is_dropped(
        self, repo, monkeypatch
    ):
        _stop_before_a_conflicting_pick_that_adds_b(
            repo, "adds b and rewrites line two", later=(("adds c", "c.txt", b"c\n"),)
        )
        await _continue(repo)
        _plant_stopped_sha(repo)
        os.remove(os.path.join(repo, "b.txt"))
        _pin_git_version(monkeypatch, (2, 42, 0))

        resp = await _skip(repo)

        assert resp.model_dump() == {"outcome": "rebased", "rebase": None, "message": None}
        assert _subjects(repo) == ["adds c", "topic side", "main side", "initial"]

    async def test_from_git_2_43_a_stopped_sha_keeps_the_first_pick_for_git_to_retry(
        self, repo, monkeypatch
    ):
        subject = "adds b and rewrites line two"
        _stop_before_a_conflicting_pick_that_adds_b(
            repo, subject, later=(("adds c", "c.txt", b"c\n"),)
        )
        await _continue(repo)
        _plant_stopped_sha(repo)
        os.remove(os.path.join(repo, "b.txt"))
        _pin_git_version(monkeypatch, (2, 43, 0))

        resp = await _skip(repo)

        assert (resp.outcome, resp.rebase.stopped_on.subject) == ("stopped", subject)
        status = await _status(repo)
        assert ("conflicts", "keep.txt") in [(e.group, e.path) for e in status.entries]

    @posix_only
    async def test_a_real_git_before_2_43_drops_the_rescheduled_pick(self, repo, monkeypatch):
        if not _clt_git_before_2_43():
            pytest.skip(f"no git older than 2.43 in {_CLT_GIT_DIR}")
        _stop_before_a_conflicting_pick_that_adds_b(
            repo, "adds b and rewrites line two", later=(("adds c", "c.txt", b"c\n"),)
        )
        monkeypatch.setenv("PATH", _CLT_GIT_DIR + os.pathsep + os.environ.get("PATH", ""))
        await _continue(repo)
        os.remove(os.path.join(repo, "b.txt"))
        stopped_sha = _read(repo, ".git/rebase-merge/stopped-sha").strip().decode()
        rebase_head = _oid(repo, "REBASE_HEAD")

        resp = await _skip(repo)

        assert await changes_rebase.git_version() < (2, 43, 0)
        assert stopped_sha == rebase_head
        assert resp.model_dump() == {"outcome": "rebased", "rebase": None, "message": None}
        assert _subjects(repo) == ["adds c", "topic side", "main side", "initial"]

    async def test_skipping_at_a_break_keeps_the_pick_after_it(self, repo, tmp_path):
        # A break writes no `stopped-sha` either: only REBASE_HEAD tells the
        # pick after it from a rescheduled one.
        _topic_behind_main(repo)
        write_file(repo, "c.txt", b"c\n")
        commit_all(repo, "adds c")
        todo = write_file(
            str(tmp_path),
            "todo",
            f"pick {_oid(repo, 'HEAD~1')}\nbreak\npick {_oid(repo)}\n".encode(),
        )
        run_git_sync(
            repo, "-c", f"sequence.editor=cp '{todo}'", "rebase", "-i", "--no-autostash", "main"
        )
        stopped = await _status(repo)

        resp = await _skip(repo)

        assert stopped.repo.state == "rebasing"
        assert resp.outcome == "rebased"
        assert _subjects(repo) == ["adds c", "topic work", "main work", "initial"]

    @pytest.mark.parametrize("command", ["fixup", "squash"])
    async def test_a_rescheduled_fixup_or_squash_is_retried_so_the_combined_message_stays_clean(
        self, repo, tmp_path, command
    ):
        # git has already counted it into the squash message; dropping it
        # would commit the combination's comment header as the message.
        _picks_onto_main(repo, [
            ("adds a", "a.txt", b"a\n"),
            ("edits a", "a.txt", b"a2\n"),
            ("adds b", "b.txt", b"b\n"),
            ("adds d", "d.txt", b"d\n"),
        ])
        a, a2, b, d = (_oid(repo, f"HEAD~{n}") for n in (3, 2, 1, 0))
        todo = write_file(
            str(tmp_path),
            "todo",
            f"break\npick {a}\nsquash {a2}\n{command} {b}\npick {d}\n".encode(),
        )
        run_git_sync(
            repo, "-c", f"sequence.editor=cp '{todo}'", "rebase", "-i", "--no-autostash", "main"
        )
        write_file(repo, "b.txt", b"mine\n")
        blocked = await _continue(repo)
        rescheduled = _read(repo, _TODO)
        os.remove(os.path.join(repo, "b.txt"))

        resp = await _skip(repo)

        assert "would be overwritten by merge" in blocked.message
        assert rescheduled.startswith(f"{command} {b}".encode())
        assert resp.model_dump() == {"outcome": "rebased", "rebase": None, "message": None}
        assert _subjects(repo) == ["adds d", "adds a", "main side", "initial"]
        message = _git_out(repo, "log", "-1", "--format=%B", "HEAD~1")
        assert "# This is a combination of" not in message
        assert _git_out(repo, "ls-tree", "--name-only", "HEAD~1").splitlines() == [
            "README.md", "a.txt", "b.txt", "keep.txt"]

    async def test_a_cancelled_skip_leaves_the_dropped_pick_out_of_the_todo_git_goes_on_to_read(
        self, repo, monkeypatch
    ):
        # git keeps running in its worker thread after the task is cancelled;
        # it is held there until the cancellation has fully unwound.
        _stop_before_a_conflicting_pick_that_adds_b(
            repo, "adds b and rewrites line two", later=(("adds c", "c.txt", b"c\n"),)
        )
        await _continue(repo)
        os.remove(os.path.join(repo, "b.txt"))
        real_run_git = changes_rebase.run_git
        held = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def rebase_once_released(args, kwargs):
            held.set()
            try:
                if release.wait(10):
                    asyncio.run(real_run_git(args, **kwargs))
            finally:
                finished.set()

        async def held_rebase(args, **kwargs):
            if args[0] == "rebase":
                return await asyncio.to_thread(rebase_once_released, args, kwargs)
            return await real_run_git(args, **kwargs)

        monkeypatch.setattr(changes_rebase, "run_git", held_rebase)
        skip = asyncio.create_task(_skip(repo))
        assert await asyncio.to_thread(held.wait, 10)

        skip.cancel()
        with pytest.raises(asyncio.CancelledError):
            await skip
        release.set()
        assert await asyncio.to_thread(finished.wait, 30)

        assert (await _status(repo)).repo.state == "clean"
        assert _subjects(repo) == ["adds c", "topic side", "main side", "initial"]
        assert _read(repo, "c.txt") == b"c\n"


class TestFirstTodoPick:
    @pytest.mark.parametrize(
        ("todo", "line", "commit"),
        [
            (b"# onto main\n\n   \npick 1a2b fix\npick 3c4d more\n", b"pick 1a2b fix\n", "1a2b"),
            (b"pick 1a2b one\rpick 3c4d two\npick 5e6f\n", b"pick 1a2b one\rpick 3c4d two\n",
             "1a2b"),
            (b"reword 1a2b last", b"reword 1a2b last", "1a2b"),
        ],
        ids=["comments-and-blank-lines", "cr-inside-a-line", "no-final-newline"],
    )
    def test_the_first_command_picking_a_commit_gives_its_line_and_commit(
        self, todo, line, commit
    ):
        start = todo.index(line)

        assert changes_rebase._first_todo_pick(todo) == (start, start + len(line), commit)

    @pytest.mark.parametrize(
        "todo",
        [
            b"exec make test\npick 1a2b\n",
            b"# comment\nbreak\npick 1a2b\n",
            b"label onto\npick 1a2b\n",
            b"pick\n",
            b"fixup -C 5e6f # amend\npick 1a2b\n",
            b"squash 5e6f\npick 1a2b\n",
            b"# only comments\n\n",
            b"",
        ],
        ids=["exec", "break", "label", "pick-without-commit", "fixup-is-left-for-git",
             "squash-is-left-for-git", "only-comments", "empty"],
    )
    def test_a_todo_not_headed_by_a_pick_gives_none(self, todo):
        assert changes_rebase._first_todo_pick(todo) is None


@requires_git
class TestAbort:
    async def test_abort_puts_the_branch_back_where_it_started(self, repo):
        _stop_on_conflict(repo)
        before = _oid(repo)
        await _start(repo, "main")
        _resolve(repo)
        bus = FakeEventBus()

        resp = await _abort(repo, event_bus=bus)

        assert resp.model_dump() == {"outcome": "aborted", "rebase": None, "message": None}
        assert _oid(repo) == before
        assert _git_out(repo, "symbolic-ref", "--short", "HEAD") == "other"
        assert _read(repo, "keep.txt") == b"one\nOTHER\nthree\n"
        status = await _status(repo)
        assert (status.repo.state, status.repo.rebase, status.entries) == ("clean", None, [])
        assert bus.published == [_changed(repo, "proj")]


    async def test_a_rebase_started_outside_the_app_can_be_aborted(self, repo):
        _stop_on_conflict(repo)
        before = _oid(repo)
        run_git_sync(repo, "rebase", "main", check=False)

        resp = await _abort(repo)

        assert resp.outcome == "aborted"
        assert _oid(repo) == before


@requires_git
class TestLinkedWorktreeSession:
    async def test_the_session_worktree_rebases_while_the_main_root_stays_clean(
        self, repo, tmp_path
    ):
        make_conflict_branches(repo)
        wt = str(tmp_path / "wt")
        run_git_sync(repo, "worktree", "add", "-q", wt, "other")
        before = _oid(wt)
        bus = FakeEventBus()

        stopped = await _start(wt, "main", event_bus=bus)
        root = await _status(repo)
        aborted = await _abort(wt, event_bus=bus)

        assert (stopped.outcome, stopped.rebase.branch, stopped.rebase.onto_ref) == (
            "stopped", "other", "main")
        assert (root.repo.state, root.repo.rebase, root.entries) == ("clean", None, [])
        assert aborted.outcome == "aborted"
        assert _oid(wt) == before
        assert bus.published == [_changed(wt, "proj", repo)] * 2


@requires_git
class TestRefusalsOutsideARepo:
    @pytest.mark.parametrize(
        "call",
        [
            lambda cwd: _start(cwd, "main"),
            lambda cwd: _continue(cwd),
            lambda cwd: _skip(cwd),
            lambda cwd: _abort(cwd),
        ],
        ids=["start", "continue", "skip", "abort"],
    )
    async def test_non_repo_is_not_a_repo(self, tmp_path, call):
        with pytest.raises(GitRefusedError) as info:
            await call(str(tmp_path))

        assert info.value.code == "not_a_repo"
