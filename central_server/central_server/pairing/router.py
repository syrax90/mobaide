"""REST endpoints for pairing, agent registration, and mobile token refresh."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..auth.dependencies import get_dashboard_user
from ..auth.models import (
    AgentRegisterRequest,
    AgentRegisterResponse,
    MobileRefreshRequest,
    MobileTokenResponse,
    PairingCompleteRequest,
    PairingCompleteResponse,
)
from .service import PairingError

logger = logging.getLogger(__name__)

router = APIRouter()


# -- Pairing (no auth — the pairing token IS the auth) -------------------------


@router.post("/api/pairing/complete", response_model=PairingCompleteResponse)
async def complete_pairing(request: Request, body: PairingCompleteRequest):
    """Mobile sends scanned/entered pairing token to bind to an agent."""
    pairing_service = request.app.state.pairing_service
    auth_service = request.app.state.auth_service

    try:
        agent_id, pairing_id = await pairing_service.consume_pairing_token(
            raw_token=body.token,
            mobile_device_id=body.device_id,
            device_name=body.device_name,
        )
    except PairingError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )

    access_token, refresh_token = await auth_service.create_mobile_tokens(
        mobile_device_id=body.device_id, agent_id=agent_id
    )
    logger.info(
        "pairing.completed agent_id=%s device_id=%s pairing_id=%s",
        agent_id,
        body.device_id,
        pairing_id,
    )
    return PairingCompleteResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        agent_id=agent_id,
        pairing_id=pairing_id,
    )


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


# -- Agent registration (dashboard auth required) ------------------------------


@router.post(
    "/api/agents/register",
    response_model=AgentRegisterResponse,
    dependencies=[Depends(get_dashboard_user)],
)
async def register_agent(request: Request, body: AgentRegisterRequest):
    """Register a new agent. Returns the agent_id and raw token (display once)."""
    db = request.app.state.db
    agent_id, raw_token = await db.register_agent(body.name)
    logger.info("agent.registered agent_id=%s name=%s", agent_id, body.name)
    return AgentRegisterResponse(agent_id=agent_id, token=raw_token)


@router.get("/api/agents", dependencies=[Depends(get_dashboard_user)])
async def list_agents(request: Request):
    """List all registered agents with online status."""
    db = request.app.state.db
    routing = request.app.state.routing
    agents = await db.list_agents()
    for agent in agents:
        agent["online"] = routing.is_agent_online(agent["agent_id"])
    return agents


@router.delete(
    "/api/pairings/{pairing_id}",
    dependencies=[Depends(get_dashboard_user)],
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
