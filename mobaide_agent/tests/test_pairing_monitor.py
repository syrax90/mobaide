"""Tests for ``mobaide.agent.pairing_monitor``.

Targets:

* ``initiate_pairing`` — synchronous urllib call. Mocks ``urlopen``
  to validate happy path, fingerprint mismatch (PairingMonitorError),
  malformed-payload rejection, and HTTP-level non-200 surfacing.
* ``write_credentials_to_env`` — atomic .env write that backfills
  RELAY_SERVER_URL only when missing.

The full ``run_pairing_monitor`` loop is integration-shaped (it ties
together initiate + WS + restart) and is intentionally out of scope.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from mobaide.agent.pairing_monitor import (
    PairingMonitorError,
    initiate_pairing,
    write_credentials_to_env,
)
from mobaide.agent.pairing_ws import PairingClaim


def _initiate_response(**overrides) -> dict:
    base = {
        "nonce": "n1",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "trust_key_fp": "ff" * 32,
        "relay_url": "https://relay.test",
        "ws_url": "wss://relay.test/ws/pairing/n1",
        "qr_payload": f"MOBA1|https://relay.test|n1|{'ff' * 32}|2999999999",
    }
    base.update(overrides)
    return base


class _FakeResp:
    def __init__(self, status: int, body: dict | str):
        self.status = status
        if isinstance(body, dict):
            self._body = json.dumps(body).encode("utf-8")
        else:
            self._body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _patch_urlopen(resp):
    return patch("urllib.request.urlopen", return_value=resp)


class TestInitiatePairing:
    def test_happy_path_returns_pending_pairing(self):
        # Pinned fp matches server response → initiate succeeds.
        with _patch_urlopen(_FakeResp(200, _initiate_response())):
            pending = initiate_pairing(
                "wss://relay.test",
                pinned_trust_key_fp="ff" * 32,
                hostname="alpha",
                os_name="Linux",
            )

        assert pending.nonce == "n1"
        assert pending.relay_url == "https://relay.test"
        assert pending.trust_key_fp == "ff" * 32
        assert pending.qr_payload.startswith("MOBA1|")

    def test_fp_mismatch_raises_pairing_monitor_error(self):
        # Server announces a different fingerprint than the agent has
        # pinned → fail-closed; do not render a QR a mobile can't
        # safely scan.
        with _patch_urlopen(
            _FakeResp(200, _initiate_response(trust_key_fp="aa" * 32))
        ):
            with pytest.raises(
                PairingMonitorError, match="fingerprint mismatch"
            ):
                initiate_pairing(
                    "wss://relay.test",
                    pinned_trust_key_fp="ff" * 32,
                )

    def test_malformed_qr_payload_raises(self):
        with _patch_urlopen(
            _FakeResp(
                200, _initiate_response(qr_payload="MOBA0|bad|payload")
            )
        ):
            with pytest.raises(
                PairingMonitorError, match="malformed QR payload"
            ):
                initiate_pairing(
                    "wss://relay.test",
                    pinned_trust_key_fp="ff" * 32,
                )

    def test_includes_metadata_in_request_body(self):
        captured: dict = {}

        def _fake_urlopen(req, timeout=None):
            captured["data"] = json.loads(req.data.decode("utf-8"))
            return _FakeResp(200, _initiate_response())

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            initiate_pairing(
                "wss://relay.test",
                pinned_trust_key_fp="ff" * 32,
                hostname="alpha",
                os_name="Linux",
                current_agent_id="agent-prior",
            )

        assert captured["data"] == {
            "hostname": "alpha",
            "os": "Linux",
            "current_agent_id": "agent-prior",
        }

    def test_omits_unset_metadata_fields(self):
        # None values are dropped; only the keys the agent actually
        # supplied land in the JSON body.
        captured: dict = {}

        def _fake_urlopen(req, timeout=None):
            captured["data"] = json.loads(req.data.decode("utf-8"))
            return _FakeResp(200, _initiate_response())

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            initiate_pairing(
                "wss://relay.test",
                pinned_trust_key_fp="ff" * 32,
            )

        assert captured["data"] == {}

    def test_outbound_request_carries_mobaide_user_agent(self):
        # Regression guard for the Cloudflare 1010 UA block: ``initiate_pairing`` must route through
        # ``mobaide._http.urlopen`` so Cloudflare's default Bot Fight Mode
        # doesn't 403 the request based on the ``Python-urllib/X.Y`` UA.
        captured: dict = {}

        def _fake_urlopen(req, timeout=None):
            captured["request"] = req
            return _FakeResp(200, _initiate_response())

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            initiate_pairing(
                "wss://relay.test",
                pinned_trust_key_fp="ff" * 32,
            )

        ua = captured["request"].get_header("User-agent")
        assert ua is not None and ua.startswith("mobaide-agent/")
        # Caller-supplied Content-Type must not have been clobbered by the
        # wrapper's header merge.
        assert (
            captured["request"].get_header("Content-type")
            == "application/json"
        )


class TestWriteCredentialsToEnv:
    @pytest.fixture
    def env_path(self, tmp_path) -> Path:
        path = tmp_path / "agent.env"
        return path

    async def test_writes_id_and_token_overriding_existing(
        self, env_path: Path
    ):
        env_path.write_text(
            "RELAY_AGENT_ID=old-id\n"
            "RELAY_AGENT_TOKEN=old-token\n"
            "RELAY_SERVER_URL=wss://relay.existing\n",
            encoding="utf-8",
        )
        claim = PairingClaim(
            agent_id="new-id",
            agent_token="new-token",
            relay_url="https://relay.test",
            agent_friendly_name="lab",
            issued_at="2026-05-07T00:00:00+00:00",
        )

        await write_credentials_to_env(env_path, claim)

        contents = env_path.read_text(encoding="utf-8")
        assert "RELAY_AGENT_ID=new-id" in contents
        assert "RELAY_AGENT_TOKEN=new-token" in contents
        # Did NOT overwrite a non-empty RELAY_SERVER_URL.
        assert "RELAY_SERVER_URL=wss://relay.existing" in contents

    async def test_backfills_relay_server_url_when_missing(
        self, env_path: Path
    ):
        env_path.write_text(
            "RELAY_AGENT_ID=\nRELAY_AGENT_TOKEN=\nRELAY_SERVER_URL=\n",
            encoding="utf-8",
        )
        claim = PairingClaim(
            agent_id="id1",
            agent_token="tok1",
            relay_url="https://relay.test",
            agent_friendly_name="lab",
            issued_at="2026-05-07T00:00:00+00:00",
        )

        await write_credentials_to_env(env_path, claim)

        contents = env_path.read_text(encoding="utf-8")
        assert "RELAY_SERVER_URL=https://relay.test" in contents

    async def test_writes_to_nonexistent_env_file(self, env_path: Path):
        # No pre-existing file: the writer creates one carrying the new
        # credentials. (init-env is a separate prerequisite, but the
        # writer itself must not crash on a missing target.)
        claim = PairingClaim(
            agent_id="i", agent_token="t",
            relay_url="https://relay.test",
            agent_friendly_name="x",
            issued_at="2026-05-07T00:00:00+00:00",
        )

        await write_credentials_to_env(env_path, claim)

        text = env_path.read_text(encoding="utf-8")
        assert "RELAY_AGENT_ID=i" in text
        assert "RELAY_AGENT_TOKEN=t" in text
