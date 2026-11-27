"""Ed25519 signature verification for the agent rollout manifest.

Intentionally duplicated from ``central_server.manifest.store`` — the two
packages cannot share code (same constraint that drives the duplicated
``relay/envelope.py`` files). The private key stays in the operator's local
release folder and signs each manifest in ``deploy/prod/release_agent.py``.
The public half is carried by the install scripts and persisted at
``<prefix>/trust/central_server.pub``.
"""
from __future__ import annotations

import base64
import json
from collections.abc import Iterable
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class ManifestSignatureError(Exception):
    """Raised when an Ed25519 signature does not validate against the
    configured public key."""


def canonical_json(body: dict[str, Any]) -> bytes:
    """Encode ``body`` as canonical JSON (sorted keys, no whitespace) for signing."""
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_signature(
    body: dict[str, Any],
    signature_b64: str,
    public_key_b64: str,
) -> None:
    """Validate an Ed25519 signature over canonical JSON of ``body``.

    Raises:
        ManifestSignatureError: signature is malformed or does not verify.
    """
    try:
        public_key_bytes = base64.b64decode(public_key_b64, validate=True)
    except (ValueError, TypeError) as exc:
        raise ManifestSignatureError("public key is not valid base64") from exc

    try:
        signature_bytes = base64.b64decode(signature_b64, validate=True)
    except (ValueError, TypeError) as exc:
        raise ManifestSignatureError("signature is not valid base64") from exc

    try:
        public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
    except ValueError as exc:
        raise ManifestSignatureError("public key is not a valid Ed25519 key") from exc

    try:
        public_key.verify(signature_bytes, canonical_json(body))
    except InvalidSignature as exc:
        raise ManifestSignatureError("signature does not verify") from exc


def verify_signature_any(
    body: dict[str, Any],
    signature_b64: str,
    public_keys_b64: Iterable[str],
) -> str:
    """Validate the signature against each key in turn; return the key that matched.

    Raises:
        ManifestSignatureError: no key verifies the signature.
    """
    last_error: ManifestSignatureError | None = None
    for public_key_b64 in public_keys_b64:
        try:
            verify_signature(body, signature_b64, public_key_b64)
        except ManifestSignatureError as exc:
            last_error = exc
            continue
        return public_key_b64
    raise last_error or ManifestSignatureError("no trusted public key is configured")
