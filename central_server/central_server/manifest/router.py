"""HTTP routes for the agent rollout manifest.

The release pipeline POSTs a signed manifest to ``/api/internal/agent_manifest``
with a release-time bearer token. Deployed agents fetch the latest manifest
from ``/api/v1/agent/manifest.json`` and verify the signature locally before
applying it via the staged-binary auto-updater.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from ..relay.envelope import CH_CONTROL, encode
from ..relay.routing import RoutingTable
from .store import (
    ManifestSignatureError,
    ManifestValidationError,
    StoredManifest,
)


logger = logging.getLogger(__name__)


router = APIRouter()


class PublishManifestRequest(BaseModel):
    body: dict[str, Any] = Field(
        ..., description="Manifest body — must contain version/tarball_url/sha256/etc."
    )
    signature_b64: str = Field(
        ..., description="Base64-encoded Ed25519 signature over canonical JSON of body."
    )


class ManifestResponse(BaseModel):
    body: dict[str, Any]
    signature_b64: str


@router.get("/api/v1/agent/manifest.json", response_model=ManifestResponse, tags=["manifest"])
async def get_manifest(request: Request) -> ManifestResponse:
    store = request.app.state.manifest_store
    record = store.read()
    if record is None:
        raise HTTPException(status_code=404, detail="No manifest published yet")
    return ManifestResponse(body=record.body, signature_b64=record.signature_b64)


@router.post("/api/internal/agent_manifest", response_model=ManifestResponse, tags=["manifest"])
async def publish_manifest(
    payload: PublishManifestRequest,
    request: Request,
    x_release_token: str | None = Header(default=None, alias="X-Release-Token"),
) -> ManifestResponse:
    config = request.app.state.config
    expected = config.release_auth_token
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Manifest publishing is disabled — RELEASE_AUTH_TOKEN is not configured",
        )
    if not x_release_token or not hmac.compare_digest(x_release_token, expected):
        raise HTTPException(status_code=401, detail="Invalid release token")

    store = request.app.state.manifest_store
    try:
        record = store.write(
            payload.body,
            payload.signature_b64,
            public_key_b64=config.manifest_signing_public_key_b64,
        )
    except ManifestValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ManifestSignatureError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Push the new manifest pointer to every connected agent over CH_CONTROL
    # so updates apply within one heartbeat cycle without a reconnect.
    # Best-effort: queue saturation is logged and never converted into an
    # HTTP error — the manifest is durably stored either way and any agent
    # missed by the broadcast picks it up at its next ``auth_ok``.
    routing = getattr(request.app.state, "routing", None)
    if routing is not None:
        broadcast_manifest_update(routing, record)

    return ManifestResponse(body=record.body, signature_b64=record.signature_b64)


def broadcast_manifest_update(
    routing: RoutingTable, record: StoredManifest
) -> int:
    """Push ``agent_manifest_update`` to every online agent.

    Returns the number of agents that accepted the frame. The frame is
    enqueued without awaiting delivery; the agent's writer task picks it
    up on the next loop iteration.

    The agent set is snapshotted once and the envelope encoded once: the
    same bytes are pushed onto every agent's queue, so the work is
    constant per fan-out rather than O(N) JSON serialisations.
    """
    body = {
        "type": "agent_manifest_update",
        "version": record.body.get("version"),
        "manifest_url": "/api/v1/agent/manifest.json",
        "manifest_signature": record.signature_b64,
    }
    envelope = encode(CH_CONTROL, "", json.dumps(body).encode())
    agents = routing.list_online_agents()
    delivered = 0
    for conn in agents:
        try:
            conn.send_queue.put_nowait(envelope)
            delivered += 1
        except asyncio.QueueFull:
            logger.warning(
                "manifest_broadcast.queue_full agent_id=%s — agent will pick up at next auth_ok",
                conn.agent_id,
            )
    logger.info(
        "manifest_broadcast.dispatched version=%s delivered=%d total_online=%d",
        record.body.get("version"),
        delivered,
        len(agents),
    )
    return delivered
