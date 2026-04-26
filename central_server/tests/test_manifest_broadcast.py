"""Cover the manifest broadcast that fires after a successful publish.

Without the broadcast, agents only learn of new manifests at their next
``auth_ok`` (i.e. on reconnect). The publish path closes that gap by
pushing an ``agent_manifest_update`` CH_CONTROL frame to every online
agent in the routing table on the same call that persists the manifest.
"""
from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from central_server.manifest.router import broadcast_manifest_update
from central_server.manifest.store import StoredManifest
from central_server.relay.envelope import CH_CONTROL, decode
from central_server.relay.routing import RoutingTable


def _stored_manifest(version: str = "0.2.0") -> StoredManifest:
    body = {
        "version": version,
        "tarball_url": f"https://example.com/agent-{version}.tar.gz",
        "tarball_sha256": "0" * 64,
        "wheel_url": f"https://example.com/agent-{version}.whl",
        "wheel_sha256": "1" * 64,
        "released_at": "2026-04-26T00:00:00+00:00",
    }
    return StoredManifest(body=body, signature_b64="sig-b64")


def test_broadcast_enqueues_frame_on_each_online_agent():
    routing = RoutingTable()
    routing.register_agent("agent-1", MagicMock())
    routing.register_agent("agent-2", MagicMock())

    delivered = broadcast_manifest_update(routing, _stored_manifest("0.3.0"))
    assert delivered == 2

    for agent_id in ("agent-1", "agent-2"):
        conn = routing.get_agent(agent_id)
        assert conn is not None
        envelope = conn.send_queue.get_nowait()
        channel, _, payload = decode(envelope)
        assert channel == CH_CONTROL
        body = json.loads(payload)
        assert body == {
            "type": "agent_manifest_update",
            "version": "0.3.0",
            "manifest_url": "/api/v1/agent/manifest.json",
            "manifest_signature": "sig-b64",
        }


def test_broadcast_with_no_agents_is_noop():
    routing = RoutingTable()
    delivered = broadcast_manifest_update(routing, _stored_manifest())
    assert delivered == 0


@pytest.mark.asyncio
async def test_publish_endpoint_broadcasts_after_write(client, app):
    """End-to-end: POST /api/internal/agent_manifest with a valid signed
    manifest and a fake-connected agent → agent's send queue receives
    the broadcast frame in the same request."""
    private_key = Ed25519PrivateKey.generate()
    public_key_b64 = base64.b64encode(
        private_key.public_key().public_bytes_raw()
    ).decode()
    body = {
        "version": "0.4.0",
        "tarball_url": "https://example.com/agent-0.4.0.tar.gz",
        "tarball_sha256": "0" * 64,
        "wheel_url": "https://example.com/agent-0.4.0.whl",
        "wheel_sha256": "1" * 64,
        "released_at": "2026-04-26T00:00:00+00:00",
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    signature_b64 = base64.b64encode(private_key.sign(canonical)).decode()

    # Wire a release token + the matching public key into the live config
    # for this request.
    app.state.config = type(app.state.config)(
        **{
            **app.state.config.__dict__,
            "release_auth_token": "test-release-token",
            "manifest_signing_public_key_b64": public_key_b64,
        }
    )
    # Register a fake-connected agent.
    app.state.routing.register_agent("agent-x", MagicMock())

    resp = await client.post(
        "/api/internal/agent_manifest",
        headers={"X-Release-Token": "test-release-token"},
        json={"body": body, "signature_b64": signature_b64},
    )
    assert resp.status_code == 200, resp.text

    conn = app.state.routing.get_agent("agent-x")
    assert conn is not None
    envelope = conn.send_queue.get_nowait()
    channel, _, payload = decode(envelope)
    assert channel == CH_CONTROL
    pushed = json.loads(payload)
    assert pushed["type"] == "agent_manifest_update"
    assert pushed["version"] == "0.4.0"
    assert pushed["manifest_signature"] == signature_b64
