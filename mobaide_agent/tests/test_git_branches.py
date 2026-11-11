"""Remote-branch listing, branch creation and checkout of the project git
service against real temp repositories: the rows ``?remote=true`` adds, the
refusal vocabulary each guard answers with, and the ``git.changed`` echo every
successful mutation publishes."""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import types

import pytest

from mobaide.agent.git import service
from mobaide.agent.git.changes_jobs import job_registry
from mobaide.agent.git.changes_service import (
    GitBadRequestError,
    GitCommandError,
    GitNotFoundError,
    GitRefusedError,
    _lock_for,
)
from mobaide.agent.git.models import (
    GitBranchCreateRequest,
    GitBranchResponse,
    GitCheckoutRequest,
)
from mobaide.agent.git.service import (
    GitItemBusyError,
    _classify_ref_failure,
    _root_matcher,
    _split_remote_ref,
    checkout_ref,
    create_branch,
    list_git_items,
)
from mobaide.core.git_exec import CmdResult, reset_caches
from mobaide.linear.worktree import WorktreeManager

from .git_repo_support import (
    GIT_BIN,
    clone_repo,
    commit_all,
    make_conflict_branches,
    norm_path,
    requires_git,
    run_git_sync,
    seed_remote,
    write_file,
)

# git >= 2.45 prints "Z" instead of "+00:00" for UTC dates.
_ISO_STRICT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$")


@pytest.fixture(autouse=True)
def _fresh_caches():
    reset_caches()
    yield
    reset_caches()


@pytest.fixture
def local(tmp_path) -> str:
    """A clone of a seeded bare remote: ``main`` on the initial commit,
    tracking ``origin/main``, with ``origin/HEAD`` pointing at it."""
    if GIT_BIN is None:
        pytest.skip("git binary not on PATH")
    return clone_repo(seed_remote(str(tmp_path)), str(tmp_path / "local"))


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


def _manager() -> WorktreeManager:
    return WorktreeManager(worktree_base=".worktrees", branch_prefix="auto")


def _git_out(repo_dir: str, *args: str) -> str:
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = os.path.devnull
    env["GIT_CONFIG_SYSTEM"] = os.path.devnull
    return subprocess.run(
        [GIT_BIN, *args], cwd=repo_dir, check=True, env=env, capture_output=True
    ).stdout.decode().strip()


def _head(repo_dir: str) -> str:
    return _git_out(repo_dir, "symbolic-ref", "--short", "HEAD")


def _rev(repo_dir: str, ref: str) -> str:
    return _git_out(repo_dir, "rev-parse", ref)


def _upstream(repo_dir: str, branch: str) -> str:
    return _git_out(repo_dir, "rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}")


def _local_branches(repo_dir: str) -> list[str]:
    return _git_out(repo_dir, "branch", "--list", "--format=%(refname:short)").split()


def _read(repo_dir: str, rel: str) -> bytes:
    with open(os.path.join(repo_dir, *rel.split("/")), "rb") as fh:
        return fh.read()


def _publish_remote_only(local: str, name: str, message: str) -> None:
    """``origin/<name>`` with its own commit and no local branch: pushed
    from ``local`` and then forgotten there."""
    run_git_sync(local, "switch", "-q", "-c", name)
    write_file(local, name.replace("/", "_") + ".txt", b"x\n")
    commit_all(local, message)
    run_git_sync(local, "push", "-q", "origin", name)
    run_git_sync(local, "switch", "-q", "main")
    run_git_sync(local, "branch", "-q", "-D", name)


def _branch_shadowed_by_tag(local: str, name: str) -> None:
    """Branch ``name`` one commit (``"<name> tip"``) past a tag of the same
    name on ``main``, so git reads the bare ``name`` as ambiguous."""
    run_git_sync(local, "tag", name)
    run_git_sync(local, "switch", "-q", "-c", name)
    write_file(local, f"{name}.txt", b"r\n")
    commit_all(local, f"{name} tip")
    run_git_sync(local, "switch", "-q", "main")


