"""Session-scoped read-only git endpoints backing the in-session Changes
viewer: ``GET /api/sessions/{session_id}/git/status`` and ``.../git/diff``.

The directory is the session's own spawn cwd (project root or linked
worktree) — clients never send a path to run in. Repo *conditions* are 200
answers with ``unavailable_reason``; only session-not-found, bad
scope/path, missing git and timeouts are HTTP errors.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..auth.dependencies import get_current_user
from .changes_models import GitDiffResponse, GitStatusResponse
from .changes_service import (
    get_session_git_diff,
    get_session_git_status,
    http_error_for,
)

router = APIRouter(
    prefix="/api/sessions/{session_id}/git",
    tags=["git"],
    dependencies=[Depends(get_current_user)],
)


async def _session_cwd(request: Request, session_id: str) -> str | None:
    manager = getattr(request.app.state, "session_manager", None)
    session = await manager.get_session(session_id) if manager else None
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found",
        )
    return session.to_info().cwd


def _raise_mapped(exc: Exception) -> None:
    mapped = http_error_for(exc)
    if mapped is None:
        raise exc
    raise HTTPException(status_code=mapped[0], detail=mapped[1]["detail"])


@router.get("/status", response_model=GitStatusResponse)
async def session_git_status(
    session_id: str,
    request: Request,
    etag: str | None = None,
) -> GitStatusResponse:
    cwd = await _session_cwd(request, session_id)
    try:
        return await get_session_git_status(
            session_id=session_id, cwd=cwd, etag=etag
        )
    except Exception as exc:
        _raise_mapped(exc)
        raise


@router.get("/diff", response_model=GitDiffResponse)
async def session_git_diff(
    session_id: str,
    request: Request,
    path: str | None = None,
    scope: str | None = None,
    context: str = "3",
    orig_path: str | None = None,
) -> GitDiffResponse:
    cwd = await _session_cwd(request, session_id)
    try:
        return await get_session_git_diff(
            cwd=cwd,
            path=path,
            scope=scope,
            context=context,
            orig_path=orig_path,
        )
    except Exception as exc:
        _raise_mapped(exc)
        raise
