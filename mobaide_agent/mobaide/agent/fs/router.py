"""Host-filesystem browsing for path suggestions: ``GET /api/fs/dirs``.

Thin wrapper over :mod:`mobaide.agent.fs.service`, which the relay
dispatcher calls too so both transports answer identically.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth.dependencies import get_current_user
from .models import FsDirsResponse
from .service import FsListError, list_dirs

router = APIRouter(
    prefix="/api/fs",
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
