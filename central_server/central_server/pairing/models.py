"""Pydantic models for the QR pairing flow.

The agent calls ``POST /api/pairing/initiate`` to get a nonce + QR payload,
the mobile calls ``POST /api/pairing/claim`` to consume the nonce, and the
central server pushes a signed ``PairingPushPayload`` over the agent's
``WS /ws/pairing/{nonce}``.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PairingInitiateRequest(BaseModel):
    """Optional metadata the agent reports at initiate time.

    All fields are advisory: the mobile UI uses ``hostname`` / ``os`` to
    let the operator confirm they're claiming the right host;
    ``current_agent_id`` distinguishes a re-pair (token rotation, possible
    owner transfer) from a first-pair (new ``agents`` row at claim).
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
    """Mobile's claim request (authenticated)."""

    nonce: str = Field(min_length=1, max_length=64)
    trust_key_fp: str = Field(min_length=1, max_length=128)
    mobile_device_id: str = Field(min_length=1, max_length=128)
    hostname_hint: str | None = Field(default=None, max_length=255)
    friendly_name: str | None = Field(default=None, max_length=128)


class PairingClaimResponse(BaseModel):
    """Server's reply to a successful claim — no token reaches the mobile."""

    agent_id: str
    agent_friendly_name: str


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
