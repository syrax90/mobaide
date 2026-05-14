"""REST + WebSocket endpoints for mobile token refresh and QR pairing.

QR pairing flow:
* ``POST /api/pairing/initiate``    — agent gets a nonce + QR payload (anon)
* ``POST /api/pairing/claim``       — mobile claims a scanned nonce (auth)
* ``WS   /ws/pairing/{nonce}``      — agent awaits the claim push (anon, single-use)
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    status,
)
from starlette.websockets import WebSocketDisconnect, WebSocketState

from ..auth.dependencies import get_non_admin_api_user, require_role
from ..auth.models import (
    MobileRefreshRequest,
    MobileTokenResponse,
    UserTokenPayload,
)
from .models import (
    PairingClaimRequest,
    PairingClaimResponse,
    PairingInitiateRequest,
    PairingInitiateResponse,
    PairingPushPayload,
)
from .qr_signer import (
    compute_trust_key_fp,
    sign_pairing_payload,
)

_require_admin = require_role("admin")

logger = logging.getLogger(__name__)

router = APIRouter()


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


@router.delete(
    "/api/pairings/{pairing_id}",
    dependencies=[Depends(_require_admin)],
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


def _resolve_public_base_url(request: Request) -> str:
    """Return the public base URL the agent will use to reach this server.

    Prefers the operator-set ``PUBLIC_BASE_URL`` config when present;
    otherwise falls back to the request's scheme + host. The fallback
    only works when the deployment puts the public hostname in the Host
    header — typical for Cloudflare Tunnel / nginx reverse proxies.
    """
    config = request.app.state.config
    if config.public_base_url:
        return config.public_base_url.rstrip("/")
    base = str(request.base_url).rstrip("/")
    return base


def _server_keys_or_503(request: Request) -> tuple[str, str]:
    """Return ``(public_key_b64, private_key_b64)`` or raise 503.

    The QR pairing flow can only operate when the relay holds the
    manifest signing private key. The trust-key endpoint follows the
    same fail-closed pattern when only the public half is set.
    """
    config = request.app.state.config
    pub = config.manifest_signing_public_key_b64
    priv = config.manifest_signing_private_key_b64
    if not pub or not priv:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "QR pairing not configured — set "
                "MANIFEST_SIGNING_PUBLIC_KEY and "
                "MANIFEST_SIGNING_PRIVATE_KEY"
            ),
        )
    return pub, priv


@router.post(
    "/api/pairing/initiate",
    response_model=PairingInitiateResponse,
    tags=["pairing"],
)
async def initiate_pairing(
    request: Request, body: PairingInitiateRequest
) -> PairingInitiateResponse:
    """Allocate a single-use pairing nonce and return its QR payload.

    Anonymous: the install scripts and the running agent both call this
    before any credentials exist. Replay-resistance comes from the
    192-bit nonce + the short TTL + the single-flight CAS at claim time.
    """
    pub_key_b64, _ = _server_keys_or_503(request)
    config = request.app.state.config
    db = request.app.state.db

    metadata = {
        k: v
        for k, v in {
            "hostname": body.hostname,
            "os": body.os,
            "current_agent_id": body.current_agent_id,
        }.items()
        if v is not None
    }
    metadata_json = json.dumps(metadata, sort_keys=True) if metadata else None
    relay_url = _resolve_public_base_url(request)
    raw_nonce, expires_at = await db.create_pairing_token(
        agent_id=None,
        ttl_seconds=config.pairing_qr_token_ttl_seconds,
        agent_metadata=metadata_json,
        relay_url=relay_url,
    )

    trust_key_fp = compute_trust_key_fp(pub_key_b64)
    exp_unix = int(datetime.fromisoformat(expires_at).timestamp())
    qr_payload = (
        f"MOBA1|{relay_url}|{raw_nonce}|{trust_key_fp}|{exp_unix}"
    )

    logger.info(
        "pairing.qr_initiated nonce_prefix=%s expires_at=%s "
        "metadata_keys=%s",
        raw_nonce[:8],
        expires_at,
        sorted(metadata.keys()),
    )

    return PairingInitiateResponse(
        nonce=raw_nonce,
        expires_at=expires_at,
        trust_key_fp=trust_key_fp,
        relay_url=relay_url,
        qr_payload=qr_payload,
    )


@router.post(
    "/api/pairing/claim",
    response_model=PairingClaimResponse,
    tags=["pairing"],
)
async def claim_pairing(
    request: Request,
    body: PairingClaimRequest,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
) -> PairingClaimResponse:
    """Consume a pairing nonce; create or transfer the agent; push to the WS.

    Authenticated as a non-admin user. The successful CAS-consume of the
    nonce is the single-flight authority: replays return 410 Gone.
    """
    pub_key_b64, priv_key_b64 = _server_keys_or_503(request)
    if body.trust_key_fp != compute_trust_key_fp(pub_key_b64):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="trust_key_fp does not match relay's current trust key",
        )

    db = request.app.state.db
    registry = request.app.state.pairing_ws_registry

    pre = await db.lookup_pairing_token_metadata(body.nonce)
    if pre is None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Unknown pairing nonce",
        )
    if pre["consumed_at"] is not None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Pairing nonce already consumed",
        )
    expires_at_iso = pre["expires_at"]
    if datetime.fromisoformat(expires_at_iso) <= datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Pairing nonce expired",
        )

    metadata: dict = json.loads(pre["agent_metadata"]) if pre["agent_metadata"] else {}
    current_agent_id = metadata.get("current_agent_id")
    hostname_meta = metadata.get("hostname")

    if current_agent_id:
        existing_owner = await db.get_agent_owner(current_agent_id)
        if existing_owner is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    "Re-pair target agent does not exist on this relay; "
                    "the agent may need to clear its credentials and "
                    "first-pair instead."
                ),
            )
        if existing_owner != user.sub:
            await db.transfer_agent_owner(current_agent_id, user.sub)
        new_token = await db.reregister_agent(current_agent_id)
        agent_id = current_agent_id
        agent_row = await db.get_agent(agent_id)
        friendly_name = (
            (agent_row or {}).get("name")
            or body.friendly_name
            or hostname_meta
            or "agent"
        )
    else:
        friendly_name = (
            body.friendly_name
            or body.hostname_hint
            or hostname_meta
            or "agent"
        )
        agent_id, new_token = await db.register_agent(
            name=friendly_name, owner_user_id=user.sub
        )

    claimed = await db.claim_pairing_nonce(
        raw_token=body.nonce,
        mobile_device_id=body.mobile_device_id,
        claimed_agent_id=agent_id,
        claimed_token_raw=new_token,
    )
    if not claimed:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Pairing nonce already consumed",
        )

    # Echo the relay_url persisted at initiate time, not whatever
    # ``request.base_url`` happens to resolve to now. Two reasons:
    # (1) when the mobile and the agent reach the relay via
    # differently-configured proxy paths (e.g. mobile direct over
    # https, agent through a tunnel that lands as http), the scheme
    # the claim handler infers can disagree with the scheme the
    # initiate handler inferred — the agent then rejects its own
    # credentials on relay_url strict-equality. (2) the WS recovery
    # branch reads the same column, so dispatch and recovery now
    # produce byte-identical payloads.
    relay_url = pre.get("relay_url") or _resolve_public_base_url(request)
    issued_at = datetime.now(timezone.utc).isoformat()
    push_payload_unsigned = {
        "type": "claim_completed",
        "agent_id": agent_id,
        "agent_token": new_token,
        "relay_url": relay_url,
        "agent_friendly_name": friendly_name,
        "issued_at": issued_at,
    }
    signature_b64 = sign_pairing_payload(priv_key_b64, push_payload_unsigned)
    push_payload = PairingPushPayload(
        **push_payload_unsigned, signature_b64=signature_b64
    )
    delivered = await registry.dispatch_to_waiter(
        body.nonce, push_payload.model_dump_json().encode("utf-8")
    )
    logger.info(
        "pairing.claim_completed agent_id=%s by_user=%s repair=%s "
        "ws_delivered=%s",
        agent_id,
        user.sub,
        bool(current_agent_id),
        delivered,
    )

    return PairingClaimResponse(
        agent_id=agent_id,
        agent_friendly_name=friendly_name,
        ws_delivered=delivered,
    )


async def _build_signed_push_for_consumed(
    db, agent_id: str, agent_token: str, relay_url: str, priv_key_b64: str
) -> bytes:
    """Re-build + re-sign the WS push payload from a consumed-credentials read.

    Used by the WS handler when a claim arrived before the agent connected:
    the original signature in the claim handler was discarded after the
    failed dispatch, so we sign a fresh payload with a current ``issued_at``.
    Mobile-side re-pair-recovery scenarios ride this path.
    """
    agent_row = await db.get_agent(agent_id) or {}
    friendly_name = agent_row.get("name") or "agent"
    issued_at = datetime.now(timezone.utc).isoformat()
    push_unsigned = {
        "type": "claim_completed",
        "agent_id": agent_id,
        "agent_token": agent_token,
        "relay_url": relay_url,
        "agent_friendly_name": friendly_name,
        "issued_at": issued_at,
    }
    signature_b64 = sign_pairing_payload(priv_key_b64, push_unsigned)
    push_payload = PairingPushPayload(
        **push_unsigned, signature_b64=signature_b64
    )
    return push_payload.model_dump_json().encode("utf-8")


@router.websocket("/ws/pairing/{nonce}")
async def pairing_ws(websocket: WebSocket, nonce: str) -> None:
    """Agent's per-nonce credential delivery channel.

    Single-use: a second WS connection for the same nonce is closed with
    4001 ``duplicate``. Bounded by the nonce's TTL: if no claim arrives
    before ``expires_at``, the WS closes with 4408 ``TTL exceeded`` and
    the agent re-renders the QR.

    Race resolution: a claim that lands before
    the agent connects, between the lookup and the waiter registration,
    or after a transient WS disconnect leaves credentials in
    ``pairing_tokens.claimed_token_raw``. After registering the waiter,
    we synchronously try ``read_and_clear_claimed_credentials``; a hit
    means the claim already happened and we deliver the credentials over
    this connection (re-signing the payload with a fresh ``issued_at``).
    A miss means the queue path (claim arrives later) is the active
    delivery channel.
    """
    app = websocket.app
    db = app.state.db
    registry = app.state.pairing_ws_registry
    config = app.state.config

    pre = await db.lookup_pairing_token_metadata(nonce)
    if pre is None:
        await websocket.close(code=4404, reason="Unknown nonce")
        return
    expires_at_iso = pre["expires_at"]
    expires_at = datetime.fromisoformat(expires_at_iso)
    if pre["consumed_at"] is None and expires_at <= datetime.now(timezone.utc):
        await websocket.close(code=4408, reason="TTL exceeded")
        return

    try:
        queue = await registry.register_waiter(nonce)
    except KeyError:
        await websocket.close(code=4001, reason="duplicate")
        return

    # The accept() and everything following live inside the try/finally so
    # an exception during the WS handshake (TLS-terminated proxy edge cases,
    # client disconnect mid-upgrade) does not leak the registered waiter.
    # The only path that bypasses the cleanup is the duplicate-close above.
    try:
        await websocket.accept()
        await websocket.send_json({"type": "ready"})

        priv_key_b64 = config.manifest_signing_private_key_b64
        consumed = None
        if priv_key_b64:
            # Recovery only fires when we can re-sign. If the operator
            # blanked MANIFEST_SIGNING_PRIVATE_KEY between claim time and
            # this reconnect, leave ``claimed_token_raw`` in place so a
            # future reconnect (after the key is restored) can still
            # deliver — single-flight is preserved by the next call to
            # ``read_and_clear_claimed_credentials``.
            consumed = await db.read_and_clear_claimed_credentials(nonce)
        else:
            logger.error(
                "pairing_ws.recovery_signing_key_missing nonce_prefix=%s",
                nonce[:8],
            )
            await websocket.close(
                code=1011, reason="server signing key unavailable"
            )
            return
        if consumed is not None:
            # Echo the persisted relay_url from initiate time. This is
            # the same column the claim handler reads when building the
            # dispatch push, so dispatch and recovery now produce
            # byte-identical payloads — the agent's strict-equality
            # check on ``relay_url`` cannot disagree between the two
            # paths. Falling back to ``request.base_url``-style
            # resolution only happens for legacy rows (pre-migration-8)
            # where the column is still NULL; those nonces will time
            # out within the 120 s TTL after upgrade, after which the
            # fallback is unused.
            relay_url = (
                consumed.get("relay_url")
                or pre.get("relay_url")
                or _resolve_public_base_url_from_app(websocket)
            )
            payload = await _build_signed_push_for_consumed(
                db,
                agent_id=consumed["claimed_agent_id"],
                agent_token=consumed["claimed_token_raw"],
                relay_url=relay_url,
                priv_key_b64=priv_key_b64,
            )
            await websocket.send_bytes(payload)
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close(code=1000, reason="claim delivered")
            return

        if pre["consumed_at"] is not None:
            await websocket.close(code=4410, reason="Nonce already delivered")
            return

        timeout_s = max(
            0.0, (expires_at - datetime.now(timezone.utc)).total_seconds()
        )
        try:
            payload = await asyncio.wait_for(queue.get(), timeout=timeout_s)
        except asyncio.TimeoutError:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close(code=4408, reason="TTL exceeded")
            return

        await websocket.send_bytes(payload)
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.close(code=1000, reason="claim delivered")
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("pairing_ws.unexpected_error nonce_prefix=%s", nonce[:8])
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.close(code=1011, reason="server error")
    finally:
        await registry.unregister_waiter(nonce)


def _resolve_public_base_url_from_app(websocket: WebSocket) -> str:
    """Legacy-row fallback for WS context (no Request).

    Used only when ``pairing_tokens.relay_url`` is NULL (rows created
    before migration 8). The dispatch/recovery paths read the persisted
    column first; this function never runs for nonces issued by the
    current server build.
    """
    config = websocket.app.state.config
    if config.public_base_url:
        return config.public_base_url.rstrip("/")
    scheme = "https" if websocket.url.scheme == "wss" else "http"
    netloc = websocket.url.netloc
    return f"{scheme}://{netloc}"
