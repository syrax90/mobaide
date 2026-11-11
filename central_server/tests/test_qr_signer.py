"""Pure-function tests for ``central_server.pairing.qr_signer``.

These helpers have no FastAPI / DB dependencies; the tests live as
plain ``def`` (not ``async def``) and avoid any fixtures from
``conftest.py`` so they run in isolation.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from central_server.db.database import _hash_token
from central_server.pairing.qr_signer import (
    canonical_payload_bytes,
    compute_trust_key_fp,
    encode_pairing_payload,
    hash_pairing_nonce,
    sign_pairing_payload,
)

# Resolved from this file, not the CWD: pytest runs from ``central_server/``
# and the shared golden lives at the repo root.
_QR_PAYLOAD_FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "tests"
        / "protocol_fixtures"
        / "pairing"
        / "qr_payload.json"
    ).read_text(encoding="utf-8")
)
_ENCODE_CASES = _QR_PAYLOAD_FIXTURE["encode_cases"]


def _ed25519_keypair_b64() -> tuple[str, str]:
    private = Ed25519PrivateKey.generate()
    public = private.public_key()
    priv_b64 = base64.b64encode(private.private_bytes_raw()).decode()
    pub_b64 = base64.b64encode(public.public_bytes_raw()).decode()
    return priv_b64, pub_b64


class TestComputeTrustKeyFp:
    def test_matches_sha256_of_decoded_bytes(self):
        # Pin the algorithm: fp == sha256(b64decode(public_key_b64)).hex()
        raw = b"\x00" * 32
        public_b64 = base64.b64encode(raw).decode()

        fp = compute_trust_key_fp(public_b64)

        assert fp == hashlib.sha256(raw).hexdigest()
        assert len(fp) == 64
        assert all(c in "0123456789abcdef" for c in fp)


class TestCanonicalPayloadBytes:
    def test_sorts_keys(self):
        out = canonical_payload_bytes({"b": 2, "a": 1})

        assert out == b'{"a":1,"b":2}'


class TestSignPairingPayload:
    def test_signature_verifies_with_matching_public_key(self):
        priv_b64, pub_b64 = _ed25519_keypair_b64()
        payload = {
            "type": "claim_completed",
            "agent_id": "abc123",
            "agent_token": "tok-xyz",
            "relay_url": "https://relay.example.com",
            "agent_friendly_name": "test-agent",
            "issued_at": "2026-05-07T12:00:00+00:00",
        }

        sig_b64 = sign_pairing_payload(priv_b64, payload)

        pub_key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(pub_b64)
        )
        sig_bytes = base64.b64decode(sig_b64)
        # Does not raise -> signature verified.
        pub_key.verify(sig_bytes, canonical_payload_bytes(payload))


class TestEncodePairingPayload:
    @pytest.mark.parametrize(
        "case",
        _ENCODE_CASES,
        ids=[c["name"] for c in _ENCODE_CASES],
    )
    def test_matches_the_shared_golden(self, case):
        # The same fixture the agent, web, Android and iOS encoders are
        # pinned to. The server used to build this string inline, so the
        # golden is what stops the extracted encoder from drifting.
        assert (
            encode_pairing_payload(
                case["relay_url"],
                case["nonce"],
                case["trust_key_fp"],
                case["exp_unix"],
            )
            == case["expect"]
        )

    def test_golden_is_not_empty(self):
        # A fixture that lost its cases would make the parametrised test
        # above vacuously green.
        assert _ENCODE_CASES


class TestHashPairingNonce:
    def test_equals_the_digest_the_database_stores(self):
        # This is the whole point of the helper: the router addresses
        # ``pairing_tokens`` rows and keys the WS waiter registry by it,
        # so any divergence from ``Database``'s own hashing turns every
        # device-code confirm into a silent ws_delivered=False.
        nonce = "Xr3sT-nonce_value-192bit"

        assert hash_pairing_nonce(nonce) == _hash_token(nonce)
