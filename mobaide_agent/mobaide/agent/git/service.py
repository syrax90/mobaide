"""Git management service — pure logic shared by the HTTP router.

Responsibilities:

* Combine ``WorktreeManager.list_branches`` and ``list_worktrees`` into the
  unified item shape returned by the API.
* Enforce a busy-guard against active terminal sessions and auto-scan
  work items before allowing destructive operations.
* Drive per-item deletes (branch / worktree / branch+worktree) with the
  per-repo lock owned by the manager, audit each outcome, and publish
  ``git.changed`` after any successful delete.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from mobaide.linear.worktree import (
    BranchDeleteError,
    BranchInfo,
    WorktreeInfo,
    WorktreeManager,
    WorktreeRemoveError,
)

from ..logging import audit_event, get_logger
from .models import (
    BusyHolder,
    GitDeleteItem,
    GitDeleteRequest,
    GitDeleteResponse,
    GitDeleteResultItem,
    GitItemRead,
    ItemKind,
)

logger = get_logger(__name__)

_LOCKED_PATTERNS = ("is locked",)


class GitItemBusyError(Exception):
    """Raised when at least one delete target is held by a session or auto-scan."""

    def __init__(self, busy_by: list[BusyHolder]) -> None:
        super().__init__(f"{len(busy_by)} item(s) busy")
        self.busy_by = busy_by


def _normalize_path(path: str | None) -> str | None:
    if not path:
        return None
    try:
        return os.path.normcase(os.path.realpath(path))
    except OSError:
        return os.path.normcase(path)


def _strip_refs_heads(branch_ref: str | None) -> str | None:
    """Strip the ``refs/heads/`` prefix that ``git worktree list --porcelain`` emits."""
    if branch_ref is None:
        return None
    return branch_ref.removeprefix("refs/heads/")


def _build_items(
    branches: list[BranchInfo],
    worktrees: list[WorktreeInfo],
) -> list[GitItemRead]:
    """Merge branches and worktrees into the unified API shape."""
    branch_to_wt: dict[str, WorktreeInfo] = {}
    for wt in worktrees:
        short = _strip_refs_heads(wt.branch)
        if short and not wt.detached:
            branch_to_wt[short] = wt

    branch_names = {b.name for b in branches}
    items: list[GitItemRead] = []

    for branch in branches:
        wt = branch_to_wt.get(branch.name)
        if wt is not None:
            items.append(
                GitItemRead(
                    kind="branch_with_worktree",
                    branch=branch.name,
                    worktree_path=wt.path,
                    is_current=branch.is_current,
                    upstream=branch.upstream,
                    ahead=branch.ahead,
                    behind=branch.behind,
                    gone=branch.gone,
                    locked=wt.locked,
                    locked_reason=wt.locked_reason,
                    prunable=wt.prunable,
                )
            )
        else:
            items.append(
                GitItemRead(
                    kind="branch_only",
                    branch=branch.name,
                    is_current=False,
                    upstream=branch.upstream,
                    ahead=branch.ahead,
                    behind=branch.behind,
                    gone=branch.gone,
                )
            )

    for wt in worktrees:
        short = _strip_refs_heads(wt.branch)
        if wt.detached or short is None or short not in branch_names:
            items.append(
                GitItemRead(
                    kind="orphan_worktree",
                    worktree_path=wt.path,
                    branch=short,
                    detached=wt.detached,
                    locked=wt.locked,
                    locked_reason=wt.locked_reason,
                    prunable=wt.prunable,
                )
            )

    return items


async def list_git_items(
    project_root: str,
    manager: WorktreeManager,
) -> list[GitItemRead]:
    """List the unified git items for ``project_root``."""
    branches = await manager.list_branches(project_root)
    worktrees = await manager.list_worktrees(project_root)
    return _build_items(branches, worktrees)


@dataclass
class _DeleteContext:
    """All collaborators a single delete batch needs."""

    project_name: str
    project_root: str
    manager: WorktreeManager
    session_manager: Any | None
    autoscan_manager: Any | None
    event_bus: Any | None
    actor: str | None


def _affected_paths(items: list[GitDeleteItem]) -> set[str]:
    paths: set[str] = set()
    for item in items:
        norm = _normalize_path(item.worktree_path)
        if norm is not None:
            paths.add(norm)
    return paths


async def _busy_holders(
    affected: set[str],
    session_manager: Any | None,
    autoscan_manager: Any | None,
) -> list[BusyHolder]:
    """Return every session / auto-scan work item whose path is in ``affected``."""
    holders: list[BusyHolder] = []
    if not affected:
        return holders

    if session_manager is not None:
        try:
            sessions = session_manager.list_sessions()
        except Exception:
            sessions = []
        for info in sessions:
            cwd = getattr(info, "cwd", None)
            norm = _normalize_path(cwd)
            if norm is not None and norm in affected:
                holders.append(
                    BusyHolder(
                        kind="session",
                        session_id=getattr(info, "session_id", None),
                        cwd=cwd,
                    )
                )

    if autoscan_manager is not None:
        try:
            workitems_by_project = await autoscan_manager.list_all_workitems()
        except Exception:
            workitems_by_project = {}
        for project_name, items in workitems_by_project.items():
            for item in items:
                norm = _normalize_path(getattr(item, "worktree_path", None))
                if norm is not None and norm in affected:
                    holders.append(
                        BusyHolder(
                            kind="auto_scan",
                            project_name=project_name,
                            worktree_path=getattr(item, "worktree_path", None),
                        )
                    )

    return holders


def _result(
    *,
    kind: ItemKind,
    target: str,
    ok: bool,
    error: str | None = None,
    error_detail: str | None = None,
) -> GitDeleteResultItem:
    return GitDeleteResultItem(
        kind=kind, target=target, ok=ok, error=error, error_detail=error_detail,
    )


def _worktree_error_code(exc: WorktreeRemoveError) -> str:
    """Map a worktree-remove failure to a wire error code.

    A dirty worktree (modified/untracked files) becomes ``force_required`` so
    the client offers a force-delete confirmation, mirroring the unmerged-branch
    flow; a locked worktree stays ``locked``; anything else is ``error``.
    """
    if exc.dirty:
        return "force_required"
    if any(p in exc.stderr for p in _LOCKED_PATTERNS):
        return "locked"
    return "error"


async def _delete_one(
    item: GitDeleteItem,
    ctx: _DeleteContext,
) -> tuple[GitDeleteResultItem, bool]:
    """Process a single delete item and return ``(result, did_change)``."""
    if item.kind == "branch_only":
        if not item.branch:
            return _result(kind=item.kind, target="", ok=False, error="invalid"), False
        try:
            await ctx.manager.delete_branch(
                ctx.project_root, item.branch, force=item.force,
            )
        except BranchDeleteError as exc:
            err_code = "force_required" if exc.unmerged else "error"
            return _result(
                kind=item.kind, target=item.branch, ok=False,
                error=err_code, error_detail=exc.stderr,
            ), False
        audit_event(
            "audit.git.delete",
            actor=ctx.actor,
            target=item.branch,
            outcome="ok",
            kind=item.kind,
            project=ctx.project_name,
            force=item.force,
        )
        return _result(kind=item.kind, target=item.branch, ok=True), True

    if item.kind == "orphan_worktree":
        if not item.worktree_path:
            return _result(kind=item.kind, target="", ok=False, error="invalid"), False
        try:
            await ctx.manager.remove_worktree(
                ctx.project_root, item.worktree_path, force=item.force,
            )
        except WorktreeRemoveError as exc:
            return _result(
                kind=item.kind, target=item.worktree_path, ok=False,
                error=_worktree_error_code(exc), error_detail=exc.stderr,
            ), False
        audit_event(
            "audit.git.delete",
            actor=ctx.actor,
            target=item.worktree_path,
            outcome="ok",
            kind=item.kind,
            project=ctx.project_name,
        )
        return _result(kind=item.kind, target=item.worktree_path, ok=True), True

    if item.kind == "branch_with_worktree":
        if not item.branch or not item.worktree_path:
            target = item.branch or item.worktree_path or ""
            return _result(kind=item.kind, target=target, ok=False, error="invalid"), False
        worktree_already_gone = not os.path.exists(item.worktree_path)
        if not worktree_already_gone:
            try:
                await ctx.manager.remove_worktree(
                    ctx.project_root, item.worktree_path, force=item.force,
                )
            except WorktreeRemoveError as exc:
                return _result(
                    kind=item.kind, target=item.worktree_path, ok=False,
                    error=_worktree_error_code(exc), error_detail=exc.stderr,
                ), False
        try:
            await ctx.manager.delete_branch(
                ctx.project_root, item.branch, force=item.force,
            )
        except BranchDeleteError as exc:
            err_code = "force_required" if exc.unmerged else "error"
            return _result(
                kind=item.kind, target=item.branch, ok=False,
                error=err_code, error_detail=exc.stderr,
            ), True
        audit_event(
            "audit.git.delete",
            actor=ctx.actor,
            target=f"{item.branch}@{item.worktree_path}",
            outcome="ok",
            kind=item.kind,
            project=ctx.project_name,
            force=item.force,
        )
        return _result(kind=item.kind, target=item.branch, ok=True), True

    return _result(kind=item.kind, target="", ok=False, error="invalid"), False


def _publish_git_changed(event_bus: Any | None, project_name: str) -> None:
    if event_bus is None:
        return
    try:
        event_bus.publish("git.changed", {"project_name": project_name})
    except Exception:
        logger.exception("git.publish_failed")


async def delete_git_items(
    request: GitDeleteRequest,
    *,
    project_name: str,
    project_root: str,
    manager: WorktreeManager,
    session_manager: Any | None,
    autoscan_manager: Any | None,
    event_bus: Any | None,
    actor: str | None,
) -> GitDeleteResponse:
    """Process a delete batch atomically with respect to busy-guard.

    Raises :class:`GitItemBusyError` if any worktree path in the batch is
    held by a session or auto-scan work item — the entire batch is rejected
    and the caller maps to HTTP 409. Otherwise each item is processed in
    order under the manager's per-repo lock; per-item failures are recorded
    in the response without aborting the batch.
    """
    affected = _affected_paths(request.items)
    busy = await _busy_holders(affected, session_manager, autoscan_manager)
    if busy:
        raise GitItemBusyError(busy_by=busy)

    ctx = _DeleteContext(
        project_name=project_name,
        project_root=project_root,
        manager=manager,
        session_manager=session_manager,
        autoscan_manager=autoscan_manager,
        event_bus=event_bus,
        actor=actor,
    )
    results: list[GitDeleteResultItem] = []
    any_change = False
    for item in request.items:
        result, did_change = await _delete_one(item, ctx)
        results.append(result)
        any_change = any_change or did_change

    if any_change:
        _publish_git_changed(event_bus, project_name)

    return GitDeleteResponse(results=results)
