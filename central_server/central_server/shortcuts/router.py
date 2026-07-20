"""User-scoped saved keyboard shortcut REST endpoints."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..auth.dependencies import get_non_admin_api_user
from ..auth.models import UserTokenPayload
from .models import (
    CreateShortcutRequest,
    ShortcutSummary,
    UpdateShortcutRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/shortcuts", tags=["shortcuts"])


@router.get("", response_model=list[ShortcutSummary])
async def list_shortcuts(
    request: Request,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
):
    """List the caller's non-deleted saved shortcuts (all session kinds)."""
    shortcut_service = request.app.state.shortcut_service
    rows = await shortcut_service.list_shortcuts(user.sub)
    return [ShortcutSummary(**r) for r in rows]


@router.post(
    "", response_model=ShortcutSummary, status_code=status.HTTP_201_CREATED
)
async def create_shortcut(
    request: Request,
    body: CreateShortcutRequest,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
):
    """Upsert: re-saving an existing (kind, chord_token) updates its name
    instead of duplicating the button; the status stays 201 on both paths."""
    shortcut_service = request.app.state.shortcut_service
    row = await shortcut_service.create_shortcut(
        user.sub, body.kind, body.chord_token, body.name
    )
    return ShortcutSummary(**row)


@router.patch("/{shortcut_id}", response_model=ShortcutSummary)
async def update_shortcut(
    request: Request,
    shortcut_id: str,
    body: UpdateShortcutRequest,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
):
    shortcut_service = request.app.state.shortcut_service
    try:
        row = await shortcut_service.rename_shortcut(
            shortcut_id, user.sub, body.name
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        )
    return ShortcutSummary(**row)


@router.delete("/{shortcut_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shortcut(
    request: Request,
    shortcut_id: str,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
):
    """Soft-delete the shortcut."""
    shortcut_service = request.app.state.shortcut_service
    try:
        await shortcut_service.delete_shortcut(shortcut_id, user.sub)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        )
