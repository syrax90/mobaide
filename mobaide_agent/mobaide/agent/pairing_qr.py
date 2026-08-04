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
import os
import re
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


DEFAULT_PAIRING_WEB_ORIGIN = "https://app.mobaide.com"

PAIRING_WEB_ORIGIN_ENV = "MOBAIDE_AGENT_PAIR_WEB_ORIGIN"


def pairing_web_origin() -> str:
    """Origin of the web client that serves ``/pair``.

    Not derivable from the payload: ``relay_url`` is the relay's
    ``PUBLIC_BASE_URL`` (``api.mobaide.com`` in production) while the SPA is a
    separate host, so this is a fixed default with an env escape hatch for
    self-hosted and development deployments. An override that is not ASCII
    ``http(s)`` is ignored rather than honoured — printing a link nobody can
    open is worse than printing the wrong host next to a payload that still
    pastes, and a non-ASCII origin would make the link line unprintable on the
    redirected consoles ``write_pairing_hints`` has to survive.
    """
    override = os.environ.get(PAIRING_WEB_ORIGIN_ENV, "").strip()
    if override.isascii() and override.startswith(("https://", "http://")):
        trimmed = override.rstrip("/")
        if trimmed not in ("https:", "http:"):
            return trimmed
    return DEFAULT_PAIRING_WEB_ORIGIN


def format_pairing_link(payload: str) -> str:
    """Build the browser pairing link for a ``MOBA1|...`` payload.

    The payload rides in the fragment, base64url-encoded without padding: a
    raw ``|`` is not a legal URL character and terminal linkifiers stop at it,
    which would hand the user a truncated link. A fragment also never reaches
    the relay's access logs.

    The web client's decoder mirrors this and is padding-tolerant
    (``web/src/protocol/pairingPayload.ts``).
    """
    fragment = (
        base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
    )
    return f"{pairing_web_origin()}/pair#{fragment}"


def _stream_can_encode(out, text: str) -> bool:
    """Whether ``out``'s declared codec can carry ``text``.

    The narrow-scope twin of ``_stream_supports_block_glyphs``: same probe,
    against the caller's string instead of a fixed glyph.
    """
    if text.isascii():
        return True
    encoding = getattr(out, "encoding", None)
    if not encoding:
        return False
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


_UNPRINTABLE_HINT = (
    "[mobaide-agent] The pairing code contains characters this console cannot "
    "render — scan the QR above instead.\n"
)


def write_pairing_hints(payload: str, *, stream=None) -> None:
    """Print the pasteable payload and the browser link under a rendered QR.

    Two hard constraints, both learned from this module's existing failure
    modes:

    * The payload and the link each occupy a whole line with no prefix and are
      never wrapped. A terminal's soft wrap is invisible to the user but a
      copied soft-wrapped line carries embedded newlines, so any prefix or
      manual break turns a working paste into a malformed one.
    * This must never raise. The labels are ASCII, but ``relay_url`` comes from
      the operator's ``PUBLIC_BASE_URL`` and may be an internationalized host,
      so the payload and link are probed against the stream's codec first and
      replaced with an ASCII notice when they do not fit. A Windows service
      redirects stdout to a ``cp437``/``cp1252`` log file, and a
      ``UnicodeEncodeError`` from the pairing monitor's render tears down the
      pairing WebSocket before ``wait_for_claim`` — the incident
      ``_stream_supports_block_glyphs`` documents — while the same error on the
      ``show-qr`` path fails the installer.

    Pasteable artifact first, link second, following ``gh auth login``.
    """
    out = stream if stream is not None else sys.stdout
    link = format_pairing_link(payload)
    if not _stream_can_encode(out, payload) or not _stream_can_encode(out, link):
        out.write(_UNPRINTABLE_HINT)
        out.flush()
        return
    out.write("[mobaide-agent] Pairing code (copy this entire line):\n")
    out.write(f"{payload}\n")
    out.write("[mobaide-agent] Or open this link in a browser:\n")
    out.write(f"{link}\n")
    out.flush()


_EXP_UNIX_PATTERN = re.compile(r"\A[+-]?[0-9]+\Z")

_INT64_MIN = -(2**63)

_INT64_MAX = 2**63 - 1


