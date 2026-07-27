"""User-scoped custom AI agent type REST endpoints."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..auth.dependencies import get_non_admin_api_user
from ..auth.models import UserTokenPayload
from .models import (
    CreateCustomAgentTypeRequest,
    CustomAgentTypeSummary,
    UpdateCustomAgentTypeRequest,
)
from .service import CustomTypeLimitError, CustomTypeNameConflictError

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/custom-agent-types", tags=["custom-agent-types"]
)


@router.get("", response_model=list[CustomAgentTypeSummary])
async def list_custom_agent_types(
    request: Request,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
):
    """List the caller's non-deleted custom agent types."""
    custom_agent_type_service = request.app.state.custom_agent_type_service
    rows = await custom_agent_type_service.list_types(user.sub)
    return [CustomAgentTypeSummary(**r) for r in rows]


@router.post(
    "",
    response_model=CustomAgentTypeSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_custom_agent_type(
    request: Request,
    body: CreateCustomAgentTypeRequest,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
):
    custom_agent_type_service = request.app.state.custom_agent_type_service
    try:
        row = await custom_agent_type_service.create_type(
            user.sub, body.name, body.command
        )
    except (CustomTypeLimitError, CustomTypeNameConflictError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        )
    return CustomAgentTypeSummary(**row)


@router.patch("/{type_id}", response_model=CustomAgentTypeSummary)
async def update_custom_agent_type(
    request: Request,
    type_id: str,
    body: UpdateCustomAgentTypeRequest,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
):
    custom_agent_type_service = request.app.state.custom_agent_type_service
    try:
        row = await custom_agent_type_service.update_type(
            type_id, user.sub, name=body.name, command=body.command
        )
    except CustomTypeNameConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        )
    return CustomAgentTypeSummary(**row)


@router.delete("/{type_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_custom_agent_type(
    request: Request,
    type_id: str,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
):
    """Soft-delete the type and (server-side) its kind-scoped shortcuts."""
    custom_agent_type_service = request.app.state.custom_agent_type_service
    try:
        await custom_agent_type_service.delete_type(type_id, user.sub)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        )
