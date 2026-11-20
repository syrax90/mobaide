"""``stop_session`` with ``remove_worktree``: which delete item the stop asks
the background delete job for, and the cases where it asks for none. The job
itself is covered in ``test_git_delete_jobs.py``; here the registry is a fake
that records the batches it was given."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from mobaide.agent.git.changes_service import GitRefusedError
from mobaide.agent.git.models import GitDeleteItem
from mobaide.agent.sessions import stop as stop_module
from mobaide.agent.sessions.stop import stop_session
from mobaide.core.models import SessionInfo, SessionState


class _FakeSession:
    def __init__(self, info: SessionInfo) -> None:
        self._info = info

    def to_info(self) -> SessionInfo:
        return self._info


class _FakeSessionManager:
    def __init__(self, *infos: SessionInfo) -> None:
        self.sessions = {info.session_id: _FakeSession(info) for info in infos}
        self.stopped: list[tuple[str, bool]] = []

    async def get_session(self, session_id: str):
        return self.sessions.get(session_id)

    async def stop_session(self, session_id: str, *, force: bool = False) -> None:
        if session_id not in self.sessions:
            raise KeyError(session_id)
        self.stopped.append((session_id, force))


class _FakeWorktreeManager:
    """Only passed through to the delete job; ``stop_session`` never calls it."""


class _FakeStore:
    def __init__(self, *workspaces) -> None:
        self._by_name = {w.name: w for w in workspaces}

    def get(self, name: str):
        return self._by_name.get(name)


class _FakeRegistry:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.started: list[dict] = []

    async def start(self, request, **kwargs):
        if self.error is not None:
            raise self.error
        self.started.append({"items": list(request.items), **kwargs})


@pytest.fixture
def registry(monkeypatch) -> _FakeRegistry:
    fake = _FakeRegistry()
    monkeypatch.setattr(stop_module, "delete_job_registry", fake)
    return fake


@pytest.fixture
def layout(tmp_path):
    root = tmp_path / "repo"
    worktree = tmp_path / "worktrees" / "repo" / "feature-x"
    root.mkdir()
    worktree.mkdir(parents=True)
    return SimpleNamespace(root=str(root), worktree=str(worktree))


def _info(cwd: str, worktree_branch: str | None = "feature/x", project: str = "repo") -> SessionInfo:
    return SessionInfo(
        session_id="s1",
        state=SessionState.RUNNING,
        created_at=datetime(2026, 11, 19, tzinfo=timezone.utc),
        pid=42,
        command=["claude"],
        cwd=cwd,
        project_name=project,
        worktree_branch=worktree_branch,
    )


def _app_state(layout, worktree_manager, session_manager=None):
    return SimpleNamespace(
        workspaces_store=_FakeStore(SimpleNamespace(name="repo", path=layout.root)),
        worktree_manager=worktree_manager,
        session_manager=session_manager,
        autoscan_manager=None,
        event_bus=None,
    )


async def _stop(sessions, app_state, *, remove_worktree=True, force=False):
    await stop_session(
        sessions,
        "s1",
        force=force,
        remove_worktree=remove_worktree,
        app_state=app_state,
        actor="alice",
    )


async def test_removes_the_worktree_and_the_branch_the_dialog_named(registry, layout):
    # The user switched branches inside the worktree after the session started:
    # only the recorded branch was confirmed, so that is the one deleted.
    sessions = _FakeSessionManager(_info(layout.worktree, "feature/x"))
    worktrees = _FakeWorktreeManager()

    await _stop(sessions, _app_state(layout, worktrees, sessions), force=True)

    assert sessions.stopped == [("s1", True)]
    assert len(registry.started) == 1
    job = registry.started[0]
    assert job["items"] == [
        GitDeleteItem(
            kind="branch_with_worktree", branch="feature/x",
            worktree_path=layout.worktree, force=True,
        ),
    ]
    assert job["workspace_name"] == "repo"
    assert job["workspace_root"] == layout.root
    assert job["manager"] is worktrees
    assert job["session_manager"] is sessions
    assert job["actor"] == "alice"


@pytest.mark.parametrize(
    ("remove_worktree", "where", "worktree_branch", "project"),
    [
        pytest.param(False, "worktree", "feature/x", "repo", id="not_requested"),
        pytest.param(True, "worktree", None, "repo", id="session_without_worktree"),
        pytest.param(True, "root", "main", "repo", id="cwd_is_the_main_checkout"),
        pytest.param(True, "worktree", "feature/x", "deleted", id="workspace_not_registered"),
    ],
)
async def test_stops_without_removing_anything(
    registry, layout, remove_worktree, where, worktree_branch, project,
):
    cwd = layout.root if where == "root" else layout.worktree
    sessions = _FakeSessionManager(_info(cwd, worktree_branch, project=project))

    await _stop(
        sessions, _app_state(layout, _FakeWorktreeManager()),
        remove_worktree=remove_worktree,
    )

    assert sessions.stopped == [("s1", False)]
    assert registry.started == []


class _FakeAutoScanManager:
    def __init__(self) -> None:
        self.released: list[tuple[str, str]] = []

    async def release_worktree(self, workspace_name: str, worktree_path: str) -> int:
        self.released.append((workspace_name, worktree_path))
        return 1


def _autoscan_info(cwd: str) -> SessionInfo:
    # Auto Scan launches with a cwd, not a project_name: the workspace comes
    # from worktree_workspace.
    return replace(_info(cwd, "feature/mobaide/cla-8"), project_name=None, worktree_workspace="repo")


async def test_auto_scan_session_removes_its_worktree_and_releases_the_work_item(
    registry, layout,
):
    sessions = _FakeSessionManager(_autoscan_info(layout.worktree))
    autoscan = _FakeAutoScanManager()
    app_state = _app_state(layout, _FakeWorktreeManager(), sessions)
    app_state.autoscan_manager = autoscan

    await _stop(sessions, app_state)

    assert len(registry.started) == 1
    job = registry.started[0]
    assert job["items"] == [
        GitDeleteItem(
            kind="branch_with_worktree", branch="feature/mobaide/cla-8",
            worktree_path=layout.worktree, force=True,
        ),
    ]
    assert job["workspace_name"] == "repo"
    # The Auto Scan work item still owns this path; its busy check must not
    # refuse a removal the user confirmed.
    assert job["autoscan_manager"] is None
    assert autoscan.released == [("repo", layout.worktree)]


async def test_refused_job_keeps_the_auto_scan_work_item(monkeypatch, layout):
    monkeypatch.setattr(
        stop_module, "delete_job_registry", _FakeRegistry(error=GitRefusedError("busy")),
    )
    sessions = _FakeSessionManager(_autoscan_info(layout.worktree))
    autoscan = _FakeAutoScanManager()
    app_state = _app_state(layout, _FakeWorktreeManager(), sessions)
    app_state.autoscan_manager = autoscan

    await _stop(sessions, app_state)

    assert sessions.stopped == [("s1", False)]
    assert autoscan.released == []


async def test_refused_job_still_reports_the_stop_as_done(monkeypatch, layout):
    monkeypatch.setattr(
        stop_module, "delete_job_registry", _FakeRegistry(error=GitRefusedError("too_many_jobs")),
    )
    sessions = _FakeSessionManager(_info(layout.worktree))

    await _stop(sessions, _app_state(layout, _FakeWorktreeManager()))

    assert sessions.stopped == [("s1", False)]


async def test_unknown_session_raises_key_error_and_removes_nothing(registry, layout):
    sessions = _FakeSessionManager()

    with pytest.raises(KeyError):
        await _stop(sessions, _app_state(layout, _FakeWorktreeManager()))

    assert registry.started == []
