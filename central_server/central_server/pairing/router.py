"""REST + WebSocket endpoints for mobile token refresh and agent pairing.

QR pairing (agent-first):
* ``POST /api/pairing/initiate``    — agent gets a nonce + QR payload (anon)
* ``POST /api/pairing/claim``       — a client claims a scanned nonce (auth)
* ``WS   /ws/pairing/{nonce}``      — agent awaits the claim push (anon, single-use)

Device-code pairing (browser-first) mints a short retypeable code, redeems
it into an ordinary pairing nonce, and rejoins the flow above at the same
WebSocket. Both directions finish through ``_complete_pairing`` so the
ownership, device-registry and trust-pin rules cannot drift apart.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    WebSocket,
    status,
)
from starlette.websockets import WebSocketDisconnect, WebSocketState

from ..auth.dependencies import get_non_admin_api_user
from ..auth.models import (
    MobileRefreshRequest,
    MobileTokenResponse,
    UserTokenPayload,
)
from ..ws_origin import enforce_ws_origin
from .device_code import (
    format_code,
    generate_code,
    hash_device_code,
    mask_email,
    normalize_code,
)
from .models import (
    DeviceCodeConfirmRequest,
    DeviceCodeMintResponse,
    DeviceCodeRedeemRequest,
    DeviceCodeRedeemResponse,
    DeviceCodeStatusResponse,
    PairingClaimRequest,
    PairingClaimResponse,
    PairingInitiateRequest,
    PairingInitiateResponse,
    PairingPushPayload,
    TrustPinResponse,
)
from .qr_signer import (
    compute_trust_key_fp,
    encode_pairing_payload,
    hash_pairing_nonce,
    sign_pairing_payload,
)

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


def _agent_metadata_json(
    *,
    hostname: str | None,
    os_name: str | None,
    current_agent_id: str | None,
) -> str | None:
    """Serialise initiate/redeem hints into ``pairing_tokens.agent_metadata``.

    Sorted keys so the stored blob is stable, and ``None`` when nothing was
    reported so the column stays NULL rather than holding ``"{}"``. Both
    pairing directions write it and ``_complete_pairing`` is the only
    reader, which is what keeps first-pair vs re-pair intent identical
    across the two front halves.
    """
    metadata = {
        k: v
        for k, v in {
            "hostname": hostname,
            "os": os_name,
            "current_agent_id": current_agent_id,
        }.items()
        if v is not None
    }
    return json.dumps(metadata, sort_keys=True) if metadata else None


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

    metadata_json = _agent_metadata_json(
        hostname=body.hostname,
        os_name=body.os,
        current_agent_id=body.current_agent_id,
    )
    relay_url = _resolve_public_base_url(request)
    raw_nonce, expires_at = await db.create_pairing_token(
        agent_id=None,
        ttl_seconds=config.pairing_qr_token_ttl_seconds,
        agent_metadata=metadata_json,
        relay_url=relay_url,
    )

    trust_key_fp = compute_trust_key_fp(pub_key_b64)
    exp_unix = int(datetime.fromisoformat(expires_at).timestamp())
    qr_payload = encode_pairing_payload(
        relay_url, raw_nonce, trust_key_fp, exp_unix
    )

    logger.info(
        "pairing.qr_initiated nonce_prefix=%s expires_at=%s "
        "metadata_keys=%s",
        raw_nonce[:8],
        expires_at,
        sorted(json.loads(metadata_json)) if metadata_json else [],
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
    """Consume a pairing nonce; create or re-register the agent for its owner; push to the WS.

    Authenticated as a non-admin user. The successful CAS-consume of the
    nonce is the single-flight authority: replays return 410 Gone.
    """
    pub_key_b64, priv_key_b64 = _server_keys_or_503(request)
    current_fp = compute_trust_key_fp(pub_key_b64)
    if body.trust_key_fp != current_fp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="trust_key_fp does not match relay's current trust key",
        )

    db = request.app.state.db
    pre = await db.lookup_pairing_token_metadata(body.nonce)
    if pre is None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Unknown pairing nonce",
        )
    return await _complete_pairing(
        request=request,
        user=user,
        pre=pre,
        mobile_device_id=body.mobile_device_id,
        mobile_device_name=body.mobile_device_name,
        platform=body.platform,
        hostname_hint=body.hostname_hint,
        friendly_name=body.friendly_name,
        priv_key_b64=priv_key_b64,
        current_fp=current_fp,
    )


async def _enforce_account_trust_pin(
    request: Request, user_id: str, current_fp: str
) -> None:
    """Compare the account's recorded signing fingerprint with the live one.

    Records the fingerprint an account first paired against, then refuses
    a later claim carrying a different one. Since a claim whose
    ``trust_key_fp`` disagrees with the relay's current key is already a
    400, this only ever fires when the relay's own signing key changed —
    it is a rotation tripwire, not an MITM detector (the relay both stores
    the pin and serves the value it is compared against).

    Two escape hatches, because the incident that forces a rotation is
    exactly the one this would otherwise brick: ``TRUST_KEY_PIN_ENFORCED``
    turns enforcement off (and lets the stale pin be replaced as accounts
    re-pair), and an account that currently owns no agents has nothing for
    the pin to protect, so it re-pins instead of locking out.
    """
    db = request.app.state.db
    config = request.app.state.config
    pinned = await db.get_user_trust_key_fp(user_id)
    if pinned is None or pinned == current_fp:
        return
    if not config.trust_key_pin_enforced:
        await db.clear_user_trust_key_fp(user_id)
        logger.warning(
            "pairing.trust_pin_cleared_unenforced user=%s pinned_prefix=%s",
            user_id,
            pinned[:8],
        )
        return
    if await db.count_agents_for_owner(user_id) == 0:
        await db.clear_user_trust_key_fp(user_id)
        logger.info(
            "pairing.trust_pin_reset_no_agents user=%s pinned_prefix=%s",
            user_id,
            pinned[:8],
        )
        return
    logger.warning(
        "pairing.trust_pin_mismatch user=%s pinned_prefix=%s current_prefix=%s",
        user_id,
        pinned[:8],
        current_fp[:8],
    )
    # Structured detail rather than a bare string: 409 already carries the
    # "agent belongs to another account" refusal, and the two need
    # different copy. Clients read the discriminator out of ``detail.error``
    # (see ``ApiError.code`` on web) instead of matching on prose.
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error": "trust_key_pin_mismatch",
            "message": (
                "This relay's trust key differs from the one this account "
                "first paired against. Pairing is refused."
            ),
        },
    )


async def _complete_pairing(
    *,
    request: Request,
    user: UserTokenPayload,
    pre: dict,
    mobile_device_id: str,
    mobile_device_name: str | None,
    platform: str | None,
    hostname_hint: str | None,
    friendly_name: str | None,
    priv_key_b64: str,
    current_fp: str,
) -> PairingClaimResponse:
    """Consume a pairing nonce and deliver credentials to its agent.

    The shared back half of both pairing directions: a QR claim and a
    device-code confirm differ only in how they came by ``pre`` (the
    ``pairing_tokens`` row). Everything security-relevant — trust pin,
    agent ownership, device handover, the single-flight CAS — lives here so
    the two front halves cannot drift.

    Addresses the token row by ``pre["token_hash"]`` throughout. The
    device-code row deliberately never stores a raw nonce, and the same
    hash keys the WebSocket waiter registry, so a raw nonce is only ever in
    flight between the server and the agent that redeemed it.
    """
    db = request.app.state.db
    registry = request.app.state.pairing_ws_registry
    token_hash = pre["token_hash"]

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

    await _enforce_account_trust_pin(request, user.sub, current_fp)

    metadata: dict = json.loads(pre["agent_metadata"]) if pre["agent_metadata"] else {}
    current_agent_id = metadata.get("current_agent_id")
    hostname_meta = metadata.get("hostname")

    # Agent-ownership guard, fail-fast before any row mutation or the nonce
    # CAS. An agent that is already paired to an account can only be
    # re-paired by that same account — knowing an ``agent_id`` is not
    # authority to take the agent over. ``initiate`` is anonymous and the
    # ``current_agent_id`` it stamps into the nonce metadata is
    # attacker-controllable, so the authenticated claim is the only place
    # ownership can be enforced. A different account must have the current
    # owner remove the agent first, after which the agent first-pairs anew.
    if current_agent_id:
        existing_owner = await db.get_agent_owner(current_agent_id)
        if existing_owner is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    "Re-pair target agent does not exist on this relay; "
                    "the agent must first-pair as a new agent instead. On "
                    "the agent host run: mobaide-agent re-pair --fresh"
                ),
            )
        if existing_owner != user.sub:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This agent is already paired to a different account; "
                    "its current owner must remove it before it can be "
                    "paired to another account."
                ),
            )

    # Device registry is last-login-wins: an authenticated claim always
    # takes the device over, revoking the prior account's refresh tokens.
    # The upsert runs before the nonce CAS, so a claim that later loses
    # the nonce race still leaves a completed handover — harmless, the
    # retrying user already owns the device.
    previous_owner = await db.upsert_mobile_device(
        device_id=mobile_device_id,
        user_id=user.sub,
        name=mobile_device_name,
        platform=platform,
    )
    if previous_owner is not None:
        logger.info(
            "mobile_device.handover device_id=%s from_user=%s to_user=%s",
            mobile_device_id,
            previous_owner,
            user.sub,
        )

    if current_agent_id:
        # Guarded above: the caller owns ``current_agent_id``. Rotate its
        # token in place (same-owner re-pair).
        new_token = await db.reregister_agent(current_agent_id)
        agent_id = current_agent_id
        agent_row = await db.get_agent(agent_id)
        resolved_name = (
            (agent_row or {}).get("name")
            or friendly_name
            or hostname_meta
            or "agent"
        )
    else:
        resolved_name = (
            friendly_name
            or hostname_hint
            or hostname_meta
            or "agent"
        )
        agent_id, new_token = await db.register_agent(
            name=resolved_name, owner_user_id=user.sub
        )

    claimed = await db.claim_pairing_nonce_by_hash(
        token_hash=token_hash,
        mobile_device_id=mobile_device_id,
        claimed_agent_id=agent_id,
        claimed_token_raw=new_token,
    )
    if not claimed:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Pairing nonce already consumed",
        )

    # Take the account's trust pin only once the claim has actually landed,
    # so a pairing that fails the ownership guard or loses the CAS race
    # cannot leave a pin behind. No-op when the account already has one.
    await db.pin_user_trust_key_fp(user.sub, current_fp)

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
        "agent_friendly_name": resolved_name,
        "issued_at": issued_at,
    }
    signature_b64 = sign_pairing_payload(priv_key_b64, push_payload_unsigned)
    push_payload = PairingPushPayload(
        **push_payload_unsigned, signature_b64=signature_b64
    )
    delivered = await registry.dispatch_to_waiter(
        token_hash, push_payload.model_dump_json().encode("utf-8")
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
        agent_friendly_name=resolved_name,
        ws_delivered=delivered,
    )


def _rate_limited(wait: int) -> HTTPException:
    """The 429 both device-code limiters raise."""
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "error": "device_code_rate_limited",
            "retry_after_seconds": wait,
        },
        headers={"Retry-After": str(wait)},
    )


async def _require_device_code_redeem_quota(request: Request) -> None:
    """Per-IP throttle on the anonymous redeem endpoint.

    Runs as a dependency, so it charges every redeem — which is right for
    a per-IP bucket, where a legitimate operator makes one or two attempts.
    The global circuit breaker deliberately does *not* live here: as a
    dependency it would charge successful pairings too, turning a
    guessing defence into a deployment-wide throughput ceiling. It is
    consumed in the handler, on failures only — see
    ``_charge_failed_device_code_redeem``.

    Falls open when unwired, matching ``_require_reset_ip_quota``.
    """
    limiter = getattr(request.app.state, "device_code_ip_limiter", None)
    if limiter is None:
        return
    ip = request.client.host if request.client else "unknown"
    wait = await limiter.try_acquire(f"ip:{ip}")
    if wait is not None:
        raise _rate_limited(wait)


async def _charge_failed_device_code_redeem(request: Request) -> None:
    """Consume a slot from the global breaker after a rejected redeem.

    The bucket a distributed guesser cannot sidestep: per-IP collapses to
    one shared bucket behind a proxy that does not forward the client
    address, and ``ResendRateLimiter`` evicts least-recently-used keys
    under an address spray. Charged only on failure, so a busy deployment
    pairing legitimately never trips it, while a spray of wrong codes does
    — regardless of how many source addresses it comes from.

    Best-effort: the caller is already raising, so a tripped breaker here
    only means the *next* redeem is refused. Falls open when unwired.
    """
    limiter = getattr(request.app.state, "device_code_global_limiter", None)
    if limiter is None:
        return
    await limiter.try_acquire("global")


async def _require_global_device_code_quota(request: Request) -> None:
    """Refuse a redeem while the global breaker is tripped.

    Uses ``peek`` rather than ``try_acquire`` so the check itself costs
    nothing: only failed redeems are charged.
    """
    limiter = getattr(request.app.state, "device_code_global_limiter", None)
    if limiter is None:
        return
    wait = await limiter.peek("global")
    if wait is not None:
        raise _rate_limited(wait)


async def _require_device_code_mint_quota(
    request: Request, user: UserTokenPayload
) -> None:
    """Cooldown-only throttle on minting, keyed by account.

    Deliberately not an hourly cap: minting invalidates the account's
    previous code, so a user who reloads the pairing screen re-mints, and a
    cap would lock them out of pairing for an hour with no way forward. The
    limiter treats ``hourly_cap=0`` as "no cap" (see the ``> 0`` guard in
    ``ResendRateLimiter.try_acquire``), leaving just the cooldown to absorb
    double-submits.
    """
    limiter = getattr(request.app.state, "device_code_mint_limiter", None)
    if limiter is None:
        return
    wait = await limiter.try_acquire(f"user:{user.sub}")
    if wait is not None:
        raise _rate_limited(wait)


@router.post(
    "/api/pairing/device-code",
    response_model=DeviceCodeMintResponse,
    tags=["pairing"],
)
async def mint_device_code(
    request: Request,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
) -> DeviceCodeMintResponse:
    """Mint a short code for the operator to retype on the agent host."""
    _server_keys_or_503(request)
    await _require_device_code_mint_quota(request, user)
    config = request.app.state.config
    db = request.app.state.db

    code = generate_code()
    display_code = format_code(code)
    request_id = str(uuid.uuid4())
    expires_at = await db.create_device_code(
        request_id=request_id,
        code_hash=hash_device_code(config.jwt_secret_key, code),
        user_id=user.sub,
        ttl_seconds=config.pairing_device_code_ttl_seconds,
    )
    logger.info(
        "pairing.device_code_minted request_id=%s by_user=%s expires_at=%s",
        request_id,
        user.sub,
        expires_at,
    )
    return DeviceCodeMintResponse(
        request_id=request_id,
        code=display_code,
        expires_at=expires_at,
        verification_command=f"mobaide-agent pair {display_code}",
    )


@router.post(
    "/api/pairing/device-code/redeem",
    response_model=DeviceCodeRedeemResponse,
    tags=["pairing"],
)
async def redeem_device_code(
    request: Request,
    body: DeviceCodeRedeemRequest,
    _ip_quota: None = Depends(_require_device_code_redeem_quota),
    _global_quota: None = Depends(_require_global_device_code_quota),
) -> DeviceCodeRedeemResponse:
    """Exchange a retyped code for a pairing nonce. Anonymous by necessity.

    The agent has no credentials yet — that is the whole point of pairing —
    so this endpoint authenticates nothing. It hands back no secret beyond
    a nonce that is worthless without the account owner's separate confirm,
    plus the masked account name the operator needs to decide whether to
    grant this machine to that account at all.
    """
    pub_key_b64, _ = _server_keys_or_503(request)
    config = request.app.state.config
    db = request.app.state.db

    normalized = normalize_code(body.code)
    if normalized is None:
        await _charge_failed_device_code_redeem(request)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed pairing code",
        )

    outcome = await db.redeem_device_code(
        code_hash=hash_device_code(config.jwt_secret_key, normalized),
        redeemer_id=body.redeemer_id,
        hostname=body.hostname,
        os_name=body.os,
        current_agent_id=body.current_agent_id,
        agent_metadata=_agent_metadata_json(
            hostname=body.hostname,
            os_name=body.os,
            current_agent_id=body.current_agent_id,
        ),
        nonce_ttl_seconds=config.pairing_device_code_confirm_ttl_seconds,
        relay_url=_resolve_public_base_url(request),
    )
    if outcome is None:
        await _charge_failed_device_code_redeem(request)
        logger.info("pairing.device_code_redeem_rejected")
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=(
                "That pairing code is not valid. It may have expired, "
                "already been used by another machine, or been replaced by "
                "a newer code."
            ),
        )

    owner = await db.get_user_by_id(outcome["user_id"])
    logger.info(
        "pairing.device_code_redeemed request_id=%s replayed=%s",
        outcome["request_id"],
        outcome["replayed"],
    )
    return DeviceCodeRedeemResponse(
        nonce=outcome["nonce"],
        expires_at=outcome["expires_at"],
        trust_key_fp=compute_trust_key_fp(pub_key_b64),
        relay_url=_resolve_public_base_url(request),
        account_hint=mask_email((owner or {}).get("email", "")),
        is_repair=bool(body.current_agent_id),
        replayed=outcome["replayed"],
    )


async def _device_code_repair_verdict(
    db, current_agent_id, user_id
) -> tuple[str | None, str | None]:
    """Resolve a re-pair target into ``(blocked_reason, agent_name)``.

    Two jobs, one lookup. The blocked reason is surfaced in the poll
    response so a re-pair destined to 404 or 409 says so while the code and
    nonce are still spendable, rather than burning both on a click.

    The name matters just as much: when the redeeming host claims an
    ``agent_id`` the confirming account already owns, approving does not
    add a machine — it *moves* that agent, rotating its token onto the
    redeemer and knocking the real host offline. "Is this your machine?" is
    the wrong question there, so the clients need something to name.
    """
    if not current_agent_id:
        return None, None
    existing_owner = await db.get_agent_owner(current_agent_id)
    if existing_owner is None:
        return "unknown_agent", None
    if existing_owner != user_id:
        return "owned_by_other", None
    agent_row = await db.get_agent(current_agent_id)
    return None, (agent_row or {}).get("name")


@router.get(
    "/api/pairing/device-code/{request_id}",
    response_model=DeviceCodeStatusResponse,
    tags=["pairing"],
)
async def get_device_code_status(
    request: Request,
    request_id: str,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
) -> DeviceCodeStatusResponse:
    """Poll a minted code. Owner-scoped: a foreign ``request_id`` is a 404."""
    db = request.app.state.db
    row = await db.get_device_code(request_id, user.sub)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown pairing request",
        )

    now = datetime.now(timezone.utc)
    expires_at = row["expires_at"]
    if row["canceled_at"] is not None:
        state = "canceled"
    elif row["confirmed_at"] is not None:
        state = "confirmed"
    elif row["redeemed_at"] is not None:
        # Past redeem the deadline that matters is the *nonce's*, not the
        # code's: the confirm CAS tests ``pairing_tokens.expires_at``.
        # Reporting the code's window here would leave the browser offering
        # a confirm button that is guaranteed to 410.
        token = await db.lookup_pairing_token_metadata_by_hash(
            row["pairing_nonce_hash"] or ""
        )
        if token is None or datetime.fromisoformat(token["expires_at"]) <= now:
            state = "expired"
        else:
            expires_at = token["expires_at"]
            state = "redeemed"
    elif datetime.fromisoformat(expires_at) <= now:
        state = "expired"
    else:
        state = "pending"

    blocked_reason = None
    repair_agent_name = None
    if state == "redeemed":
        blocked_reason, repair_agent_name = await _device_code_repair_verdict(
            db, row["redeem_agent_id"], user.sub
        )
    return DeviceCodeStatusResponse(
        status=state,
        expires_at=expires_at,
        hostname=row["redeem_hostname"],
        os=row["redeem_os"],
        is_repair=bool(row["redeem_agent_id"]),
        repair_agent_name=repair_agent_name,
        blocked_reason=blocked_reason,
    )


@router.post(
    "/api/pairing/device-code/{request_id}/confirm",
    response_model=PairingClaimResponse,
    tags=["pairing"],
)
async def confirm_device_code(
    request: Request,
    request_id: str,
    body: DeviceCodeConfirmRequest,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
) -> PairingClaimResponse:
    """Approve a redeemed code and release credentials to the waiting agent."""
    pub_key_b64, priv_key_b64 = _server_keys_or_503(request)
    db = request.app.state.db

    row = await db.get_device_code(request_id, user.sub)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown pairing request",
        )
    if row["canceled_at"] is not None or row["confirmed_at"] is not None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This pairing request is no longer open",
        )
    if row["redeemed_at"] is None or row["pairing_nonce_hash"] is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No machine has entered this code yet",
        )

    pre = await db.lookup_pairing_token_metadata_by_hash(
        row["pairing_nonce_hash"]
    )
    if pre is None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Pairing nonce expired",
        )

    result = await _complete_pairing(
        request=request,
        user=user,
        pre=pre,
        mobile_device_id=body.mobile_device_id,
        mobile_device_name=body.mobile_device_name,
        platform=body.platform,
        hostname_hint=row["redeem_hostname"],
        friendly_name=body.friendly_name,
        priv_key_b64=priv_key_b64,
        current_fp=compute_trust_key_fp(pub_key_b64),
    )
    # Marked only after the pairing landed, so a confirm rejected by the
    # ownership guard leaves the request open for a retry.
    await db.mark_device_code_confirmed(request_id, user.sub)
    return result


@router.delete(
    "/api/pairing/device-code/{request_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["pairing"],
)
async def cancel_device_code(
    request: Request,
    request_id: str,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
) -> Response:
    """Abandon a pairing request and expire any nonce it handed out."""
    db = request.app.state.db
    if not await db.cancel_device_code(request_id, user.sub):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown pairing request",
        )
    logger.info(
        "pairing.device_code_canceled request_id=%s by_user=%s",
        request_id,
        user.sub,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/api/pairing/trust-pin",
    response_model=TrustPinResponse,
    tags=["pairing"],
)
async def get_trust_pin(
    request: Request,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
) -> TrustPinResponse:
    """Report the relay's signing fingerprint and the account's recorded pin.

    Advisory only. Clients may display the pair and warn when they differ,
    but must not copy ``account_trust_key_fp`` into a local trust store.
    """
    pub_key_b64, _ = _server_keys_or_503(request)
    db = request.app.state.db
    return TrustPinResponse(
        relay_url=_resolve_public_base_url(request),
        relay_trust_key_fp=compute_trust_key_fp(pub_key_b64),
        account_trust_key_fp=await db.get_user_trust_key_fp(user.sub),
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
    if not await enforce_ws_origin(websocket):
        return

    app = websocket.app
    db = app.state.db
    registry = app.state.pairing_ws_registry
    config = app.state.config

    # Hashing at the boundary keeps the raw nonce out of server memory and
    # lets the device-code confirm path — which only ever holds the hash —
    # dispatch to this same waiter.
    token_hash = hash_pairing_nonce(nonce)

    pre = await db.lookup_pairing_token_metadata_by_hash(token_hash)
    if pre is None:
        await websocket.close(code=4404, reason="Unknown nonce")
        return
    expires_at_iso = pre["expires_at"]
    expires_at = datetime.fromisoformat(expires_at_iso)
    if pre["consumed_at"] is None and expires_at <= datetime.now(timezone.utc):
        await websocket.close(code=4408, reason="TTL exceeded")
        return

    try:
        queue = await registry.register_waiter(token_hash)
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
        await registry.unregister_waiter(token_hash)


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
