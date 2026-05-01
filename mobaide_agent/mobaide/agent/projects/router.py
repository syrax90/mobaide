"""REST endpoints for the mutable projects store.

All handlers are thin wrappers around :mod:`mobaide.agent.projects.service`
so the relay proxy can reuse the same business logic without going through HTTP.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..auth.dependencies import get_current_user
from .models import (
    ProjectReadModel,
    ProjectUpdateModel,
    ProjectWriteModel,
    ReorderRequest,
)
from .service import (
    DuplicateProjectError,
    ProjectInUseError,
    ReorderSetMismatchError,
    create_project,
    delete_project,
    list_projects,
    reorder_projects,
    update_project,
)

router = APIRouter(
    prefix="/api/projects",
    tags=["projects"],
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


def _autoscan_manager(request: Request):
    return getattr(request.app.state, "autoscan_manager", None)


def _event_bus(request: Request):
    return getattr(request.app.state, "event_bus", None)


def _actor(request: Request) -> str | None:
    return getattr(request.state, "username", None)


@router.get("", response_model=list[ProjectReadModel])
async def list_projects_endpoint(request: Request) -> list[dict]:
    """Return all configured projects (no secrets)."""
    return list_projects(_store(request))


@router.post(
    "",
    response_model=ProjectReadModel,
    status_code=status.HTTP_201_CREATED,
)
async def create_project_endpoint(
    body: ProjectWriteModel,
    request: Request,
) -> dict:
    """Create a new project. Returns 409 if the name is already taken."""
    try:
        return create_project(
            _store(request),
            body,
            event_bus=_event_bus(request),
            actor=_actor(request),
        )
    except DuplicateProjectError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc),
        )


@router.patch("/order", response_model=list[ProjectReadModel])
async def reorder_projects_endpoint(
    body: ReorderRequest,
    request: Request,
) -> list[dict]:
    """Atomically replace project ordering. 400 on set mismatch."""
    try:
        return reorder_projects(
            _store(request),
            body,
            event_bus=_event_bus(request),
            actor=_actor(request),
        )
    except ReorderSetMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc),
        )


@router.put("/{name}", response_model=ProjectReadModel)
async def update_project_endpoint(
    name: str,
    body: ProjectUpdateModel,
    request: Request,
) -> dict:
    """Update a project. Supports rename via ``body.name``.

    * 404 if ``name`` is unknown.
    * 409 if renaming while an auto-scan is active for the old name, or if
      the new name collides with another existing project.
    """
    try:
        return update_project(
            _store(request),
            _autoscan_manager(request),
            name,
            body,
            event_bus=_event_bus(request),
            actor=_actor(request),
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc).strip("'"),
        )
    except ProjectInUseError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc),
        )


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project_endpoint(
    name: str,
    request: Request,
) -> None:
    """Remove a project. 404 if unknown, 409 if a scan is active for it."""
    try:
        delete_project(
            _store(request),
            _autoscan_manager(request),
            name,
            event_bus=_event_bus(request),
            actor=_actor(request),
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc).strip("'"),
        )
    except ProjectInUseError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc),
        )