def _fetch_as_ghost(local: str) -> None:
    """``ghost/*`` remote-tracking refs of every ``origin`` branch with no
    ``ghost`` remote configured: what an ad-hoc ``git fetch <url>`` or an
    interrupted ``git remote rename`` leaves behind."""
    run_git_sync(
        local,
        "fetch",
        "-q",
        _git_out(local, "remote", "get-url", "origin"),
        "refs/heads/*:refs/remotes/ghost/*",
    )


def _add_linked_worktree(local: str, branch: str) -> str:
    """``<local>/wt/<branch>`` checked out on ``branch`` (created here)."""
    run_git_sync(local, "branch", branch)
    path = os.path.join(local, "wt", branch)
    run_git_sync(local, "worktree", "add", "-q", path, branch)
    return path


def _fake_running_job(monkeypatch, local: str, job_id: str = "job-1") -> asyncio.Lock:
    """What a fetch/pull/push job looks like from the service: registered as
    the active job of ``local``; the returned toplevel lock is the one the
    job would hold for its whole run, for the test to acquire."""
    monkeypatch.setattr(
        job_registry,
        "active_job_id",
        lambda toplevel: job_id if toplevel == norm_path(local) else None,
    )
    return _lock_for(norm_path(local))


def _sessions(*cwds: str | None) -> types.SimpleNamespace:
    sessions = [
        types.SimpleNamespace(session_id=f"s-{index}", cwd=cwd)
        for index, cwd in enumerate(cwds, start=1)
    ]
    return types.SimpleNamespace(list_sessions=lambda: sessions)


async def _create(
    root: str,
    name: str,
    *,
    start_point: str | None = None,
    checkout: bool = False,
    force: bool = False,
    bus: FakeEventBus | None = None,
    session_manager=None,
    autoscan_manager=None,
) -> GitBranchResponse:
    return await create_branch(
        GitBranchCreateRequest(
            name=name, start_point=start_point, checkout=checkout, force=force,
        ),
        project_name="proj",
        project_root=root,
        manager=_manager(),
        session_manager=session_manager,
        autoscan_manager=autoscan_manager,
        event_bus=bus,
        actor=None,
    )


async def _checkout(
    root: str,
    ref: str,
    *,
    force: bool = False,
    bus: FakeEventBus | None = None,
    session_manager=None,
    autoscan_manager=None,
) -> GitBranchResponse:
    return await checkout_ref(
        GitCheckoutRequest(ref=ref, force=force),
        project_name="proj",
        project_root=root,
        manager=_manager(),
        session_manager=session_manager,
        autoscan_manager=autoscan_manager,
        event_bus=bus,
        actor=None,
    )


