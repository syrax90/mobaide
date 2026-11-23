"""``/api/agent/settings`` routes (direct transport).

The relay dispatcher (``relay/client.py`` ``_dispatch_agent_settings``)
answers the same paths with the same service calls, models and error bodies.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from mobaide.agent.auth.dependencies import get_current_user

from .models import AgentSettingsResponse, KeepAwakeSettingsRequest
from .service import AgentSettingsError, AgentSettingsService

router = APIRouter(
    prefix="/api/agent/settings",
    tags=["agent-settings"],
    dependencies=[Depends(get_current_user)],
)


def _service(request: Request) -> AgentSettingsService:
    service = getattr(request.app.state, "agent_settings", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "settings_unavailable", "message": "Agent settings are not available."},
        )
    return service


@router.get("", response_model=AgentSettingsResponse)
async def get_agent_settings(request: Request) -> AgentSettingsResponse:
    return _service(request).status()


@router.put("/keep_awake", response_model=AgentSettingsResponse)
async def put_keep_awake(
    body: KeepAwakeSettingsRequest, request: Request
) -> AgentSettingsResponse:
    try:
        return await _service(request).set_keep_awake(body.enabled)
    except AgentSettingsError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail()) from exc
