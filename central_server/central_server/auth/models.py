from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field


@dataclass
class MobileTokenPayload:
    """Decoded JWT payload for mobile session tokens."""

    sub: str  # mobile_device_id
    agent_id: str
    user_id: str  # owner user_id
    token_type: str  # "mobile_access"
    exp: int | None  # None = token has no `exp` claim (never expires)
    iat: int


@dataclass
class UserTokenPayload:
    """Decoded JWT payload for user (dashboard) session tokens."""

    sub: str
    email: str
    role: str  # "admin" | "viewer"
    token_type: str  # "dashboard"
    exp: int | None  # None = token has no `exp` claim (never expires)
    iat: int


class MobileRefreshRequest(BaseModel):
    refresh_token: str


class MobileTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


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
    agent_version: str | None = None
    agent_protocol_version: int | None = None


class ReregisterAgentResponse(BaseModel):
    """Returned on credential rotation — includes new raw token."""

    agent_id: str
    token: str


class UserRegisterRequest(BaseModel):
    email: str
    password: str


class UserLoginRequest(BaseModel):
    email: str
    password: str


class GoogleAuthRequest(BaseModel):
    id_token: str


class AuthTokenResponse(BaseModel):
    """Response for register / login / verify-email / google.

    When ``status == "verification_required"`` (returned by /register when the
    server enforces verification), ``access_token`` is ``None`` and the client
    must call /verify-email with the code from the OTP email to obtain the JWT.
    For all other ``status`` values (currently just ``"ok"``) a valid
    ``access_token`` is always present.
    """

    access_token: str | None = None
    token_type: str = "bearer"
    user_id: str
    email: str
    role: str
    status: str = "ok"


class VerifyEmailRequest(BaseModel):
    email: str
    code: str


class ResendVerificationRequest(BaseModel):
    email: str


class ResendVerificationResponse(BaseModel):
    status: str  # "sent" | "rate_limited"
    cooldown_seconds_remaining: int = 0


class ForgotPasswordRequest(BaseModel):
    email: str


class ForgotPasswordResponse(BaseModel):
    status: str = "ok"


class ResetPasswordRequest(BaseModel):
    email: str
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")
    new_password: str = Field(..., min_length=8, max_length=128)


class ResetPasswordResponse(BaseModel):
    status: str = "ok"
