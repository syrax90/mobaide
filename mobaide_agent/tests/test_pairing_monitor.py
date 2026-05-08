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
    _pairing_ws_url,
    initiate_pairing,
    write_credentials_to_env,
)
from mobaide.agent.pairing_ws import PairingClaim


def _initiate_response(**overrides) -> dict:
    # Note: no ``ws_url`` field. The agent derives the pairing WS URL
    # locally from RELAY_SERVER_URL + nonce; the server does not return
    # it. See ``_pairing_ws_url`` in ``mobaide.agent.pairing_monitor``.
    base = {
        "nonce": "n1",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "trust_key_fp": "ff" * 32,
        "relay_url": "https://relay.test",
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
        # WS URL is derived from RELAY_SERVER_URL (the wss:// arg above) +
        # the response nonce — never read from the server response. Pin
        # the exact value so a regression that re-introduces a
        # server-supplied ws_url field gets caught here.
        assert pending.ws_url == "wss://relay.test/ws/pairing/n1"

    def test_ws_url_ignores_buggy_server_supplied_https(self):
        # Regression guard for the offline-after-pairing bug: a deployed
        # central_server that erroneously hands back ``https://`` as
        # ``ws_url`` would break the websockets client. The agent now
        # derives the URL locally from RELAY_SERVER_URL + nonce, so a
        # buggy server-side field is simply ignored.
        buggy = _initiate_response(
            ws_url="https://relay.test/ws/pairing/n1",  # Pretend stale server.
        )
        with _patch_urlopen(_FakeResp(200, buggy)):
            pending = initiate_pairing(
                "wss://relay.test",
                pinned_trust_key_fp="ff" * 32,
            )

        assert pending.ws_url == "wss://relay.test/ws/pairing/n1"
        assert pending.ws_url.startswith("wss://")

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


class TestPairingWsUrlHelper:
    @pytest.mark.parametrize(
        "relay_url,expected",
        [
            ("wss://relay.example.com",
             "wss://relay.example.com/ws/pairing/N1"),
            ("wss://relay.example.com:9000",
             "wss://relay.example.com:9000/ws/pairing/N1"),
            ("https://relay.example.com",
             "wss://relay.example.com/ws/pairing/N1"),
            ("https://relay.example.com:9000",
             "wss://relay.example.com:9000/ws/pairing/N1"),
        ],
    )
    def test_secure_scheme_translation(self, relay_url, expected):
        assert _pairing_ws_url(relay_url, "N1") == expected

    def test_path_query_fragment_dropped(self):
        # WS endpoint is pinned at /ws/pairing/{nonce}; any path on the
        # input relay URL is intentionally discarded — the relay tunnel
        # path and the WS endpoint live at the same origin root.
        out = _pairing_ws_url(
            "https://relay.example.com/some/path?q=1#frag", "abc",
        )

        assert out == "wss://relay.example.com/ws/pairing/abc"

    def test_unsupported_scheme_raises(self):
        with pytest.raises(ValueError, match="unsupported scheme"):
            _pairing_ws_url("ftp://relay.example.com", "n")

    def test_missing_host_raises(self):
        with pytest.raises(ValueError, match="not a valid URL"):
            _pairing_ws_url("https://", "n")

    def test_user_info_rejected(self):
        # urlparse('wss://api@evil.example') resolves to evil.example;
        # refuse upfront to mirror _trust_key_url_from_relay_server_url.
        with pytest.raises(ValueError, match="user-info"):
            _pairing_ws_url(
                "wss://attacker@relay.example.com", "n",
            )

    def test_cleartext_refused_by_default(self, monkeypatch):
        monkeypatch.delenv(
            "MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH",
            raising=False,
        )
        with pytest.raises(ValueError, match="cleartext"):
            _pairing_ws_url("ws://relay.example.com", "n")
        with pytest.raises(ValueError, match="cleartext"):
            _pairing_ws_url("http://localhost:8080", "n")

    def test_cleartext_allowed_with_insecure_env_var(self, monkeypatch):
        monkeypatch.setenv(
            "MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH", "1",
        )
        assert (
            _pairing_ws_url("ws://localhost", "N1")
            == "ws://localhost/ws/pairing/N1"
        )
        assert (
            _pairing_ws_url("http://localhost:8080", "N1")
            == "ws://localhost:8080/ws/pairing/N1"
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
