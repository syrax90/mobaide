"""User-scoped create-session mode picker order REST endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..auth.dependencies import get_non_admin_api_user
from ..auth.models import UserTokenPayload
from .models import SessionModeOrder, UpdateSessionModeOrderRequest

router = APIRouter(
    prefix="/api/session-mode-order", tags=["session-mode-order"]
)


@router.get("", response_model=SessionModeOrder)
async def get_session_mode_order(
    request: Request,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
):
    """The caller's mode-picker order; an empty ``order`` means default."""
    service = request.app.state.session_mode_order_service
    return SessionModeOrder(**await service.get_order(user.sub))


@router.put("", response_model=SessionModeOrder)
async def put_session_mode_order(
    request: Request,
    body: UpdateSessionModeOrderRequest,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
):
    """Replace the order and push ``session_mode_order.updated`` to the
    caller's other connected devices."""
    service = request.app.state.session_mode_order_service
    return SessionModeOrder(**await service.set_order(user.sub, body.order))
