"""End-to-end tests for the git management endpoints (CLA-46).

The router is exercised through an ``ASGITransport`` so we go through the
real auth dependency, the projects store, and the wired
``app.state.worktree_manager``. The underlying ``_run_git`` is exercised
both directly (against a real on-disk repo built with ``git init``) and
indirectly through the merge logic in ``service._build_items``.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import types

import pytest
from httpx import ASGITransport, AsyncClient

from mobaide.agent.app import create_app
from mobaide.agent.config import AgentConfig
from mobaide.agent.git.models import (
    GitDeleteItem,
    GitDeleteRequest,
)
from mobaide.agent.git.changes_service import resolve_repo_dirs
from mobaide.agent.git.service import (
    GitItemBusyError,
    _build_items,
    _busy_holders,
    delete_git_items,
)
from mobaide.core import ProjectRecord
from mobaide.linear.worktree import (
    BranchDeleteError,
    BranchInfo,
    WorktreeInfo,
    WorktreeRemoveError,
)

from .conftest import TEST_JWT_SECRET, TEST_PASSWORD, TEST_USERNAME
from .git_repo_support import clone_repo, seed_remote


GIT_BIN = shutil.which("git")
requires_git = pytest.mark.skipif(GIT_BIN is None, reason="git binary not on PATH")


def _run_git_sync(repo: str, *args: str) -> None:
    """Synchronous helper for fixture setup only — never used by SUT."""
    env = dict(os.environ)
    # Force-isolate from the developer's global git config so a missing
    # user.name/user.email or commit signing setup doesn't break commits.
    env.setdefault("GIT_AUTHOR_NAME", "Test")
    env.setdefault("GIT_AUTHOR_EMAIL", "test@example.com")
    env.setdefault("GIT_COMMITTER_NAME", "Test")
    env.setdefault("GIT_COMMITTER_EMAIL", "test@example.com")
    env.setdefault("GIT_CONFIG_GLOBAL", os.path.devnull)
    env.setdefault("GIT_CONFIG_SYSTEM", os.path.devnull)
    subprocess.run(
        [GIT_BIN, *args],
        cwd=repo,
        check=True,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


@pytest.fixture
def real_repo(tmp_path) -> str:
    """A real git repository with two branches and one extra worktree.

    Layout:
        <repo>/.git
        <repo>/  -- HEAD on main
        <repo>/wt/topic  -- worktree on branch 'topic'
        branches: main (current), topic (with worktree), stale (no worktree)
    """
    if GIT_BIN is None:
        pytest.skip("git binary not on PATH")
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    _run_git_sync(repo, "init", "-b", "main")
    # Ensure user identity is set on the repo as well, for safety.
    _run_git_sync(repo, "config", "user.email", "test@example.com")
    _run_git_sync(repo, "config", "user.name", "Test")
    # Initial commit.
    (tmp_path / "repo" / "README.md").write_text("hello")
    _run_git_sync(repo, "add", "README.md")
    _run_git_sync(repo, "commit", "-m", "initial")
    # Create two extra branches.
    _run_git_sync(repo, "branch", "topic")
    _run_git_sync(repo, "branch", "stale")
    # Add a worktree for 'topic' under <repo>/wt/topic.
    wt_path = str(tmp_path / "repo" / "wt" / "topic")
    _run_git_sync(repo, "worktree", "add", wt_path, "topic")
    return repo


@pytest.fixture
async def app_with_git(tmp_path, real_repo):
    cfg = AgentConfig(
        jwt_secret_key=TEST_JWT_SECRET,
        user_store_path=str(tmp_path / "users.json"),
        projects_store_path=str(tmp_path / "projects.json"),
        max_sessions=4,
        log_level="DEBUG",
    )
    application = create_app(config=cfg)
    application.state.user_store.add_user(TEST_USERNAME, TEST_PASSWORD)
    store = application.state.projects_store
    store.create(ProjectRecord(name="alpha", path=real_repo))
    # Seed a non-git project to exercise the 400 path.
    non_git = str(tmp_path / "plain")
    os.makedirs(non_git, exist_ok=True)
    store.create(ProjectRecord(name="plain", path=non_git))
    yield application
    await application.state.session_manager.shutdown()


@pytest.fixture
async def client_git(app_with_git):
    transport = ASGITransport(app=app_with_git)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def auth_headers_g(client_git) -> dict[str, str]:
    resp = await client_git.post(
        "/api/auth/token",
        json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


class TestBuildItems:
    def test_branch_with_worktree_merge(self):
        branches = [
            BranchInfo(
                name="topic", sha="sha", upstream="origin/topic",
                ahead=2, behind=1, gone=False, is_current=False,
            ),
        ]
        worktrees = [
            WorktreeInfo(
                path="/repo/wt/topic", head="sha", branch="refs/heads/topic",
                detached=False, locked=False, locked_reason=None,
                prunable=False, prunable_reason=None,
            ),
        ]
        items = _build_items(branches, worktrees)
        assert len(items) == 1
        item = items[0]
        assert item.kind == "branch_with_worktree"
        assert item.branch == "topic"
        assert item.worktree_path == "/repo/wt/topic"
        assert item.upstream == "origin/topic"
        assert item.ahead == 2
        assert item.behind == 1

    def test_branch_only_when_no_worktree(self):
        branches = [
            BranchInfo(
                name="stale", sha="sha", upstream=None,
                ahead=0, behind=0, gone=True, is_current=False,
            ),
        ]
        items = _build_items(branches, [])
        assert len(items) == 1
        item = items[0]
        assert item.kind == "branch_only"
        assert item.branch == "stale"
        assert item.worktree_path is None
        assert item.gone is True

    def test_orphan_worktree_when_no_matching_branch(self):
        # Worktree whose branch ref is not present in `for-each-ref refs/heads/`.
        worktrees = [
            WorktreeInfo(
                path="/repo/wt/orphan", head="sha", branch="refs/heads/orphan",
                detached=False, locked=False, locked_reason=None,
                prunable=True, prunable_reason="gitdir missing",
            ),
        ]
        items = _build_items([], worktrees)
        assert len(items) == 1
        item = items[0]
        assert item.kind == "orphan_worktree"
        assert item.worktree_path == "/repo/wt/orphan"
        assert item.branch == "orphan"
        assert item.prunable is True

    def test_detached_worktree_classified_as_orphan(self):
        # Detached HEADs have no branch ref; must not be matched against any
        # branch and must surface as orphan_worktree with detached=True.
        worktrees = [
            WorktreeInfo(
                path="/repo/wt/detached", head="sha", branch=None,
                detached=True, locked=False, locked_reason=None,
                prunable=False, prunable_reason=None,
            ),
        ]
        items = _build_items([], worktrees)
        assert len(items) == 1
        assert items[0].kind == "orphan_worktree"
        assert items[0].detached is True

    def test_locked_reason_propagated(self):
        branches = [
            BranchInfo(
                name="topic", sha="sha", upstream=None,
                ahead=0, behind=0, gone=False, is_current=False,
            ),
        ]
        worktrees = [
            WorktreeInfo(
                path="/repo/wt/topic", head="sha", branch="refs/heads/topic",
                detached=False, locked=True, locked_reason="manual lock",
                prunable=False, prunable_reason=None,
            ),
        ]
        items = _build_items(branches, worktrees)
        assert items[0].locked is True
        assert items[0].locked_reason == "manual lock"

    def test_is_current_propagated_from_branch_info(self):
        """Regression: ``branch_with_worktree`` must not hardcode ``is_current``.

        The Android client uses ``isDeletable = !is_current`` to gate
        long-press selection — a hardcoded True breaks the entire delete
        flow for any branch checked out in a worktree, which is the
        primary use case (cleaning up auto-scan feature branches).
        """
        branches = [
            BranchInfo(
                name="main", sha="s1", upstream=None,
                ahead=0, behind=0, gone=False, is_current=True,
            ),
            BranchInfo(
                name="topic", sha="s2", upstream=None,
                ahead=0, behind=0, gone=False, is_current=False,
            ),
        ]
        worktrees = [
            WorktreeInfo(
                path="/repo", head="s1", branch="refs/heads/main",
                detached=False, locked=False, locked_reason=None,
                prunable=False, prunable_reason=None,
            ),
            WorktreeInfo(
                path="/repo/wt/topic", head="s2", branch="refs/heads/topic",
                detached=False, locked=False, locked_reason=None,
                prunable=False, prunable_reason=None,
            ),
        ]
        items = _build_items(branches, worktrees)
        by_branch = {it.branch: it for it in items if it.branch}
        assert by_branch["main"].is_current is True
        assert by_branch["topic"].is_current is False


class TestBusyHolders:


    async def test_session_holder_matches_realpath_normalized(self, tmp_path):
        # The busy-guard must compare paths after realpath+normcase normalization
        # so trailing slashes / case differences don't false-negative.
        wt_dir = tmp_path / "wt"
        wt_dir.mkdir()
        canonical = os.path.normcase(os.path.realpath(str(wt_dir)))

        # Build a fake session_manager.list_sessions() that returns one
        # session whose cwd points to the same path (with a trailing slash to
        # exercise the normalizer).
        session = types.SimpleNamespace(
            session_id="s-1",
            cwd=str(wt_dir) + os.sep,
        )
        session_manager = types.SimpleNamespace(list_sessions=lambda: [session])

        result = await _busy_holders(
            {canonical},
            session_manager=session_manager,
            autoscan_manager=None,
        )
        assert len(result) == 1
        assert result[0].kind == "session"
        assert result[0].session_id == "s-1"

    async def test_session_with_unrelated_cwd_does_not_match(self, tmp_path):
        canonical = os.path.normcase(os.path.realpath(str(tmp_path / "wt")))
        session = types.SimpleNamespace(
            session_id="s-2",
            cwd="/totally/unrelated/path",
        )
        session_manager = types.SimpleNamespace(list_sessions=lambda: [session])

        result = await _busy_holders(
            {canonical},
            session_manager=session_manager,
            autoscan_manager=None,
        )
        assert result == []


    async def test_autoscan_workitem_match(self, tmp_path):
        wt_dir = tmp_path / "wt-autoscan"
        wt_dir.mkdir()
        canonical = os.path.normcase(os.path.realpath(str(wt_dir)))

        workitem = types.SimpleNamespace(worktree_path=str(wt_dir))

        async def list_all_workitems():
            return {"alpha": [workitem]}

        autoscan_manager = types.SimpleNamespace(
            list_all_workitems=list_all_workitems,
        )

        result = await _busy_holders(
            {canonical},
            session_manager=None,
            autoscan_manager=autoscan_manager,
        )
        assert len(result) == 1
        assert result[0].kind == "auto_scan"
        assert result[0].project_name == "alpha"

    async def test_session_manager_exception_is_swallowed(self):
        # The busy-guard must not crash if the session manager misbehaves; it
        # simply contributes zero holders from that source.
        def boom():
            raise RuntimeError("session manager unavailable")
        session_manager = types.SimpleNamespace(list_sessions=boom)

        result = await _busy_holders(
            {"/some/canonical/path"},
            session_manager=session_manager,
            autoscan_manager=None,
        )
        assert result == []


class _FakeManager:
    """Minimal stand-in for WorktreeManager exposing only what the service
    calls. Delete operations are recorded so tests can assert ordering."""

    def __init__(self) -> None:
        self.delete_branch_calls: list[tuple[str, str, bool]] = []
        self.remove_worktree_calls: list[tuple[str, str, bool]] = []
        self.delete_branch_error: Exception | None = None
        self.remove_worktree_error: Exception | None = None

    async def delete_branch(
        self, project_root: str, name: str, *, force: bool = False,
    ) -> None:
        self.delete_branch_calls.append((project_root, name, force))
        if self.delete_branch_error is not None:
            raise self.delete_branch_error

    async def remove_worktree(
        self, project_root: str, path: str, *, force: bool = False,
    ) -> None:
        self.remove_worktree_calls.append((project_root, path, force))
        if self.remove_worktree_error is not None:
            raise self.remove_worktree_error


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    def publish(self, topic: str, payload: dict) -> None:
        self.published.append((topic, payload))


class TestDeleteGitItems:
    async def test_busy_guard_short_circuits_without_calling_manager(self, tmp_path):
        wt_dir = tmp_path / "wt"
        wt_dir.mkdir()
        manager = _FakeManager()
        bus = _FakeBus()
        # A session holds the worktree path.
        session = types.SimpleNamespace(session_id="s-1", cwd=str(wt_dir))
        session_manager = types.SimpleNamespace(list_sessions=lambda: [session])

        request = GitDeleteRequest(items=[
            GitDeleteItem(
                kind="orphan_worktree", worktree_path=str(wt_dir),
            ),
        ])

        with pytest.raises(GitItemBusyError) as exc_info:
            await delete_git_items(
                request,
                project_name="alpha",
                project_root=str(tmp_path),
                manager=manager,
                session_manager=session_manager,
                autoscan_manager=None,
                event_bus=bus,
                actor="tester",
            )
        assert any(h.kind == "session" for h in exc_info.value.busy_by)
        # Manager and bus must both be untouched.
        assert manager.remove_worktree_calls == []
        assert manager.delete_branch_calls == []
        assert bus.published == []

    async def test_branch_only_happy_path_publishes_event(self, tmp_path):
        manager = _FakeManager()
        bus = _FakeBus()
        request = GitDeleteRequest(items=[
            GitDeleteItem(kind="branch_only", branch="feature/x"),
        ])

        response = await delete_git_items(
            request,
            project_name="alpha",
            project_root=str(tmp_path),
            manager=manager,
            session_manager=None,
            autoscan_manager=None,
            event_bus=bus,
            actor="tester",
        )

        assert manager.delete_branch_calls == [(str(tmp_path), "feature/x", False)]
        assert manager.remove_worktree_calls == []
        assert len(response.results) == 1
        assert response.results[0].ok is True
        assert response.results[0].kind == "branch_only"
        # git.changed must fire after a successful delete. ``tmp_path`` is
        # not a git repo, so the resolved toplevel/common_dir come back None.
        assert bus.published == [
            (
                "git.changed",
                {"project_name": "alpha", "toplevel": None, "common_dir": None},
            ),
        ]

    async def test_branch_only_unmerged_returns_force_required(self, tmp_path):
        manager = _FakeManager()
        manager.delete_branch_error = BranchDeleteError(
            name="feature/x", stderr="not fully merged", unmerged=True,
        )
        bus = _FakeBus()
        request = GitDeleteRequest(items=[
            GitDeleteItem(kind="branch_only", branch="feature/x"),
        ])

        response = await delete_git_items(
            request,
            project_name="alpha",
            project_root=str(tmp_path),
            manager=manager,
            session_manager=None,
            autoscan_manager=None,
            event_bus=bus,
            actor="tester",
        )
        assert response.results[0].ok is False
        assert response.results[0].error == "force_required"
        # No state changed → must NOT publish git.changed.
        assert bus.published == []

    async def test_branch_with_worktree_removes_then_deletes_in_order(self, tmp_path):
        # Order matters: removing the worktree must happen BEFORE the branch
        # delete, otherwise git refuses to drop a branch that's still checked out.
        # The worktree directory must exist on disk so the ``worktree_already_gone``
        # guard in the service does not skip the remove_worktree call.
        wt_dir = tmp_path / "wt" / "x"
        wt_dir.mkdir(parents=True)
        wt_path = str(wt_dir)

        manager = _FakeManager()
        bus = _FakeBus()
        request = GitDeleteRequest(items=[
            GitDeleteItem(
                kind="branch_with_worktree",
                branch="feature/x",
                worktree_path=wt_path,
            ),
        ])

        response = await delete_git_items(
            request,
            project_name="alpha",
            project_root=str(tmp_path),
            manager=manager,
            session_manager=None,
            autoscan_manager=None,
            event_bus=bus,
            actor="tester",
        )
        assert manager.remove_worktree_calls == [(str(tmp_path), wt_path, False)]
        assert manager.delete_branch_calls == [(str(tmp_path), "feature/x", False)]
        assert response.results[0].ok is True
        # ``tmp_path`` is not a git repo, so toplevel/common_dir resolve None.
        assert bus.published == [
            (
                "git.changed",
                {"project_name": "alpha", "toplevel": None, "common_dir": None},
            ),
        ]

    async def test_orphan_worktree_lock_error_maps_to_locked(self, tmp_path):
        manager = _FakeManager()
        manager.remove_worktree_error = WorktreeRemoveError(
            path="/repo/wt/x", stderr="fatal: '/repo/wt/x' is locked",
        )
        bus = _FakeBus()
        request = GitDeleteRequest(items=[
            GitDeleteItem(
                kind="orphan_worktree", worktree_path="/repo/wt/x",
            ),
        ])
        response = await delete_git_items(
            request,
            project_name="alpha",
            project_root=str(tmp_path),
            manager=manager,
            session_manager=None,
            autoscan_manager=None,
            event_bus=bus,
            actor="tester",
        )
        assert response.results[0].ok is False
        assert response.results[0].error == "locked"
        assert bus.published == []

    async def test_orphan_worktree_dirty_returns_force_required(self, tmp_path):
        # A dirty worktree (modified/untracked files) must map to force_required
        # so the client can offer a force-delete confirmation, mirroring the
        # unmerged-branch flow.
        manager = _FakeManager()
        manager.remove_worktree_error = WorktreeRemoveError(
            path="/repo/wt/x",
            stderr="fatal: '/repo/wt/x' contains modified or untracked files",
            dirty=True,
        )
        bus = _FakeBus()
        request = GitDeleteRequest(items=[
            GitDeleteItem(kind="orphan_worktree", worktree_path="/repo/wt/x"),
        ])
        response = await delete_git_items(
            request,
            project_name="alpha",
            project_root=str(tmp_path),
            manager=manager,
            session_manager=None,
            autoscan_manager=None,
            event_bus=bus,
            actor="tester",
        )
        assert response.results[0].ok is False
        assert response.results[0].error == "force_required"
        assert bus.published == []

    async def test_orphan_worktree_force_forwarded_to_manager(self, tmp_path):
        # force=True on the request must reach remove_worktree so the retry
        # actually passes --force to git.
        manager = _FakeManager()
        bus = _FakeBus()
        request = GitDeleteRequest(items=[
            GitDeleteItem(
                kind="orphan_worktree", worktree_path="/repo/wt/x", force=True,
            ),
        ])
        response = await delete_git_items(
            request,
            project_name="alpha",
            project_root=str(tmp_path),
            manager=manager,
            session_manager=None,
            autoscan_manager=None,
            event_bus=bus,
            actor="tester",
        )
        assert manager.remove_worktree_calls == [(str(tmp_path), "/repo/wt/x", True)]
        assert response.results[0].ok is True
        # ``tmp_path`` is not a git repo, so toplevel/common_dir resolve None.
        assert bus.published == [
            (
                "git.changed",
                {"project_name": "alpha", "toplevel": None, "common_dir": None},
            ),
        ]

    async def test_invalid_branch_only_payload_returns_invalid(self, tmp_path):
        manager = _FakeManager()
        request = GitDeleteRequest(items=[
            GitDeleteItem(kind="branch_only", branch=None),
        ])
        response = await delete_git_items(
            request,
            project_name="alpha",
            project_root=str(tmp_path),
            manager=manager,
            session_manager=None,
            autoscan_manager=None,
            event_bus=None,
            actor="tester",
        )
        assert response.results[0].ok is False
        assert response.results[0].error == "invalid"
        assert manager.delete_branch_calls == []

    async def test_batch_partial_failure_publishes_only_when_anything_changed(
        self, tmp_path,
    ):
        # Two items: one succeeds, one fails. git.changed must still publish
        # because at least one delete completed.
        manager = _FakeManager()
        bus = _FakeBus()
        # First call (delete_branch for "ok") succeeds; second call must fail.
        # We toggle the error after the first call by clearing it via a side
        # effect using a counter.
        calls = {"n": 0}
        original = manager.delete_branch

        async def patched_delete_branch(root, name, *, force=False):
            calls["n"] += 1
            if calls["n"] == 2:
                raise BranchDeleteError(
                    name=name, stderr="not fully merged", unmerged=True,
                )
            await original(root, name, force=force)

        manager.delete_branch = patched_delete_branch  # type: ignore[method-assign]

        request = GitDeleteRequest(items=[
            GitDeleteItem(kind="branch_only", branch="ok"),
            GitDeleteItem(kind="branch_only", branch="bad"),
        ])
        response = await delete_git_items(
            request,
            project_name="alpha",
            project_root=str(tmp_path),
            manager=manager,
            session_manager=None,
            autoscan_manager=None,
            event_bus=bus,
            actor="tester",
        )
        results = response.results
        assert len(results) == 2
        assert results[0].ok is True
        assert results[1].ok is False
        assert results[1].error == "force_required"
        # Exactly one publish, since at least one delete succeeded. ``tmp_path``
        # is not a git repo, so toplevel/common_dir resolve None.
        assert bus.published == [
            (
                "git.changed",
                {"project_name": "alpha", "toplevel": None, "common_dir": None},
            ),
        ]


@requires_git
class TestGitRouterEndpoints:
    async def test_get_items_unknown_project_returns_404(
        self, client_git, auth_headers_g,
    ):
        resp = await client_git.get(
            "/api/projects/does-not-exist/git/items",
            headers=auth_headers_g,
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    async def test_get_items_non_git_project_returns_400(
        self, client_git, auth_headers_g,
    ):
        resp = await client_git.get(
            "/api/projects/plain/git/items",
            headers=auth_headers_g,
        )
        assert resp.status_code == 400
        assert "not a git repository" in resp.json()["detail"]

    async def test_get_items_requires_auth(self, client_git):
        resp = await client_git.get("/api/projects/alpha/git/items")
        assert resp.status_code in (401, 403)

    async def test_get_items_returns_real_repo_shape(
        self, client_git, auth_headers_g,
    ):
        # `alpha` was set up by real_repo fixture: branches main / topic / stale,
        # one extra worktree (wt/topic). Plus the main repo as its own worktree.
        resp = await client_git.get(
            "/api/projects/alpha/git/items",
            headers=auth_headers_g,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        items = body["items"]
        kinds = [i["kind"] for i in items]
        # main and topic both have worktrees → 2 branch_with_worktree.
        # stale has no worktree → 1 branch_only.
        assert kinds.count("branch_with_worktree") == 2
        assert kinds.count("branch_only") == 1
        branches = {
            i["branch"] for i in items if i.get("kind") != "orphan_worktree"
        }
        assert {"main", "topic", "stale"} <= branches


    async def test_post_items_delete_publishes_git_changed_event(
        self, client_git, auth_headers_g, app_with_git, real_repo,
    ):
        # Subscribe to the event bus before the delete so we can capture the
        # publish without racing the handler.
        bus = app_with_git.state.event_bus
        queue = bus.subscribe()

        try:
            resp = await client_git.post(
                "/api/projects/alpha/git/items:delete",
                headers=auth_headers_g,
                json={"items": [{"kind": "branch_only", "branch": "stale"}]},
            )
            assert resp.status_code == 200

            # The server publishes synchronously inside the request handler,
            # so the queue must have the event by now.
            event = await asyncio.wait_for(queue.get(), timeout=2.0)
            assert event.type == "git.changed"
            # "alpha" is registered against ``real_repo``, a real repo, so
            # toplevel/common_dir resolve to its actual realpath — computed
            # via the same helper the production code calls.
            toplevel, common_dir = await resolve_repo_dirs(real_repo)
            assert event.payload == {
                "project_name": "alpha",
                "toplevel": toplevel,
                "common_dir": common_dir,
            }
            assert toplevel is not None
            assert common_dir is not None
        finally:
            bus.unsubscribe(queue)

    async def test_post_items_delete_busy_returns_409_with_busy_by(
        self, client_git, auth_headers_g, app_with_git, real_repo,
    ):
        # Force a busy holder by stubbing session_manager.list_sessions to
        # return a fake session whose cwd matches the topic worktree path.
        topic_wt = os.path.join(real_repo, "wt", "topic")
        fake_session = types.SimpleNamespace(
            session_id="s-busy",
            cwd=topic_wt,
        )

        original_list = app_with_git.state.session_manager.list_sessions
        app_with_git.state.session_manager.list_sessions = (  # type: ignore[method-assign]
            lambda: [fake_session]
        )
        try:
            resp = await client_git.post(
                "/api/projects/alpha/git/items:delete",
                headers=auth_headers_g,
                json={"items": [{
                    "kind": "branch_with_worktree",
                    "branch": "topic",
                    "worktree_path": topic_wt,
                }]},
            )
        finally:
            app_with_git.state.session_manager.list_sessions = original_list  # type: ignore[method-assign]

        assert resp.status_code == 409
        body = resp.json()
        # FastAPI wraps detail under "detail"; BusyByResponse goes under it.
        detail = body["detail"]
        assert "busy_by" in detail
        assert any(h["kind"] == "session" for h in detail["busy_by"])
        assert any(h.get("session_id") == "s-busy" for h in detail["busy_by"])

    async def test_post_items_delete_dirty_worktree_force_required_then_force(
        self, client_git, auth_headers_g, real_repo,
    ):
        # An untracked file makes ``git worktree remove`` refuse without
        # --force. The first (force=false) attempt must surface force_required;
        # retrying the same item with force=true must remove the worktree and
        # delete the branch in one shot.
        topic_wt = os.path.join(real_repo, "wt", "topic")
        with open(os.path.join(topic_wt, "scratch.txt"), "w", encoding="utf-8") as fh:
            fh.write("uncommitted work")

        item = {
            "kind": "branch_with_worktree",
            "branch": "topic",
            "worktree_path": topic_wt,
        }
        resp = await client_git.post(
            "/api/projects/alpha/git/items:delete",
            headers=auth_headers_g,
            json={"items": [item]},
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]
        assert result["ok"] is False
        assert result["error"] == "force_required"
        assert os.path.exists(topic_wt)

        resp2 = await client_git.post(
            "/api/projects/alpha/git/items:delete",
            headers=auth_headers_g,
            json={"items": [{**item, "force": True}]},
        )
        assert resp2.status_code == 200, resp2.text
        assert resp2.json()["results"][0]["ok"] is True

        # The worktree and its branch must both be gone from the listing.
        resp3 = await client_git.get(
            "/api/projects/alpha/git/items",
            headers=auth_headers_g,
        )
        items = resp3.json()["items"]
        assert all(i.get("worktree_path") != topic_wt for i in items)
        assert all(i.get("branch") != "topic" for i in items)


class TestGitBranchEndpoints:
    """The routes added: ``?remote=true`` on the listing, ``POST
    /branches`` and ``POST /checkout``, with the 200 / 400 / 404 / 409 shapes
    the three clients branch on."""

    async def _branches(self, client, headers, project="alpha"):
        resp = await client.get(f"/api/projects/{project}/git/items", headers=headers)
        assert resp.status_code == 200, resp.text
        return {
            i["branch"]: i for i in resp.json()["items"] if i.get("branch") is not None
        }

    async def test_get_items_remote_true_adds_tip_fields_and_remote_rows(
        self, client_git, auth_headers_g, app_with_git, tmp_path,
    ):
        clone = clone_repo(seed_remote(str(tmp_path)), str(tmp_path / "beta"))
        app_with_git.state.projects_store.create(ProjectRecord(name="beta", path=clone))

        plain = await client_git.get(
            "/api/projects/beta/git/items", headers=auth_headers_g,
        )
        remote = await client_git.get(
            "/api/projects/beta/git/items", params={"remote": "true"}, headers=auth_headers_g,
        )

        assert plain.status_code == 200 and remote.status_code == 200
        assert [i["kind"] for i in plain.json()["items"]] == ["branch_with_worktree"]
        assert [(i["kind"], i["branch"], i["remote"]) for i in remote.json()["items"]] == [
            ("branch_with_worktree", "main", None),
            ("remote_branch", "origin/main", "origin"),
        ]
        for item in remote.json()["items"]:
            assert item["subject"] == "initial"
            assert isinstance(item["committed_at"], str)

    async def test_get_worktree_bases_lists_default_and_last_used(
        self, client_git, auth_headers_g, app_with_git, tmp_path,
    ):
        clone = clone_repo(seed_remote(str(tmp_path)), str(tmp_path / "beta"))
        store = app_with_git.state.projects_store
        store.create(ProjectRecord(name="beta", path=clone))
        store.set_last_worktree_base("beta", "main")

        resp = await client_git.get(
            "/api/projects/beta/git/worktree-bases", headers=auth_headers_g,
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["default"] == "origin/main"
        assert body["last_used"] == "main"
        assert [(r["ref"], r["kind"]) for r in body["refs"]][0] == ("origin/main", "remote")
        assert {r["ref"] for r in body["refs"]} == {"origin/main", "main"}


    async def test_post_items_delete_reports_a_remote_branch_row_with_no_such_remote(
        self, client_git, auth_headers_g,
    ):
        """``alpha`` has no ``origin`` remote configured, so the delete is
        attempted and reported as a per-item failure inside the normal 200
        response."""
        resp = await client_git.post(
            "/api/projects/alpha/git/items:delete",
            headers=auth_headers_g,
            json={"items": [{"kind": "remote_branch", "branch": "origin/main"}]},
        )
        assert resp.status_code == 200, resp.text
        assert [
            (r["kind"], r["target"], r["ok"], r["error"])
            for r in resp.json()["results"]
        ] == [("remote_branch", "origin/main", False, "not_found")]

    async def test_post_items_delete_removes_a_remote_branch_on_the_remote(
        self, client_git, auth_headers_g, app_with_git, tmp_path,
    ):
        clone = clone_repo(seed_remote(str(tmp_path)), str(tmp_path / "beta"))
        _run_git_sync(clone, "push", "-q", "origin", "main:refs/heads/feature/x")
        app_with_git.state.projects_store.create(ProjectRecord(name="beta", path=clone))

        resp = await client_git.post(
            "/api/projects/beta/git/items:delete",
            headers=auth_headers_g,
            json={"items": [{"kind": "remote_branch", "branch": "origin/feature/x"}]},
        )

        assert resp.status_code == 200, resp.text
        assert [
            (r["kind"], r["target"], r["ok"], r["error"]) for r in resp.json()["results"]
        ] == [("remote_branch", "origin/feature/x", True, None)]
        listed = await client_git.get(
            "/api/projects/beta/git/items", params={"remote": "true"}, headers=auth_headers_g,
        )
        assert [
            i["branch"] for i in listed.json()["items"] if i["kind"] == "remote_branch"
        ] == ["origin/main"]

    async def test_post_branches_creates_and_lists_the_branch(
        self, client_git, auth_headers_g,
    ):
        resp = await client_git.post(
            "/api/projects/alpha/git/branches",
            headers=auth_headers_g,
            json={"name": "feature/new"},
        )

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"branch": "feature/new", "checked_out": False, "created": True}
        branches = await self._branches(client_git, auth_headers_g)
        assert branches["feature/new"]["kind"] == "branch_only"
        assert branches["main"]["is_current"] is True

    async def test_post_branches_with_checkout_switches_the_root(
        self, client_git, auth_headers_g,
    ):
        resp = await client_git.post(
            "/api/projects/alpha/git/branches",
            headers=auth_headers_g,
            json={"name": "feature/new", "start_point": "stale", "checkout": True},
        )

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"branch": "feature/new", "checked_out": True, "created": True}
        branches = await self._branches(client_git, auth_headers_g)
        assert branches["feature/new"]["is_current"] is True
        assert branches["main"]["is_current"] is False

    @pytest.mark.parametrize(
        ("body", "status", "detail"),
        [
            ({"name": "-x"}, 400, {"code": "bad_branch_name"}),
            ({"name": "stale"}, 409, {"code": "exists", "message": "stale"}),
            (
                {"name": "n", "start_point": "nope"},
                409,
                {"code": "unknown_start_point", "message": "nope"},
            ),
        ],
    )
    async def test_post_branches_refusals(
        self, client_git, auth_headers_g, body, status, detail,
    ):
        resp = await client_git.post(
            "/api/projects/alpha/git/branches", headers=auth_headers_g, json=body,
        )

        assert resp.status_code == status, resp.text
        assert resp.json() == {"detail": detail}
        assert set(await self._branches(client_git, auth_headers_g)) == {"main", "topic", "stale"}

    async def test_post_branches_requires_auth(self, client_git):
        resp = await client_git.post(
            "/api/projects/alpha/git/branches", json={"name": "x"},
        )
        assert resp.status_code in (401, 403)

    async def test_post_checkout_switches_the_root_and_publishes_git_changed(
        self, client_git, auth_headers_g, app_with_git, real_repo,
    ):
        bus = app_with_git.state.event_bus
        queue = bus.subscribe()
        try:
            resp = await client_git.post(
                "/api/projects/alpha/git/checkout",
                headers=auth_headers_g,
                json={"ref": "stale"},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json() == {"branch": "stale", "checked_out": True, "created": False}

            event = await asyncio.wait_for(queue.get(), timeout=2.0)
            toplevel, common_dir = await resolve_repo_dirs(real_repo)
            assert event.type == "git.changed"
            assert event.payload == {
                "project_name": "alpha",
                "toplevel": toplevel,
                "common_dir": common_dir,
            }
        finally:
            bus.unsubscribe(queue)

        branches = await self._branches(client_git, auth_headers_g)
        assert branches["stale"]["is_current"] is True
        assert branches["main"]["is_current"] is False

    async def test_post_checkout_unknown_ref_is_404_with_a_coded_detail(
        self, client_git, auth_headers_g,
    ):
        resp = await client_git.post(
            "/api/projects/alpha/git/checkout",
            headers=auth_headers_g,
            json={"ref": "nope"},
        )

        assert resp.status_code == 404
        assert resp.json() == {"detail": {"code": "ref_not_found"}}

    async def test_post_checkout_branch_in_another_worktree_is_409(
        self, client_git, auth_headers_g, real_repo,
    ):
        resp = await client_git.post(
            "/api/projects/alpha/git/checkout",
            headers=auth_headers_g,
            json={"ref": "topic"},
        )

        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "checked_out_elsewhere"
        assert os.path.realpath(detail["message"]) == os.path.realpath(
            os.path.join(real_repo, "wt", "topic")
        )
        assert (await self._branches(client_git, auth_headers_g))["main"]["is_current"] is True

    async def test_post_checkout_busy_returns_409_with_busy_by(
        self, client_git, auth_headers_g, app_with_git, real_repo,
    ):
        fake_session = types.SimpleNamespace(session_id="s-busy", cwd=real_repo)
        original_list = app_with_git.state.session_manager.list_sessions
        app_with_git.state.session_manager.list_sessions = (  # type: ignore[method-assign]
            lambda: [fake_session]
        )
        try:
            resp = await client_git.post(
                "/api/projects/alpha/git/checkout",
                headers=auth_headers_g,
                json={"ref": "stale"},
            )
        finally:
            app_with_git.state.session_manager.list_sessions = original_list  # type: ignore[method-assign]

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert [(h["kind"], h["session_id"]) for h in detail["busy_by"]] == [("session", "s-busy")]
        assert "code" not in detail
        assert (await self._branches(client_git, auth_headers_g))["main"]["is_current"] is True

    async def test_post_checkout_session_in_a_linked_worktree_does_not_block(
        self, client_git, auth_headers_g, app_with_git, real_repo,
    ):
        topic_wt = os.path.join(real_repo, "wt", "topic")
        fake_session = types.SimpleNamespace(session_id="s-linked", cwd=topic_wt)
        original_list = app_with_git.state.session_manager.list_sessions
        app_with_git.state.session_manager.list_sessions = (  # type: ignore[method-assign]
            lambda: [fake_session]
        )
        try:
            resp = await client_git.post(
                "/api/projects/alpha/git/checkout",
                headers=auth_headers_g,
                json={"ref": "stale"},
            )
        finally:
            app_with_git.state.session_manager.list_sessions = original_list  # type: ignore[method-assign]

        assert resp.status_code == 200, resp.text
        assert (await self._branches(client_git, auth_headers_g))["stale"]["is_current"] is True
