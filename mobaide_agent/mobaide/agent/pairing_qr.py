"""QR rendering, payload encoding/decoding, and pending-pairing persistence.

Used by the ``mobaide-agent pair-qr`` / ``show-qr`` / ``re-pair`` /
``pair`` CLI verbs and the agent app's pairing monitor. The trust-key
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
import secrets
import sys
import time
from dataclasses import asdict, dataclass, replace
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


@dataclass(frozen=True)
class PendingDeviceCode:
    """Local state for one device-code redemption attempt.

    Written twice: once with ``pending=None`` before the redeem request, so
    a retry re-presents the same ``redeemer_id`` rather than looking like a
    second machine racing the code, and again with the nonce the moment the
    response lands — *before* the operator is prompted, because that prompt
    blocks on human input and a Ctrl-C there would otherwise strand a code
    the relay has already spent.

    ``confirmed`` is what keeps that early write safe: a resumed run that
    has not been confirmed is prompted again, so persisting sooner never
    turns into skipping the agent-side approval.
    """

    redeemer_id: str
    code_fp: str
    pending: PendingPairing | None = None
    account_hint: str | None = None
    confirmed: bool = False


def new_redeemer_id() -> str:
    """Random identity for one device-code redemption attempt."""
    return secrets.token_urlsafe(12)


def pending_device_code_path(layout) -> Path:
    """Path of the device-code pairing state.

    Deliberately a different file from ``pending_pairing.json``. On a fresh
    install the agent service is already running its own QR pairing cycle,
    and a ``pair <code>`` invocation that shared the file would overwrite
    the nonce that cycle is waiting on.
    """
    return Path(layout.prefix) / "run" / "pending_device_code.json"


# The relay's device-code alphabet, duplicated here for the same reason
# ``compute_trust_key_fp`` is: the agent cannot import from
# ``central_server``. Must stay in lockstep with
# ``central_server.pairing.device_code.CODE_ALPHABET``.
DEVICE_CODE_ALPHABET = frozenset("BCDFGHJKLMNPQRSTVWXZ")


def canonical_device_code(code: str) -> str:
    """Fold a typed code to the form the relay hashes.

    The relay upper-cases and drops everything outside its alphabet, so
    ``wdjb-mjht`` and ``WDJB MJHT`` name the same code. The local record has
    to agree exactly — filtering more loosely (say, on ``isalnum``) keeps
    characters the relay discards, so a stray digit makes a resume look like
    a *different* code: the record is dropped, the fresh redeem is refused
    as already-used, and the error path then deletes the very
    ``redeemer_id`` and nonce that could have completed the pairing.
    """
    return "".join(ch for ch in code.upper() if ch in DEVICE_CODE_ALPHABET)


def device_code_fingerprint(code: str) -> str:
    """Digest identifying which code a pending record belongs to.

    Stored instead of the code itself so a resume can tell "continue the
    request I started" from "the operator typed a different code", without
    leaving a live pairing code at rest on the agent host.
    """
    return hashlib.sha256(
        canonical_device_code(code).encode("utf-8")
    ).hexdigest()


def save_pending_device_code(layout, record: PendingDeviceCode) -> None:
    """Persist device-code redemption state."""
    from ._env_writer import atomic_write, restrict_perms

    target = pending_device_code_path(layout)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "redeemer_id": record.redeemer_id,
        "code_fp": record.code_fp,
        "account_hint": record.account_hint,
        "confirmed": record.confirmed,
        "pending": asdict(record.pending) if record.pending is not None else None,
    }
    atomic_write(target, json.dumps(body, indent=2) + "\n")
    restrict_perms(target)


def load_pending_device_code(layout, code_fp: str) -> PendingDeviceCode | None:
    """Read state for ``code_fp``, or ``None`` when it belongs elsewhere.

    Returns ``None`` when the stored record names a *different* code, so a
    later ``mobaide-agent pair OTHERCODE`` starts a fresh redemption instead
    of silently waiting out the earlier request's nonce.

    ``pending`` comes back ``None`` when the record predates a successful
    redeem *or* its nonce has since expired — the caller then knows the id
    is still worth resending but the nonce is not worth dialling.
    """
    target = pending_device_code_path(layout)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(text)
        redeemer_id = data["redeemer_id"]
        raw_pending = data.get("pending")
    except (json.JSONDecodeError, TypeError, KeyError):
        return None
    if not isinstance(redeemer_id, str) or not redeemer_id:
        return None
    if data.get("code_fp") != code_fp:
        return None
    account_hint = data.get("account_hint")
    confirmed = data.get("confirmed") is True
    base = PendingDeviceCode(
        redeemer_id=redeemer_id,
        code_fp=code_fp,
        account_hint=account_hint if isinstance(account_hint, str) else None,
        confirmed=confirmed,
    )
    if not isinstance(raw_pending, dict):
        return base
    try:
        pending = PendingPairing(**raw_pending)
    except TypeError:
        return base
    parsed = parse_qr_payload(pending.qr_payload)
    if parsed is None or parsed.exp_unix <= int(time.time()):
        return base
    return replace(base, pending=pending)


def clear_pending_device_code(layout) -> None:
    """Remove the device-code pairing file. Idempotent."""
    target = pending_device_code_path(layout)
    try:
        target.unlink()
    except OSError:
        pass