@requires_git
class TestListRemoteBranches:
    async def test_remote_rows_follow_the_local_ones_and_skip_the_head_symref(self, local):
        _publish_remote_only(local, "feature/x", "feature work")

        items = await list_git_items(local, _manager(), remote=True)

        assert [i.kind for i in items] == [
            "branch_with_worktree", "remote_branch", "remote_branch"]
        assert [
            (i.branch, i.remote, i.subject, i.is_current)
            for i in items if i.kind == "remote_branch"
        ] == [
            ("origin/feature/x", "origin", "feature work", False),
            ("origin/main", "origin", "initial", False),
        ]
        assert all(_ISO_STRICT.match(i.committed_at or "") for i in items)


    async def test_a_branch_shadowed_by_a_tag_is_listed_by_its_exact_name(self, local):
        _branch_shadowed_by_tag(local, "release")
        run_git_sync(local, "tag", "topic")
        path = _add_linked_worktree(local, "topic")
        assert _rev(local, "refs/tags/release") != _rev(local, "refs/heads/release")

        items = await list_git_items(local, _manager(), remote=True)

        assert [
            (i.kind, i.branch, i.subject) for i in items if i.kind != "remote_branch"
        ] == [
            ("branch_with_worktree", "main", "initial"),
            ("branch_only", "release", "release tip"),
            ("branch_with_worktree", "topic", "initial"),
        ]
        assert os.path.realpath(items[2].worktree_path) == os.path.realpath(path)

    async def test_a_remote_named_with_a_slash_keeps_its_whole_name(self, local):
        run_git_sync(local, "remote", "add", "up/stream", _git_out(local, "remote", "get-url", "origin"))
        run_git_sync(local, "fetch", "-q", "up/stream")

        items = await list_git_items(local, _manager(), remote=True)

        assert {(i.branch, i.remote) for i in items if i.kind == "remote_branch"} == {
            ("origin/main", "origin"),
            ("up/stream/main", "up/stream"),
        }

    async def test_a_ref_under_no_configured_remote_is_not_offered(self, local):
        _fetch_as_ghost(local)
        assert _rev(local, "refs/remotes/ghost/main") == _rev(local, "origin/main")

        items = await list_git_items(local, _manager(), remote=True)

        assert [(i.branch, i.remote) for i in items if i.kind == "remote_branch"] == [
            ("origin/main", "origin")]

    async def test_a_capped_listing_keeps_the_whole_records_and_drops_the_cut_one(
        self, local, monkeypatch
    ):
        _publish_remote_only(local, "feature/x", "feature work")
        real_run_git = service.run_git

        async def cut_listing(args, **kwargs):
            result = await real_run_git(args, **kwargs)
            if args[0] != "for-each-ref":
                return result
            return CmdResult(rc=-9, stdout=result.stdout[:-6], stderr=b"", truncated=True)

        monkeypatch.setattr(service, "run_git", cut_listing)

        items = await list_git_items(local, _manager(), remote=True)

        assert [(i.kind, i.branch, i.remote, i.subject) for i in items] == [
            ("branch_with_worktree", "main", None, "initial"),
            ("remote_branch", "origin/feature/x", "origin", "feature work"),
        ]
        assert all(_ISO_STRICT.match(i.committed_at or "") for i in items)

    async def test_a_listing_cut_exactly_on_a_record_boundary_keeps_every_record(
        self, local, monkeypatch
    ):
        _publish_remote_only(local, "feature/x", "feature work")
        real_run_git = service.run_git

        async def cut_on_boundary(args, **kwargs):
            result = await real_run_git(args, **kwargs)
            if args[0] != "for-each-ref":
                return result
            assert result.stdout.endswith(b"\n")
            return CmdResult(rc=-9, stdout=result.stdout, stderr=b"", truncated=True)

        monkeypatch.setattr(service, "run_git", cut_on_boundary)

        items = await list_git_items(local, _manager(), remote=True)

        assert [(i.kind, i.branch, i.remote, i.subject) for i in items] == [
            ("branch_with_worktree", "main", None, "initial"),
            ("remote_branch", "origin/feature/x", "origin", "feature work"),
            ("remote_branch", "origin/main", "origin", "initial"),
        ]
        assert all(_ISO_STRICT.match(i.committed_at or "") for i in items)

    async def test_tip_lookup_failure_degrades_locally_but_surfaces_with_remote(
        self, local, monkeypatch
    ):
        async def boom(project_root):
            raise GitCommandError("for-each-ref exploded")

        monkeypatch.setattr(service, "_ref_tips", boom)

        items = await list_git_items(local, _manager())
        assert [(i.branch, i.subject, i.committed_at) for i in items] == [("main", None, None)]

        with pytest.raises(GitCommandError):
            await list_git_items(local, _manager(), remote=True)

    async def test_a_failed_listing_that_was_not_cut_short_still_fails(
        self, local, monkeypatch
    ):
        real_run_git = service.run_git

        async def failed_listing(args, **kwargs):
            if args[0] != "for-each-ref":
                return await real_run_git(args, **kwargs)
            return CmdResult(rc=128, stdout=b"", stderr=b"fatal: x\n", truncated=False)

        monkeypatch.setattr(service, "run_git", failed_listing)

        with pytest.raises(GitCommandError, match=r"^fatal: x$"):
            await list_git_items(local, _manager(), remote=True)

        items = await list_git_items(local, _manager())
        assert [(i.branch, i.subject, i.committed_at) for i in items] == [("main", None, None)]


