"""File-backed manifest store with atomic writes.

The manifest is a small JSON document that points at the latest ``mobaide_agent``
release tarball + wheel and carries a base64-encoded Ed25519 signature over the
canonical JSON of the manifest body. Reads are non-blocking (last-good cache);
writes are atomic via ``os.replace``.
"""
from __future__ import annotations

import base64
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


_REQUIRED_FIELDS: tuple[str, ...] = (
    "version",
    "tarball_url",
    "tarball_sha256",
    "wheel_url",
    "wheel_sha256",
    "released_at",
)


@dataclass(frozen=True)
class StoredManifest:
    body: dict[str, Any]
    signature_b64: str

    @property
    def version(self) -> str:
        return str(self.body.get("version", ""))


class ManifestSignatureError(Exception):
    """Raised when an Ed25519 signature does not validate against the
    configured public key."""


class ManifestValidationError(Exception):
    """Raised when a posted manifest is missing required fields."""


class ManifestStore:
    """Persist and retrieve the agent rollout manifest.

    Thread-safe: ``read()`` returns a lazily-loaded copy of the most recent
    manifest, ``write()`` performs an atomic file replace and updates the
    in-memory copy under a lock.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        self._cached: StoredManifest | None = None
        self._cached_loaded = False

    def read(self) -> StoredManifest | None:
        with self._lock:
            if not self._cached_loaded:
                self._load_from_disk_locked()
            return self._cached

    def write(
        self,
        body: dict[str, Any],
        signature_b64: str,
        *,
        public_key_b64: str | None = None,
    ) -> StoredManifest:
        for field in _REQUIRED_FIELDS:
            if not body.get(field):
                raise ManifestValidationError(f"manifest missing required field: {field}")

        if public_key_b64:
            verify_signature(body, signature_b64, public_key_b64)

        record = {"body": body, "signature_b64": signature_b64}
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self._path)
            self._cached = StoredManifest(body=body, signature_b64=signature_b64)
            self._cached_loaded = True
            return self._cached

    def _load_from_disk_locked(self) -> None:
        self._cached_loaded = True
        if not self._path.exists():
            self._cached = None
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            body = raw["body"]
            signature_b64 = raw["signature_b64"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            self._cached = None
            return
        if not isinstance(body, dict) or not isinstance(signature_b64, str):
            self._cached = None
            return
        self._cached = StoredManifest(body=body, signature_b64=signature_b64)


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
