"""Pydantic models for the QR pairing flow.

The agent calls ``POST /api/pairing/initiate`` to get a nonce + QR payload,
the mobile calls ``POST /api/pairing/claim`` to consume the nonce, and the
central server pushes a signed ``PairingPushPayload`` over the agent's
``WS /ws/pairing/{nonce}``.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..auth.models import MOBILE_DEVICE_ID_PATTERN


class PairingInitiateRequest(BaseModel):
    """Optional metadata the agent reports at initiate time.

    All fields are advisory: the mobile UI uses ``hostname`` / ``os`` to
    let the operator confirm they're claiming the right host;
    ``current_agent_id`` distinguishes a re-pair (token rotation for the
    same owner) from a first-pair (new ``agents`` row at claim). A claim by
    a different account is rejected — ownership is not transferable by
    pairing.
    """

    hostname: str | None = Field(default=None, max_length=255)
    os: str | None = Field(default=None, max_length=64)
    current_agent_id: str | None = Field(default=None, max_length=64)


class PairingInitiateResponse(BaseModel):
    """Server's reply to ``POST /api/pairing/initiate``.

    ``qr_payload`` is the canonical ``MOBA1|...`` text the agent renders.
    ``trust_key_fp`` is the SHA-256 fingerprint of the server's manifest
    signing public key — the agent cross-checks this against its own
    pinned key before trusting the flow. The pairing WS URL is *not*
    returned: the agent derives it locally from ``RELAY_SERVER_URL`` +
    ``nonce`` so a deployment-skewed server cannot hand back a buggy
    scheme (``https://...``) that the websockets client would reject.
    """

    nonce: str
    expires_at: str
    trust_key_fp: str
    relay_url: str
    qr_payload: str


class PairingClaimRequest(BaseModel):
    """Mobile's claim request (authenticated).

    ``mobile_device_id`` is constrained to a canonical 36-char UUID
    string — same defence-in-depth check as ``MobileTokenRequest``.
    """

    nonce: str = Field(min_length=1, max_length=64)
    trust_key_fp: str = Field(min_length=1, max_length=128)
    mobile_device_id: str = Field(pattern=MOBILE_DEVICE_ID_PATTERN)
    mobile_device_name: str | None = Field(default=None, max_length=255)
    hostname_hint: str | None = Field(default=None, max_length=255)
    friendly_name: str | None = Field(default=None, max_length=128)


class PairingClaimResponse(BaseModel):
    """Server's reply to a successful claim — no token reaches the mobile.

    ``ws_delivered`` is a diagnostic hint, not an authority signal. ``True``
    means the credential push was dispatched directly to the agent's pairing
    WebSocket (the agent was connected at claim time); ``False`` means the
    push was staged in the server-side recovery slot for the agent's next
    WS reconnect to consume. The mobile uses this to refine the timeout-error
    copy when the agent does not subsequently come online — it does NOT
    treat ``True`` as "the agent is now paired" (the agent could still fail
    to validate the signature, fail to write ``.env``, or fail to start its
    relay client; only the routing-table ``agent.status_changed=true``
    event is authoritative).
    """

    agent_id: str
    agent_friendly_name: str
    ws_delivered: bool | None = None


class PairingPushPayload(BaseModel):
    """Signed payload pushed over the agent's pairing WS.

    ``signature_b64`` is an Ed25519 signature over the canonical-JSON of
    every other field (sorted keys, no whitespace), produced with the
    server's manifest-signing private key. The agent verifies with its
    pinned public key — see ``mobaide.agent.pairing_ws``.
    """

    type: Literal["claim_completed"] = "claim_completed"
    agent_id: str
    agent_token: str
    relay_url: str
    agent_friendly_name: str
    issued_at: str
    signature_b64: str
