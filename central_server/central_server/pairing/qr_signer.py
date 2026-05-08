"""Cryptographic + URL helpers for QR pairing.

These are deliberately small pure functions with no FastAPI / DB
dependencies so they can be unit-tested without an app fixture.
"""
from __future__ import annotations

import base64
import hashlib
import json
from urllib.parse import urlsplit, urlunsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def compute_trust_key_fp(public_key_b64: str) -> str:
    """SHA-256 hex of the decoded Ed25519 public key bytes.

    Mirrored on the agent (`mobaide_agent/mobaide/agent/pairing_qr.py`)
    and the Android client (`TrustKeyPinning`); the three implementations
    must produce identical fingerprints for the same key.
    """
    key_bytes = base64.b64decode(public_key_b64, validate=True)
    return hashlib.sha256(key_bytes).hexdigest()


def canonical_payload_bytes(payload: dict) -> bytes:
    """Canonical JSON form used as the signature input.

    Sorted keys, no whitespace, UTF-8. Must match the agent-side
    canonical form exactly — keep both implementations in lockstep.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def sign_pairing_payload(private_key_b64: str, payload: dict) -> str:
    """Ed25519-sign the canonical JSON of ``payload``; return base64.

    ``payload`` MUST exclude any ``signature_b64`` field; the caller adds
    the returned string to the dict before serialising for the wire.
    """
    private_bytes = base64.b64decode(private_key_b64, validate=True)
    private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)
    sig = private_key.sign(canonical_payload_bytes(payload))
    return base64.b64encode(sig).decode("ascii")


def derive_ws_url(public_base_url: str, nonce: str) -> str:
    """Convert ``https://host[:port]`` → ``wss://host[:port]/ws/pairing/{nonce}``.

    Preserves explicit port. Replaces ``http``/``https`` with ``ws``/``wss``.
    Discards any path/query/fragment on the input — the WS endpoint is
    pinned at ``/ws/pairing/{nonce}`` regardless of base URL path.
    """
    parts = urlsplit(public_base_url)
    scheme = parts.scheme.lower()
    if scheme in ("http", "ws"):
        ws_scheme = "ws"
    elif scheme in ("https", "wss", ""):
        ws_scheme = "wss"
    else:
        raise ValueError(f"Unsupported scheme for ws derivation: {scheme!r}")
    netloc = parts.netloc or parts.path
    if not netloc:
        raise ValueError("Cannot derive WS URL: missing host")
    path = f"/ws/pairing/{nonce}"
    return urlunsplit((ws_scheme, netloc, path, "", ""))
