"""Stop a session and, on request, remove the worktree the agent made for it.

The removal runs as a background delete job because a large checkout can
outlast the relay's request budget. It is best effort: a failure leaves the
worktree on the Git screen.
"""
from __future__ import annotations

import os
from typing import Any

from mobaide.core.models import SessionInfo

from ..git.delete_jobs import delete_job_registry
from ..git.models import GitDeleteItem, GitDeleteRequest
from ..logging import get_logger

logger = get_logger(__name__)


async def stop_session(
    session_manager: Any,
    session_id: str,
    *,
    force: bool,
    remove_worktree: bool,
    app_state: Any,
    actor: str | None,
) -> None:
    """Stop ``session_id``; raises ``KeyError`` when it does not exist."""
    info: SessionInfo | None = None
    if remove_worktree:
        session = await session_manager.get_session(session_id)
        if session is not None:
            info = session.to_info()
    await session_manager.stop_session(session_id, force=force)
    if info is not None and info.worktree_branch:
        await _start_worktree_removal(info, app_state=app_state, actor=actor)


async def _start_worktree_removal(info: SessionInfo, *, app_state: Any, actor: str | None) -> None:
    store = getattr(app_state, "workspaces_store", None)
    manager = getattr(app_state, "worktree_manager", None)
    workspace_name = info.project_name or info.worktree_workspace or ""
    workspace = store.get(workspace_name) if store is not None else None
    if (
        workspace is None
        or manager is None
        or not info.cwd
        or os.path.normcase(os.path.realpath(info.cwd))
        == os.path.normcase(os.path.realpath(workspace.path))
    ):
        logger.warning("session.worktree_removal_skipped", session_id=info.session_id)
        return
    # The branch the dialog named, even if the user switched the worktree to
    # another one since: only that branch was confirmed. ``force`` because the
    # user asked for the removal knowing it drops uncommitted and unmerged work.
    item = GitDeleteItem(
        kind="branch_with_worktree",
        branch=info.worktree_branch,
        worktree_path=info.cwd,
        force=True,
    )
    autoscan_manager = getattr(app_state, "autoscan_manager", None)
    try:
        # No Auto Scan busy check: an Auto Scan work item only waits for its
        # issue to be done to remove this worktree, and the user removes it now.
        await delete_job_registry.start(
            GitDeleteRequest(items=[item]),
            workspace_name=workspace.name,
            workspace_root=workspace.path,
            manager=manager,
            session_manager=getattr(app_state, "session_manager", None),
            autoscan_manager=None,
            event_bus=getattr(app_state, "event_bus", None),
            actor=actor,
        )
    except Exception:
        # Busy (another session works in the same worktree) or too many jobs.
        logger.warning(
            "session.worktree_removal_refused", session_id=info.session_id, exc_info=True,
        )
        return
    if autoscan_manager is not None:
        try:
            await autoscan_manager.release_worktree(workspace.name, info.cwd)
        except Exception:
            logger.warning(
                "session.autoscan_release_failed", session_id=info.session_id, exc_info=True,
            )
