"""Base-branch choice for new worktrees: the default the manager picks, an
explicit ``base_branch``, the ``GET /worktree-bases`` listing and the
per-workspace memory of the last choice."""
from __future__ import annotations

import asyncio
import atexit
import os
import shutil
import subprocess
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mobaide.agent.git.service import list_worktree_bases
from mobaide.core.git_exec import reset_caches
from mobaide.core.workspaces_store import WorkspaceRecord, WorkspacesStore
from mobaide.linear.worktree import (
    WorktreeCreationError,
    WorktreeManager,
    WorktreeResult,
    create_standard_worktree,
)

from .git_repo_support import (
    GIT_BIN,
    clone_repo,
    commit_all,
    init_repo,
    run_git_sync,
    seed_remote,
    write_file,
)


@pytest.fixture(autouse=True)
def _fresh_caches():
    reset_caches()
    yield
    reset_caches()


_WORKTREES_DIR = tempfile.mkdtemp(prefix="mobaide-wt-test-")
atexit.register(shutil.rmtree, _WORKTREES_DIR, ignore_errors=True)


def _manager() -> WorktreeManager:
    return WorktreeManager(
        worktrees_dir=_WORKTREES_DIR, legacy_worktree_base=".worktrees", branch_prefix="auto"
    )


def _rev(repo: str, ref: str) -> str:
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    return subprocess.run(
        [GIT_BIN, "rev-parse", ref], cwd=repo, check=True, env=env, capture_output=True,
    ).stdout.decode().strip()


@pytest.fixture
def clone(tmp_path) -> str:
    """A clone whose remote has ``main`` (``origin/HEAD``) and a newer
    ``develop``; the clone also has an unpushed local ``wip``."""
    if GIT_BIN is None:
        pytest.skip("git binary not on PATH")
    bare = seed_remote(str(tmp_path))
    seed = str(tmp_path / "seed")
    run_git_sync(seed, "checkout", "-q", "-b", "develop")
    write_file(seed, "dev.txt", b"dev\n")
    commit_all(seed, "develop work")
    run_git_sync(seed, "push", "-q", "origin", "develop")
    local = clone_repo(bare, str(tmp_path / "local"))
    run_git_sync(local, "branch", "wip")
    return local


class TestDefaultBase:
    async def test_origin_head_wins(self, clone):
        run_git_sync(clone, "remote", "set-head", "origin", "develop")
        assert await _manager().default_base(clone) == "origin/develop"

    async def test_falls_back_to_origin_main_without_origin_head(self, clone):
        run_git_sync(clone, "remote", "set-head", "origin", "--delete")
        assert await _manager().default_base(clone) == "origin/main"

    async def test_local_main_without_any_remote(self, tmp_path):
        if GIT_BIN is None:
            pytest.skip("git binary not on PATH")
        repo = init_repo(str(tmp_path / "solo"))
        assert await _manager().default_base(repo) == "main"

    async def test_none_when_nothing_matches(self, tmp_path):
        if GIT_BIN is None:
            pytest.skip("git binary not on PATH")
        repo = str(tmp_path / "other")
        os.makedirs(repo)
        run_git_sync(repo, "init", "-q", "-b", "trunk")
        write_file(repo, "a.txt", b"a\n")
        commit_all(repo, "first")
        assert await _manager().default_base(repo) is None


class TestCreateWorktreeWithBase:
    async def test_remote_base_is_fetched_and_used(self, clone, tmp_path):
        seed = str(tmp_path / "seed")
        write_file(seed, "later.txt", b"later\n")
        commit_all(seed, "pushed after the clone")
        run_git_sync(seed, "push", "-q", "origin", "develop")

        result = await _manager().create_worktree(
            "s1", clone, branch_name="feat-1", base_branch="origin/develop",
        )

        assert result.is_new
        assert _rev(result.path, "HEAD") == _rev(seed, "develop")

    async def test_local_base(self, clone):
        run_git_sync(clone, "checkout", "-q", "wip")
        write_file(clone, "wip.txt", b"wip\n")
        commit_all(clone, "local only")

        result = await _manager().create_worktree(
            "s2", clone, branch_name="feat-2", base_branch="wip",
        )

        assert _rev(result.path, "HEAD") == _rev(clone, "wip")

    async def test_local_branch_beats_a_same_named_tag(self, clone):
        run_git_sync(clone, "tag", "wip", "origin/develop")
        run_git_sync(clone, "checkout", "-q", "wip")
        write_file(clone, "wip.txt", b"wip\n")
        commit_all(clone, "local only")

        result = await _manager().create_worktree(
            "s3", clone, branch_name="feat-3", base_branch="wip",
        )

        assert _rev(result.path, "HEAD") == _rev(clone, "refs/heads/wip")

    async def test_unknown_base_raises(self, clone):
        with pytest.raises(WorktreeCreationError) as err:
            await _manager().create_worktree(
                "s4", clone, branch_name="feat-4", base_branch="origin/nope",
            )
        assert "origin/nope" in err.value.stderr
        assert not os.path.exists(os.path.join(clone, ".worktrees", "s4"))

    async def test_dash_base_rejected(self, clone):
        with pytest.raises(WorktreeCreationError) as err:
            await _manager().create_worktree(
                "s5", clone, branch_name="feat-5", base_branch="--upload-pack=x",
            )
        assert "must not start with '-'" in err.value.stderr

    async def test_existing_branch_ignores_a_deleted_base(self, clone):
        run_git_sync(clone, "branch", "feat-7")

        result = await _manager().create_worktree(
            "s7", clone, branch_name="feat-7", base_branch="deleted-base",
        )

        assert result.is_new
        assert _rev(result.path, "HEAD") == _rev(clone, "feat-7")

    async def test_no_base_uses_the_default(self, clone):
        run_git_sync(clone, "remote", "set-head", "origin", "develop")

        result = await _manager().create_worktree("s6", clone, branch_name="feat-6")

        assert _rev(result.path, "HEAD") == _rev(clone, "origin/develop")


