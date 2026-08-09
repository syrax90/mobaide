"""Pydantic models for both pairing directions.

QR (agent-first): the agent calls ``POST /api/pairing/initiate`` to get a
nonce + QR payload, a client calls ``POST /api/pairing/claim`` to consume
it, and the server pushes a signed ``PairingPushPayload`` over the agent's
``WS /ws/pairing/{nonce}``.

Device code (browser-first): a signed-in client mints a short code, the
operator retypes it into ``mobaide-agent pair``, which redeems it for a
nonce and waits on the same WebSocket. Only the front half differs; the
credential delivery is shared.
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
    platform: str | None = Field(default=None, max_length=32)
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


class DeviceCodeMintResponse(BaseModel):
    """Server's reply to ``POST /api/pairing/device-code``.

    ``request_id`` is the handle every follow-up call uses, so the code
    itself never travels in a URL path or query string where the relay's
    access log would capture it.
    """

    request_id: str
    code: str
    expires_at: str
    verification_command: str


class DeviceCodeRedeemRequest(BaseModel):
    """The agent's anonymous redeem, keyed by the retyped code.

    ``redeemer_id`` is generated and persisted by the agent *before* the
    request goes out. The nonce is handed out exactly once, so without a
    stable redeemer identity a response lost in transit would leave the
    operator holding a burnt code and no way to reach the pairing
    WebSocket; a retry carrying the same id is answered from the existing
    row instead of rejected as a double redeem.
    """

    code: str = Field(min_length=1, max_length=64)
    redeemer_id: str = Field(min_length=8, max_length=64)
    hostname: str | None = Field(default=None, max_length=255)
    os: str | None = Field(default=None, max_length=64)
    current_agent_id: str | None = Field(default=None, max_length=64)


class DeviceCodeRedeemResponse(BaseModel):
    """What the agent needs to wait for its credentials.

    Shaped like ``PairingInitiateResponse`` minus the QR text: the agent
    derives the WS URL from ``RELAY_SERVER_URL`` + ``nonce`` exactly as it
    does for the QR flow.

    ``account_hint`` is a masked address, and it is the load-bearing
    control of the reversed direction. Because the browser mints the code,
    a confirmation shown only in the browser is confirmed by whoever minted
    it — including an attacker who talked the operator into running the
    command. Naming the account on the agent's own terminal is what lets
    the human refuse.

    ``nonce`` is ``None`` on a replayed redeem: it is never stored
    server-side, so a retrying agent reads it back from its own pending
    file.
    """

    nonce: str | None = None
    expires_at: str
    trust_key_fp: str
    relay_url: str
    account_hint: str
    is_repair: bool = False
    replayed: bool = False


class DeviceCodeStatusResponse(BaseModel):
    """Poll state for the browser half of the flow.

    ``hostname`` / ``os`` come from the anonymous redeem body and are
    therefore attacker-settable — the same caveat that applies to the QR
    flow's ``current_agent_id``. They are shown so a legitimate operator
    can recognise their machine, not as an authentication signal.

    ``blocked_reason`` surfaces the ownership verdict *before* the confirm
    click, so a re-pair that is going to 404 or 409 says so while the code
    and nonce are still spendable.

    ``repair_agent_name`` is set only when the redeeming host claims an
    agent this account already owns. Approving then *moves* that agent —
    rotating its token onto the redeemer and knocking the real host
    offline — so the clients must name it rather than asking the
    first-pair question.
    """

    status: Literal[
        "pending", "redeemed", "confirmed", "canceled", "expired"
    ]
    expires_at: str
    hostname: str | None = None
    os: str | None = None
    is_repair: bool = False
    repair_agent_name: str | None = None
    blocked_reason: Literal["unknown_agent", "owned_by_other"] | None = None


class DeviceCodeConfirmRequest(BaseModel):
    """Browser's approval of a redeemed code.

    Carries the same device fields as ``PairingClaimRequest`` because the
    confirm runs the identical completion path.
    """

    mobile_device_id: str = Field(pattern=MOBILE_DEVICE_ID_PATTERN)
    mobile_device_name: str | None = Field(default=None, max_length=255)
    platform: str | None = Field(default=None, max_length=32)
    friendly_name: str | None = Field(default=None, max_length=128)


class TrustPinResponse(BaseModel):
    """The relay's current signing fingerprint and the account's pin.

    Read-only and advisory. Clients display it and may warn on a
    difference; they must NOT write it into their local trust store.
    """

    relay_url: str
    relay_trust_key_fp: str
    account_trust_key_fp: str | None = None


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