class TestSplitRemoteRef:
    def test_the_longest_configured_remote_wins(self):
        assert _split_remote_ref("up/stream/main", ["up", "up/stream"]) == ("up/stream", "main")
        assert _split_remote_ref("origin/feature/x", ["origin"]) == ("origin", "feature/x")


@requires_git
class TestCreateBranch:
    async def test_creates_at_head_without_moving_it_and_publishes_git_changed(self, local):
        bus = FakeEventBus()

        resp = await _create(local, "feature/a", bus=bus)

        assert resp == GitBranchResponse(branch="feature/a", checked_out=False, created=True)
        assert _rev(local, "feature/a") == _rev(local, "main")
        assert _head(local) == "main"
        assert bus.published == [_changed(local, "proj")]


    async def test_checkout_switches_the_root_and_tracks_a_remote_start_point(self, local):
        _publish_remote_only(local, "feature/x", "feature work")
        bus = FakeEventBus()

        resp = await _create(
            local, "feature/x", start_point="origin/feature/x", checkout=True, bus=bus
        )

        assert resp == GitBranchResponse(branch="feature/x", checked_out=True, created=True)
        assert _head(local) == "feature/x"
        assert _upstream(local, "feature/x") == "origin/feature/x"
        assert bus.published == [_changed(local, "proj")]

    @pytest.mark.parametrize("checkout", [False, True])
    async def test_a_listed_branch_shadowed_by_a_tag_starts_from_the_branch(
        self, local, checkout
    ):
        _branch_shadowed_by_tag(local, "release")
        items = await list_git_items(local, _manager(), remote=True)
        (row,) = [i for i in items if i.kind == "branch_only"]

        resp = await _create(local, "hotfix", start_point=row.branch, checkout=checkout)

        assert resp == GitBranchResponse(branch="hotfix", checked_out=checkout, created=True)
        assert (
            _rev(local, "hotfix")
            == _rev(local, "refs/heads/release")
            != _rev(local, "refs/tags/release")
        )
        assert _head(local) == ("hotfix" if checkout else "main")

    async def test_a_listed_remote_branch_shadowed_by_a_tag_starts_from_and_tracks_it(
        self, local
    ):
        _publish_remote_only(local, "feature/x", "feature work")
        run_git_sync(local, "tag", "origin/feature/x")
        items = await list_git_items(local, _manager(), remote=True)
        (row,) = [i for i in items if i.subject == "feature work"]

        resp = await _create(local, "mine", start_point=row.branch)

        assert resp == GitBranchResponse(branch="mine", checked_out=False, created=True)
        assert (
            _rev(local, "mine")
            == _rev(local, "refs/remotes/origin/feature/x")
            != _rev(local, "refs/tags/origin/feature/x")
        )
        assert (
            _git_out(local, "rev-parse", "--symbolic-full-name", "mine@{upstream}")
            == "refs/remotes/origin/feature/x"
        )

    async def test_an_existing_name_is_refused_before_anything_changes(self, local):
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _create(local, "main", bus=bus)

        assert (info.value.code, info.value.message) == ("exists", "main")
        assert bus.published == []

    @pytest.mark.parametrize(
        "name",
        ["", "-x", "HEAD", "@", "@{-1}", "a b", "x..y", "a.lock", "a\0b", "x" * 256],
    )
    async def test_a_name_the_agent_or_git_rejects_is_bad_branch_name(self, local, name):
        with pytest.raises(GitBadRequestError) as info:
            await _create(local, name)

        assert info.value.code == "bad_branch_name"
        assert _local_branches(local) == ["main"]

    @pytest.mark.parametrize("start", ["nope", "-x", "--output=x"])
    async def test_an_unknown_or_option_shaped_start_point_is_refused(self, local, start):
        with pytest.raises(GitRefusedError) as info:
            await _create(local, "feature/a", start_point=start)

        assert (info.value.code, info.value.message) == ("unknown_start_point", start)
        assert _local_branches(local) == ["main"]

    async def test_a_session_in_the_root_blocks_a_checkout_but_not_a_plain_create(self, local):
        busy = _sessions(local)

        with pytest.raises(GitItemBusyError) as info:
            await _create(local, "feature/a", checkout=True, session_manager=busy)
        assert [(h.kind, h.session_id) for h in info.value.busy_by] == [("session", "s-1")]
        assert _local_branches(local) == ["main"]

        resp = await _create(local, "feature/a", session_manager=busy)

        assert resp.checked_out is False
        assert _local_branches(local) == ["feature/a", "main"]
        assert _head(local) == "main"

    async def test_force_creates_and_checks_out_under_a_session_in_the_root(self, local):
        resp = await _create(
            local, "feature/a", checkout=True, force=True, session_manager=_sessions(local),
        )

        assert resp == GitBranchResponse(branch="feature/a", checked_out=True, created=True)
        assert _head(local) == "feature/a"

    async def test_a_running_job_refuses_a_plain_create_before_queuing_on_its_lock(
        self, local, monkeypatch
    ):
        lock = _fake_running_job(monkeypatch, local)
        await lock.acquire()
        try:
            with pytest.raises(GitRefusedError) as info:
                await asyncio.wait_for(_create(local, "feature/a"), 10.0)
        finally:
            lock.release()

        assert (info.value.code, info.value.message) == ("job_running", "job-1")
        assert _local_branches(local) == ["main"]


