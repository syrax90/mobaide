"""User-scoped prompt template REST endpoints."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..auth.dependencies import get_non_admin_api_user
from ..auth.models import UserTokenPayload
from .models import (
    CreateTemplateRequest,
    TemplateSummary,
    UpdateTemplateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/templates", tags=["templates"])


@router.get("", response_model=list[TemplateSummary])
async def list_templates(
    request: Request,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
):
    """List the caller's non-deleted prompt templates."""
    template_service = request.app.state.template_service
    rows = await template_service.list_templates(user.sub)
    return [TemplateSummary(**r) for r in rows]


@router.post(
    "", response_model=TemplateSummary, status_code=status.HTTP_201_CREATED
)
async def create_template(
    request: Request,
    body: CreateTemplateRequest,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
):
    template_service = request.app.state.template_service
    row = await template_service.create_template(user.sub, body.name, body.body)
    return TemplateSummary(**row)


@router.patch("/{template_id}", response_model=TemplateSummary)
async def update_template(
    request: Request,
    template_id: str,
    body: UpdateTemplateRequest,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
):
    template_service = request.app.state.template_service
    try:
        row = await template_service.update_template(
            template_id,
            user.sub,
            name=body.name,
            body=body.body,
            is_favorite=body.is_favorite,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        )
    return TemplateSummary(**row)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    request: Request,
    template_id: str,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
):
    """Soft-delete the template. Idempotent: deleting an already-deleted
    template still returns 204."""
    template_service = request.app.state.template_service
    try:
        await template_service.delete_template(template_id, user.sub)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        )


@router.post("/{template_id}/touch", response_model=TemplateSummary)
async def touch_template(
    request: Request,
    template_id: str,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
):
    """Bump ``last_used_at`` for picker-tap recents tracking."""
    template_service = request.app.state.template_service
    try:
        row = await template_service.touch_template(template_id, user.sub)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        )
    return TemplateSummary(**row)
