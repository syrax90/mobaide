"""Tests for ``mobaide.agent.pairing_ws._parse_and_verify``.

We test the verification helper directly rather than the websocket
transport — the network-handling outer ``wait_for_claim`` is integration-
shaped (real WS server). The verification rules covered here are the
agent's defense against:

* tampered server pushes (signature must verify with pinned key),
* a server impersonating another relay (relay_url must match what the
  agent originally connected to),
* badly-clocked or clock-skewed pushes (issued_at within 5 min of now),
* malformed credentials (``_AGENT_CRED_RE`` rejects shell metas / NUL).
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mobaide.agent.pairing_ws import (
    PairingClaim,
    PairingWsClient,
    PairingWsError,
)


def _ed25519_keypair_b64() -> tuple[str, str]:
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    return (
        base64.b64encode(priv.private_bytes_raw()).decode(),
        base64.b64encode(pub.public_bytes_raw()).decode(),
    )


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _signed_push(
    *,
    private_key_b64: str,
    issued_at: str | None = None,
    overrides: dict | None = None,
) -> dict:
    """Build a signed claim_completed push that ``_parse_and_verify`` accepts.

    Tests can partially override individual fields to exercise rejections.
    """
    priv = Ed25519PrivateKey.from_private_bytes(
        base64.b64decode(private_key_b64)
    )
    base = {
        "type": "claim_completed",
        "agent_id": "agent-12hex",
        "agent_token": "tok-urlsafe-32-bytes-1234567890",
        "relay_url": "https://relay.test",
        "agent_friendly_name": "lab-host",
        "issued_at": issued_at
        or datetime.now(timezone.utc).isoformat(),
    }
    if overrides:
        base.update(overrides)
    sig = priv.sign(_canonical(base))
    return {**base, "signature_b64": base64.b64encode(sig).decode()}


class TestParseAndVerifyHappyPath:
    def test_returns_pairing_claim(self):
        priv_b64, pub_b64 = _ed25519_keypair_b64()
        push = _signed_push(private_key_b64=priv_b64)

        claim = PairingWsClient._parse_and_verify(
            push,
            trust_pub_key_b64=pub_b64,
            expected_relay_url="https://relay.test",
        )

        assert isinstance(claim, PairingClaim)
        assert claim.agent_id == "agent-12hex"
        assert claim.agent_token == "tok-urlsafe-32-bytes-1234567890"
        assert claim.relay_url == "https://relay.test"
        assert claim.agent_friendly_name == "lab-host"

    def test_relay_url_trailing_slash_normalised(self):
        # Either side of the comparison may carry a trailing slash; the
        # check is rstrip("/")-normalised.
        priv_b64, pub_b64 = _ed25519_keypair_b64()
        push = _signed_push(
            private_key_b64=priv_b64,
            overrides={"relay_url": "https://relay.test/"},
        )

        claim = PairingWsClient._parse_and_verify(
            push,
            trust_pub_key_b64=pub_b64,
            expected_relay_url="https://relay.test",
        )

        assert claim.relay_url == "https://relay.test/"


class TestParseAndVerifyRejections:
    def test_rejects_non_dict(self):
        with pytest.raises(PairingWsError, match="JSON object"):
            PairingWsClient._parse_and_verify(
                "not-a-dict",
                trust_pub_key_b64="aa",
                expected_relay_url="https://relay.test",
            )

    def test_rejects_unexpected_type(self):
        priv_b64, pub_b64 = _ed25519_keypair_b64()
        push = _signed_push(
            private_key_b64=priv_b64,
            overrides={"type": "definitely_not_claim_completed"},
        )

        with pytest.raises(PairingWsError, match="unexpected push type"):
            PairingWsClient._parse_and_verify(
                push,
                trust_pub_key_b64=pub_b64,
                expected_relay_url="https://relay.test",
            )

    def test_rejects_missing_signature(self):
        priv_b64, pub_b64 = _ed25519_keypair_b64()
        push = _signed_push(private_key_b64=priv_b64)
        del push["signature_b64"]

        with pytest.raises(PairingWsError, match="missing signature_b64"):
            PairingWsClient._parse_and_verify(
                push,
                trust_pub_key_b64=pub_b64,
                expected_relay_url="https://relay.test",
            )

    def test_rejects_tampered_payload(self):
        priv_b64, pub_b64 = _ed25519_keypair_b64()
        push = _signed_push(private_key_b64=priv_b64)
        # Tamper the agent_token AFTER signing.
        push["agent_token"] = "not-the-real-token"

        with pytest.raises(PairingWsError, match="signature verification"):
            PairingWsClient._parse_and_verify(
                push,
                trust_pub_key_b64=pub_b64,
                expected_relay_url="https://relay.test",
            )

    def test_rejects_signature_with_wrong_key(self):
        # Push is signed with key A but verifier holds key B → reject.
        priv_a_b64, _ = _ed25519_keypair_b64()
        _, pub_b_b64 = _ed25519_keypair_b64()
        push = _signed_push(private_key_b64=priv_a_b64)

        with pytest.raises(PairingWsError, match="signature verification"):
            PairingWsClient._parse_and_verify(
                push,
                trust_pub_key_b64=pub_b_b64,
                expected_relay_url="https://relay.test",
            )

    def test_rejects_relay_url_mismatch(self):
        priv_b64, pub_b64 = _ed25519_keypair_b64()
        push = _signed_push(
            private_key_b64=priv_b64,
            overrides={"relay_url": "https://relay.other"},
        )

        with pytest.raises(PairingWsError, match="relay_url mismatch"):
            PairingWsClient._parse_and_verify(
                push,
                trust_pub_key_b64=pub_b64,
                expected_relay_url="https://relay.test",
            )

    def test_rejects_clock_skew_too_large(self):
        # ``issued_at`` more than 5 min off → reject.
        priv_b64, pub_b64 = _ed25519_keypair_b64()
        skewed = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).isoformat()
        push = _signed_push(private_key_b64=priv_b64, issued_at=skewed)

        with pytest.raises(PairingWsError, match="clock skew"):
            PairingWsClient._parse_and_verify(
                push,
                trust_pub_key_b64=pub_b64,
                expected_relay_url="https://relay.test",
            )

    def test_rejects_malformed_issued_at(self):
        priv_b64, pub_b64 = _ed25519_keypair_b64()
        push = _signed_push(
            private_key_b64=priv_b64,
            overrides={"issued_at": "not-an-iso-timestamp"},
        )

        with pytest.raises(PairingWsError, match="invalid issued_at"):
            PairingWsClient._parse_and_verify(
                push,
                trust_pub_key_b64=pub_b64,
                expected_relay_url="https://relay.test",
            )

    @pytest.mark.parametrize(
        "bad",
        [
            "tok with space",        # space rejected
            "tok\nwith-newline",     # newline rejected (smuggle .env line)
            "tok\rwith-cr",          # CR rejected
            "tok\x00with-nul",       # NUL rejected
            "tok!with-bang",         # ! not in regex
        ],
    )
    def test_rejects_credentials_failing_regex(self, bad):
        priv_b64, pub_b64 = _ed25519_keypair_b64()
        push = _signed_push(
            private_key_b64=priv_b64,
            overrides={"agent_token": bad},
        )

        with pytest.raises(PairingWsError, match="agent_token failed"):
            PairingWsClient._parse_and_verify(
                push,
                trust_pub_key_b64=pub_b64,
                expected_relay_url="https://relay.test",
            )

    def test_rejects_missing_required_field(self):
        # A push that is correctly signed but omits one required field
        # (server bug or unexpected schema drift). The signature must
        # cover the *exact* fields that the agent will read, so we sign
        # the truncated dict directly rather than mutating after sign.
        priv_b64, pub_b64 = _ed25519_keypair_b64()
        priv = Ed25519PrivateKey.from_private_bytes(
            base64.b64decode(priv_b64)
        )
        truncated = {
            "type": "claim_completed",
            # agent_id deliberately absent
            "agent_token": "tok-urlsafe-32-bytes-1234567890",
            "relay_url": "https://relay.test",
            "agent_friendly_name": "lab-host",
            "issued_at": datetime.now(timezone.utc).isoformat(),
        }
        sig = priv.sign(_canonical(truncated))
        push = {**truncated, "signature_b64": base64.b64encode(sig).decode()}

        with pytest.raises(PairingWsError, match="missing field"):
            PairingWsClient._parse_and_verify(
                push,
                trust_pub_key_b64=pub_b64,
                expected_relay_url="https://relay.test",
            )
