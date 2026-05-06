"""Cover ``GET /api/v1/agent/trust_key``.

The endpoint exposes the manifest-signing public key so the agent's
install scripts and the ``mobaide-agent fetch-trust-key`` CLI can
bootstrap ``<prefix>/trust/central_server.pub`` without operator-side
env var plumbing. Returns 503 when ``MANIFEST_SIGNING_PUBLIC_KEY`` is
not configured (mirrors the existing manifest-publish 503 pattern).
The endpoint is anonymous — the install host has no credentials yet.
"""
from __future__ import annotations

import base64

import pytest


@pytest.mark.asyncio
async def test_trust_key_returns_configured_key(client, app):
    public_key_b64 = base64.b64encode(b"\x00" * 32).decode()
    app.state.config = type(app.state.config)(
        **{
            **app.state.config.__dict__,
            "manifest_signing_public_key_b64": public_key_b64,
        }
    )

    resp = await client.get("/api/v1/agent/trust_key")

    assert resp.status_code == 200
    assert resp.json() == {"public_key_b64": public_key_b64}


@pytest.mark.asyncio
async def test_trust_key_503_when_unconfigured(client, app):
    app.state.config = type(app.state.config)(
        **{
            **app.state.config.__dict__,
            "manifest_signing_public_key_b64": None,
        }
    )

    resp = await client.get("/api/v1/agent/trust_key")

    assert resp.status_code == 503
    assert "MANIFEST_SIGNING_PUBLIC_KEY" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_trust_key_is_anonymous(client, app):
    """No Authorization header is required — install hosts fetch this
    before they have any credentials."""
    public_key_b64 = base64.b64encode(b"\x11" * 32).decode()
    app.state.config = type(app.state.config)(
        **{
            **app.state.config.__dict__,
            "manifest_signing_public_key_b64": public_key_b64,
        }
    )

    resp = await client.get(
        "/api/v1/agent/trust_key",
        headers={},
    )

    assert resp.status_code == 200
    assert resp.json() == {"public_key_b64": public_key_b64}
