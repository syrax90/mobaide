from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel


@dataclass
class MobileTokenPayload:
    """Decoded JWT payload for mobile session tokens."""

    sub: str  # mobile_device_id
    agent_id: str
    token_type: str  # "mobile_access"
    exp: int
    iat: int


# -- Pydantic request/response models -----------------------------------------


class PairingCompleteRequest(BaseModel):
    token: str
    device_id: str
    device_name: str | None = None


class PairingCompleteResponse(BaseModel):
    access_token: str
    refresh_token: str
    agent_id: str
    pairing_id: str
    token_type: str = "bearer"


class MobileRefreshRequest(BaseModel):
    refresh_token: str


class MobileTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class DashboardLoginRequest(BaseModel):
    username: str
    password: str


class AgentRegisterRequest(BaseModel):
    name: str


class AgentRegisterResponse(BaseModel):
    agent_id: str
    token: str
