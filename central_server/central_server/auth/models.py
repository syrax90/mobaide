from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, model_validator


@dataclass
class MobileTokenPayload:
    """Decoded JWT payload for mobile session tokens.

    ``sub`` is the per-install device UUID (client-generated, stable for
    the lifetime of the app install). ``user_id`` is the owning account.
    Refresh tokens are scoped to ``(sub, agent_id)`` so revoking one
    device leaves the user's other devices intact.
    """

    sub: str  # per-install device UUID
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


MOBILE_DEVICE_ID_PATTERN = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
"""Canonical 36-char UUID regex shared between wire schemas that carry a
mobile device id (see ``MobileTokenRequest``, ``PairingClaimRequest``)."""


class MobileTokenRequest(BaseModel):
    """Body for ``POST /api/agents/{agent_id}/token``.

    The mobile client identifies the *device* asking for tokens. The
    ``user_id`` is taken from the dashboard JWT in the Authorization
    header. ``mobile_device_name`` is an auto-generated label like
    ``"Google Pixel 8"`` that the user sees on the "My devices" screen.

    ``mobile_device_id`` is constrained to a canonical 36-char UUID
    string (defence in depth: narrows the attack surface for
    cross-tenant collisions and prevents convenient short literals like
    ``"admin"``).
    """

    mobile_device_id: str = Field(pattern=MOBILE_DEVICE_ID_PATTERN)
    mobile_device_name: str | None = Field(default=None, max_length=255)


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

    ``scheduled_deletion_at`` (ISO-8601, UTC) is non-null when the account is
    in the soft-delete grace window — clients surface a "cancel scheduled
    deletion" banner so the user can restore the account.
    """

    access_token: str | None = None
    token_type: str = "bearer"
    user_id: str
    email: str
    role: str
    status: str = "ok"
    scheduled_deletion_at: str | None = None


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


class DeleteAccountRequest(BaseModel):
    """Re-auth proof for ``POST /api/auth/me/delete``.

    The client must supply ONE of:
      * ``method="password"`` with a non-empty ``password`` (password users)
      * ``method="google"`` with a fresh ``id_token`` + the ``nonce`` the
        client used when obtaining it (Google-OAuth users)

    ``mode`` chooses between the soft-delete grace window (``scheduled``,
    default — 14 days, cancellable) and an irreversible cascade purge
    (``immediate``). Omitting ``mode`` preserves the legacy behavior for
    older clients.

    Existing session JWT alone is rejected — re-auth is a hard requirement
    for self-delete (Auth0 / Google Identity guidance).
    """

    method: Literal["password", "google"]
    # Nullable in the wire schema so older Android builds that omitted the
    # field — and any future JSON caller that explicitly sends ``null`` —
    # still parse cleanly. The validator below normalises ``None`` to
    # ``"scheduled"`` so service code can treat the field as always-present.
    mode: Literal["scheduled", "immediate"] | None = "scheduled"
    password: str | None = None
    id_token: str | None = None
    nonce: str | None = None

    @model_validator(mode="after")
    def _check_proof_shape(self) -> "DeleteAccountRequest":
        if self.mode is None:
            self.mode = "scheduled"
        if self.method == "password":
            if not self.password:
                raise ValueError("password is required when method=password")
        elif self.method == "google":
            if not self.id_token or not self.nonce:
                raise ValueError(
                    "id_token and nonce are required when method=google"
                )
        return self


class CancelAccountDeletionResponse(BaseModel):
    status: str = "cancelled"
    scheduled_deletion_at: str | None = None
