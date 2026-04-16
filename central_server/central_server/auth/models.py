from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel


@dataclass
class MobileTokenPayload:
    """Decoded JWT payload for mobile session tokens."""

    sub: str  # mobile_device_id
    agent_id: str
    user_id: str  # owner user_id
    token_type: str  # "mobile_access"
    exp: int
    iat: int


@dataclass
class UserTokenPayload:
    """Decoded JWT payload for user (dashboard) session tokens."""

    sub: str  # user_id
    email: str
    role: str  # "admin" | "viewer"
    token_type: str  # "dashboard"
    exp: int
    iat: int


# -- Pydantic request/response models -----------------------------------------


class MobileRefreshRequest(BaseModel):
    refresh_token: str


class MobileTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


# -- Agent management models --------------------------------------------------


class CreateAgentRequest(BaseModel):
    name: str


class CreateAgentResponse(BaseModel):
    """Returned once on creation — includes raw token."""

    agent_id: str
    name: str
    token: str


class AgentSummary(BaseModel):
    """Agent listing response — no token."""

    agent_id: str
    name: str
    online: bool
    created_at: str
    last_seen_at: str | None


class ReregisterAgentResponse(BaseModel):
    """Returned on credential rotation — includes new raw token."""

    agent_id: str
    token: str


# -- User auth request/response models ----------------------------------------


class UserRegisterRequest(BaseModel):
    email: str
    password: str


class UserLoginRequest(BaseModel):
    email: str
    password: str


class GoogleAuthRequest(BaseModel):
    id_token: str


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str
    role: str
