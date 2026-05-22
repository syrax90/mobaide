"""User-scoped agent management REST endpoints."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..auth.dependencies import get_non_admin_api_user
from ..auth.models import (
    AgentSummary,
    CreateAgentRequest,
    CreateAgentResponse,
    MobileTokenRequest,
    MobileTokenResponse,
    ReregisterAgentResponse,
    UserTokenPayload,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.post("", response_model=CreateAgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    request: Request,
    body: CreateAgentRequest,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
):
    """Create a new agent owned by the authenticated user. Returns credentials once."""
    agent_service = request.app.state.agent_service
    try:
        agent_id, raw_token = await agent_service.create_agent(body.name, user.sub)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        )
    return CreateAgentResponse(agent_id=agent_id, name=body.name, token=raw_token)


@router.get("", response_model=list[AgentSummary])
async def list_agents(
    request: Request,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
):
    """List agents owned by the authenticated user, with online status."""
    agent_service = request.app.state.agent_service
    agents = await agent_service.list_user_agents(user.sub)
    return [AgentSummary(**a) for a in agents]


@router.post("/{agent_id}/reregister", response_model=ReregisterAgentResponse)
async def reregister_agent(
    request: Request,
    agent_id: str,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
):
    """Regenerate agent credentials. Owner only."""
    agent_service = request.app.state.agent_service
    try:
        new_token = await agent_service.reregister_agent(agent_id, user.sub)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        )
    return ReregisterAgentResponse(agent_id=agent_id, token=new_token)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    request: Request,
    agent_id: str,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
):
    """Delete an agent and cascade all related data. Owner only."""
    agent_service = request.app.state.agent_service
    try:
        await agent_service.delete_agent(agent_id, user.sub)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        )


@router.post("/{agent_id}/token", response_model=MobileTokenResponse)
async def get_mobile_tokens(
    request: Request,
    agent_id: str,
    body: MobileTokenRequest,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
):
    """Get mobile access + refresh tokens for an agent the user owns.

    The mobile client supplies its per-install ``mobile_device_id`` so the
    issued JWT's ``sub`` claim and the refresh-token row are scoped to
    that specific device, not to the user account.
    """
    db = request.app.state.db
    if not await db.is_agent_owner(agent_id, user.sub):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not the owner of this agent",
        )
    accepted = await db.upsert_mobile_device(
        device_id=body.mobile_device_id,
        user_id=user.sub,
        name=body.mobile_device_name,
    )
    if not accepted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="mobile_device_id is already registered to a different user",
        )
    auth_service = request.app.state.auth_service
    access, refresh = await auth_service.create_mobile_tokens(
        mobile_device_id=body.mobile_device_id,
        agent_id=agent_id,
        user_id=user.sub,
    )
    return MobileTokenResponse(access_token=access, refresh_token=refresh)