def parse_qr_payload(text: str) -> ParsedQrPayload | None:
    """Parse a QR string. Returns None on any malformedness.

    Strict: rejects unknown versions, wrong field counts, blank required
    fields, and anything the mobile clients' 64-bit integer parse would
    reject for the expiry. Pinned across all four implementations by
    ``tests/protocol_fixtures/pairing/qr_payload.json``.

    ``int()`` alone is *looser* than Kotlin's ``toLongOrNull`` and Swift's
    ``Int64(_:)``: it strips surrounding whitespace, honours ``_`` digit
    separators, accepts non-ASCII digits, and has no 64-bit ceiling. Each of
    those would parse here and be refused on a phone, so the shape is checked
    before the conversion and the result is range-checked after it. The shape
    pattern is anchored with ``\\A``/``\\Z`` rather than ``^``/``$`` because
    Python's ``$`` also matches *before* a trailing newline, which would accept
    a payload every other implementation rejects. Blankness
    likewise means "empty once trimmed", matching ``isBlank()`` /
    ``trimmingCharacters``; the values themselves are stored verbatim.

    Expiry is deliberately not range-checked against the clock — that decision
    belongs to the caller's state machine, and every client pins the fact that
    a negative ``exp_unix`` parses.
    """
    parts = text.split("|")
    if len(parts) != 5:
        return None
    version, relay_url, nonce, trust_key_fp, exp_str = parts
    if version != "MOBA1":
        return None
    if not relay_url.strip() or not nonce.strip() or not trust_key_fp.strip():
        return None
    if not _EXP_UNIX_PATTERN.match(exp_str):
        return None
    try:
        exp_unix = int(exp_str)
    except ValueError:
        # The shape check does not bound length, and CPython caps int()
        # string conversion at sys.get_int_max_str_digits() (4300). Without
        # this the function would *raise* where its whole contract is to
        # return None, and the pairing monitor's retry loop would re-read the
        # same payload forever instead of letting the TTL evict it.
        return None
    if not _INT64_MIN <= exp_unix <= _INT64_MAX:
        return None
    return ParsedQrPayload(
        version=version,
        relay_url=relay_url,
        nonce=nonce,
        trust_key_fp=trust_key_fp,
        exp_unix=exp_unix,
    )


_BLOCK_GLYPH_PROBE = "▀"


def _stream_supports_block_glyphs(out) -> bool:
    """Return True iff ``out``'s declared encoding can carry U+2580–U+259F.

    segno's ``compact=True`` terminal renderer emits Unicode half-block
    glyphs. On a Windows service whose stdout is redirected to a
    ``cp1252`` log file the write raises ``UnicodeEncodeError`` mid-
    pairing-handshake and the WS tears down before ``wait_for_claim``
    runs — see ``pairing_monitor.run_pairing_monitor``. We probe the
    stream's ``encoding`` attribute (``None`` for raw binary wrappers,
    a codec name for ``TextIOWrapper``) by attempting to encode a
    representative block glyph, and fall back to ASCII rendering when
    the codec cannot carry it.
    """
    encoding = getattr(out, "encoding", None)
    if not encoding:
        return False
    try:
        _BLOCK_GLYPH_PROBE.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def _render_qr_ascii(code, out) -> None:
    """ASCII-only QR renderer for streams that can't encode half-blocks.

    Uses ``##`` for dark modules and two spaces for light modules with a
    4-module quiet zone (ISO/IEC 18004 minimum, matches segno's own
    default). Two characters per module approximates a square cell in
    standard monospace fonts so the result remains scannable from a
    cp1252-encoded service log on Windows, or any other code page that
    cannot carry U+2580–U+259F.
    """
    quiet = 4
    matrix = code.matrix
    size = len(matrix)
    blank_row = "  " * (size + 2 * quiet) + "\n"
    pad = "  " * quiet
    for _ in range(quiet):
        out.write(blank_row)
    for row in matrix:
        out.write(pad)
        for cell in row:
            out.write("##" if cell else "  ")
        out.write(pad)
        out.write("\n")
    for _ in range(quiet):
        out.write(blank_row)


def render_qr_to_terminal(payload: str, *, stream=None) -> None:
    """Render the QR code to a terminal stream using segno.

    Defaults to error-correction level M for a payload-size /
    code-density balance that fits comfortably in 80x24. When the
    target stream's encoding can carry segno's half-block glyphs
    (UTF-8 Linux/macOS, Windows Terminal, PowerShell 7 with UTF-8
    default, ``cmd.exe`` after ``chcp 65001``), ``compact=True`` is
    used so the code looks rectangular. Otherwise (``cp1252`` /
    ``cp437`` / ASCII / unknown encoding — typical Windows service
    stdout redirected to a log file) the function falls back to an
    ASCII ``##`` rendering so the call never raises
    ``UnicodeEncodeError``.
    """
    import segno

    out = stream if stream is not None else sys.stdout
    code = segno.make(payload, error="m")
    if _stream_supports_block_glyphs(out):
        code.terminal(out=out, compact=True)
    else:
        _render_qr_ascii(code, out)
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
