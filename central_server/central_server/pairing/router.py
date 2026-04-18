"""REST endpoints for mobile token refresh and pairing management."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..auth.dependencies import require_role
from ..auth.models import MobileRefreshRequest, MobileTokenResponse

_require_admin = require_role("admin")

logger = logging.getLogger(__name__)

router = APIRouter()


# -- Mobile token refresh ------------------------------------------------------


@router.post("/api/mobile/refresh", response_model=MobileTokenResponse)
async def refresh_mobile_token(request: Request, body: MobileRefreshRequest):
    """Exchange a refresh token for a new access+refresh pair."""
    auth_service = request.app.state.auth_service
    result = await auth_service.refresh_mobile_tokens(body.refresh_token)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )
    access, refresh = result
    return MobileTokenResponse(access_token=access, refresh_token=refresh)


# -- Pairing management (admin only — mirrors the HTMX dashboard route) ------


@router.delete(
    "/api/pairings/{pairing_id}",
    dependencies=[Depends(_require_admin)],
)
async def revoke_pairing(request: Request, pairing_id: str):
    """Revoke a pairing between a mobile device and an agent."""
    pairing_service = request.app.state.pairing_service
    if not await pairing_service.revoke_pairing(pairing_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pairing not found or already revoked",
        )
    return {"status": "revoked"}
