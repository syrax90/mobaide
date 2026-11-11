"""Cover ``mobaide.manifest_signing`` Ed25519 verification edge cases.

Specifically pins the malformed-input branches (bad base64, truncated key
bytes, wrong-length signature, body tampering) that gate the auto-updater
from accepting a forged manifest.
"""
from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mobaide.manifest_signing import (
    ManifestSignatureError,
    canonical_json,
    verify_signature,
)


@pytest.fixture
def signing_pair():
    private_key = Ed25519PrivateKey.generate()
    public_key_b64 = base64.b64encode(
        private_key.public_key().public_bytes_raw()
    ).decode()
    return private_key, public_key_b64


@pytest.fixture
def signed_body(signing_pair):
    private_key, public_key_b64 = signing_pair
    body = {"version": "1.0.0", "tarball_url": "x", "released_at": "now"}
    sig_b64 = base64.b64encode(private_key.sign(canonical_json(body))).decode()
    return body, sig_b64, public_key_b64


class TestCanonicalJson:
    def test_keys_are_sorted(self):
        encoded = canonical_json({"b": 1, "a": 2}).decode()
        assert encoded == '{"a":2,"b":1}'


class TestVerifySignatureSuccess:
    def test_valid_signature_passes(self, signed_body):
        body, sig_b64, public_key_b64 = signed_body
        verify_signature(body, sig_b64, public_key_b64)


class TestVerifySignatureFailure:
    def test_tampered_body_fails(self, signed_body):
        body, sig_b64, public_key_b64 = signed_body
        tampered = dict(body)
        tampered["version"] = "9.9.9"
        with pytest.raises(ManifestSignatureError, match="does not verify"):
            verify_signature(tampered, sig_b64, public_key_b64)

    def test_wrong_public_key_fails(self, signed_body):
        body, sig_b64, _ = signed_body
        other = Ed25519PrivateKey.generate()
        wrong_pub = base64.b64encode(other.public_key().public_bytes_raw()).decode()
        with pytest.raises(ManifestSignatureError, match="does not verify"):
            verify_signature(body, sig_b64, wrong_pub)

    def test_malformed_public_key_base64_fails(self, signed_body):
        body, sig_b64, _ = signed_body
        with pytest.raises(ManifestSignatureError, match="public key is not valid base64"):
            verify_signature(body, sig_b64, "@@not-base64@@")

    def test_truncated_public_key_bytes_fails(self, signed_body):
        body, sig_b64, _ = signed_body
        # Valid base64, but Ed25519 keys must be exactly 32 bytes — 16 bytes here.
        too_short = base64.b64encode(b"\x00" * 16).decode()
        with pytest.raises(ManifestSignatureError, match="not a valid Ed25519 key"):
            verify_signature(body, sig_b64, too_short)

    def test_malformed_signature_base64_fails(self, signed_body):
        body, _, public_key_b64 = signed_body
        with pytest.raises(ManifestSignatureError, match="signature is not valid base64"):
            verify_signature(body, "###", public_key_b64)

    def test_wrong_length_signature_fails(self, signed_body):
        body, _, public_key_b64 = signed_body
        # Valid base64, but Ed25519 signatures must be exactly 64 bytes.
        wrong_sig = base64.b64encode(b"\x00" * 32).decode()
        with pytest.raises(ManifestSignatureError, match="does not verify"):
            verify_signature(body, wrong_sig, public_key_b64)
