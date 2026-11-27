"""Trust data that a signed release manifest gives the agent.

The install scripts pin one release public key at
``<prefix>/trust/central_server.pub``. A manifest signed with a trusted
release key can carry a ``trust`` object with two key lists:

* ``release_public_keys`` — more keys that may sign a manifest. A key that
  was added stays trusted; only an agent release removes one.
* ``relay_pairing_public_keys`` — the keys the relay may sign the pairing
  credential push with. The newest manifest states the full list.

The relay only transports the manifest, so it cannot add a key to either
list. The caller checks the signature and the manifest version before it
records anything here.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ._env_writer import atomic_write

logger = logging.getLogger(__name__)

MANIFEST_TRUST_FILENAME = "manifest_trust.json"
MAX_KEYS_PER_LIST = 4

_lock = threading.Lock()
_record_lock = threading.Lock()
# Last state per trust dir, so a read-only trust dir still works until restart.
_memory: dict[str, "ManifestTrust"] = {}


@dataclass(frozen=True)
class ManifestTrust:
    release_public_keys: tuple[str, ...] = ()
    relay_pairing_public_keys: tuple[str, ...] = ()
    released_at: str = ""


def _is_ed25519_public_key(key_b64: object) -> bool:
    if not isinstance(key_b64, str):
        return False
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(key_b64, validate=True))
    except (ValueError, TypeError, binascii.Error):
        return False
    return True


def _clean_keys(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    keys: list[str] = []
    for item in raw:
        if _is_ed25519_public_key(item) and item not in keys:
            keys.append(item)
    return tuple(keys[:MAX_KEYS_PER_LIST])


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _trust_file(layout) -> Path:
    return layout.trust_dir / MANIFEST_TRUST_FILENAME


def _read_stored(path: Path) -> ManifestTrust | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    released_at = raw.get("released_at")
    return ManifestTrust(
        release_public_keys=_clean_keys(raw.get("release_public_keys")),
        relay_pairing_public_keys=_clean_keys(raw.get("relay_pairing_public_keys")),
        released_at=released_at if isinstance(released_at, str) else "",
    )


def load_manifest_trust(layout) -> ManifestTrust:
    path = _trust_file(layout)
    stored = _read_stored(path)
    with _lock:
        cached = _memory.get(str(path))
    if stored is None or cached is None:
        return stored or cached or ManifestTrust()
    cached_time = _parse_time(cached.released_at)
    stored_time = _parse_time(stored.released_at)
    if cached_time and (stored_time is None or cached_time > stored_time):
        return cached
    return stored


def release_verification_keys(layout, pinned_key_b64: str) -> tuple[str, ...]:
    """Keys that may sign a release manifest, the pinned key first."""
    extra = load_manifest_trust(layout).release_public_keys
    return (pinned_key_b64, *(key for key in extra if key != pinned_key_b64))


def pairing_verification_keys(layout) -> tuple[str, ...]:
    """Keys that may sign the pairing credential push.

    Only the relay pairing keys of the newest verified manifest: the release
    key signs releases, never a pairing. Empty until a manifest names one.
    """
    return load_manifest_trust(layout).relay_pairing_public_keys


def record_manifest_trust(layout, manifest_body: dict[str, Any]) -> bool:
    """Store the trust data of a verified manifest; return True on a change.

    Only a manifest released after the stored one is applied, so a replayed
    older manifest cannot bring back a retired pairing key.
    """
    trust = manifest_body.get("trust")
    if not isinstance(trust, dict):
        return False
    with _record_lock:
        return _record(layout, trust, manifest_body.get("released_at"))


def _record(layout, trust: dict[str, Any], released_at: object) -> bool:
    released_time = _parse_time(released_at)
    if released_time is None:
        logger.warning("trust_store.ignored reason=released_at_missing")
        return False

    current = load_manifest_trust(layout)
    current_time = _parse_time(current.released_at)
    if current_time is not None and released_time <= current_time:
        return False

    release_keys = list(current.release_public_keys)
    for key in _clean_keys(trust.get("release_public_keys")):
        if key in release_keys:
            continue
        if len(release_keys) >= MAX_KEYS_PER_LIST:
            logger.warning("trust_store.release_key_ignored reason=list_full")
            break
        release_keys.append(key)
    updated = ManifestTrust(
        release_public_keys=tuple(release_keys),
        relay_pairing_public_keys=_clean_keys(trust.get("relay_pairing_public_keys")),
        released_at=str(released_at),
    )
    changed = (
        updated.release_public_keys != current.release_public_keys
        or updated.relay_pairing_public_keys != current.relay_pairing_public_keys
    )

    path = _trust_file(layout)
    with _lock:
        _memory[str(path)] = updated
    try:
        atomic_write(
            path,
            json.dumps(
                {
                    "release_public_keys": list(updated.release_public_keys),
                    "relay_pairing_public_keys": list(updated.relay_pairing_public_keys),
                    "released_at": updated.released_at,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            mode=0o644,
        )
    except OSError as exc:
        logger.warning("trust_store.write_failed path=%s: %s", path, exc)
    if changed:
        logger.info(
            "trust_store.updated release_keys=%d pairing_keys=%d released_at=%s",
            len(updated.release_public_keys),
            len(updated.relay_pairing_public_keys),
            updated.released_at,
        )
    return changed
