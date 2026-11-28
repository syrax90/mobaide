"""Host-filesystem browsing for path suggestions (``GET /api/fs/dirs``) and
the file suggestions of a session's chat composer
(``GET /api/sessions/{session_id}/files``).

Thin wrappers over :mod:`mobaide.agent.fs.service` and
:mod:`mobaide.agent.fs.session_files`, which the relay dispatcher calls too
so both transports answer identically.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth.dependencies import get_current_user
from .models import FsDirsResponse, SessionFilesResponse
from .service import FsListError, list_dirs
from .session_files import LIMIT_DEFAULT, list_session_files

router = APIRouter(
    prefix="/api/fs",
    tags=["fs"],
    dependencies=[Depends(get_current_user)],
)

session_files_router = APIRouter(
    prefix="/api/sessions/{session_id}",
    tags=["fs"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/dirs", response_model=FsDirsResponse)
async def list_dirs_endpoint(path: str, q: str | None = None) -> FsDirsResponse:
    """Subdirectories of ``path`` for client-side path completion."""
    try:
        return await list_dirs(path, q)
    except FsListError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None


@session_files_router.get("/files", response_model=SessionFilesResponse)
async def list_session_files_endpoint(
    session_id: str,
    request: Request,
    q: str | None = None,
    limit: int = LIMIT_DEFAULT,
) -> SessionFilesResponse:
    """Files and folders of the session's own folder that match ``q``, best
    first; ``limit`` is clamped to 1–50."""
    manager = getattr(request.app.state, "session_manager", None)
    session = await manager.get_session(session_id) if manager else None
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    try:
        return await list_session_files(session.to_info().cwd, q, limit)
    except FsListError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None
