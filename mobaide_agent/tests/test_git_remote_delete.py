"""``remote_branch`` and worktree-only deletes of the project git service
against a real bare remote: which refs a ``git push`` deletion removes, the
refusal vocabulary each remote failure maps to, and the positional results of
a batch mixing local and remote items."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from mobaide.agent.git import service
from mobaide.agent.git.models import (
    GitDeleteItem,
    GitDeleteRequest,
    GitDeleteResponse,
)
from mobaide.agent.git.service import (
    _push_outcomes,
    _tracking_source,
    delete_git_items,
)
from mobaide.core.git_exec import CmdResult, reset_caches
from mobaide.linear.worktree import WorktreeManager

from .git_repo_support import (
    GIT_BIN,
    clone_repo,
    norm_path,
    posix_only,
    requires_git,
    run_git_sync,
    seed_remote,
    write_file,
)

_DEFAULT_REFSPEC = "+refs/heads/*:refs/remotes/origin/*"


@pytest.fixture(autouse=True)
def _fresh_caches():
    reset_caches()
    yield
    reset_caches()


@pytest.fixture
def bare(tmp_path) -> str:
    """A bare remote whose ``main`` (its HEAD) carries the initial commit."""
    if GIT_BIN is None:
        pytest.skip("git binary not on PATH")
    return seed_remote(str(tmp_path))


@pytest.fixture
def local(bare, tmp_path) -> str:
    """A clone of ``bare`` on ``main``, with ``origin/HEAD`` pointing at
    ``origin/main``."""
    return clone_repo(bare, str(tmp_path / "local"))


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


def _refs(repo_dir: str, prefix: str) -> list[str]:
    return _git_out(repo_dir, "for-each-ref", "--format=%(refname)", prefix).split()


def _local_branches(repo_dir: str) -> list[str]:
    return _git_out(repo_dir, "branch", "--list", "--format=%(refname:short)").split()


def _publish(local: str, name: str) -> None:
    """``refs/heads/<name>`` on the remote at ``main``'s tip, tracked by
    ``refs/remotes/origin/<name>`` in ``local`` and no local branch."""
    run_git_sync(local, "push", "-q", "origin", f"main:refs/heads/{name}")


def _add_linked_worktree(local: str, branch: str) -> str:
    """``<local>/wt/<branch>`` checked out on ``branch`` (created here)."""
    run_git_sync(local, "branch", branch)
    path = os.path.join(local, "wt", branch)
    run_git_sync(local, "worktree", "add", "-q", path, branch)
    return path


def _fork(bare: str, tmp_path) -> str:
    """A bare copy of ``bare`` at ``<tmp_path>/fork.git`` with every branch
    ``bare`` has now."""
    path = str(tmp_path / "fork.git")
    run_git_sync(str(tmp_path), "clone", "-q", "--bare", bare, path)
    return path


def _remote(branch: str | None) -> GitDeleteItem:
    return GitDeleteItem(kind="remote_branch", branch=branch)


async def _delete(
    root: str, *items: GitDeleteItem, bus: FakeEventBus | None = None
) -> GitDeleteResponse:
    return await delete_git_items(
        GitDeleteRequest(items=list(items)),
        project_name="proj",
        project_root=root,
        manager=_manager(),
        session_manager=None,
        autoscan_manager=None,
        event_bus=bus,
        actor=None,
    )


def _outcomes(response: GitDeleteResponse) -> list[tuple[str, str, bool, str | None]]:
    return [(r.kind, r.target, r.ok, r.error) for r in response.results]


def _fail_push(monkeypatch, answer) -> None:
    """Route every ``git push`` of the service to ``answer`` (a result to
    return or an exception to raise); every other command runs for real."""
    real_run_git = service.run_git

    async def fake_run_git(args, **kwargs):
        if args[0] != "push":
            return await real_run_git(args, **kwargs)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(service, "run_git", fake_run_git)


@requires_git
class TestDeleteRemoteBranch:
    async def test_deletes_the_branch_on_the_remote_and_its_tracking_ref(self, bare, local):
        _publish(local, "feature/x")
        bus = FakeEventBus()

        response = await _delete(local, _remote("origin/feature/x"), bus=bus)

        assert _outcomes(response) == [("remote_branch", "origin/feature/x", True, None)]
        assert "refs/heads/feature/x" not in _refs(bare, "refs/heads/")
        assert "refs/remotes/origin/feature/x" not in _refs(local, "refs/remotes/")
        assert "refs/heads/main" in _refs(bare, "refs/heads/")
        assert bus.published == [_changed(local, "proj")]

    async def test_a_branch_already_gone_on_the_remote_drops_the_stale_tracking_ref(
        self, bare, local
    ):
        _publish(local, "gone")
        run_git_sync(bare, "update-ref", "-d", "refs/heads/gone")
        assert "refs/remotes/origin/gone" in _refs(local, "refs/remotes/")

        response = await _delete(local, _remote("origin/gone"))

        assert _outcomes(response) == [("remote_branch", "origin/gone", True, None)]
        assert "refs/remotes/origin/gone" not in _refs(local, "refs/remotes/")

    async def test_the_remote_head_symref_is_not_found_and_nothing_is_pushed(
        self, bare, local
    ):
        bus = FakeEventBus()

        response = await _delete(local, _remote("origin/HEAD"), bus=bus)

        assert _outcomes(response) == [("remote_branch", "origin/HEAD", False, "not_found")]
        assert _refs(bare, "refs/heads/") == ["refs/heads/main"]
        assert "refs/remotes/origin/HEAD" in _refs(local, "refs/remotes/")
        assert bus.published == []

    @pytest.mark.parametrize(
        "branch",
        ["origin/nope", "ghost/main", "main", "origin"],
        ids=["untracked-branch", "no-such-remote", "local-name", "remote-alone"],
    )
    async def test_a_name_that_is_not_a_listed_remote_branch_is_not_found(
        self, bare, local, branch
    ):
        response = await _delete(local, _remote(branch))

        assert _outcomes(response) == [("remote_branch", branch, False, "not_found")]
        assert _refs(bare, "refs/heads/") == ["refs/heads/main"]


    @pytest.mark.parametrize("branch", [None, "", "origin/a\0b"])
    async def test_a_missing_or_nul_branch_is_invalid(self, local, branch):
        response = await _delete(local, _remote(branch))

        assert [(r.kind, r.ok, r.error) for r in response.results] == [
            ("remote_branch", False, "invalid")]

    async def test_the_remote_default_branch_is_rejected_with_the_remote_reason(
        self, bare, local
    ):
        bus = FakeEventBus()

        response = await _delete(local, _remote("origin/main"), bus=bus)

        result = response.results[0]
        assert (result.kind, result.target, result.ok, result.error) == (
            "remote_branch", "origin/main", False, "rejected")
        assert "deletion of the current branch prohibited" in (result.error_detail or "")
        assert "refs/heads/main" in _refs(bare, "refs/heads/")
        assert "refs/remotes/origin/main" in _refs(local, "refs/remotes/")
        assert bus.published == []

    @posix_only
    async def test_a_declining_pre_receive_hook_is_hook_failed(self, bare, local):
        _publish(local, "feature/x")
        hook = write_file(bare, "hooks/pre-receive", b"#!/bin/sh\nexit 1\n")
        os.chmod(hook, 0o755)

        response = await _delete(local, _remote("origin/feature/x"))

        result = response.results[0]
        assert (result.ok, result.error) == (False, "hook_failed")
        assert "hook declined" in (result.error_detail or "")
        assert "refs/heads/feature/x" in _refs(bare, "refs/heads/")
        assert "refs/remotes/origin/feature/x" in _refs(local, "refs/remotes/")

    async def test_a_fetch_refspec_mapping_two_branches_to_the_ref_refuses_the_delete(
        self, bare, local
    ):
        _publish(local, "feature/x")
        run_git_sync(bare, "branch", "team/feature/x", "main")
        run_git_sync(
            local, "config", "--add", "remote.origin.fetch",
            "+refs/heads/team/*:refs/remotes/origin/*",
        )

        response = await _delete(local, _remote("origin/feature/x"))

        result = response.results[0]
        assert (result.ok, result.error) == (False, "error")
        assert "fetch refspecs" in (result.error_detail or "")
        assert {"refs/heads/feature/x", "refs/heads/team/feature/x"} <= set(
            _refs(bare, "refs/heads/"))

    async def test_an_unreachable_push_url_is_network(self, bare, local):
        _publish(local, "feature/x")
        run_git_sync(local, "remote", "set-url", "origin", "https://127.0.0.1:9/x.git")

        response = await _delete(local, _remote("origin/feature/x"))

        result = response.results[0]
        assert (result.ok, result.error) == (False, "network")
        assert result.error_detail
        assert "refs/heads/feature/x" in _refs(bare, "refs/heads/")

    async def test_a_push_that_outlasts_its_budget_is_git_timeout(
        self, bare, local, monkeypatch
    ):
        _publish(local, "feature/x")
        _fail_push(monkeypatch, CmdResult(rc=-9, stdout=b"", stderr=b"", timed_out=True))

        response = await _delete(local, _remote("origin/feature/x"))

        assert _outcomes(response) == [
            ("remote_branch", "origin/feature/x", False, "git_timeout")]
        assert "refs/heads/feature/x" in _refs(bare, "refs/heads/")


@requires_git
class TestPushUrlGuard:
    @pytest.mark.parametrize("also_fetch_url", [False, True], ids=["fork", "fetch-url-and-fork"])
    async def test_a_push_url_to_another_repository_refuses_every_item_of_the_remote(
        self, bare, local, tmp_path, also_fetch_url
    ):
        _publish(local, "feature/x")
        _publish(local, "feature/y")
        fork = _fork(bare, tmp_path)
        if also_fetch_url:
            run_git_sync(
                local, "config", "--add", "remote.origin.pushurl",
                _git_out(local, "config", "remote.origin.url"),
            )
        run_git_sync(local, "config", "--add", "remote.origin.pushurl", fork)
        bus = FakeEventBus()

        response = await _delete(
            local, _remote("origin/feature/x"), _remote("origin/feature/y"), bus=bus
        )

        assert _outcomes(response) == [
            ("remote_branch", "origin/feature/x", False, "error"),
            ("remote_branch", "origin/feature/y", False, "error"),
        ]
        for result in response.results:
            assert "remote 'origin' pushes to a different repository" in (
                result.error_detail or "")
        for repo in (bare, fork):
            assert {"refs/heads/feature/x", "refs/heads/feature/y"} <= set(
                _refs(repo, "refs/heads/"))
        assert {"refs/remotes/origin/feature/x", "refs/remotes/origin/feature/y"} <= set(
            _refs(local, "refs/remotes/"))
        assert bus.published == []

    async def test_a_second_url_without_a_push_url_refuses_every_item_of_the_remote(
        self, bare, local, tmp_path
    ):
        _publish(local, "feature/x")
        _publish(local, "feature/y")
        fork = _fork(bare, tmp_path)
        run_git_sync(local, "remote", "set-url", "--add", "origin", fork)
        bus = FakeEventBus()

        response = await _delete(
            local, _remote("origin/feature/x"), _remote("origin/feature/y"), bus=bus
        )

        assert _outcomes(response) == [
            ("remote_branch", "origin/feature/x", False, "error"),
            ("remote_branch", "origin/feature/y", False, "error"),
        ]
        for result in response.results:
            assert "remote 'origin' pushes to a different repository" in (
                result.error_detail or "")
        for repo in (bare, fork):
            assert {"refs/heads/feature/x", "refs/heads/feature/y"} <= set(
                _refs(repo, "refs/heads/"))
        assert {"refs/remotes/origin/feature/x", "refs/remotes/origin/feature/y"} <= set(
            _refs(local, "refs/remotes/"))
        assert bus.published == []

    async def test_a_push_url_equal_to_the_fetch_url_deletes(self, bare, local):
        _publish(local, "feature/x")
        run_git_sync(
            local, "config", "remote.origin.pushurl",
            _git_out(local, "config", "remote.origin.url"),
        )

        response = await _delete(local, _remote("origin/feature/x"))

        assert _outcomes(response) == [("remote_branch", "origin/feature/x", True, None)]
        assert "refs/heads/feature/x" not in _refs(bare, "refs/heads/")
        assert "refs/remotes/origin/feature/x" not in _refs(local, "refs/remotes/")

    async def test_a_push_insteadof_rewrite_of_the_fetch_url_deletes(self, bare, local):
        _publish(local, "feature/x")
        fetch_url = Path(bare).as_uri()
        run_git_sync(local, "remote", "set-url", "origin", fetch_url)
        run_git_sync(local, "config", f"url.{bare}.pushInsteadOf", fetch_url)
        assert _git_out(local, "remote", "get-url", "origin") == fetch_url
        assert _git_out(local, "remote", "get-url", "--push", "origin") == bare

        response = await _delete(local, _remote("origin/feature/x"))

        assert _outcomes(response) == [("remote_branch", "origin/feature/x", True, None)]
        assert "refs/heads/feature/x" not in _refs(bare, "refs/heads/")
        assert "refs/remotes/origin/feature/x" not in _refs(local, "refs/remotes/")

    async def test_only_the_remote_pushing_elsewhere_is_refused_in_a_batch(
        self, bare, local, tmp_path
    ):
        _publish(local, "feature/x")
        _publish(local, "feature/y")
        fork = _fork(bare, tmp_path)
        other = seed_remote(str(tmp_path / "other"))
        run_git_sync(local, "remote", "add", "other", other)
        run_git_sync(local, "push", "-q", "other", "main:refs/heads/topic")
        run_git_sync(local, "config", "remote.origin.pushurl", fork)
        bus = FakeEventBus()

        response = await _delete(
            local,
            _remote("origin/feature/x"),
            _remote("other/topic"),
            _remote("origin/feature/y"),
            bus=bus,
        )

        assert _outcomes(response) == [
            ("remote_branch", "origin/feature/x", False, "error"),
            ("remote_branch", "other/topic", True, None),
            ("remote_branch", "origin/feature/y", False, "error"),
        ]
        assert "remote 'origin' pushes to a different repository" in (
            response.results[2].error_detail or "")
        assert "refs/heads/topic" not in _refs(other, "refs/heads/")
        assert "refs/remotes/other/topic" not in _refs(local, "refs/remotes/")
        for repo in (bare, fork):
            assert {"refs/heads/feature/x", "refs/heads/feature/y"} <= set(
                _refs(repo, "refs/heads/"))
        assert bus.published == [_changed(local, "proj")]


@requires_git
class TestMixedDeleteBatch:
    async def test_results_stay_positional_and_a_worktree_removal_keeps_its_branch(
        self, bare, local
    ):
        path = _add_linked_worktree(local, "topic")
        run_git_sync(local, "branch", "stale")
        _publish(local, "feature/x")
        bus = FakeEventBus()

        response = await _delete(
            local,
            GitDeleteItem(kind="orphan_worktree", worktree_path=path),
            _remote("origin/feature/x"),
            GitDeleteItem(kind="branch_only", branch="stale"),
            _remote("origin/nope"),
            bus=bus,
        )

        assert _outcomes(response) == [
            ("orphan_worktree", path, True, None),
            ("remote_branch", "origin/feature/x", True, None),
            ("branch_only", "stale", True, None),
            ("remote_branch", "origin/nope", False, "not_found"),
        ]
        assert not os.path.exists(path)
        assert _local_branches(local) == ["main", "topic"]
        assert _refs(bare, "refs/heads/") == ["refs/heads/main"]
        assert bus.published == [_changed(local, "proj")]

    async def test_a_remote_group_that_raises_keeps_the_local_results(
        self, bare, local, monkeypatch
    ):
        run_git_sync(local, "branch", "stale")
        _publish(local, "feature/x")
        _fail_push(monkeypatch, RuntimeError("boom"))
        bus = FakeEventBus()

        response = await _delete(
            local,
            _remote("origin/feature/x"),
            GitDeleteItem(kind="branch_only", branch="stale"),
            bus=bus,
        )

        assert _outcomes(response) == [
            ("remote_branch", "origin/feature/x", False, "error"),
            ("branch_only", "stale", True, None),
        ]
        assert response.results[0].error_detail == "boom"
        assert _local_branches(local) == ["main"]
        assert bus.published == [_changed(local, "proj")]

    async def test_a_batch_where_only_remote_deletes_fail_publishes_nothing(
        self, bare, local
    ):
        bus = FakeEventBus()

        response = await _delete(
            local, _remote("origin/main"), _remote("origin/HEAD"), bus=bus
        )

        assert [r.error for r in response.results] == ["rejected", "not_found"]
        assert bus.published == []


class TestTrackingSource:
    @pytest.mark.parametrize(
        ("tracking_ref", "refspecs", "expected"),
        [
            ("refs/remotes/origin/feature/x", [_DEFAULT_REFSPEC], "refs/heads/feature/x"),
            ("refs/remotes/origin/main", ["refs/heads/*:refs/remotes/origin/*"], "refs/heads/main"),
            (
                "refs/remotes/origin/main",
                ["+refs/heads/main:refs/remotes/origin/main"],
                "refs/heads/main",
            ),
            ("refs/remotes/origin/dev", ["+refs/heads/main:refs/remotes/origin/main"], None),
            (
                "refs/remotes/origin/x",
                ["+refs/heads/team/*:refs/remotes/origin/*"],
                "refs/heads/team/x",
            ),
            (
                "refs/remotes/origin/feature/x",
                [_DEFAULT_REFSPEC, "+refs/heads/team/*:refs/remotes/origin/*"],
                None,
            ),
            (
                "refs/remotes/origin/pr/7",
                ["+refs/pull/*/head:refs/remotes/origin/pr/*"],
                None,
            ),
            (
                "refs/remotes/origin/pr/7",
                [_DEFAULT_REFSPEC, "+refs/pull/*/head:refs/remotes/origin/pr/*"],
                None,
            ),
            (
                "refs/remotes/origin/feature/x",
                [_DEFAULT_REFSPEC, "+refs/pull/*/head:refs/remotes/origin/pr/*"],
                "refs/heads/feature/x",
            ),
            (
                "refs/remotes/origin/feature/x",
                [_DEFAULT_REFSPEC, "^refs/heads/secret", "refs/heads/*"],
                "refs/heads/feature/x",
            ),
            ("refs/remotes/upstream/main", [_DEFAULT_REFSPEC], None),
            ("refs/remotes/origin/main", [], None),
        ],
        ids=[
            "default",
            "default-without-plus",
            "exact",
            "exact-other-ref",
            "renamed-namespace",
            "two-sources",
            "pull-refs-only",
            "pull-ref-alongside-default",
            "branch-alongside-pull-refs",
            "negative-and-malformed-skipped",
            "other-remote",
            "no-refspecs",
        ],
    )
    def test_maps_the_tracking_ref_to_exactly_one_branch(
        self, tracking_ref, refspecs, expected
    ):
        assert _tracking_source(tracking_ref, refspecs) == expected


class TestPushOutcomes:
    def test_keys_each_ref_line_by_the_remote_ref_and_skips_the_rest(self):
        stdout = (
            b"To /tmp/remote.git\n"
            b"-\t:refs/heads/feature/x\t[deleted]\n"
            b"!\t:refs/heads/main\t[remote rejected] (deletion of the current branch prohibited)\n"
            b"Done\n"
        )

        assert _push_outcomes(stdout) == {
            "refs/heads/feature/x": ("-", "[deleted]"),
            "refs/heads/main": (
                "!", "[remote rejected] (deletion of the current branch prohibited)"),
        }
