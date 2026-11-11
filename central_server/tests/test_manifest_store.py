"""Cover ``ManifestStore``: required-field validation, signature gating,
and the read cache."""
from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from central_server.manifest.store import (
    ManifestSignatureError,
    ManifestStore,
    ManifestValidationError,
    canonical_json,
    verify_signature,
)


def _valid_body() -> dict:
    return {
        "version": "1.2.3",
        "tarball_url": "https://example.com/agent-1.2.3.tar.gz",
        "tarball_sha256": "0" * 64,
        "wheel_url": "https://example.com/agent-1.2.3.whl",
        "wheel_sha256": "1" * 64,
        "released_at": "2026-04-26T00:00:00+00:00",
    }


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
    body = _valid_body()
    sig_b64 = base64.b64encode(private_key.sign(canonical_json(body))).decode()
    return body, sig_b64, public_key_b64


class TestRead:
    def test_read_corrupt_json_returns_none(self, tmp_path):
        path = tmp_path / "manifest.json"
        path.write_text("{not valid json", encoding="utf-8")
        store = ManifestStore(path)
        assert store.read() is None

    def test_read_missing_required_envelope_keys_returns_none(self, tmp_path):
        path = tmp_path / "manifest.json"
        # Body present, but signature_b64 key missing entirely.
        path.write_text(json.dumps({"body": _valid_body()}), encoding="utf-8")
        store = ManifestStore(path)
        assert store.read() is None


class TestWriteValidation:
    @pytest.mark.parametrize(
        "missing_field",
        [
            "version",
            "wheel_sha256",
        ],
    )
    def test_missing_required_field_raises(self, tmp_path, missing_field):
        store = ManifestStore(tmp_path / "manifest.json")
        body = _valid_body()
        body.pop(missing_field)
        with pytest.raises(ManifestValidationError, match=missing_field):
            store.write(body, "sig")

    def test_signature_gating_rejects_bad_signature(self, tmp_path, signed_body):
        body, _, public_key_b64 = signed_body
        store = ManifestStore(tmp_path / "manifest.json")
        bad_sig = base64.b64encode(b"\x00" * 64).decode()
        with pytest.raises(ManifestSignatureError):
            store.write(body, bad_sig, public_key_b64=public_key_b64)
        assert not (tmp_path / "manifest.json").exists()

    def test_signature_gating_skipped_when_no_public_key(
        self, tmp_path, signed_body,
    ):
        body, _, _ = signed_body
        store = ManifestStore(tmp_path / "manifest.json")
        # No public_key_b64 — signature is opaque, write proceeds.
        record = store.write(body, "any-string-here")
        assert record.signature_b64 == "any-string-here"


class TestWriteRead:
    def test_write_then_read_returns_the_just_written_record(
        self, tmp_path, signed_body,
    ):
        body, sig_b64, public_key_b64 = signed_body
        store = ManifestStore(tmp_path / "manifest.json")
        store.write(body, sig_b64, public_key_b64=public_key_b64)
        record = store.read()
        assert record is not None
        assert record.body == body
        assert record.signature_b64 == sig_b64

    def test_write_updates_in_memory_cache_without_disk_reread(
        self, tmp_path, signed_body,
    ):
        body, sig_b64, _ = signed_body
        target = tmp_path / "manifest.json"
        store = ManifestStore(target)
        store.write(body, sig_b64)

        # Mutate the file out from under the store; cached read should still
        # reflect what write() persisted (last-good-cache invariant).
        target.write_text("{garbage", encoding="utf-8")
        cached = store.read()
        assert cached is not None
        assert cached.body == body


class TestVerifySignatureModule:
    """Quick sanity for the central_server-side verify_signature copy.

    The full edge-case coverage lives in the agent package
    (``test_manifest_signing.py``); this test pins the duplication
    contract that both copies behave identically on the happy and unhappy
    path. CI's protocol-integrity workflow keeps the *constants* aligned
    across the two packages — these guards extend the same intent to the
    runtime behavior of the verifier itself.
    """

    def test_tampered_body_fails(self, signed_body):
        body, sig_b64, public_key_b64 = signed_body
        tampered = dict(body)
        tampered["version"] = "9.9.9"
        with pytest.raises(ManifestSignatureError, match="does not verify"):
            verify_signature(tampered, sig_b64, public_key_b64)
