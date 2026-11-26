"""Cover ``GET /api/v1/agent/pairing_key``.

The endpoint gives the public half of the relay pairing key to the release
script, which compares it with the key it puts into the signed manifest.
Agents do not read it: they take the key only from a signed manifest.
Answers 200 with a null key when ``RELAY_PAIRING_PRIVATE_KEY`` is not
configured.
"""
from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@pytest.mark.asyncio
async def test_pairing_key_returns_only_the_public_half(client, app):
    private = Ed25519PrivateKey.generate()
    private_b64 = base64.b64encode(private.private_bytes_raw()).decode()
    public_b64 = base64.b64encode(private.public_key().public_bytes_raw()).decode()
    app.state.config = type(app.state.config)(
        **{
            **app.state.config.__dict__,
            "relay_pairing_private_key_b64": private_b64,
        }
    )

    resp = await client.get("/api/v1/agent/pairing_key")

    assert resp.status_code == 200
    assert resp.json() == {"public_key_b64": public_b64}
    assert private_b64 not in resp.text


@pytest.mark.asyncio
async def test_pairing_key_null_when_unconfigured(client, app):
    app.state.config = type(app.state.config)(
        **{
            **app.state.config.__dict__,
            "relay_pairing_private_key_b64": None,
        }
    )

    resp = await client.get("/api/v1/agent/pairing_key")

    assert resp.status_code == 200
    assert resp.json() == {"public_key_b64": None}
