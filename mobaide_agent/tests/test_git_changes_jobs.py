"""Fetch / pull / push jobs of the session git Changes service: the
``--progress`` parser, the failure vocabulary, the throttled event stream a
job publishes, and the registry against real clones of a local bare
remote — one job per repository, cancel and timeout through a sleeping
``pre-push`` hook, and the ``job_running`` refusal a write meets meanwhile."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess

import pytest

from mobaide.agent.git import changes_jobs
from mobaide.agent.git.changes_jobs import (
    FINISHED_TTL,
    GitJob,
    ProgressTracker,
    classify_job_failure,
    job_registry,
    summarise_success,
)
from mobaide.agent.git.changes_models import (
    JOB_LINE_LIMIT,
    JOB_MESSAGE_LIMIT,
    GitCommitRequest,
)
from mobaide.agent.git.changes_mutations import (
    commit_session,
    get_session_commit_context,
    stage_session_paths,
)
from mobaide.agent.git.changes_rebase import (
    abort_session_rebase,
    continue_session_rebase,
    skip_session_rebase,
    start_session_rebase,
)
from mobaide.agent.git.changes_service import (
    GitNotFoundError,
    GitRefusedError,
    ResolvedRepo,
    _lock_for,
    get_session_git_status,
    http_error_for,
)
from mobaide.core.git_exec import CmdResult, GitCwdMissingError, reset_caches

from .git_repo_support import (
    GIT_BIN,
    clone_repo,
    commit_all,
    init_repo,
    norm_path,
    posix_only,
    repo,  # noqa: F401 - pytest fixture
    requires_git,
    run_git_sync,
    seed_remote,
    write_file,
)

SESSION_ID = "sess-1"


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

    def types(self) -> list[str]:
        return [event_type for event_type, _ in self.published]


@pytest.fixture
def remote(tmp_path) -> str:
    """A bare remote whose ``main`` carries the initial commit."""
    if GIT_BIN is None:
        pytest.skip("git binary not on PATH")
    return seed_remote(str(tmp_path))


@pytest.fixture
def local(remote, tmp_path) -> str:
    return clone_repo(remote, str(tmp_path / "local"))


@pytest.fixture
def other(remote, tmp_path) -> str:
    return clone_repo(remote, str(tmp_path / "other"))


def _changed(repo_dir: str, project_name: str) -> tuple[str, dict]:
    return (
        "git.changed",
        {
            "project_name": project_name,
            "toplevel": norm_path(repo_dir),
            "common_dir": norm_path(repo_dir) + "/.git",
        },
    )


def _fake_repo() -> ResolvedRepo:
    return ResolvedRepo(
        toplevel="/repo", git_dir="/repo/.git", common_dir="/repo/.git",
        is_linked_worktree=False,
    )


def _git_out(repo_dir: str, *args: str) -> str:
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = os.path.devnull
    env["GIT_CONFIG_SYSTEM"] = os.path.devnull
    return subprocess.run(
        [GIT_BIN, *args], cwd=repo_dir, check=True, env=env, capture_output=True
    ).stdout.decode().strip()


def _read(repo_dir: str, rel: str) -> bytes:
    with open(os.path.join(repo_dir, *rel.split("/")), "rb") as fh:
        return fh.read()


def _install_sleeping_pre_push(repo_dir: str) -> None:
    run_git_sync(repo_dir, "config", "core.hooksPath", ".git/hooks")
    hook = write_file(repo_dir, ".git/hooks/pre-push", b"#!/bin/sh\nsleep 30\n")
    os.chmod(hook, 0o755)


def _push_from(other: str, rel: str, message: str) -> None:
    write_file(other, rel, b"t\n")
    commit_all(other, message)
    run_git_sync(other, "push", "-q", "origin", "main")


def _diverge(local: str, other: str) -> None:
    """``other`` pushes a commit ``local`` lacks while ``local`` commits its own."""
    _push_from(other, "theirs.txt", "theirs")
    write_file(local, "ours.txt", b"o\n")
    commit_all(local, "ours")


async def _start(op, cwd, *, bus=None, project_name="proj", **kwargs):
    return await job_registry.start(
        op, cwd=cwd, session_id=SESSION_ID, project_name=project_name,
        event_bus=bus, **kwargs,
    )


async def _run(op, cwd, *, bus=None, project_name="proj", **kwargs):
    """Start ``op`` and wait for it; the finished snapshot."""
    first = await _start(op, cwd, bus=bus, project_name=project_name, **kwargs)
    await job_registry._jobs[first.job_id].task
    return job_registry.get(first.job_id)


async def _status(cwd: str):
    return await get_session_git_status(session_id=SESSION_ID, cwd=cwd, etag=None)


class TestProgressTracker:
    def test_percent_line_sets_phase_percent_and_line(self):
        tracker = ProgressTracker()

        changed = tracker.feed(b"Writing objects:  45% (14/32)\r")

        assert changed is True
        assert (tracker.phase, tracker.percent, tracker.line) == (
            "Writing objects", 45, "Writing objects:  45% (14/32)")
        assert list(tracker.tail) == []

    def test_carriage_return_rewrites_keep_the_latest_counter(self):
        tracker = ProgressTracker()

        tracker.feed(
            b"Writing objects:  33% (1/3)\rWriting objects:  66% (2/3)\r"
            b"Writing objects: 100% (3/3)\r"
        )

        assert (tracker.phase, tracker.percent) == ("Writing objects", 100)

    def test_remote_prefix_and_trailing_padding_are_stripped(self):
        tracker = ProgressTracker()

        tracker.feed(b"remote: Counting objects:  25% (1/4)        \r")

        assert (tracker.phase, tracker.percent, tracker.line) == (
            "Counting objects", 25, "Counting objects:  25% (1/4)")

    def test_count_line_has_no_percent_until_done(self):
        tracker = ProgressTracker()

        tracker.feed(b"Enumerating objects: 4\r")
        counting = (tracker.phase, tracker.percent)
        tracker.feed(b"Enumerating objects: 4, done.\n")

        assert counting == ("Enumerating objects", None)
        assert (tracker.phase, tracker.percent) == ("Enumerating objects", 100)

    def test_rebase_step_is_a_percentage_of_the_total(self):
        tracker = ProgressTracker()

        tracker.feed(b"Rebasing (1/4)\r")

        assert (tracker.phase, tracker.percent, tracker.line) == (
            "Rebasing", 25, "Rebasing (1/4)")

    def test_partial_chunks_join_before_parsing(self):
        tracker = ProgressTracker()

        first = tracker.feed(b"Writing objects:  3")
        second = tracker.feed(b"0% (3/10)\r")

        assert first is False
        assert second is True
        assert tracker.percent == 30

    def test_unterminated_progress_shows_but_is_not_filed_in_the_tail(self):
        tracker = ProgressTracker()

        tracker.feed(b"Compressing objects:  50% (1/2)")

        assert (tracker.phase, tracker.percent) == ("Compressing objects", 50)
        assert list(tracker.tail) == []

    def test_ordinary_lines_go_to_the_tail_and_become_the_line(self):
        tracker = ProgressTracker()

        tracker.feed(b"To /srv/app.git\n   e223296..822cef6  main -> main\n")

        assert list(tracker.tail) == [
            "To /srv/app.git", "   e223296..822cef6  main -> main"]
        assert tracker.line == "   e223296..822cef6  main -> main"
        assert (tracker.phase, tracker.percent) == (None, None)

    def test_crlf_is_one_separator_and_blank_lines_are_dropped(self):
        tracker = ProgressTracker()

        tracker.feed(b"first\r\n\nsecond\r\n")

        assert list(tracker.tail) == ["first", "second"]

    def test_feed_reports_no_change_for_a_repeated_frame(self):
        tracker = ProgressTracker()
        tracker.feed(b"Writing objects:  45% (14/32)\r")

        assert tracker.feed(b"Writing objects:  45% (14/32)\r") is False

    def test_tail_keeps_the_last_fifty_lines_clipped(self):
        tracker = ProgressTracker()
        lines = [f"line {i:03d} " + "x" * 600 for i in range(60)]

        tracker.feed("\n".join(lines).encode() + b"\n")

        assert len(tracker.tail) == 50
        assert tracker.tail[0].startswith("line 010")
        assert tracker.tail[-1].startswith("line 059")
        assert all(len(line) == JOB_MESSAGE_LIMIT for line in tracker.tail)
        assert len(tracker.line) == JOB_LINE_LIMIT

    def test_flush_promotes_the_trailing_partial_line(self):
        tracker = ProgressTracker()
        tracker.feed(b"fatal: Not possible to fast-forward, aborting.")
        before = tracker.line

        tracker.flush()

        assert before is None
        assert list(tracker.tail) == ["fatal: Not possible to fast-forward, aborting."]
        assert tracker.line == "fatal: Not possible to fast-forward, aborting."

    def test_an_unterminated_stream_keeps_a_bounded_pending_tail(self):
        tracker = ProgressTracker()

        for _ in range(64):
            tracker.feed(b"x" * 16384)
        pending = len(tracker._partial)
        tracker.feed(b"\rWriting objects:  50% (1/2)\r")

        assert pending == changes_jobs._PARTIAL_CAP
        assert (tracker.phase, tracker.percent) == ("Writing objects", 50)
        assert [len(line) for line in tracker.tail] == [JOB_MESSAGE_LIMIT]


class TestClassifyJobFailure:
    @pytest.mark.parametrize(
        ("err", "out", "code"),
        [
            (["fatal: could not read Username for 'https://github.com': "
              "terminal prompts disabled"], [], "auth_required"),
            (["ERROR: Permission to acme/app.git denied to bob.",
              "fatal: Could not read from remote repository."], [], "auth_required"),
            (["remote: Permission to acme/app.git denied to bob.",
              "fatal: unable to access 'https://github.com/acme/app.git/': "
              "The requested URL returned error: 403"], [], "auth_required"),
            (["fatal: unable to access 'https://git.example.com/app.git/': "
              "The requested URL returned error: 403"], [], "auth_required"),
            (["fatal: unable to access 'https://git.example.com/app.git/': "
              "The requested URL returned error: 401"], [], "auth_required"),
            (["fatal: unable to access 'https://x/': Could not resolve host: x"],
             [], "network"),
            (["ERROR: Repository not found.",
              "fatal: Could not read from remote repository."], [], "remote_not_found"),
            (["fatal: Not possible to fast-forward, aborting."], [], "not_fast_forward"),
            ([" ! [rejected]        main -> main (fetch first)",
              "error: failed to push some refs to 'x'"], [], "not_fast_forward"),
            ([" ! [rejected]        main -> main (non-fast-forward)",
              "error: failed to push some refs to 'x'"], [], "not_fast_forward"),
            ([" ! [rejected]        main -> main (stale info)",
              "error: failed to push some refs to 'x'"], [], "rejected"),
            ([" ! [remote rejected] main -> main (branch is currently checked out)",
              "error: failed to push some refs to 'x'"], [], "rejected"),
            (["remote: error: hook declined to update refs/heads/main",
              " ! [remote rejected] main -> main (hook declined)"], [], "hook_failed"),
            (["error: failed to push some refs to 'x'"], [], "hook_failed"),
            ([], ["Auto-merging keep.txt",
                  "CONFLICT (content): Merge conflict in keep.txt",
                  "Automatic merge failed; fix conflicts and then commit the result."],
             "conflicts"),
            (["error: could not apply e667040... main side"],
             ["CONFLICT (content): Merge conflict in keep.txt"], "conflicts"),
            (["error: Your local changes to the following files would be "
              "overwritten by merge:", "\tkeep.txt"], [], "dirty_worktree"),
            (["error: Pulling is not possible because you have unmerged files."],
             [], "unmerged"),
            (["fatal: You have not concluded your merge (MERGE_HEAD exists)."],
             [], "operation_in_progress"),
            (["fatal: It seems that there is already a rebase-merge directory, and",
              "I wonder if you are in the middle of another rebase.  If that is the"],
             [], "operation_in_progress"),
            (["fatal: It seems that there is already a rebase-apply directory, and",
              "I wonder if you are in the middle of another rebase.  If that is the"],
             [], "operation_in_progress"),
            (["error: The pre-rebase hook refused to rebase."], [], "hook_failed"),
            (["There is no tracking information for the current branch."],
             [], "no_upstream"),
            (["fatal: No configured push destination."], [], "no_remote"),
            (["fatal: You are not currently on a branch."], [], "detached_head"),
            (["error: src refspec main does not match any",
              "error: failed to push some refs to 'x'"], [], "no_head"),
            (["fatal: Unable to create '/repo/.git/index.lock': File exists."],
             [], "index_locked"),
            (["fatal: something else entirely"], [], "git_error"),
            ([], [], "git_error"),
        ],
    )
    def test_error_code_follows_the_ordered_rules(self, err, out, code):
        assert classify_job_failure("push", err_tail=err, out_tail=out)[0] == code

    def test_auth_beats_remote_not_found_when_both_match(self):
        code, _ = classify_job_failure(
            "fetch",
            err_tail=["git@github.com: Permission denied (publickey).",
                      "fatal: Could not read from remote repository."],
            out_tail=[],
        )

        assert code == "auth_required"

    def test_message_is_gits_own_error_lines_squashed(self):
        code, message = classify_job_failure(
            "push",
            err_tail=[
                "To /srv/app.git",
                " ! [rejected]        main -> main (fetch first)",
                "error: failed to push some refs to '/srv/app.git'",
                "hint: Updates were rejected because the remote contains work",
            ],
            out_tail=[],
        )

        assert code == "not_fast_forward"
        assert message == (
            "! [rejected] main -> main (fetch first)\n"
            "error: failed to push some refs to '/srv/app.git'"
        )

    def test_stdout_conflict_lines_lead_the_message(self):
        _, message = classify_job_failure(
            "pull",
            err_tail=["error: could not apply e667040... main side",
                      "hint: Resolve all conflicts manually"],
            out_tail=["Auto-merging keep.txt",
                      "CONFLICT (content): Merge conflict in keep.txt"],
        )

        assert message == (
            "CONFLICT (content): Merge conflict in keep.txt\n"
            "error: could not apply e667040... main side"
        )

    def test_message_falls_back_to_the_last_plain_line_skipping_hints(self):
        _, message = classify_job_failure(
            "pull", err_tail=["Something odd happened", "hint: try again"], out_tail=[]
        )

        assert message == "Something odd happened"

    def test_message_defaults_when_git_said_nothing(self):
        assert classify_job_failure("fetch", err_tail=[], out_tail=[]) == (
            "git_error", "git fetch failed")

    def test_rebase_is_classified_with_its_own_fallback_message(self):
        assert classify_job_failure("rebase", err_tail=[], out_tail=[]) == (
            "git_error", "git rebase failed")

    def test_message_is_capped(self):
        _, message = classify_job_failure(
            "push", err_tail=["fatal: " + "x" * 1000], out_tail=[]
        )

        assert len(message) == JOB_MESSAGE_LIMIT


class TestSummariseSuccess:
    def test_push_reports_the_ref_update(self):
        assert summarise_success(
            "push", err_tail=["To /srv/app.git", "   e223296..822cef6  main -> main"],
            out_tail=[],
        ) == "e223296..822cef6 main -> main"

    def test_fetch_reports_the_last_ref_update(self):
        assert summarise_success(
            "fetch",
            err_tail=["From /srv/app",
                      "   e223296..822cef6  main       -> origin/main",
                      " * [new branch]      feature    -> origin/feature"],
            out_tail=[],
        ) == "* [new branch] feature -> origin/feature"

    def test_push_with_nothing_to_send_echoes_git(self):
        assert summarise_success(
            "push", err_tail=["Everything up-to-date"], out_tail=[]
        ) == "Everything up-to-date"

    def test_silent_fetch_and_push_fall_back_per_op(self):
        assert summarise_success("fetch", err_tail=[], out_tail=[]) == "Already up to date"
        assert summarise_success("push", err_tail=[], out_tail=[]) == "Pushed"

    def test_pull_reports_the_last_stdout_line(self):
        assert summarise_success(
            "pull",
            err_tail=["From /srv/app", "   a..b  main -> origin/main"],
            out_tail=["Updating a..b", "Fast-forward"],
        ) == "Fast-forward"

    def test_pull_without_stdout_uses_stderr_minus_hints(self):
        assert summarise_success(
            "pull",
            err_tail=["Successfully rebased and updated refs/heads/main.", "hint: x"],
            out_tail=[],
        ) == "Successfully rebased and updated refs/heads/main."


class TestJobPublisher:
    @pytest.fixture(autouse=True)
    def _fast_throttle(self, monkeypatch):
        monkeypatch.setattr(changes_jobs, "MIN_PROGRESS_INTERVAL", 0.05)

    @staticmethod
    def _job(bus) -> GitJob:
        return GitJob(
            job_id="a" * 32, session_id=SESSION_ID, op="push", repo=_fake_repo(),
            project_name="proj", event_bus=bus,
        )

    def test_snapshot_carries_every_key_with_nulls_while_running(self):
        assert self._job(None).snapshot().model_dump(mode="json") == {
            "job_id": "a" * 32,
            "session_id": SESSION_ID,
            "op": "push",
            "status": "running",
            "phase": None,
            "percent": None,
            "line": None,
            "ok": None,
            "error_code": None,
            "message": None,
        }

    async def test_first_change_publishes_at_once_and_the_rest_wait_for_the_tick(self):
        bus = FakeEventBus()
        job = self._job(bus)

        job.feed(b"Writing objects:  10% (1/10)\r")
        job.feed(b"Writing objects:  20% (2/10)\r")
        job.feed(b"Writing objects:  30% (3/10)\r")
        immediate = [payload["percent"] for _, payload in bus.published]
        await asyncio.sleep(0.1)

        assert immediate == [10]
        assert [payload["percent"] for _, payload in bus.published] == [10, 30]
        assert bus.types() == ["git.job.progress", "git.job.progress"]
        assert bus.published[0][1]["status"] == "running"

    async def test_a_fast_producer_is_throttled_to_one_frame_per_interval(self):
        bus = FakeEventBus()
        job = self._job(bus)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 0.3
        feeds = 0

        while loop.time() < deadline:
            feeds += 1
            pct = feeds % 101
            job.feed(f"Writing objects: {pct:3d}% ({pct}/100)\r".encode())
            await asyncio.sleep(0.001)
        frames_during = len(bus.published)
        await asyncio.sleep(0.1)

        assert feeds > 20
        assert 2 <= frames_during <= 8
        assert len(bus.published) <= frames_during + 1
        assert bus.published[-1][1]["percent"] == feeds % 101

    async def test_a_quiet_stretch_lets_the_next_change_publish_at_once(self):
        bus = FakeEventBus()
        job = self._job(bus)
        job.feed(b"Writing objects:  10% (1/10)\r")
        await asyncio.sleep(0.1)

        job.feed(b"Writing objects:  50% (5/10)\r")

        assert [payload["percent"] for _, payload in bus.published] == [10, 50]

    async def test_unchanged_output_publishes_nothing(self):
        bus = FakeEventBus()
        job = self._job(bus)

        job.feed(b"Total 3 (delta 0), reused 0 (delta 0)\n")
        job.feed(b"Total 3 (delta 0), reused 0 (delta 0)\n")
        await asyncio.sleep(0.1)

        assert len(bus.published) == 1

    async def test_finish_publishes_done_once_then_git_changed_and_drops_the_pending_frame(self):
        bus = FakeEventBus()
        job = self._job(bus)
        job.feed(b"Writing objects:  10% (1/10)\r")
        job.feed(b"Writing objects:  90% (9/10)\r")
        job.status, job.ok, job.message = "done", True, "main -> main"

        job.finish()
        job.finish()
        job.touch()
        await asyncio.sleep(0.1)

        assert bus.types() == ["git.job.progress", "git.job.done", "git.changed"]
        done = bus.published[1][1]
        assert (done["status"], done["ok"], done["percent"], done["message"]) == (
            "done", True, 90, "main -> main")
        assert bus.published[2] == (
            "git.changed",
            {"project_name": "proj", "toplevel": "/repo", "common_dir": "/repo/.git"},
        )

    async def test_a_failing_bus_does_not_break_the_job(self):
        class BrokenBus:
            def publish(self, *_args) -> None:
                raise RuntimeError("bus down")

        job = self._job(BrokenBus())

        job.feed(b"Writing objects:  10% (1/10)\r")
        job.status = "done"
        job.finish()

        assert job.snapshot().percent == 10


@requires_git
class TestJobRegistry:
    async def test_start_answers_the_first_snapshot_and_publishes_it(self, local):
        bus = FakeEventBus()

        first = await _start("fetch", local, bus=bus)

        assert re.fullmatch(r"[0-9a-f]{32}", first.job_id)
        assert first.model_dump(mode="json") == {
            "job_id": first.job_id,
            "session_id": SESSION_ID,
            "op": "fetch",
            "status": "running",
            "phase": None,
            "percent": None,
            "line": None,
            "ok": None,
            "error_code": None,
            "message": None,
        }
        assert bus.published == [("git.job.progress", first.model_dump(mode="json"))]
        assert job_registry.active_job_id(norm_path(local)) == first.job_id

    async def test_a_finished_job_publishes_done_once_then_git_changed(self, local):
        bus = FakeEventBus()

        done = await _run("fetch", local, bus=bus)

        assert (done.status, done.ok, done.error_code) == ("done", True, None)
        assert bus.types()[0] == "git.job.progress"
        assert bus.types().count("git.job.done") == 1
        assert bus.types()[-2:] == ["git.job.done", "git.changed"]
        assert bus.published[-2][1] == done.model_dump(mode="json")
        assert bus.published[-1] == _changed(local, "proj")
        assert job_registry.active_job_id(norm_path(local)) is None

    async def test_session_without_a_project_publishes_an_empty_name(self, local):
        bus = FakeEventBus()

        await _run("fetch", local, bus=bus, project_name=None)

        assert bus.published[-1] == _changed(local, "")

    async def test_start_refuses_outside_a_repo_and_a_vanished_cwd(self, tmp_path):
        with pytest.raises(GitRefusedError) as info:
            await _start("fetch", str(tmp_path))
        with pytest.raises(GitCwdMissingError):
            await _start("fetch", str(tmp_path / "gone"))

        assert info.value.code == "not_a_repo"

    async def test_unknown_job_is_not_found_on_get_and_cancel(self):
        with pytest.raises(GitNotFoundError) as info:
            job_registry.get("deadbeef")
        with pytest.raises(GitNotFoundError):
            await job_registry.cancel("deadbeef")

        assert http_error_for(info.value) == (404, {"detail": {"code": "job_not_found"}})

    async def test_cancel_of_a_finished_job_is_a_no_op(self, local):
        done = await _run("fetch", local)

        again = await job_registry.cancel(done.job_id)

        assert again == done

    async def test_finished_jobs_outlive_the_ttl_only_until_the_next_start(self, local):
        done = await _run("fetch", local)
        assert job_registry.get(done.job_id) == done
        job_registry._jobs[done.job_id].finished_at -= FINISHED_TTL + 1

        fresh = await _run("fetch", local)

        with pytest.raises(GitNotFoundError):
            job_registry.get(done.job_id)
        assert job_registry.get(fresh.job_id).ok is True

    async def test_push_ahead_updates_the_remote_and_reports_the_ref_move(
        self, local, remote
    ):
        write_file(local, "a.txt", b"a\n")
        commit_all(local, "add a")

        done = await _run("push", local)

        assert (done.ok, done.error_code) == (True, None)
        assert done.message.endswith("main -> main")
        assert (done.phase, done.percent) == ("Writing objects", 100)
        assert _git_out(remote, "rev-parse", "main") == _git_out(local, "rev-parse", "HEAD")
        assert (await _status(local)).repo.upstream.ahead == 0

    async def test_publish_sets_the_upstream_of_a_new_branch(self, local, remote):
        run_git_sync(local, "checkout", "-q", "-b", "feature")

        done = await _run("push", local)

        assert (done.ok, done.error_code) == (True, None)
        assert done.message == "* [new branch] feature -> feature"
        assert _git_out(local, "rev-parse", "--abbrev-ref", "@{upstream}") == "origin/feature"
        assert _git_out(remote, "rev-parse", "--verify", "refs/heads/feature")

    async def test_publish_of_a_dash_named_branch_is_not_read_as_options(
        self, local, remote, monkeypatch
    ):
        real_run_git = changes_jobs.run_git
        calls: list[list[str]] = []

        async def record(args, **kwargs):
            if args[0] == "push":
                calls.append(list(args))
            return await real_run_git(args, **kwargs)

        monkeypatch.setattr(changes_jobs, "run_git", record)
        run_git_sync(local, "update-ref", "refs/heads/-foo", "HEAD")
        run_git_sync(local, "symbolic-ref", "HEAD", "refs/heads/-foo")

        done = await _run("push", local)

        assert calls == [["push", "--progress", "--set-upstream", "--", "origin", "-foo"]]
        assert (done.ok, done.error_code) == (True, None)
        assert done.message == "* [new branch] -foo -> -foo"
        assert _git_out(local, "rev-parse", "--abbrev-ref", "@{upstream}") == "origin/-foo"
        assert _git_out(remote, "rev-parse", "--verify", "refs/heads/-foo")

    @pytest.mark.parametrize(
        ("force", "lease"), [(False, []), (True, ["--force-with-lease"])]
    )
    async def test_push_recreates_a_pruned_upstream_instead_of_publishing_a_new_branch(
        self, local, other, remote, monkeypatch, force, lease
    ):
        real_run_git = changes_jobs.run_git
        calls: list[list[str]] = []

        async def record(args, **kwargs):
            if args[0] == "push":
                calls.append(list(args))
            return await real_run_git(args, **kwargs)

        monkeypatch.setattr(changes_jobs, "run_git", record)
        run_git_sync(local, "checkout", "-q", "-b", "topic")
        run_git_sync(local, "push", "-q", "origin", "HEAD:refs/heads/release")
        run_git_sync(local, "branch", "--set-upstream-to=origin/release", "topic")
        write_file(local, "a.txt", b"a\n")
        commit_all(local, "add a")
        run_git_sync(other, "push", "-q", "origin", "--delete", "release")
        run_git_sync(local, "fetch", "-q", "--prune")

        done = await _run("push", local, force=force)

        assert calls == [
            ["push", "--progress", *lease, "--", "origin", "refs/heads/topic:refs/heads/release"]
        ]
        assert (done.ok, done.error_code) == (True, None)
        assert done.message == "* [new branch] topic -> release"
        assert _git_out(
            remote, "for-each-ref", "--format=%(refname:short)", "refs/heads/"
        ) == "main\nrelease"
        assert _git_out(remote, "rev-parse", "release") == _git_out(local, "rev-parse", "HEAD")
        assert _git_out(local, "config", "branch.topic.merge") == "refs/heads/release"
        upstream = (await _status(local)).repo.upstream
        assert (upstream.ref, upstream.ahead, upstream.behind) == ("origin/release", 0, 0)

    async def test_push_recreates_a_pruned_upstream_when_a_tag_shadows_the_branch(
        self, local, other, remote, monkeypatch
    ):
        real_run_git = changes_jobs.run_git
        calls: list[list[str]] = []

        async def record(args, **kwargs):
            if args[0] == "push":
                calls.append(list(args))
            return await real_run_git(args, **kwargs)

        monkeypatch.setattr(changes_jobs, "run_git", record)
        run_git_sync(local, "checkout", "-q", "-b", "topic")
        run_git_sync(local, "push", "-q", "origin", "HEAD:refs/heads/release")
        run_git_sync(local, "branch", "--set-upstream-to=origin/release", "topic")
        write_file(local, "a.txt", b"a\n")
        commit_all(local, "add a")
        run_git_sync(other, "push", "-q", "origin", "--delete", "release")
        run_git_sync(local, "fetch", "-q", "--prune")
        run_git_sync(local, "tag", "topic")

        done = await _run("push", local)

        assert calls == [
            ["push", "--progress", "--", "origin", "refs/heads/topic:refs/heads/release"]
        ]
        assert (done.ok, done.error_code) == (True, None)
        assert done.message == "* [new branch] topic -> release"
        assert _git_out(
            remote, "for-each-ref", "--format=%(refname:short)", "refs/heads/"
        ) == "main\nrelease"
        assert _git_out(remote, "rev-parse", "release") == _git_out(local, "rev-parse", "HEAD")
        assert _git_out(local, "config", "branch.topic.merge") == "refs/heads/release"
        upstream = (await _status(local)).repo.upstream
        assert (upstream.ref, upstream.ahead, upstream.behind) == ("origin/release", 0, 0)

    async def test_publish_of_a_tag_shadowed_branch_names_the_branch_not_heads_slash(
        self, local, monkeypatch
    ):
        real_run_git = changes_jobs.run_git
        calls: list[list[str]] = []

        async def record(args, **kwargs):
            if args[0] != "push":
                return await real_run_git(args, **kwargs)
            calls.append(list(args))
            return CmdResult(rc=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(changes_jobs, "run_git", record)
        run_git_sync(local, "checkout", "-q", "-b", "topic")
        run_git_sync(local, "tag", "topic")

        done = await _run("push", local)

        assert calls == [["push", "--progress", "--set-upstream", "--", "origin", "topic"]]
        assert (done.ok, done.error_code) == (True, None)

    async def test_publish_ignores_an_upstream_on_a_remote_that_is_not_listed(
        self, local, remote, monkeypatch
    ):
        real_run_git = changes_jobs.run_git
        calls: list[list[str]] = []

        async def record(args, **kwargs):
            if args[0] == "push":
                calls.append(list(args))
            return await real_run_git(args, **kwargs)

        monkeypatch.setattr(changes_jobs, "run_git", record)
        run_git_sync(local, "checkout", "-q", "-b", "topic")
        run_git_sync(local, "config", "branch.topic.remote", "gone")
        run_git_sync(local, "config", "branch.topic.merge", "refs/heads/release")

        done = await _run("push", local)

        assert calls == [["push", "--progress", "--set-upstream", "--", "origin", "topic"]]
        assert (done.ok, done.error_code) == (True, None)
        assert _git_out(local, "rev-parse", "--abbrev-ref", "@{upstream}") == "origin/topic"
        assert _git_out(remote, "rev-parse", "--verify", "refs/heads/topic")

    async def test_push_behind_the_remote_is_not_fast_forward(self, local, other):
        _diverge(local, other)

        done = await _run("push", local)

        assert (done.ok, done.error_code) == (False, "not_fast_forward")
        assert done.message.startswith("! [rejected] main -> main (fetch first)")

    async def test_force_with_lease_after_an_amend(self, local, remote):
        write_file(local, "a.txt", b"a\n")
        commit_all(local, "add a")
        run_git_sync(local, "push", "-q", "origin", "main")
        run_git_sync(local, "commit", "-q", "--amend", "-m", "add a, reworded")

        plain = await _run("push", local)
        forced = await _run("push", local, force=True)

        assert plain.error_code == "not_fast_forward"
        assert (forced.ok, forced.error_code) == (True, None)
        assert forced.message.endswith("main -> main (forced update)")
        assert _git_out(remote, "log", "-1", "--format=%s") == "add a, reworded"

    async def test_stale_lease_is_rejected_and_the_remote_keeps_its_commit(
        self, local, other, remote
    ):
        _push_from(other, "theirs.txt", "theirs")
        run_git_sync(local, "commit", "-q", "--amend", "-m", "initial, reworded")

        done = await _run("push", local, force=True)

        assert (done.ok, done.error_code) == (False, "rejected")
        assert "(stale info)" in done.message
        assert _git_out(remote, "log", "-1", "--format=%s") == "theirs"

    async def test_fetch_updates_the_remote_tracking_branch(self, local, other):
        _push_from(other, "theirs.txt", "theirs")

        done = await _run("fetch", local)

        assert (done.ok, done.error_code) == (True, None)
        assert done.message.endswith("main -> origin/main")
        assert _git_out(local, "rev-parse", "origin/main") == _git_out(other, "rev-parse", "HEAD")
        assert (await _status(local)).repo.upstream.behind == 1

    async def test_fetch_prunes_a_deleted_remote_branch(self, local, other):
        run_git_sync(other, "push", "-q", "origin", "main:gone")
        run_git_sync(local, "fetch", "-q")
        run_git_sync(other, "push", "-q", "origin", "--delete", "gone")

        done = await _run("fetch", local)

        assert done.ok is True
        assert "origin/gone" not in _git_out(local, "branch", "-r")

    async def test_fetch_ignores_a_detached_head(self, local):
        run_git_sync(local, "checkout", "-q", "--detach")

        done = await _run("fetch", local)

        assert (done.ok, done.error_code) == (True, None)

    async def test_pull_fast_forwards(self, local, other):
        _push_from(other, "theirs.txt", "theirs")

        done = await _run("pull", local)

        assert (done.ok, done.error_code, done.message) == (True, None, "Fast-forward")
        assert _git_out(local, "rev-parse", "HEAD") == _git_out(other, "rev-parse", "HEAD")
        assert _read(local, "theirs.txt") == b"t\n"

    async def test_pull_of_diverged_branches_is_not_fast_forward_by_default(
        self, local, other
    ):
        _diverge(local, other)

        done = await _run("pull", local)

        assert (done.ok, done.error_code) == (False, "not_fast_forward")
        assert done.message == "fatal: Not possible to fast-forward, aborting."
        assert (await _status(local)).repo.state == "clean"
        assert _git_out(local, "log", "-1", "--format=%s") == "ours"

    async def test_pull_merge_of_diverged_branches_makes_a_merge_commit(self, local, other):
        _diverge(local, other)

        done = await _run("pull", local, pull_mode="merge")

        assert (done.ok, done.error_code) == (True, None)
        assert len(_git_out(local, "log", "-1", "--format=%P").split()) == 2
        assert (await _status(local)).repo.state == "clean"

    async def test_pull_rebase_of_diverged_branches_replays_ours_on_top(self, local, other):
        _diverge(local, other)

        done = await _run("pull", local, pull_mode="rebase")

        assert (done.ok, done.error_code) == (True, None)
        assert _git_out(local, "log", "-2", "--format=%s") == "ours\ntheirs"
        assert (await _status(local)).repo.upstream.ahead == 1

    @pytest.mark.parametrize(
        ("mode", "state"), [("merge", "merging"), ("rebase", "rebasing")]
    )
    async def test_conflicting_pull_stops_with_conflicts_mid_operation(
        self, local, other, mode, state
    ):
        write_file(other, "keep.txt", b"one\nOTHER\nthree\n")
        commit_all(other, "other side")
        run_git_sync(other, "push", "-q", "origin", "main")
        write_file(local, "keep.txt", b"one\nMAIN\nthree\n")
        commit_all(local, "main side")

        done = await _run("pull", local, pull_mode=mode)

        assert (done.ok, done.error_code) == (False, "conflicts")
        assert done.message.startswith("CONFLICT (content): Merge conflict in keep.txt")
        assert (await _status(local)).repo.state == state

    async def test_pull_over_local_changes_is_dirty_worktree(self, local, other):
        write_file(other, "keep.txt", b"one\nOTHER\nthree\n")
        commit_all(other, "other side")
        run_git_sync(other, "push", "-q", "origin", "main")
        write_file(local, "keep.txt", b"one\nDIRTY\nthree\n")

        done = await _run("pull", local)

        assert (done.ok, done.error_code) == (False, "dirty_worktree")
        assert _read(local, "keep.txt") == b"one\nDIRTY\nthree\n"

    async def test_pull_without_an_upstream_is_refused(self, local):
        run_git_sync(local, "checkout", "-q", "-b", "feature")

        done = await _run("pull", local)

        assert (done.ok, done.error_code) == (False, "no_upstream")

    async def test_detached_head_refuses_pull_and_push(self, local):
        run_git_sync(local, "checkout", "-q", "--detach")

        pulled = await _run("pull", local)
        pushed = await _run("push", local)

        assert (pulled.error_code, pushed.error_code) == ("detached_head", "detached_head")

    async def test_unborn_branch_is_no_head(self, tmp_path, remote):
        unborn = init_repo(str(tmp_path / "unborn"), initial_commit=False)
        run_git_sync(unborn, "remote", "add", "origin", remote)

        done = await _run("push", unborn)

        assert (done.ok, done.error_code) == (False, "no_head")

    async def test_repo_without_a_remote_is_no_remote(self, repo):
        done = await _run("fetch", repo)

        assert (done.ok, done.error_code) == (False, "no_remote")

    async def test_network_argv_env_and_budget_per_op(self, local, monkeypatch):
        real_run_git = changes_jobs.run_git
        calls: list[tuple[list[str], dict]] = []

        async def record(args, **kwargs):
            if args[0] in ("fetch", "pull", "push"):
                calls.append((list(args), kwargs))
            return await real_run_git(args, **kwargs)

        monkeypatch.setattr(changes_jobs, "run_git", record)
        run_git_sync(local, "checkout", "-q", "-b", "feature")

        await _run("fetch", local)
        await _run("pull", local)
        await _run("push", local, force=True)

        assert [argv for argv, _ in calls] == [
            ["fetch", "--progress", "--prune", "--", "origin"],
            ["push", "--progress", "--force-with-lease", "--set-upstream", "--", "origin", "feature"],
        ]
        for _, kwargs in calls:
            assert kwargs["cwd"] == norm_path(local)
            assert kwargs["timeout"] == changes_jobs.JOB_TIMEOUT
            assert kwargs["env_extra"] == {
                "GIT_ASKPASS": "", "SSH_ASKPASS_REQUIRE": "never", "GCM_INTERACTIVE": "never"}
            assert kwargs["handle"] is not None
            assert callable(kwargs["on_stderr"])

    @pytest.mark.parametrize(
        ("mode", "flags"),
        [("ff_only", ["--ff-only"]), ("rebase", ["--rebase"]),
         ("merge", ["--no-rebase", "--no-edit"])],
    )
    async def test_pull_mode_selects_the_flags(self, local, monkeypatch, mode, flags):
        real_run_git = changes_jobs.run_git
        calls: list[list[str]] = []

        async def record(args, **kwargs):
            if args[0] == "pull":
                calls.append(list(args))
            return await real_run_git(args, **kwargs)

        monkeypatch.setattr(changes_jobs, "run_git", record)

        await _run("pull", local, pull_mode=mode)

        assert calls == [["pull", "--progress", "--no-stat", *flags]]

    @posix_only
    async def test_one_job_per_toplevel_and_cancel_ends_it(self, local):
        _install_sleeping_pre_push(local)
        write_file(local, "a.txt", b"a\n")
        commit_all(local, "add a")
        bus = FakeEventBus()
        first = await _start("push", local, bus=bus)

        with pytest.raises(GitRefusedError) as info:
            await _start("fetch", local)
        held = _lock_for(norm_path(local)).locked()
        cancelled = await job_registry.cancel(first.job_id)

        assert (info.value.code, info.value.message) == ("job_running", first.job_id)
        assert http_error_for(info.value) == (
            409, {"detail": {"code": "job_running", "message": first.job_id}})
        assert held is True
        assert (cancelled.status, cancelled.ok, cancelled.error_code, cancelled.message) == (
            "done", False, "cancelled", "cancelled")
        assert _lock_for(norm_path(local)).locked() is False
        assert job_registry.active_job_id(norm_path(local)) is None
        assert bus.types()[-2:] == ["git.job.done", "git.changed"]
        assert (await _start("fetch", local)).op == "fetch"

    async def test_cancel_landing_after_git_exited_reports_the_real_outcome(
        self, local, remote, monkeypatch
    ):
        real_run_git = changes_jobs.run_git

        async def cancel_after_exit(args, **kwargs):
            result = await real_run_git(args, **kwargs)
            if args[0] == "push":
                kwargs["handle"].cancel()
            return result

        monkeypatch.setattr(changes_jobs, "run_git", cancel_after_exit)
        write_file(local, "a.txt", b"a\n")
        commit_all(local, "add a")
        bus = FakeEventBus()

        done = await _run("push", local, bus=bus)

        assert (done.status, done.ok, done.error_code) == ("done", True, None)
        assert done.message.endswith("main -> main")
        assert _git_out(remote, "rev-parse", "main") == _git_out(local, "rev-parse", "HEAD")
        assert job_registry.active_job_id(norm_path(local)) is None
        assert bus.types()[-2:] == ["git.job.done", "git.changed"]

    @pytest.mark.parametrize("rc", [-9, 1])
    async def test_cancel_that_killed_git_is_cancelled_whatever_the_exit_code(
        self, local, monkeypatch, rc
    ):
        real_run_git = changes_jobs.run_git

        async def killed(args, **kwargs):
            if args[0] != "push":
                return await real_run_git(args, **kwargs)
            kwargs["handle"].cancel()
            return CmdResult(rc=rc, stdout=b"", stderr=b"", timed_out=False)

        monkeypatch.setattr(changes_jobs, "run_git", killed)
        write_file(local, "a.txt", b"a\n")
        commit_all(local, "add a")

        done = await _run("push", local)

        assert (done.status, done.ok, done.error_code, done.message) == (
            "done", False, "cancelled", "cancelled")
        assert job_registry.active_job_id(norm_path(local)) is None

    async def test_cancel_while_waiting_for_the_repo_lock_never_spawns_git(
        self, local, monkeypatch
    ):
        real_run_git = changes_jobs.run_git
        spawned: list[list[str]] = []

        async def record(args, **kwargs):
            spawned.append(list(args))
            return await real_run_git(args, **kwargs)

        monkeypatch.setattr(changes_jobs, "run_git", record)
        lock = _lock_for(norm_path(local))
        await lock.acquire()
        first = await _start("fetch", local)

        cancelling = asyncio.create_task(job_registry.cancel(first.job_id))
        while not job_registry._jobs[first.job_id].handle.cancelled:
            await asyncio.sleep(0.01)
        lock.release()
        cancelled = await cancelling

        assert (cancelled.status, cancelled.error_code) == ("done", "cancelled")
        assert spawned == []

    @posix_only
    async def test_timeout_kills_the_push_and_reports_git_timeout(self, local, monkeypatch):
        monkeypatch.setattr(changes_jobs, "JOB_TIMEOUT", 0.5)
        _install_sleeping_pre_push(local)
        write_file(local, "a.txt", b"a\n")
        commit_all(local, "add a")

        done = await _run("push", local)

        assert (done.ok, done.error_code) == (False, "git_timeout")
        assert done.message.startswith("git push exceeded")
        assert job_registry.active_job_id(norm_path(local)) is None

    @pytest.mark.parametrize(("op", "step"), [("fetch", "remote"), ("push", "symbolic-ref")])
    async def test_preflight_timeout_reports_git_timeout_without_spawning_the_network_command(
        self, local, monkeypatch, caplog, op, step
    ):
        real_run_git = changes_jobs.run_git
        spawned: list[list[str]] = []

        async def stall(args, **kwargs):
            spawned.append(list(args))
            if args[0] == step:
                return CmdResult(rc=-1, stdout=b"", stderr=b"", timed_out=True)
            return await real_run_git(args, **kwargs)

        monkeypatch.setattr(changes_jobs, "run_git", stall)
        bus = FakeEventBus()

        with caplog.at_level(logging.ERROR, logger=changes_jobs.logger.name):
            done = await _run(op, local, bus=bus)

        assert (done.status, done.ok, done.error_code) == ("done", False, "git_timeout")
        assert done.message == f"git {op} preflight exceeded 5 s"
        assert [argv[0] for argv in spawned] == [step]
        assert bus.types().count("git.job.done") == 1
        assert bus.types()[-2:] == ["git.job.done", "git.changed"]
        assert bus.published[-2][1] == done.model_dump(mode="json")
        assert bus.published[-1] == _changed(local, "proj")
        assert job_registry.active_job_id(norm_path(local)) is None
        assert [r.getMessage() for r in caplog.records if r.name == changes_jobs.logger.name] == []

    @posix_only
    async def test_shutdown_cancels_running_jobs(self, local):
        _install_sleeping_pre_push(local)
        write_file(local, "a.txt", b"a\n")
        commit_all(local, "add a")
        first = await _start("push", local)

        await job_registry.shutdown()

        assert (job_registry.get(first.job_id).status,
                job_registry.get(first.job_id).error_code) == ("done", "cancelled")


@requires_git
@posix_only
class TestWritesDuringAJob:
    async def test_stage_and_commit_are_refused_while_a_job_owns_the_repo(self, local):
        _install_sleeping_pre_push(local)
        write_file(local, "a.txt", b"a\n")
        commit_all(local, "add a")
        write_file(local, "b.txt", b"b\n")
        first = await _start("push", local)

        with pytest.raises(GitRefusedError) as staged:
            await stage_session_paths(
                cwd=local, paths=["b.txt"], project_name=None, event_bus=None)
        with pytest.raises(GitRefusedError) as committed:
            await commit_session(
                cwd=local, request=GitCommitRequest(message="x"),
                project_name=None, event_bus=None)
        context = await get_session_commit_context(cwd=local)
        await job_registry.cancel(first.job_id)
        await stage_session_paths(cwd=local, paths=["b.txt"], project_name=None, event_bus=None)

        assert (staged.value.code, staged.value.message) == ("job_running", first.job_id)
        assert (committed.value.code, committed.value.message) == ("job_running", first.job_id)
        assert context.head.subject == "add a"
        assert [(e.group, e.path) for e in (await _status(local)).entries] == [("staged", "b.txt")]

    @pytest.mark.parametrize(
        "call",
        [
            lambda cwd, bus: start_session_rebase(
                cwd=cwd, onto="origin/main", project_name=None, event_bus=bus),
            lambda cwd, bus: continue_session_rebase(cwd=cwd, project_name=None, event_bus=bus),
            lambda cwd, bus: skip_session_rebase(cwd=cwd, project_name=None, event_bus=bus),
            lambda cwd, bus: abort_session_rebase(cwd=cwd, project_name=None, event_bus=bus),
        ],
        ids=["start", "continue", "skip", "abort"],
    )
    async def test_rebase_routes_are_refused_while_a_job_owns_the_repo(self, local, call):
        _install_sleeping_pre_push(local)
        write_file(local, "a.txt", b"a\n")
        commit_all(local, "add a")
        head = _git_out(local, "rev-parse", "HEAD")
        bus = FakeEventBus()
        first = await _start("push", local)

        with pytest.raises(GitRefusedError) as refused:
            await call(local, bus)
        await job_registry.cancel(first.job_id)

        assert (refused.value.code, refused.value.message) == ("job_running", first.job_id)
        assert _git_out(local, "rev-parse", "HEAD") == head
        assert (await _status(local)).repo.state == "clean"
        assert bus.published == []
