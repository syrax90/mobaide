"""Cross-path parity for the manifest body bytes the server hands to the agent.

The agent verifies the Ed25519 signature against the *body it received* — so
``broadcast_manifest_update`` (push) and ``auth_ok`` (pull on connect) MUST
hand over identical body bytes for the same ``StoredManifest``. Each path is
already covered by its own focused test; what's missing — and what this
module pins — is the *cross-path equivalence* invariant.

Why a separate test:
  A future refactor that filters or re-serialises the dict on one path
  (e.g. drops an unknown field, sorts keys differently, swaps ``json.dumps``
  for ``orjson``) would pass both per-path tests and silently break
  end-to-end signature verification at runtime — recreating the same fail-
  closed "signature_failed" UX as the original relative-URL bug.

The test runs entirely in-process: a fake routing connection captures the
broadcast frame, and the auth_ok flow is exercised via the WS test client.
Both bodies are then compared as raw JSON bytes (the canonical form the
agent feeds into Ed25519).
"""
from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.testclient import TestClient

from central_server.manifest.router import broadcast_manifest_update
from central_server.manifest.store import ManifestStore, StoredManifest, canonical_json
from central_server.relay.envelope import CH_CONTROL, PROTOCOL_VERSION, decode


def _signed_manifest_record() -> tuple[StoredManifest, str]:
    """Produce a StoredManifest with a real Ed25519 signature for the given body."""
    private_key = Ed25519PrivateKey.generate()
    public_key_b64 = base64.b64encode(private_key.public_key().public_bytes_raw()).decode()
    body = {
        "version": "0.9.0",
        "tarball_url": "https://example.com/agent-0.9.0.tar.gz",
        "tarball_sha256": "a" * 64,
        "wheel_url": "https://example.com/mobaide_agent-0.9.0-py3-none-any.whl",
        "wheel_sha256": "b" * 64,
        "released_at": "2026-04-26T00:00:00+00:00",
    }
    sig_b64 = base64.b64encode(private_key.sign(canonical_json(body))).decode()
    return StoredManifest(body=body, signature_b64=sig_b64), public_key_b64


def test_broadcast_and_auth_ok_emit_identical_body_bytes(
    app, registered_agent, tmp_path,
):
    """The body the broadcast frame carries must match the body auth_ok carries.

    Both paths read from the same ``StoredManifest`` snapshot in this test —
    if the production code ever introduces a transform on one path but not
    the other (filter, re-key, alternative encoder), the bytes the agent
    feeds into ``verify_signature`` will diverge by path and the same signed
    manifest will succeed on one path and fail-closed on the other.
    """
    record, _public_key_b64 = _signed_manifest_record()

    # Re-point ``manifest_store`` at a tmp_path-scoped file. The default
    # config points the store at a fixed relative path (``data/agent_manifest.json``),
    # which would otherwise leak the seeded body to sibling tests that also
    # exercise the auth_ok manifest fields (see ``test_agent_ws_protocol``).
    store = ManifestStore(tmp_path / "manifest.json")
    store.write(record.body, record.signature_b64)
    app.state.manifest_store = store

    # --- Path A: broadcast. Capture the body the agent would receive over
    # ``CH_CONTROL`` after a publish.
    app.state.routing.register_agent("agent-parity", MagicMock())
    delivered = broadcast_manifest_update(app.state.routing, record)
    assert delivered == 1
    conn = app.state.routing.get_agent("agent-parity")
    envelope = conn.send_queue.get_nowait()
    _channel, _, payload = decode(envelope)
    broadcast_ctrl = json.loads(payload)
    broadcast_body = broadcast_ctrl["manifest_body"]
    broadcast_signature = broadcast_ctrl["manifest_signature"]

    # --- Path B: auth_ok. Drive a real WS handshake and capture the body
    # the server emits in the reply.
    agent_id, raw_token = registered_agent
    client = TestClient(app)
    with client.websocket_connect("/ws/agent-tunnel") as ws:
        ws.send_text(json.dumps({
            "type": "auth",
            "agent_id": agent_id,
            "token": raw_token,
            "protocol_version": PROTOCOL_VERSION,
        }))
        resp = json.loads(ws.receive_text())
    auth_ok_body = resp["manifest_body"]
    auth_ok_signature = resp["manifest_signature"]

    # Byte-equality on the canonical-JSON form — the same form the agent
    # passes to ``verify_signature``.
    assert canonical_json(broadcast_body) == canonical_json(auth_ok_body), (
        "broadcast vs auth_ok manifest body bytes diverged — agent-side "
        "Ed25519 verification will succeed on one path and fail on the other"
    )
    # Belt-and-braces: also pin dict equality so a future change that swaps
    # the canonical encoder for an equivalent one still trips this test.
    assert broadcast_body == auth_ok_body
    assert broadcast_signature == auth_ok_signature


def test_broadcast_body_round_trips_through_verify_signature():
    """The broadcast body + signature must verify against the public key
    that signed it — i.e. nothing in ``broadcast_manifest_update`` mutates
    the body between ``StoredManifest`` and the wire frame.

    This is the agent-side perspective: take the bytes off the wire,
    re-canonicalise, verify. A regression that, say, added a server-side
    timestamp into ``manifest_body`` would canonicalise differently from
    what was signed and silently fail-closed on every agent.
    """
    record, public_key_b64 = _signed_manifest_record()

    app_routing_table_with_agent = _fake_routing_with_one_agent()
    delivered = broadcast_manifest_update(app_routing_table_with_agent, record)
    assert delivered == 1
    conn = app_routing_table_with_agent.get_agent("agent-verify")
    _, _, payload = decode(conn.send_queue.get_nowait())
    ctrl = json.loads(payload)

    # Reconstruct what the agent does: feed the received body + signature
    # into the same verify routine. The agent-side verifier
    # (``mobaide.manifest_signing.verify_signature``) and the server-side
    # verifier (``central_server.manifest.store.verify_signature``) share
    # the exact same canonical-JSON + Ed25519 contract — importing the
    # server-side copy here also pins that no server-only transform leaked
    # into the wire body.
    from central_server.manifest.store import verify_signature

    verify_signature(
        ctrl["manifest_body"],
        ctrl["manifest_signature"],
        public_key_b64,
    )


def _fake_routing_with_one_agent():
    """Build a real RoutingTable with a single fake agent connection."""
    from central_server.relay.routing import RoutingTable

    routing = RoutingTable()
    routing.register_agent("agent-verify", MagicMock())
    return routing
