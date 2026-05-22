"""User-scoped device management — list and revoke own devices."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..auth.dependencies import get_non_admin_api_user
from ..auth.models import UserTokenPayload
from .models import DeviceResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/devices", tags=["devices"])


@router.get("", response_model=list[DeviceResponse])
async def list_devices(
    request: Request,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
) -> list[DeviceResponse]:
    """Return the authenticated user's non-revoked devices."""
    db = request.app.state.db
    rows = await db.list_user_devices(user.sub)
    return [DeviceResponse(**row) for row in rows]


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_device(
    request: Request,
    device_id: str,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
) -> None:
    """Revoke one of the authenticated user's devices.

    Idempotency / safety: returns 404 if the device does not belong to the
    user (or was already revoked). On success, also invalidates every
    outstanding refresh token bound to that device across all agents — the
    revoked device fails on its next ``/api/mobile/refresh`` round-trip
    and signs out, while the user's other devices stay logged in.
    """
    db = request.app.state.db
    revoked = await db.revoke_mobile_device(device_id, user.sub)
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found",
        )
    count = await db.revoke_all_refresh_tokens_for_device(device_id)
    logger.info(
        "device.revoked user_id=%s device_id=%s tokens_revoked=%d",
        user.sub,
        device_id,
        count,
    )
