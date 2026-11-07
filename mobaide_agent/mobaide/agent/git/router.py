"""HTTP endpoints for the in-app git management screen.

Four endpoints, all nested under ``/api/projects/{name}/git`` and protected
by the same JWT auth dependency as the projects router:

* ``GET /items`` — list local branches and worktrees as a unified array,
  plus the remote-tracking branches with ``?remote=true``.
* ``POST /items:delete`` — batch-delete branches and/or worktrees.
* ``POST /branches`` — create a branch, optionally checking it out.
* ``POST /checkout`` — check a local or remote-tracking branch out in the
  project root.

The colon-suffixed RPC-style ``items:delete`` is used instead of
``DELETE /items`` because some proxies/clients drop request bodies on
DELETE; the server still treats it as a destructive action with full audit
coverage.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from mobaide.core.projects_store import safe_path_exists

from ..auth.dependencies import get_current_user
from .changes_service import http_error_for
from .models import (
    BusyByResponse,
    GitBranchCreateRequest,
    GitBranchResponse,
    GitCheckoutRequest,
    GitDeleteRequest,
    GitDeleteResponse,
    GitItemsResponse,
)
from .service import (
    GitItemBusyError,
    checkout_ref,
    create_branch,
    delete_git_items,
    list_git_items,
)

router = APIRouter(
    prefix="/api/projects/{name}/git",
    tags=["git"],
    dependencies=[Depends(get_current_user)],
)


def _store(request: Request):
    store = getattr(request.app.state, "projects_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="projects store not configured",
        )
    return store


def _worktree_manager(request: Request):
    manager = getattr(request.app.state, "worktree_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="worktree manager not configured",
        )
    return manager


def _session_manager(request: Request):
    return getattr(request.app.state, "session_manager", None)


def _autoscan_manager(request: Request):
    return getattr(request.app.state, "autoscan_manager", None)


def _event_bus(request: Request):
    return getattr(request.app.state, "event_bus", None)


def _actor(request: Request) -> str | None:
    return getattr(request.state, "username", None)


def _resolve_project(request: Request, name: str) -> tuple[str, str]:
    """Look up ``name`` in the projects store and assert it is git-backed.

    Returns ``(project_name, project_root)``. Raises 404 / 400 otherwise.
    """
    store = _store(request)
    project = store.get(name)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{name}' not found",
        )
    project_root = project.path
    if not safe_path_exists(Path(project_root) / ".git"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Project '{name}' is not a git repository",
        )
    return project.name, project_root


def _raise_mapped(exc: Exception) -> None:
    mapped = http_error_for(exc)
    if mapped is None:
        raise exc
    raise HTTPException(status_code=mapped[0], detail=mapped[1]["detail"])


def _raise_busy(exc: GitItemBusyError) -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=BusyByResponse(busy_by=exc.busy_by).model_dump(),
    )


@router.get("/items", response_model=GitItemsResponse)
async def list_git_items_endpoint(
    name: str,
    request: Request,
    remote: bool = Query(False),
) -> GitItemsResponse:
    """Return the unified list of local branches and worktrees, with the
    remote-tracking branches appended when ``remote`` is set."""
    _, project_root = _resolve_project(request, name)
    manager = _worktree_manager(request)
    try:
        items = await list_git_items(project_root, manager, remote=remote)
    except Exception as exc:
        _raise_mapped(exc)
        raise
    return GitItemsResponse(items=items)


@router.post("/items:delete", response_model=GitDeleteResponse)
async def delete_git_items_endpoint(
    name: str,
    body: GitDeleteRequest,
    request: Request,
) -> GitDeleteResponse:
    """Delete one or more branches/worktrees in a single batch.

    Returns 409 with ``{busy_by: [...]}`` if any worktree in the batch is
    held by a running session or auto-scan work item; the client should
    surface a busy-conflict dialog and refuse to retry until the user
    stops the holder. Per-item git failures (unmerged branch, locked
    worktree, etc.) are returned in the 200 body so the user can act on
    each row independently.
    """
    project_name, project_root = _resolve_project(request, name)
    manager = _worktree_manager(request)
    try:
        return await delete_git_items(
            body,
            project_name=project_name,
            project_root=project_root,
            manager=manager,
            session_manager=_session_manager(request),
            autoscan_manager=_autoscan_manager(request),
            event_bus=_event_bus(request),
            actor=_actor(request),
        )
    except GitItemBusyError as exc:
        _raise_busy(exc)
        raise


@router.post("/branches", response_model=GitBranchResponse)
async def create_branch_endpoint(
    name: str,
    body: GitBranchCreateRequest,
    request: Request,
) -> GitBranchResponse:
    """Create a branch in the project's repository.

    ``400 bad_branch_name`` for a name git would refuse, ``409 exists`` /
    ``unknown_start_point`` for the obvious conflicts; with ``checkout``
    the refusals of ``POST /checkout`` apply as well.
    """
    project_name, project_root = _resolve_project(request, name)
    manager = _worktree_manager(request)
    try:
        return await create_branch(
            body,
            project_name=project_name,
            project_root=project_root,
            manager=manager,
            session_manager=_session_manager(request),
            autoscan_manager=_autoscan_manager(request),
            event_bus=_event_bus(request),
            actor=_actor(request),
        )
    except GitItemBusyError as exc:
        _raise_busy(exc)
        raise
    except Exception as exc:
        _raise_mapped(exc)
        raise


@router.post("/checkout", response_model=GitBranchResponse)
async def checkout_endpoint(
    name: str,
    body: GitCheckoutRequest,
    request: Request,
) -> GitBranchResponse:
    """Check ``ref`` out in the project root.

    Returns 409 with ``{busy_by: [...]}`` while a session or auto-scan work
    item runs in the project root (a linked worktree's session does not
    count) unless ``force`` is set, and a coded 409 — ``dirty``, ``checked_out_elsewhere``,
    ``operation_in_progress``, ``job_running`` — for the states git or the
    agent refuse to switch in; ``404 ref_not_found`` for a ref that is
    neither a local nor a remote-tracking branch.
    """
    project_name, project_root = _resolve_project(request, name)
    manager = _worktree_manager(request)
    try:
        return await checkout_ref(
            body,
            project_name=project_name,
            project_root=project_root,
            manager=manager,
            session_manager=_session_manager(request),
            autoscan_manager=_autoscan_manager(request),
            event_bus=_event_bus(request),
            actor=_actor(request),
        )
    except GitItemBusyError as exc:
        _raise_busy(exc)
        raise
    except Exception as exc:
        _raise_mapped(exc)
        raise
