"""Cover the trust data an agent takes from a signed release manifest.

The caller (the updater, or the trust refresh before a pairing) checks the
signature and the version first. This module decides what a verified manifest
may change:

* release keys are only added, up to a fixed limit;
* the relay pairing keys are the list of the newest manifest;
* a manifest that is not newer than the stored one changes nothing.

Keys are made at test run time.
"""
from __future__ import annotations

import base64
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import mobaide.agent.trust_store as trust_store
from mobaide.agent.trust_store import (
    MANIFEST_TRUST_FILENAME,
    MAX_KEYS_PER_LIST,
    ManifestTrust,
    load_manifest_trust,
    pairing_verification_keys,
    record_manifest_trust,
    release_verification_keys,
)
from mobaide.agent.updater import InstallLayout

EARLIER = "2026-05-01T00:00:00+00:00"
LATER = "2026-06-01T00:00:00+00:00"
LATEST = "2026-07-01T00:00:00+00:00"


def _new_key() -> str:
    return base64.b64encode(
        Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    ).decode()


def _manifest(released_at, *, release=(), pairing=()) -> dict:
    return {
        "version": "1.0.0",
        "released_at": released_at,
        "trust": {
            "release_public_keys": list(release),
            "relay_pairing_public_keys": list(pairing),
        },
    }


@pytest.fixture
def layout(tmp_path: Path) -> InstallLayout:
    layout = InstallLayout(prefix=tmp_path / "mobaide")
    layout.trust_dir.mkdir(parents=True)
    return layout


class TestReleaseKeys:
    def test_a_newer_manifest_adds_keys_and_never_removes_one(self, layout):
        first, second = _new_key(), _new_key()

        assert record_manifest_trust(layout, _manifest(EARLIER, release=[first]))
        assert record_manifest_trust(layout, _manifest(LATER, release=[second]))
        # A manifest that lists no release key leaves the added keys trusted.
        assert not record_manifest_trust(layout, _manifest(LATEST))

        assert load_manifest_trust(layout).release_public_keys == (first, second)

    def test_the_list_stops_at_the_limit(self, layout):
        first_batch = [_new_key() for _ in range(MAX_KEYS_PER_LIST - 1)]
        second_batch = [_new_key() for _ in range(3)]

        record_manifest_trust(layout, _manifest(EARLIER, release=first_batch))
        record_manifest_trust(layout, _manifest(LATER, release=second_batch))

        assert load_manifest_trust(layout).release_public_keys == (
            *first_batch, second_batch[0],
        )

    def test_the_pinned_key_comes_first_and_is_not_repeated(self, layout):
        pinned, added = _new_key(), _new_key()
        record_manifest_trust(layout, _manifest(EARLIER, release=[added, pinned]))

        assert release_verification_keys(layout, pinned) == (pinned, added)

    def test_entries_that_are_not_ed25519_public_keys_are_dropped(self, layout):
        good = _new_key()
        unusable = [
            "@@not-base64@@",
            base64.b64encode(b"\x00" * 16).decode(),
            42,
            None,
        ]

        record_manifest_trust(
            layout, _manifest(EARLIER, release=[*unusable, good, good]),
        )

        assert load_manifest_trust(layout).release_public_keys == (good,)


class TestRelayPairingKeys:
    def test_the_newest_manifest_states_the_full_list(self, layout):
        pinned, retired, current = _new_key(), _new_key(), _new_key()

        record_manifest_trust(layout, _manifest(EARLIER, pairing=[retired]))
        record_manifest_trust(layout, _manifest(LATER, pairing=[current]))

        assert pairing_verification_keys(layout, pinned) == (pinned, current)

    def test_release_keys_do_not_sign_the_credential_push(self, layout):
        pinned, added_release_key = _new_key(), _new_key()
        record_manifest_trust(
            layout, _manifest(EARLIER, release=[added_release_key]),
        )

        assert pairing_verification_keys(layout, pinned) == (pinned,)


class TestManifestOrder:
    @pytest.mark.parametrize("replayed_at", [EARLIER, LATER])
    def test_a_manifest_that_is_not_newer_changes_nothing(self, layout, replayed_at):
        release_key, pairing_key = _new_key(), _new_key()
        record_manifest_trust(
            layout, _manifest(LATER, release=[release_key], pairing=[pairing_key]),
        )
        stored = load_manifest_trust(layout)

        changed = record_manifest_trust(
            layout,
            _manifest(replayed_at, release=[_new_key()], pairing=[_new_key()]),
        )

        assert not changed
        assert load_manifest_trust(layout) == stored

    @pytest.mark.parametrize(
        "released_at",
        [None, "", "not-a-time", "2026-06-01T00:00:00"],
    )
    def test_a_manifest_without_a_usable_release_time_is_ignored(
        self, layout, released_at
    ):
        # Without a time with an offset the manifests cannot be ordered.
        changed = record_manifest_trust(
            layout, _manifest(released_at, release=[_new_key()]),
        )

        assert not changed
        assert load_manifest_trust(layout) == ManifestTrust()

    @pytest.mark.parametrize("trust", [None, "keys", ["keys"]])
    def test_a_newer_manifest_without_trust_data_keeps_the_stored_keys(
        self, layout, trust
    ):
        pairing_key = _new_key()
        record_manifest_trust(layout, _manifest(EARLIER, pairing=[pairing_key]))
        body = {"version": "1.0.1", "released_at": LATER}
        if trust is not None:
            body["trust"] = trust

        assert not record_manifest_trust(layout, body)
        assert load_manifest_trust(layout).relay_pairing_public_keys == (pairing_key,)


class TestStorage:
    def test_recorded_keys_are_read_back_after_a_restart(self, layout, monkeypatch):
        release_key, pairing_key = _new_key(), _new_key()
        record_manifest_trust(
            layout, _manifest(EARLIER, release=[release_key], pairing=[pairing_key]),
        )
        # A new process starts with an empty in-memory copy.
        monkeypatch.setattr(trust_store, "_memory", {})

        assert load_manifest_trust(layout) == ManifestTrust(
            release_public_keys=(release_key,),
            relay_pairing_public_keys=(pairing_key,),
            released_at=EARLIER,
        )

    def test_a_trust_dir_that_cannot_be_written_keeps_the_keys_in_memory(
        self, layout, monkeypatch
    ):
        def _refuse_write(*_args, **_kwargs):
            raise PermissionError("read-only trust dir")

        monkeypatch.setattr(trust_store, "atomic_write", _refuse_write)
        pairing_key = _new_key()

        assert record_manifest_trust(layout, _manifest(EARLIER, pairing=[pairing_key]))

        assert not (layout.trust_dir / MANIFEST_TRUST_FILENAME).exists()
        assert load_manifest_trust(layout).relay_pairing_public_keys == (pairing_key,)

    @pytest.mark.parametrize("content", ["not json {{", "[]", '"text"'])
    def test_a_damaged_trust_file_gives_no_extra_key(self, layout, content):
        pinned = _new_key()
        (layout.trust_dir / MANIFEST_TRUST_FILENAME).write_text(
            content, encoding="utf-8",
        )

        assert load_manifest_trust(layout) == ManifestTrust()
        assert release_verification_keys(layout, pinned) == (pinned,)
        assert pairing_verification_keys(layout, pinned) == (pinned,)
