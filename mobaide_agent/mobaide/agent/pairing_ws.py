"""Agent-side pairing WebSocket client.

Connects to the central server's ``WS /ws/pairing/{nonce}``, awaits the
signed credential push, validates the Ed25519 signature against the
pinned trust public key, and returns the parsed claim. The atomic .env
write is the caller's responsibility — this module is concerned only
with transport + verification.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import websockets
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from mobaide.manifest_signing import canonical_json

logger = logging.getLogger(__name__)

# Restrictive enough to refuse newline injection (CR/LF/NUL) and shell
# metacharacters, lenient enough to allow base64-url, hex, UUIDs, and
# colon-prefixed tokens that some auth schemes generate.
_AGENT_CRED_RE = re.compile(r"^[A-Za-z0-9_\-\.:+/=]{1,1024}$")

# Maximum allowed clock skew between server's ``issued_at`` and the
# agent's local clock when validating a push. The push window is
# ~120s anyway (TTL); 5 minutes is generous tolerance for badly
# synchronised hosts without expanding replay surface meaningfully.
_MAX_CLOCK_SKEW = timedelta(minutes=5)


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


class PairingWsClient:
    """Single-use client; create a new instance per pairing nonce."""

    async def wait_for_claim(
        self,
        ws_url: str,
        timeout_s: float,
        trust_pub_key_b64: str,
        expected_relay_url: str,
    ) -> PairingClaim:
        """Connect, await the push, validate, return the claim.

        Raises:
            PairingWsError: any transport, validation, or timeout error.
                The caller should surface this to the operator and
                re-render a fresh QR — the nonce is single-use.
        """
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
                ready = await asyncio.wait_for(ws.recv(), timeout=15)
                ready_msg = self._decode_text(ready)
                if not isinstance(ready_msg, dict) or ready_msg.get("type") != "ready":
                    raise PairingWsError(
                        f"Expected 'ready' frame, got: {ready_msg!r}"
                    )
                logger.info("pairing_ws.ready_received ws_url=%s", ws_url)

                logger.info(
                    "pairing_ws.recv_claim_begin ws_url=%s timeout_s=%.1f",
                    ws_url, timeout_s,
                )
                payload_raw = await asyncio.wait_for(
                    ws.recv(), timeout=max(1.0, timeout_s)
                )
                if isinstance(payload_raw, str):
                    payload_bytes = payload_raw.encode("utf-8")
                else:
                    payload_bytes = bytes(payload_raw)
                push = json.loads(payload_bytes.decode("utf-8"))
                logger.info(
                    "pairing_ws.recv_claim_payload ws_url=%s bytes=%d",
                    ws_url, len(payload_bytes),
                )
        except asyncio.TimeoutError as exc:
            logger.warning(
                "pairing_ws.timeout ws_url=%s timeout_s=%.1f", ws_url, timeout_s,
            )
            raise PairingWsError(
                "pairing ws timed out before claim arrived"
            ) from exc
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

        claim = self._parse_and_verify(
            push,
            trust_pub_key_b64=trust_pub_key_b64,
            expected_relay_url=expected_relay_url,
        )
        logger.info(
            "pairing_ws.claim_verified agent_id=%s relay_url=%s",
            claim.agent_id, claim.relay_url,
        )
        return claim

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
        if relay_url.rstrip("/") != expected_relay_url.rstrip("/"):
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
