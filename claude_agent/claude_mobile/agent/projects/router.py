from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..auth.dependencies import get_current_user

router = APIRouter(
    prefix="/api/projects",
    tags=["projects"],
    dependencies=[Depends(get_current_user)],
)


@router.get("")
async def list_projects(request: Request) -> list[dict]:
    """Return the configured projects (without secrets).

    Empty list when ``PROJECTS`` is not configured.
    """
    pc = getattr(request.app.state, "projects_config", None)
    if pc is None:
        return []
    return pc.list_public()
