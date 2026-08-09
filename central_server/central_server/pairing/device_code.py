"""Short-code helpers for the reversed (device-code) pairing flow.

Pure functions with no FastAPI / DB dependencies, matching the house style
of ``qr_signer``.

The code is displayed in a browser and retyped on a terminal, so the
alphabet is RFC 8628 §6.1's recommendation: 20 consonants, which cannot
spell a word and contain no visually confusable pair (no ``0``/``O``, no
``1``/``I``/``l``). Eight characters over 20 symbols is 20**8 ~= 2**34.6.

That is far below the 192-bit QR nonce, so the entropy is not the whole
defence: the code is single-use, an account holds at most one live code,
the TTL is minutes, and the anonymous redeem endpoint is rate-limited per
IP with a global circuit breaker. A per-code attempt counter is
deliberately absent — a wrong guess HMACs to a row that does not exist,
so there is nothing to increment.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"
CODE_LENGTH = 8
_GROUP_SIZE = 4

_ALPHABET_SET = frozenset(CODE_ALPHABET)


def generate_code() -> str:
    """Return a fresh code in normalised form (no grouping).

    Callers hash this value and run it through :func:`format_code` for
    display, so the stored hash is never of a hyphenated string that a
    retyped code could not reproduce.
    """
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def normalize_code(raw: str) -> str | None:
    """Canonicalise operator input, or ``None`` when it cannot be a code.

    Upper-cases, then keeps only alphabet characters — hyphens, spaces and
    the newline a terminal paste drags along all vanish. Filtering by
    character class rather than matching a regex is deliberate: Python's
    ``$`` matches *before* a trailing newline, so an anchored pattern would
    accept ``"WDJBMJHT\\n"`` while the agent-side clients reject it.
    """
    filtered = "".join(ch for ch in raw.upper() if ch in _ALPHABET_SET)
    if len(filtered) != CODE_LENGTH:
        return None
    return filtered


def format_code(normalized: str) -> str:
    """Re-apply display grouping to an already-normalised code."""
    groups = [
        normalized[i : i + _GROUP_SIZE]
        for i in range(0, len(normalized), _GROUP_SIZE)
    ]
    return "-".join(groups)


def hash_device_code(pepper: str, normalized_code: str) -> str:
    """Peppered HMAC-SHA256 of a normalised code, hex-encoded.

    Mirrors ``AuthService._hash_code``: a 34.6-bit secret is small enough
    that a plain digest would be brute-forceable from a database dump
    alone, so the server's JWT secret is folded in as a pepper. Unlike the
    verification OTP there is no ``user_id`` in the input — the redeeming
    agent is anonymous and looks the row up by code alone.
    """
    return hmac.new(
        pepper.encode("utf-8"),
        f"device_code:{normalized_code}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


_SAFE_HINT_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"
)
_MAX_DOMAIN_TAIL = 32


def _sanitize(segment: str) -> str:
    """Reduce a segment to characters that are safe on a terminal line.

    ``mask_email``'s output is printed directly above the agent's ``[y/N]``
    pairing prompt, which is the reversed flow's load-bearing control. The
    address it is derived from is attacker-registerable and unvalidated, so
    a raw echo would let CR, ESC or a newline repaint the prompt — and even
    plain letters can spell a reassuring lie. Anything outside the
    DNS-legal set becomes ``?`` rather than being dropped, so tampering
    stays visible instead of silently vanishing.
    """
    return "".join(ch if ch in _SAFE_HINT_CHARS else "?" for ch in segment)


def _mask_segment(segment: str) -> str:
    cleaned = _sanitize(segment)
    if len(cleaned) <= 2:
        return "***"
    return f"{cleaned[0]}***{cleaned[-1]}"


def mask_email(email: str) -> str:
    """Redact an address down to a shape its owner can still recognise.

    Shown to whoever redeems a code so the human at the terminal can tell
    whether the account about to own their machine is theirs. The redeemer
    already had to know a live code, so this is not a new disclosure
    channel; masking keeps a guessed code from enumerating addresses.

    The domain tail is preserved (it is the recognisable part) but
    sanitised and length-capped: it is the one span an attacker controls
    verbatim, and an unbounded one could push the prompt off screen.
    """
    local, sep, domain = email.rpartition("@")
    if not sep:
        return _mask_segment(email)
    host, dot, rest = domain.partition(".")
    if not (host and dot):
        return f"{_mask_segment(local)}@{_mask_segment(domain)}"
    tail = _sanitize(rest)
    if len(tail) > _MAX_DOMAIN_TAIL:
        tail = tail[:_MAX_DOMAIN_TAIL] + "…"
    return f"{_mask_segment(local)}@{_sanitize(host[0])}***{dot}{tail}"