class TestCreateStandardWorktreeBase:
    async def test_base_forwarded_and_blank_dropped(self):
        mgr = AsyncMock()
        mgr.create_worktree.return_value = WorktreeResult(path="/r/wt", is_new=True, branch="b")

        await create_standard_worktree(mgr, "/r", "b", base_branch="origin/dev")
        await create_standard_worktree(mgr, "/r", "b", base_branch="  ")

        calls = mgr.create_worktree.await_args_list
        assert calls[0].kwargs["base_branch"] == "origin/dev"
        assert calls[1].kwargs["base_branch"] is None


class TestListWorktreeBases:
    async def test_default_first_then_newest(self, clone):
        resp = await list_worktree_bases(clone, _manager(), last_used=None)

        assert resp.default == "origin/main"
        assert resp.refs[0].ref == "origin/main"
        assert {(r.ref, r.kind) for r in resp.refs} == {
            ("origin/main", "remote"),
            ("origin/develop", "remote"),
            ("main", "local"),
            ("wip", "local"),
        }
        assert all(r.subject for r in resp.refs)
        assert resp.last_used is None

    async def test_last_used_kept_only_while_it_exists(self, clone):
        kept = await list_worktree_bases(clone, _manager(), last_used="origin/develop")
        gone = await list_worktree_bases(clone, _manager(), last_used="origin/deleted")

        assert kept.last_used == "origin/develop"
        assert gone.last_used is None


    async def test_branch_pushed_after_the_clone_is_listed(self, clone, tmp_path):
        seed = str(tmp_path / "seed")
        run_git_sync(seed, "checkout", "-q", "-b", "pushed-later")
        write_file(seed, "later.txt", b"later\n")
        commit_all(seed, "pushed after the clone")
        run_git_sync(seed, "push", "-q", "origin", "pushed-later")

        resp = await list_worktree_bases(clone, _manager(), last_used=None)

        assert ("origin/pushed-later", "remote") in {(r.ref, r.kind) for r in resp.refs}

    async def test_branch_deleted_on_the_remote_is_dropped(self, clone, tmp_path):
        run_git_sync(str(tmp_path / "seed"), "push", "-q", "origin", "--delete", "develop")

        resp = await list_worktree_bases(clone, _manager(), last_used=None)

        assert "origin/develop" not in {r.ref for r in resp.refs}

    async def test_unreachable_remote_lists_the_cached_refs(self, clone, tmp_path):
        run_git_sync(clone, "remote", "set-url", "origin", str(tmp_path / "gone.git"))

        resp = await list_worktree_bases(clone, _manager(), last_used=None)

        assert {r.ref for r in resp.refs} >= {"origin/main", "origin/develop"}

    async def test_recent_fetch_is_reused_then_refetched_once_stale(
        self, clone, tmp_path, monkeypatch
    ):
        mgr = _manager()
        seed = str(tmp_path / "seed")
        await list_worktree_bases(clone, mgr, last_used=None)
        run_git_sync(seed, "checkout", "-q", "-b", "fresh")
        run_git_sync(seed, "push", "-q", "origin", "fresh")

        cached = await list_worktree_bases(clone, mgr, last_used=None)
        monkeypatch.setattr("mobaide.linear.worktree._REMOTES_FRESH_FOR", 0.0)
        stale = await list_worktree_bases(clone, mgr, last_used=None)

        assert "origin/fresh" not in {r.ref for r in cached.refs}
        assert "origin/fresh" in {r.ref for r in stale.refs}


