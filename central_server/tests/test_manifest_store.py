"""Cover ``ManifestStore``: required-field validation, signature gating,
atomic writes, and the read cache.

Atomic-write correctness is verified via two read-back assertions and the
absence of a stale ``.tmp`` file rather than by injecting failure mid-write
(which would require process-level fault injection out of scope here).
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

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
    def test_read_missing_file_returns_none(self, tmp_path):
        store = ManifestStore(tmp_path / "absent.json")
        assert store.read() is None

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

    def test_read_wrong_field_types_returns_none(self, tmp_path):
        path = tmp_path / "manifest.json"
        # body is a list rather than a dict — should be rejected.
        path.write_text(
            json.dumps({"body": [1, 2, 3], "signature_b64": "x"}),
            encoding="utf-8",
        )
        store = ManifestStore(path)
        assert store.read() is None

    def test_read_returns_stored_manifest(self, tmp_path, signed_body):
        body, sig_b64, _ = signed_body
        path = tmp_path / "manifest.json"
        path.write_text(
            json.dumps({"body": body, "signature_b64": sig_b64}),
            encoding="utf-8",
        )
        store = ManifestStore(path)
        record = store.read()
        assert record is not None
        assert record.body == body
        assert record.signature_b64 == sig_b64
        assert record.version == "1.2.3"


class TestWriteValidation:
    @pytest.mark.parametrize(
        "missing_field",
        [
            "version",
            "tarball_url",
            "tarball_sha256",
            "wheel_url",
            "wheel_sha256",
            "released_at",
        ],
    )
    def test_missing_required_field_raises(self, tmp_path, missing_field):
        store = ManifestStore(tmp_path / "manifest.json")
        body = _valid_body()
        body.pop(missing_field)
        with pytest.raises(ManifestValidationError, match=missing_field):
            store.write(body, "sig")

    def test_empty_string_field_treated_as_missing(self, tmp_path):
        store = ManifestStore(tmp_path / "manifest.json")
        body = _valid_body()
        body["version"] = ""
        with pytest.raises(ManifestValidationError, match="version"):
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


class TestWriteAtomicity:
    def test_write_creates_parent_directories(self, tmp_path, signed_body):
        body, sig_b64, _ = signed_body
        nested = tmp_path / "data" / "manifests" / "agent.json"
        store = ManifestStore(nested)
        store.write(body, sig_b64)
        assert nested.is_file()

    def test_write_leaves_no_tmp_file(self, tmp_path, signed_body):
        body, sig_b64, _ = signed_body
        target = tmp_path / "manifest.json"
        store = ManifestStore(target)
        store.write(body, sig_b64)

        leftover_tmps = list(tmp_path.glob("*.tmp"))
        assert leftover_tmps == [], f"stale tmp files: {leftover_tmps}"

    def test_overwrite_preserves_only_latest_record(self, tmp_path, signed_body):
        body, sig_b64, _ = signed_body
        target = tmp_path / "manifest.json"
        store = ManifestStore(target)
        store.write(body, sig_b64)

        new_body = dict(body)
        new_body["version"] = "1.2.4"
        store.write(new_body, "sig2")

        on_disk = json.loads(target.read_text(encoding="utf-8"))
        assert on_disk["body"]["version"] == "1.2.4"
        assert on_disk["signature_b64"] == "sig2"

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
    (``test_manifest_signing.py``); these two tests pin the duplication
    contract that both copies behave identically on the happy and unhappy
    paths. CI's protocol-integrity workflow keeps the *constants* aligned
    across the two packages — these guards extend the same intent to the
    runtime behavior of the verifier itself.
    """

    def test_valid_signature_passes(self, signed_body):
        body, sig_b64, public_key_b64 = signed_body
        verify_signature(body, sig_b64, public_key_b64)

    def test_tampered_body_fails(self, signed_body):
        body, sig_b64, public_key_b64 = signed_body
        tampered = dict(body)
        tampered["version"] = "9.9.9"
        with pytest.raises(ManifestSignatureError, match="does not verify"):
            verify_signature(tampered, sig_b64, public_key_b64)
