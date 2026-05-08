"""Pure-function tests for ``central_server.pairing.qr_signer``.

These helpers have no FastAPI / DB dependencies; the tests live as
plain ``def`` (not ``async def``) and avoid any fixtures from
``conftest.py`` so they run in isolation.
"""
from __future__ import annotations

import base64
import hashlib

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from central_server.pairing.qr_signer import (
    canonical_payload_bytes,
    compute_trust_key_fp,
    sign_pairing_payload,
)


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

    def test_distinct_keys_yield_distinct_fingerprints(self):
        a = base64.b64encode(b"\x00" * 32).decode()
        b = base64.b64encode(b"\x01" * 32).decode()

        assert compute_trust_key_fp(a) != compute_trust_key_fp(b)

    def test_invalid_base64_raises(self):
        with pytest.raises((ValueError, base64.binascii.Error)):
            compute_trust_key_fp("not~valid~b64@@@")


class TestCanonicalPayloadBytes:
    def test_sorts_keys(self):
        out = canonical_payload_bytes({"b": 2, "a": 1})

        assert out == b'{"a":1,"b":2}'

    def test_no_whitespace(self):
        out = canonical_payload_bytes({"k": "v"})

        # No spaces between separators — pinned by the agent-side mirror.
        assert b" " not in out

    def test_dict_order_does_not_affect_output(self):
        a = canonical_payload_bytes({"x": 1, "y": 2, "z": 3})
        b = canonical_payload_bytes({"z": 3, "y": 2, "x": 1})

        assert a == b


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

    def test_tampered_payload_fails_verification(self):
        priv_b64, pub_b64 = _ed25519_keypair_b64()
        payload = {"agent_id": "abc", "agent_token": "tok"}
        sig_b64 = sign_pairing_payload(priv_b64, payload)

        tampered = {"agent_id": "abc", "agent_token": "tok-tampered"}
        pub_key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(pub_b64)
        )
        with pytest.raises(InvalidSignature):
            pub_key.verify(
                base64.b64decode(sig_b64),
                canonical_payload_bytes(tampered),
            )

    def test_signature_is_deterministic_for_same_payload_and_key(self):
        # Ed25519 signatures are deterministic by spec (RFC 8032 §5.1.6).
        priv_b64, _ = _ed25519_keypair_b64()
        payload = {"a": 1, "b": "two"}

        s1 = sign_pairing_payload(priv_b64, payload)
        s2 = sign_pairing_payload(priv_b64, payload)

        assert s1 == s2
