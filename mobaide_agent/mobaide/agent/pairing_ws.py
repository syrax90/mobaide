"""Agent-side pairing WebSocket client.

Connects to the central server's ``WS /ws/pairing/{nonce}``, awaits the
signed credential push, validates the Ed25519 signature against the
pinned trust public key, and returns the parsed claim. The atomic .env
write is the caller's responsibility — this module is concerned only
with transport + verification.

Two-phase API
-------------
The flow has two natural breakpoints — the ``"ready"`` handshake after
which the server is prepared to dispatch a claim, and the long-wait
``"claim_completed"`` push. They are exposed as a two-phase API so the
caller can interleave external state changes (e.g. saving
``pending_pairing.json`` so an installer renders the QR) **between** the
two: nothing on disk signals the agent is ready for pairing until the
WS handshake is complete. This closes the race where the QR was shown,
the mobile claimed, and the agent's WS had not yet opened — server's
recovery path then re-signed a push that mismatched the initiate-time
``relay_url`` and the agent rejected its own credentials.

Use the context-manager form ``PairingWsClient.connect(...)`` for the
two-phase flow; the legacy ``wait_for_claim`` is preserved for callers
that want one-shot semantics (CLI ``pair-qr --wait``, tests).
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator
from urllib.parse import SplitResult, urlsplit

import websockets
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from mobaide.manifest_signing import canonical_json

logger = logging.getLogger(__name__)

_AGENT_CRED_RE = re.compile(r"^[A-Za-z0-9_\-\.:+/=]{1,1024}$")

_MAX_CLOCK_SKEW = timedelta(minutes=5)

_READY_TIMEOUT_S = 15.0

_DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443, "ws": 80, "wss": 443}

# Protocol families for scheme-equivalence matching. ``_relay_urls_match``
# treats ``http`` and ``https`` as equivalent (and ``ws`` and ``wss``),
# but refuses cross-family equivalence (``ws`` ≠ ``http``). Cross-family
# is unreachable from any legitimate central_server response — the
# initiate / claim / WS-recovery branches all derive relay_url from
# ``request.base_url`` or ``websocket.url.scheme``, both of which yield
# only the http/https family — but defense-in-depth says: don't accept a
# scheme drift that no legitimate code path can produce.
_SCHEME_FAMILY: dict[str, str] = {
    "http": "http", "https": "http",
    "ws": "ws", "wss": "ws",
}


class PairingWsError(Exception):
    """Raised when the pairing WS rejects a push or times out."""


@dataclass(frozen=True)
class PairingClaim:
    """Validated credentials delivered over the pairing WS."""

    agent_id: str
    agent_token: str
    relay_url: str
    agent_friendly_name: str
    issued_at: str


def _verify_signature(
    payload: dict, signature_b64: str, trust_pub_key_b64: str
) -> None:
    """Ed25519-verify the canonical JSON of ``payload``."""
    pub_bytes = base64.b64decode(trust_pub_key_b64, validate=True)
    pub_key = Ed25519PublicKey.from_public_bytes(pub_bytes)
    sig = base64.b64decode(signature_b64, validate=True)
    pub_key.verify(sig, canonical_json(payload))


def _normalised_port(parts: SplitResult) -> int | None:
    """Return the effective non-default port for a ``urlsplit`` result.

    A port that is implicit OR explicitly equal to its scheme's default
    is normalised to ``None``. This way ``http://host``,
    ``http://host:80``, ``https://host``, and ``https://host:443`` all
    yield ``None`` — any combination of them compares equal, which is
    the intended scheme-equivalence behaviour. Only an explicit
    non-default port (e.g. ``:9000``) is preserved for strict
    comparison against the other side.
    """
    default = _DEFAULT_PORTS.get(parts.scheme.lower())
    if parts.port is None:
        return None
    if default is not None and parts.port == default:
        return None
    return parts.port


def _relay_urls_match(a: str, b: str) -> bool:
    """Compare two relay URLs treating ``http`` and ``https`` as equivalent.

    The agent's security boundary is the Ed25519 signature plus the
    pre-pinned trust-key fingerprint plus the agent-controlled
    ``RELAY_SERVER_URL`` — NOT the URL scheme of a server-supplied
    relay_url. Scheme drift between the initiate handler (which reads
    ``request.base_url``) and the WS recovery handler (which reads
    ``websocket.url.scheme``) is a deployment artefact behind reverse
    proxies and must not block pairing. Host and path still have to
    match.

    Scheme equivalence is gated to the same protocol family
    (``{http, https}`` or ``{ws, wss}``). Cross-family equivalence
    (e.g. ``ws://`` claim against ``https://`` pending) is rejected
    even when host/port match: no legitimate central_server code
    path produces such a drift, and accepting it would weaken the
    threat model under trust-key compromise (an attacker who can sign
    could plant ``ws://attacker`` into ``RELAY_SERVER_URL`` via the
    ``write_credentials_to_env`` blank-backfill branch).

    Port comparison: explicit default ports are normalised to
    implicit so ``https://host`` ≡ ``https://host:443`` ≡
    ``http://host`` ≡ ``http://host:80``. An explicit non-default
    port (``:9000``) on either side must match exactly.

    Userinfo, query string, and fragment are rejected outright on
    either side. Starlette's ``request.base_url`` (the central
    server's only legitimate source) never produces them, so any
    presence on the claim side is a smuggled payload from a
    trust-key-compromised push that would otherwise ride along into
    ``RELAY_SERVER_URL`` via ``write_credentials_to_env``'s blank-
    backfill branch and inject basic-auth credentials or path-shaping
    artefacts into downstream relay-client URL construction.
    """
    pa, pb = urlsplit(a), urlsplit(b)
    if pa.username is not None or pa.password is not None:
        return False
    if pb.username is not None or pb.password is not None:
        return False
    if pa.query or pa.fragment or pb.query or pb.fragment:
        return False
    if pa.hostname != pb.hostname:
        return False
    if pa.path.rstrip("/") != pb.path.rstrip("/"):
        return False
    scheme_a = pa.scheme.lower()
    scheme_b = pb.scheme.lower()
    family_a = _SCHEME_FAMILY.get(scheme_a)
    family_b = _SCHEME_FAMILY.get(scheme_b)
    if family_a is None or family_b is None:
        if scheme_a != scheme_b:
            return False
    elif family_a != family_b:
        return False
    return _normalised_port(pa) == _normalised_port(pb)


class PairingWsClient:
    """Single-use client; create a new instance per pairing nonce.

    Two entry points share validation/transport internals:

    * ``connect(...)`` — async context manager that opens the WS, awaits
      the server's ``"ready"`` frame, and yields a
      :class:`PairingWsConnection`. Callers do whatever they need
      (write a pending-pairing file, render the QR) and then
      ``await conn.wait_for_claim(...)``.
    * ``wait_for_claim(...)`` — one-shot helper that opens + handshakes
      + waits + parses in a single coroutine. Kept for the CLI verbs
      and existing tests.
    """

    @classmethod
    @contextlib.asynccontextmanager
    async def connect(
        cls,
        ws_url: str,
    ) -> "AsyncIterator[PairingWsConnection]":
        """Open the WS, complete the ``"ready"`` handshake, yield a connection."""
        logger.info("pairing_ws.connecting ws_url=%s", ws_url)
        try:
            async with websockets.connect(
                ws_url,
                open_timeout=15,
                close_timeout=5,
                # Explicit keepalive: a Cloudflare-style idle drop
                # (free-tier WebSocket connections close at 100 s,
                # business-tier configurable) surfaces as a
                # ConnectionClosedError within ~25 s instead of stalling
                # an ``await ws.recv()`` until ``timeout_s`` (up to
                # 120 s for a fresh nonce). Fast drop-detection is what
                # lets ``run_pairing_monitor``'s retain-pending +
                # exponential-backoff retry hit the server's
                # ``read_and_clear_claimed_credentials`` recovery path
                # while the nonce is still in its TTL window. The
                # python-websockets default is also 20 s/20 s, but
                # pinning the values explicitly survives a library
                # default change.
                ping_interval=20,
                ping_timeout=20,
            ) as ws:
                logger.info("pairing_ws.connected ws_url=%s", ws_url)
                ready = await asyncio.wait_for(ws.recv(), timeout=_READY_TIMEOUT_S)
                ready_msg = cls._decode_text(ready)
                if not isinstance(ready_msg, dict) or ready_msg.get("type") != "ready":
                    raise PairingWsError(
                        f"Expected 'ready' frame, got: {ready_msg!r}"
                    )
                logger.info("pairing_ws.ready_received ws_url=%s", ws_url)
                yield PairingWsConnection(ws=ws, ws_url=ws_url)
        except asyncio.TimeoutError as exc:
            logger.warning(
                "pairing_ws.ready_timeout ws_url=%s", ws_url,
            )
            raise PairingWsError(
                "pairing ws timed out before 'ready' frame arrived"
            ) from exc
        except PairingWsError:
            raise
        except (
            websockets.WebSocketException,
            ConnectionError,
            OSError,
            json.JSONDecodeError,
        ) as exc:
            logger.warning(
                "pairing_ws.transport_error ws_url=%s exc_type=%s: %s",
                ws_url, type(exc).__name__, exc,
            )
            raise PairingWsError(f"pairing ws transport failure: {exc}") from exc

    async def wait_for_claim(
        self,
        ws_url: str,
        timeout_s: float,
        trust_pub_key_b64: str,
        expected_relay_url: str,
    ) -> PairingClaim:
        """One-shot: connect, await ``"ready"``, await the push, validate.

        Raises:
            PairingWsError: any transport, validation, or timeout error.
                The caller should surface this to the operator and
                re-render a fresh QR — the nonce is single-use.
        """
        async with self.connect(ws_url) as conn:
            return await conn.wait_for_claim(
                timeout_s=timeout_s,
                trust_pub_key_b64=trust_pub_key_b64,
                expected_relay_url=expected_relay_url,
            )

    @staticmethod
    def _decode_text(raw):
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    @classmethod
    def _parse_and_verify(
        cls, push: object, *, trust_pub_key_b64: str, expected_relay_url: str
    ) -> PairingClaim:
        if not isinstance(push, dict):
            raise PairingWsError("push frame is not a JSON object")
        if push.get("type") != "claim_completed":
            raise PairingWsError(
                f"unexpected push type: {push.get('type')!r}"
            )
        signature_b64 = push.get("signature_b64")
        if not isinstance(signature_b64, str) or not signature_b64:
            raise PairingWsError("push missing signature_b64")

        unsigned = {k: v for k, v in push.items() if k != "signature_b64"}
        try:
            _verify_signature(unsigned, signature_b64, trust_pub_key_b64)
        except (InvalidSignature, ValueError) as exc:
            raise PairingWsError(
                f"signature verification failed: {exc}"
            ) from exc

        try:
            agent_id = str(push["agent_id"])
            agent_token = str(push["agent_token"])
            relay_url = str(push["relay_url"])
            friendly_name = str(push["agent_friendly_name"])
            issued_at = str(push["issued_at"])
        except KeyError as exc:
            raise PairingWsError(f"push missing field: {exc}") from exc

        if not _AGENT_CRED_RE.match(agent_id):
            raise PairingWsError("agent_id failed credential validation")
        if not _AGENT_CRED_RE.match(agent_token):
            raise PairingWsError("agent_token failed credential validation")
        if not _relay_urls_match(relay_url, expected_relay_url):
            raise PairingWsError(
                f"relay_url mismatch: expected {expected_relay_url!r}, "
                f"got {relay_url!r}"
            )
        try:
            issued_dt = datetime.fromisoformat(issued_at)
        except ValueError as exc:
            raise PairingWsError(f"invalid issued_at: {exc}") from exc
        if issued_dt.tzinfo is None:
            issued_dt = issued_dt.replace(tzinfo=timezone.utc)
        skew = abs(datetime.now(timezone.utc) - issued_dt)
        if skew > _MAX_CLOCK_SKEW:
            raise PairingWsError(
                f"issued_at clock skew {skew} exceeds max {_MAX_CLOCK_SKEW}"
            )

        return PairingClaim(
            agent_id=agent_id,
            agent_token=agent_token,
            relay_url=relay_url,
            agent_friendly_name=friendly_name,
            issued_at=issued_at,
        )


class PairingWsConnection:
    """Open, ready WS connection waiting for a claim push.

    Yielded by :meth:`PairingWsClient.connect`. The contract: by the
    time a caller has this object in hand, the server has sent
    ``"ready"`` and is prepared to dispatch a claim. Save state and
    render the QR HERE — anything externally visible after this point
    is safe because the agent is now reachable for dispatch.
    """

    def __init__(self, ws, ws_url: str) -> None:
        self._ws = ws
        self._ws_url = ws_url

    async def wait_for_claim(
        self,
        *,
        timeout_s: float,
        trust_pub_key_b64: str,
        expected_relay_url: str,
    ) -> PairingClaim:
        """Wait for the signed claim push, validate, and return the parsed claim."""
        logger.info(
            "pairing_ws.recv_claim_begin ws_url=%s timeout_s=%.1f",
            self._ws_url, timeout_s,
        )
        try:
            payload_raw = await asyncio.wait_for(
                self._ws.recv(), timeout=max(1.0, timeout_s)
            )
        except asyncio.TimeoutError as exc:
            logger.warning(
                "pairing_ws.timeout ws_url=%s timeout_s=%.1f",
                self._ws_url, timeout_s,
            )
            raise PairingWsError(
                "pairing ws timed out before claim arrived"
            ) from exc
        except (
            websockets.WebSocketException,
            ConnectionError,
            OSError,
        ) as exc:
            logger.warning(
                "pairing_ws.transport_error ws_url=%s exc_type=%s: %s",
                self._ws_url, type(exc).__name__, exc,
            )
            raise PairingWsError(
                f"pairing ws transport failure: {exc}"
            ) from exc

        if isinstance(payload_raw, str):
            payload_bytes = payload_raw.encode("utf-8")
        else:
            payload_bytes = bytes(payload_raw)
        try:
            push = json.loads(payload_bytes.decode("utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning(
                "pairing_ws.malformed_push ws_url=%s: %s", self._ws_url, exc,
            )
            raise PairingWsError(
                f"pairing ws delivered non-JSON payload: {exc}"
            ) from exc
        logger.info(
            "pairing_ws.recv_claim_payload ws_url=%s bytes=%d",
            self._ws_url, len(payload_bytes),
        )

        claim = PairingWsClient._parse_and_verify(
            push,
            trust_pub_key_b64=trust_pub_key_b64,
            expected_relay_url=expected_relay_url,
        )
        logger.info(
            "pairing_ws.claim_verified agent_id=%s relay_url=%s",
            claim.agent_id, claim.relay_url,
        )
        return claim
