"""QR rendering, payload encoding/decoding, and pending-pairing persistence.

Used by the ``mobaide-agent pair-qr`` / ``show-qr`` / ``re-pair`` CLI
verbs and the agent app's pairing monitor. The trust-key
fingerprint algorithm here MUST stay in lockstep with the central
server's ``central_server.pairing.qr_signer.compute_trust_key_fp`` and
the Android client's ``TrustKeyPinning`` — the three are independently
implemented but produce identical hex strings for the same Ed25519 key.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .install_paths import read_trust_public_key


@dataclass(frozen=True)
class ParsedQrPayload:
    """In-memory form of a ``MOBA1|...`` QR text. Used only for tests.

    The agent never parses its own QRs (it renders), but symmetry with
    the mobile parser keeps both implementations honest.
    """

    version: str
    relay_url: str
    nonce: str
    trust_key_fp: str
    exp_unix: int


@dataclass(frozen=True)
class PendingPairing:
    """Persistent state for a still-valid pairing nonce.

    Written by ``pair-qr`` after a successful initiate; read by
    ``show-qr`` to re-render without a fresh initiate, and by the
    pairing monitor on agent restart so a still-valid nonce survives
    a service bounce.
    """

    nonce: str
    expires_at: str
    trust_key_fp: str
    ws_url: str
    relay_url: str
    qr_payload: str


def compute_trust_key_fp(public_key_b64: str) -> str:
    """SHA-256 hex of the decoded Ed25519 public key bytes."""
    key_bytes = base64.b64decode(public_key_b64, validate=True)
    return hashlib.sha256(key_bytes).hexdigest()


def read_pinned_trust_key_fp(layout) -> str | None:
    """Return the fingerprint of the locally pinned trust public key.

    Returns ``None`` if the agent has not yet been bootstrapped via
    ``mobaide-agent fetch-trust-key`` (or the equivalent install-script
    step). The ``pair-qr`` flow is fail-closed: without a pinned key
    we cannot verify the server's response signature and refuse to
    proceed.
    """
    pub_key_b64 = read_trust_public_key(layout)
    if not pub_key_b64:
        return None
    try:
        return compute_trust_key_fp(pub_key_b64)
    except (ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
        return None


def encode_qr_payload(
    relay_url: str, nonce: str, trust_key_fp: str, exp_unix: int
) -> str:
    """Build the canonical ``MOBA1|...`` QR text."""
    return f"MOBA1|{relay_url}|{nonce}|{trust_key_fp}|{exp_unix}"


def parse_qr_payload(text: str) -> ParsedQrPayload | None:
    """Parse a QR string. Returns None on any malformedness.

    Strict: rejects unknown versions, wrong field counts, non-numeric
    expiry. Designed so the mobile parser can mirror this exactly.
    """
    parts = text.split("|")
    if len(parts) != 5:
        return None
    version, relay_url, nonce, trust_key_fp, exp_str = parts
    if version != "MOBA1":
        return None
    if not relay_url or not nonce or not trust_key_fp:
        return None
    try:
        exp_unix = int(exp_str)
    except ValueError:
        return None
    return ParsedQrPayload(
        version=version,
        relay_url=relay_url,
        nonce=nonce,
        trust_key_fp=trust_key_fp,
        exp_unix=exp_unix,
    )


def render_qr_to_terminal(payload: str, *, stream=None) -> None:
    """Render the QR code to a terminal stream using segno.

    Defaults to error-correction level M for a payload-size /
    code-density balance that fits comfortably in 80x24.
    ``compact=True`` uses Unicode half-blocks so the code looks
    rectangular on monospace terminals.
    """
    import segno

    out = stream if stream is not None else sys.stdout
    code = segno.make(payload, error="m")
    code.terminal(out=out, compact=True)
    out.write("\n")
    out.flush()


def pending_pairing_path(layout) -> Path:
    """Return the path of the pending-pairing JSON (created lazily)."""
    return Path(layout.prefix) / "run" / "pending_pairing.json"


def save_pending_pairing(layout, pending: PendingPairing) -> None:
    """Persist a pending-pairing record for the agent to recover later."""
    from ._env_writer import atomic_write, restrict_perms

    target = pending_pairing_path(layout)
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(target, json.dumps(asdict(pending), indent=2) + "\n")
    restrict_perms(target)


def load_pending_pairing(layout) -> PendingPairing | None:
    """Load a still-valid pending-pairing record, or None.

    Returns None when the file does not exist, is malformed, or has
    expired (now >= exp_unix from the QR payload). Stale records are
    not deleted here — the next ``pair-qr`` writes a fresh one.
    """
    target = pending_pairing_path(layout)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(text)
        pending = PendingPairing(**data)
    except (json.JSONDecodeError, TypeError):
        return None
    parsed = parse_qr_payload(pending.qr_payload)
    if parsed is None:
        return None
    if parsed.exp_unix <= int(time.time()):
        return None
    return pending


def clear_pending_pairing(layout) -> None:
    """Remove the pending-pairing file. Idempotent."""
    target = pending_pairing_path(layout)
    try:
        target.unlink()
    except OSError:
        pass