@requires_git
class TestCheckoutRef:
    async def test_switches_to_a_local_branch_and_publishes_git_changed(self, local):
        run_git_sync(local, "branch", "topic")
        bus = FakeEventBus()

        resp = await _checkout(local, "topic", bus=bus)

        assert resp == GitBranchResponse(branch="topic", checked_out=True, created=False)
        assert _head(local) == "topic"
        assert bus.published == [_changed(local, "proj")]

    async def test_a_remote_ref_creates_a_tracking_branch_of_the_same_name(self, local):
        _publish_remote_only(local, "feature/x", "feature work")

        resp = await _checkout(local, "origin/feature/x")

        assert resp == GitBranchResponse(branch="feature/x", checked_out=True, created=True)
        assert _head(local) == "feature/x"
        assert _upstream(local, "feature/x") == "origin/feature/x"
        assert _rev(local, "feature/x") == _rev(local, "origin/feature/x")

    async def test_a_remote_ref_prefers_the_existing_local_branch_of_that_name(self, local):
        _publish_remote_only(local, "feature/x", "feature work")
        run_git_sync(local, "branch", "feature/x", "main")

        resp = await _checkout(local, "origin/feature/x")

        assert resp == GitBranchResponse(branch="feature/x", checked_out=True, created=False)
        assert _head(local) == "feature/x"
        assert _rev(local, "HEAD") == _rev(local, "main") != _rev(local, "origin/feature/x")

    @pytest.mark.parametrize("ref", ["nope", "origin/nope"])
    async def test_a_ref_that_is_neither_local_nor_remote_tracking_is_not_found(self, local, ref):
        with pytest.raises(GitNotFoundError) as info:
            await _checkout(local, ref)

        assert info.value.code == "ref_not_found"
        assert _head(local) == "main"


    async def test_a_ghost_ref_never_stands_in_for_the_local_branch_of_its_short_name(
        self, local
    ):
        _fetch_as_ghost(local)
        run_git_sync(local, "switch", "-q", "-c", "topic")

        with pytest.raises(GitNotFoundError) as info:
            await _checkout(local, "ghost/main")

        assert info.value.code == "ref_not_found"
        assert _head(local) == "topic"


    @pytest.mark.parametrize("ref", ["", "-x"])
    async def test_an_empty_or_option_shaped_ref_is_bad_ref(self, local, ref):
        with pytest.raises(GitBadRequestError) as info:
            await _checkout(local, ref)

        assert info.value.code == "bad_ref"
        assert _head(local) == "main"

    @pytest.mark.parametrize("where", ["", "sub"])
    async def test_a_session_at_the_root_or_below_blocks_the_switch(self, local, where):
        _add_linked_worktree(local, "topic")
        run_git_sync(local, "branch", "target")
        os.makedirs(os.path.join(local, "sub"), exist_ok=True)
        bus = FakeEventBus()

        with pytest.raises(GitItemBusyError) as info:
            await _checkout(
                local, "target", bus=bus,
                session_manager=_sessions(os.path.join(local, where)),
            )

        assert [(h.kind, h.session_id) for h in info.value.busy_by] == [("session", "s-1")]
        assert _head(local) == "main"
        assert bus.published == []

    @pytest.mark.parametrize("where", ["wt/topic", "wt/topic/deeper", "-other"])
    async def test_a_session_in_a_linked_worktree_or_a_sibling_does_not_block(self, local, where):
        _add_linked_worktree(local, "topic")
        run_git_sync(local, "branch", "target")
        cwd = local + where if where.startswith("-") else os.path.join(local, where)
        os.makedirs(cwd, exist_ok=True)

        resp = await _checkout(local, "target", session_manager=_sessions(cwd, None))

        assert resp.checked_out is True
        assert _head(local) == "target"

    async def test_a_project_registered_at_a_nested_linked_worktree_guards_its_own_root(
        self, local
    ):
        feat = _add_linked_worktree(local, "feat")
        run_git_sync(local, "branch", "target")

        with pytest.raises(GitItemBusyError) as info:
            await _checkout(feat, "target", session_manager=_sessions(feat))
        assert [(h.kind, h.session_id) for h in info.value.busy_by] == [("session", "s-1")]
        assert _head(feat) == "feat"

        resp = await _checkout(feat, "target", session_manager=_sessions(local))

        assert resp == GitBranchResponse(branch="target", checked_out=True, created=False)
        assert _head(feat) == "target"
        assert _head(local) == "main"

    async def test_force_checks_out_under_a_session_or_auto_scan_item_in_the_root(
        self, local
    ):
        run_git_sync(local, "branch", "target")
        bus = FakeEventBus()

        async def list_all_workitems():
            return {"proj": [types.SimpleNamespace(worktree_path=local)]}

        resp = await _checkout(
            local, "target", force=True, bus=bus,
            session_manager=_sessions(local),
            autoscan_manager=types.SimpleNamespace(list_all_workitems=list_all_workitems),
        )

        assert resp == GitBranchResponse(branch="target", checked_out=True, created=False)
        assert _head(local) == "target"
        assert len(bus.published) == 1

    async def test_force_does_not_skip_the_operation_in_progress_refusal(
        self, local, monkeypatch
    ):
        run_git_sync(local, "branch", "target")

        async def merging(_toplevel):
            return "merging"

        monkeypatch.setattr(service, "repo_state", merging)

        with pytest.raises(GitRefusedError) as info:
            await _checkout(local, "target", force=True, session_manager=_sessions(local))

        assert info.value.code == "operation_in_progress"
        assert _head(local) == "main"

    async def test_an_auto_scan_item_in_the_root_blocks_too(self, local):
        run_git_sync(local, "branch", "target")

        async def list_all_workitems():
            return {"proj": [types.SimpleNamespace(worktree_path=local)]}

        autoscan = types.SimpleNamespace(list_all_workitems=list_all_workitems)

        with pytest.raises(GitItemBusyError) as info:
            await _checkout(local, "target", autoscan_manager=autoscan)

        assert [(h.kind, h.project_name) for h in info.value.busy_by] == [("auto_scan", "proj")]
        assert _head(local) == "main"


    async def test_a_running_job_is_refused_before_queuing_on_the_lock_it_holds(
        self, local, monkeypatch
    ):
        run_git_sync(local, "branch", "target")
        lock = _fake_running_job(monkeypatch, local)
        await lock.acquire()
        try:
            with pytest.raises(GitRefusedError) as info:
                await asyncio.wait_for(_checkout(local, "target"), 10.0)
        finally:
            lock.release()

        assert (info.value.code, info.value.message) == ("job_running", "job-1")
        assert _head(local) == "main"

    async def test_unrelated_local_changes_travel_with_the_switch(self, local):
        run_git_sync(local, "branch", "target")
        write_file(local, "README.md", b"edited\n")

        resp = await _checkout(local, "target")

        assert resp.branch == "target"
        assert _head(local) == "target"
        assert _git_out(local, "status", "--porcelain").split() == ["M", "README.md"]
        assert _read(local, "README.md") == b"edited\n"

    async def test_a_change_the_switch_would_overwrite_is_dirty(self, local):
        run_git_sync(local, "switch", "-q", "-c", "target")
        write_file(local, "keep.txt", b"one\nTARGET\nthree\n")
        commit_all(local, "target side")
        run_git_sync(local, "switch", "-q", "main")
        write_file(local, "keep.txt", b"one\nLOCAL\nthree\n")
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _checkout(local, "target", bus=bus)

        assert info.value.code == "dirty"
        assert _head(local) == "main"
        assert _read(local, "keep.txt") == b"one\nLOCAL\nthree\n"
        assert bus.published == []

    async def test_a_branch_checked_out_in_a_linked_worktree_is_refused_with_its_path(self, local):
        path = _add_linked_worktree(local, "topic")

        with pytest.raises(GitRefusedError) as info:
            await _checkout(local, "topic")

        assert info.value.code == "checked_out_elsewhere"
        assert os.path.realpath(info.value.message) == os.path.realpath(path)
        assert _head(local) == "main"

    async def test_a_merge_in_progress_is_refused_before_git_runs(self, local):
        make_conflict_branches(local)
        run_git_sync(local, "merge", "other", check=False)
        run_git_sync(local, "branch", "target")

        with pytest.raises(GitRefusedError) as info:
            await _checkout(local, "target")

        assert (info.value.code, info.value.message) == ("operation_in_progress", "merging")
        assert _head(local) == "main"


