"""Cryptographic helpers for QR pairing.

These are deliberately small pure functions with no FastAPI / DB
dependencies so they can be unit-tested without an app fixture.

The pairing WS URL is *not* computed here. It is derived on the agent
from ``RELAY_SERVER_URL`` + the response ``nonce`` (see
``mobaide_agent/mobaide/agent/pairing_monitor._pairing_ws_url``). Keeping
the URL agent-side eliminates a class of deployment-skew bugs where a
stale server image returned ``https://...`` and the websockets client
hard-rejected the scheme.
"""
from __future__ import annotations

import base64
import hashlib
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def compute_trust_key_fp(public_key_b64: str) -> str:
    """SHA-256 hex of the decoded Ed25519 public key bytes.

    Mirrored on the agent (`mobaide_agent/mobaide/agent/pairing_qr.py`)
    and the Android client (`TrustKeyPinning`); the three implementations
    must produce identical fingerprints for the same key.
    """
    key_bytes = base64.b64decode(public_key_b64, validate=True)
    return hashlib.sha256(key_bytes).hexdigest()


def encode_pairing_payload(
    relay_url: str, nonce: str, trust_key_fp: str, exp_unix: int
) -> str:
    """Build the canonical ``MOBA1|...`` text the clients parse.

    Byte-identical to the agent's ``pairing_qr.encode_qr_payload`` and to
    the ``parse_moba1`` implementations on web, Android and iOS.
    """
    return f"MOBA1|{relay_url}|{nonce}|{trust_key_fp}|{exp_unix}"


def hash_pairing_nonce(raw_nonce: str) -> str:
    """SHA-256 hex of a raw pairing nonce — the ``pairing_tokens`` key.

    Must stay identical to the digest ``Database`` stores, since this is
    what the pairing router uses to address a token row and to key the
    WebSocket waiter registry. The nonce is 192 bits of ``token_urlsafe``,
    so an unpeppered digest is enough (contrast ``device_code``, whose
    34.6-bit code needs a pepper).
    """
    return hashlib.sha256(raw_nonce.encode()).hexdigest()


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
