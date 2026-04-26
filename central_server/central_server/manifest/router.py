"""HTTP routes for the agent rollout manifest.

The release pipeline POSTs a signed manifest to ``/api/internal/agent_manifest``
with a release-time bearer token. Deployed agents fetch the latest manifest
from ``/api/v1/agent/manifest.json`` and verify the signature locally before
applying it via the staged-binary auto-updater.
"""
from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .store import (
    ManifestSignatureError,
    ManifestValidationError,
)


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
    return ManifestResponse(body=record.body, signature_b64=record.signature_b64)