class TestRootMatcher:
    def test_the_root_and_its_subdirectories_match_but_linked_worktrees_and_siblings_do_not(
        self, tmp_path
    ):
        root = str(tmp_path / "repo")
        linked = os.path.join(root, "wt", "topic")

        matches = _root_matcher(root, [root, linked])

        def norm(path: str) -> str:
            return os.path.normcase(os.path.realpath(path))

        assert matches(norm(root))
        assert matches(norm(root + os.sep))
        assert matches(norm(os.path.join(root, "sub", "dir")))
        assert not matches(norm(linked))
        assert not matches(norm(os.path.join(linked, "deeper")))
        assert not matches(norm(root + "-other"))
        assert not matches(norm(str(tmp_path / "elsewhere")))


class TestClassifyRefFailure:
    @pytest.mark.parametrize(
        ("stderr", "code", "message"),
        [
            (
                "fatal: 'topic' is already used by worktree at '/wt/topic'\n",
                "checked_out_elsewhere",
                "/wt/topic",
            ),
            (
                "fatal: 'topic' is already checked out at '/wt/topic'\n",
                "checked_out_elsewhere",
                "/wt/topic",
            ),
            (
                "error: Your local changes to the following files would be overwritten"
                " by checkout:\n\tkeep.txt\n",
                "dirty",
                None,
            ),
            ("fatal: cannot switch branch while merging\n", "operation_in_progress", None),
            ("fatal: a branch named 'x' already exists\n", "exists", None),
            (
                "fatal: Unable to create '/r/.git/index.lock': File exists.\n",
                "index_locked",
                None,
            ),
        ],
    )
    def test_maps_gits_refusals_onto_the_vocabulary(self, stderr, code, message):
        exc = _classify_ref_failure(
            CmdResult(rc=128, stdout=b"", stderr=stderr.encode()), "git switch failed"
        )

        assert isinstance(exc, GitRefusedError)
        assert exc.code == code
        assert exc.message == (stderr.strip() if message is None else message)

    def test_an_invalid_name_is_a_bad_request_and_anything_else_a_command_error(self):
        bad = _classify_ref_failure(
            CmdResult(rc=128, stdout=b"", stderr=b"fatal: 'a b' is not a valid branch name\n"),
            "git branch failed",
        )
        other = _classify_ref_failure(
            CmdResult(rc=128, stdout=b"", stderr=b"fatal: something else\n"),
            "git branch failed",
        )

        assert isinstance(bad, GitBadRequestError)
        assert bad.code == "bad_branch_name"
        assert isinstance(other, GitCommandError)
        assert other.message == "fatal: something else"
