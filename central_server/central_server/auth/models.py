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
    platform: str | None = Field(default=None, max_length=32)


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


class RefreshableSessionRequest(BaseModel):
    """Mixin for the auth endpoints that mint a user session.

    A client that sets ``refreshable`` receives a short-lived access token
    (``exp`` from ``user_session_access_expire_minutes``) plus an opaque
    refresh token redeemable at ``POST /api/auth/refresh``. Defaulting to
    ``False`` keeps the field additive: clients that never send it keep
    receiving the ``dashboard_token_expire_minutes`` token — which, at the
    default TTL of 0, carries no ``exp`` claim at all.

    Opting in is explicit rather than inferred from the ``Origin`` header:
    browsers send ``Origin`` on same-origin POSTs and native clients do not,
    so sniffing it would work today but would silently hand a short-lived,
    refresh-requiring token to any future WebView-based client.

    ``session_transport`` chooses where the refresh token is delivered.
    ``"cookie"`` puts it in an HttpOnly cookie the page cannot read and omits
    it from the body; ``"body"`` (and the legacy ``None``) return it in the
    JSON. Nullable rather than defaulted because openapi-typescript emits a
    property carrying a ``default`` as *non-optional* TypeScript, which would
    make the field required on every existing caller of these five endpoints.
    """

    refreshable: bool = False
    session_transport: Literal["body", "cookie"] | None = None


AD_CLICK_ID_PATTERN = r"^[A-Za-z0-9_-]{1,200}$"
AdClickKind = Literal["gclid", "wbraid", "gbraid"]


class AdAttribution(BaseModel):
    """Google Ads click attribution the web client copies out of the
    ``mobaide_ads`` cookie when it creates an account.

    The cookie is minted and HMAC-signed by this server (``ads/consent.py``)
    when the visitor accepts the banner on ``mobaide.com``; the client copies
    its fields here verbatim and the signature is what proves the consent —
    a value without a valid ``signature`` is ignored at sign-up, never
    rejected, so a planted or stale cookie cannot cost anyone the account.
    ``click_id``/``click_id_kind`` are null on a consent-only visit; native
    clients never send the object.
    """

    click_id: str | None = Field(default=None, pattern=AD_CLICK_ID_PATTERN)
    click_id_kind: AdClickKind | None = None
    clicked_at: int | None = Field(default=None, ge=0, le=4_102_444_800)
    signature: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _click_fields_travel_together(self) -> "AdAttribution":
        if (self.click_id is None) != (self.click_id_kind is None):
            raise ValueError("click_id and click_id_kind must be set together")
        return self


class UserRegisterRequest(RefreshableSessionRequest):
    email: str
    password: str
    ad_attribution: AdAttribution | None = None


class UserLoginRequest(RefreshableSessionRequest):
    email: str
    password: str


class GoogleAuthRequest(RefreshableSessionRequest):
    id_token: str
    ad_attribution: AdAttribution | None = None


class AppleAuthRequest(RefreshableSessionRequest):
    """Sign in with Apple identity-token exchange.

    ``nonce`` is required (unlike the Google mobile path): the iOS client
    always embeds its generated nonce verbatim in the identity token, so the
    server can bind the token to this request and reject replays.
    """

    id_token: str = Field(..., min_length=1)
    nonce: str = Field(..., min_length=1)
    ad_attribution: AdAttribution | None = None


class UserRefreshRequest(BaseModel):
    """Body for ``POST /api/auth/refresh`` and ``POST /api/auth/logout``.

    Unauthenticated by design — the refresh token *is* the credential, so
    presenting a dead access token alongside it must not matter.

    Optional because a browser session carries the token in an HttpOnly
    cookie instead and posts no body at all. A body token always wins, so a
    native client's request is routed exactly as before.
    """

    refresh_token: str | None = Field(None, min_length=1)


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

    ``refresh_token`` and ``expires_in`` are non-null only when the request
    asked for a refreshable session (see ``RefreshableSessionRequest``). This
    is also the response of ``POST /api/auth/refresh``, where ``status`` is
    always ``"ok"``.

    ``session_transport`` echoes where the refresh token went, so a client
    can distinguish "refreshable, credential is in your cookie jar" from "not
    refreshable, a 401 is terminal" — both carry a null ``refresh_token`` and
    demand opposite recovery behaviour.
    """

    access_token: str | None = None
    token_type: str = "bearer"
    user_id: str
    email: str
    role: str
    status: str = "ok"
    scheduled_deletion_at: str | None = None
    refresh_token: str | None = None
    expires_in: int | None = None
    session_transport: Literal["body", "cookie"] = "body"


class VerifyEmailRequest(RefreshableSessionRequest):
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
      * ``method="apple"`` with a fresh ``id_token`` + ``nonce`` (Sign in
        with Apple users)

    ``mode`` chooses between the soft-delete grace window (``scheduled``,
    default — 14 days, cancellable) and an irreversible cascade purge
    (``immediate``). Omitting ``mode`` preserves the legacy behavior for
    older clients.

    Existing session JWT alone is rejected — re-auth is a hard requirement
    for self-delete (Auth0 / Google Identity guidance).
    """

    method: Literal["password", "google", "apple"]
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
        elif self.method in ("google", "apple"):
            if not self.id_token or not self.nonce:
                raise ValueError(
                    f"id_token and nonce are required when method={self.method}"
                )
        return self


class CancelAccountDeletionResponse(BaseModel):
    status: str = "cancelled"
    scheduled_deletion_at: str | None = None
