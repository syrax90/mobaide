"""REST endpoints for the mutable projects store.

All handlers are thin wrappers around :mod:`mobaide.agent.projects.service`
so the relay proxy can reuse the same business logic without going through HTTP.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from mobaide.linear.client import LinearApiClient
from mobaide.linear.rate_limiter import LinearRateLimiter

from ..auth.dependencies import get_current_user
from ..logging import get_logger
from .models import (
    LinearLabelModel,
    LinearStateModel,
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

logger = get_logger(__name__)

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


def _resolve_api_key_or_404(request: Request, name: str) -> str:
    store = _store(request)
    project = store.get(name)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{name}' not found",
        )
    api_key = project.linear_api_key
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Project '{name}' has no linear_api_key configured; "
                "set one before fetching Linear metadata."
            ),
        )
    return api_key


def _shared_rate_limiter(request: Request) -> LinearRateLimiter | None:
    return getattr(request.app.state, "linear_rate_limiter", None)


@router.get(
    "/{name}/linear/labels",
    response_model=list[LinearLabelModel],
)
async def list_linear_labels(
    name: str,
    request: Request,
) -> list[dict[str, str]]:
    """Return labels visible to the project's Linear API key, deduped by name.

    Used by the Android project form to populate the multi-select chip
    group for trigger labels and the dropdown for the removal label.

    * 404 if the project is unknown.
    * 400 if the project has no Linear API key.
    * 502 if the upstream Linear API call fails.
    """
    api_key = _resolve_api_key_or_404(request, name)
    client = LinearApiClient(
        api_key=api_key, rate_limiter=_shared_rate_limiter(request),
    )
    try:
        return await client.get_labels()
    except Exception as exc:
        logger.warning(
            "linear.discovery.labels_failed project=%s error=%s", name, exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch labels from Linear",
        )


@router.get(
    "/{name}/linear/states",
    response_model=list[LinearStateModel],
)
async def list_linear_states(
    name: str,
    request: Request,
) -> list[dict[str, str]]:
    """Return workflow states visible to the project's Linear API key.

    Used by the Android project form to populate the multi-select chip
    group for trigger states and the dropdowns for active / done states.

    * 404 if the project is unknown.
    * 400 if the project has no Linear API key.
    * 502 if the upstream Linear API call fails.
    """
    api_key = _resolve_api_key_or_404(request, name)
    client = LinearApiClient(
        api_key=api_key, rate_limiter=_shared_rate_limiter(request),
    )
    try:
        return await client.get_workflow_states()
    except Exception as exc:
        logger.warning(
            "linear.discovery.states_failed project=%s error=%s", name, exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch workflow states from Linear",
        )