class TestRefreshRemotes:
    async def test_concurrent_callers_share_one_fetch(self):
        fetches: list[tuple[str, ...]] = []
        release = asyncio.Event()

        async def fake_fetch(*args: str, cwd: str) -> None:
            fetches.append(args)
            await release.wait()

        mgr = _manager()
        with patch("mobaide.linear.worktree._fetch", fake_fetch):
            calls = [asyncio.create_task(mgr.refresh_remotes("/r")) for _ in range(3)]
            await asyncio.sleep(0)
            release.set()
            await asyncio.gather(*calls)

        assert fetches == [("--all", "--prune")]

    async def test_runs_while_a_create_holds_the_repo_lock(self, tmp_path):
        fetches: list[str] = []

        async def fake_fetch(*args: str, cwd: str) -> None:
            fetches.append(cwd)

        mgr = _manager()
        root = str(tmp_path)
        with patch("mobaide.linear.worktree._fetch", fake_fetch):
            async with mgr._lock_for(root):
                await asyncio.wait_for(mgr.refresh_remotes(root), timeout=5)

        assert fetches == [root]

    async def test_caller_answers_after_the_wait_while_the_fetch_runs_on(self):
        fetches: list[tuple[str, ...]] = []
        release = asyncio.Event()
        done = asyncio.Event()

        async def fake_fetch(*args: str, cwd: str) -> None:
            fetches.append(args)
            await release.wait()
            done.set()

        mgr = _manager()
        with patch("mobaide.linear.worktree._fetch", fake_fetch), patch(
            "mobaide.linear.worktree._REMOTES_WAIT", 0.01,
        ):
            await asyncio.wait_for(mgr.refresh_remotes("/r"), timeout=5)
            assert fetches == [("--all", "--prune")]
            assert not done.is_set()

            await asyncio.wait_for(mgr.refresh_remotes("/r"), timeout=5)
            assert fetches == [("--all", "--prune")]

            release.set()
            await asyncio.wait_for(done.wait(), timeout=5)
            await asyncio.sleep(0)
            await asyncio.wait_for(mgr.refresh_remotes("/r"), timeout=5)

        assert fetches == [("--all", "--prune")]

    async def test_refetches_once_the_last_fetch_is_stale(self):
        fetches: list[str] = []

        async def fake_fetch(*args: str, cwd: str) -> None:
            fetches.append(cwd)

        mgr = _manager()
        with patch("mobaide.linear.worktree._fetch", fake_fetch), patch(
            "mobaide.linear.worktree._REMOTES_FRESH_FOR", 0.0,
        ):
            await mgr.refresh_remotes("/r")
            await mgr.refresh_remotes("/r")

        assert fetches == ["/r", "/r"]

    async def test_a_failed_fetch_answers_and_is_not_retried_at_once(self):
        attempts: list[str] = []

        async def failing_fetch(*args: str, cwd: str) -> None:
            attempts.append(cwd)
            raise OSError("offline")

        mgr = _manager()
        with patch("mobaide.linear.worktree._fetch", failing_fetch):
            await asyncio.wait_for(mgr.refresh_remotes("/r"), timeout=5)
            await asyncio.wait_for(mgr.refresh_remotes("/r"), timeout=5)

        assert attempts == ["/r"]


class TestWorkspacesStoreLastBase:
    def test_round_trips_and_survives_reload_and_rename(self, tmp_path):
        from mobaide.core.workspaces_store import WorkspaceUpdatePatch

        path = str(tmp_path / "projects.json")
        store = WorkspacesStore(path)
        store.load(seed_json="[]")
        store.create(WorkspaceRecord(name="p", path=str(tmp_path)))
        assert store.get_last_worktree_base("p") is None

        store.set_last_worktree_base("p", "origin/dev")
        store.update("p", WorkspaceUpdatePatch(name="q"))
        store.set_last_worktree_base("missing", "x")

        reloaded = WorkspacesStore(path)
        reloaded.load()
        assert reloaded.get_last_worktree_base("q") == "origin/dev"
        assert "last_worktree_base" not in reloaded.list_public()[0]


class TestSessionManagerRemembersBase:
    async def test_terminal_worktree_records_the_base(self, tmp_path):
        from mobaide.core.models import SessionConfig
        from mobaide.core.session_manager import SessionManager

        (tmp_path / ".git").mkdir()
        store = WorkspacesStore(str(tmp_path / "projects.json"))
        store.load(seed_json="[]")
        store.create(WorkspaceRecord(name="p", path=str(tmp_path)))
        manager = SessionManager.__new__(SessionManager)
        manager._workspaces_store = store
        manager._worktree_mgr = MagicMock()
        config = SessionConfig(
            session_id="s", command=["sh"], project_name="p",
            worktree=True, branch_name="b", base_branch="origin/dev",
        )

        with patch(
            "mobaide.linear.worktree.create_standard_worktree",
            new=AsyncMock(return_value=WorktreeResult(
                path=str(tmp_path), is_new=True, branch="b",
            )),
        ) as create:
            await manager._resolve_workspace_cwd(config)

        assert create.await_args.kwargs["base_branch"] == "origin/dev"
        assert store.get_last_worktree_base("p") == "origin/dev"

        config.base_branch = None
        with patch(
            "mobaide.linear.worktree.create_standard_worktree",
            new=AsyncMock(return_value=WorktreeResult(
                path=str(tmp_path), is_new=True, branch="b",
            )),
        ):
            await manager._resolve_workspace_cwd(config)

        assert store.get_last_worktree_base("p") is None
