"""Tests for the agent half of the reversed (device-code) pairing flow.

Two units: the ``pending_device_code.json`` persistence slot in
``mobaide.agent.pairing_qr`` (deliberately a different file from the QR
flow's, so a running pairing monitor cannot clobber a ``pair <code>``
invocation), and ``pairing_monitor.redeem_device_code``, which is the
reversed-direction twin of ``initiate_pairing``.
"""
from __future__ import annotations

import io
import json
import time
import urllib.error
from dataclasses import dataclass, replace
from pathlib import Path
from unittest.mock import patch

import pytest

from mobaide.agent.pairing_monitor import (
    DeviceCodeRejected,
    PairingMonitorError,
    redeem_device_code,
)
from mobaide.agent.pairing_qr import (
    PendingPairing,
    clear_pending_device_code,
    device_code_fingerprint,
    encode_qr_payload,
    load_pending_device_code,
    load_pending_pairing,
    new_redeemer_id,
    pending_device_code_path,
    pending_pairing_path,
    save_pending_device_code,
    save_pending_pairing,
)

# The relay validates ``redeemer_id`` with min_length=8; an id shorter than
# that would 422 before the code was ever looked up.
_SERVER_REDEEMER_ID_MIN_LENGTH = 8

# Fingerprint of the code these lifecycle tests redeem with; a resume is
# keyed on this, so a case that checks a *different* code must derive its
# own fingerprint rather than reuse it.
_CODE_FP = device_code_fingerprint("WDJB-MJHT")


@dataclass
class _StubLayout:
    """Minimal stand-in for ``mobaide.agent.updater.InstallLayout``."""

    prefix: Path

    @property
    def trust_public_key(self) -> Path:
        return self.prefix / "trust" / "central_server.pub"


@pytest.fixture
def layout(tmp_path) -> _StubLayout:
    return _StubLayout(prefix=tmp_path)


def _pending(nonce: str = "nonce-abc", *, exp_offset: int = 10_000) -> PendingPairing:
    exp = int(time.time()) + exp_offset
    return PendingPairing(
        nonce=nonce,
        expires_at="2099-01-01T00:00:00+00:00",
        trust_key_fp="ff" * 32,
        ws_url=f"wss://relay.test/ws/pairing/{nonce}",
        relay_url="https://relay.test",
        qr_payload=encode_qr_payload("https://relay.test", nonce, "ff" * 32, exp),
    )


class TestNewRedeemerId:
    def test_satisfies_the_relay_s_minimum_length(self):
        assert len(new_redeemer_id()) >= _SERVER_REDEEMER_ID_MIN_LENGTH

    def test_is_not_reused_between_attempts(self):
        # Two hosts sharing an id would each be answered with the other's
        # replay instead of being told the code was taken.
        assert len({new_redeemer_id() for _ in range(50)}) == 50


class TestPendingDeviceCodeLifecycle:
    def test_save_then_load_round_trips(self, layout):
        pending = _pending()

        save_pending_device_code(layout, "redeemer-1", _CODE_FP, pending)

        assert load_pending_device_code(layout, _CODE_FP) == ("redeemer-1", pending)

    def test_the_pre_redeem_write_keeps_the_id_without_a_nonce(self, layout):
        # Written before the request goes out, so a retry re-presents the
        # same identity instead of looking like a second machine racing.
        save_pending_device_code(layout, "redeemer-1", _CODE_FP, None)

        assert load_pending_device_code(layout, _CODE_FP) == ("redeemer-1", None)

    def test_it_does_not_share_a_file_with_the_qr_flow(self, layout):
        # The agent service runs its own QR pairing cycle on a fresh
        # install; a shared file would have `pair <code>` overwrite the
        # nonce that cycle is waiting on.
        qr_pending = _pending("qr-nonce")
        device_pending = _pending("device-nonce")

        save_pending_pairing(layout, qr_pending)
        save_pending_device_code(layout, "redeemer-1", _CODE_FP, device_pending)

        assert pending_device_code_path(layout) != pending_pairing_path(layout)
        assert load_pending_pairing(layout) == qr_pending
        assert load_pending_device_code(layout, _CODE_FP) == (
            "redeemer-1",
            device_pending,
        )

    def test_an_expired_nonce_still_yields_the_redeemer_id(self, layout):
        # The id is still worth resending — the relay replays the same
        # redemption — but the nonce is no longer worth dialling.
        save_pending_device_code(
            layout, "redeemer-1", _CODE_FP, _pending(exp_offset=-10)
        )

        assert load_pending_device_code(layout, _CODE_FP) == ("redeemer-1", None)

    def test_a_malformed_payload_yields_the_redeemer_id(self, layout):
        save_pending_device_code(
            layout,
            "redeemer-1",
            _CODE_FP,
            replace(_pending(), qr_payload="MOBA0|garbage"),
        )

        assert load_pending_device_code(layout, _CODE_FP) == ("redeemer-1", None)

    def test_load_returns_none_when_absent(self, layout):
        assert load_pending_device_code(layout, _CODE_FP) is None

    def test_load_returns_none_on_corrupt_json(self, layout):
        target = pending_device_code_path(layout)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("not-json{{{", encoding="utf-8")

        assert load_pending_device_code(layout, _CODE_FP) is None

    def test_load_returns_none_without_a_redeemer_id(self, layout):
        target = pending_device_code_path(layout)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"pending": None}), encoding="utf-8")

        assert load_pending_device_code(layout, _CODE_FP) is None

    def test_a_different_code_fp_does_not_resume(self, layout):
        # A Ctrl-C'd `pair CODE-A` followed by `pair CODE-B` must not
        # silently wait out CODE-A's nonce under CODE-B's redemption.
        pending = _pending()
        save_pending_device_code(layout, "redeemer-1", _CODE_FP, pending)

        other_fp = device_code_fingerprint("QRST-VWXZ")
        assert load_pending_device_code(layout, other_fp) is None
        # The original code's own record is untouched and still resumable.
        assert load_pending_device_code(layout, _CODE_FP) == ("redeemer-1", pending)

    def test_clear_is_idempotent(self, layout):
        clear_pending_device_code(layout)
        save_pending_device_code(layout, "redeemer-1", _CODE_FP, _pending())
        clear_pending_device_code(layout)
        clear_pending_device_code(layout)

        assert not pending_device_code_path(layout).exists()


def _redeem_response(**overrides) -> dict:
    base = {
        "nonce": "n1",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "trust_key_fp": "ff" * 32,
        "relay_url": "https://relay.test",
        "account_hint": "p***l@g***.com",
        "is_repair": False,
        "replayed": False,
    }
    base.update(overrides)
    return base


class _FakeResp:
    def __init__(self, status: int, body: dict):
        self.status = status
        self._body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _patch_urlopen(resp):
    return patch("mobaide._http.urlopen", return_value=resp)


def _http_error(code: int, body: str):
    return urllib.error.HTTPError(
        "https://relay.test/api/pairing/device-code/redeem",
        code,
        "err",
        {},
        io.BytesIO(body.encode("utf-8")),
    )


class TestRedeemDeviceCode:
    def test_happy_path_derives_the_ws_url_locally(self):
        with _patch_urlopen(_FakeResp(200, _redeem_response())):
            pending, hint, is_repair = redeem_device_code(
                "wss://relay.test",
                "ff" * 32,
                code="WDJB-MJHT",
                redeemer_id="redeemer-1",
                hostname="alpha",
                os_name="Linux",
            )

        # Same rule as the QR flow: the WS URL comes from RELAY_SERVER_URL
        # + nonce, never from the response body.
        assert pending.ws_url == "wss://relay.test/ws/pairing/n1"
        assert pending.nonce == "n1"
        assert pending.relay_url == "https://relay.test"
        assert pending.qr_payload.startswith("MOBA1|")
        assert hint == "p***l@g***.com"
        assert is_repair is False

    def test_a_fingerprint_it_did_not_pin_is_refused(self):
        # Fail-closed: this is the one check standing between the agent
        # and a relay whose signing key it never vouched for.
        with _patch_urlopen(
            _FakeResp(200, _redeem_response(trust_key_fp="ee" * 32))
        ):
            with pytest.raises(PairingMonitorError, match="fingerprint mismatch"):
                redeem_device_code(
                    "wss://relay.test",
                    "ff" * 32,
                    code="WDJB-MJHT",
                    redeemer_id="redeemer-1",
                )

    def test_a_replayed_redeem_reports_no_pending_pairing(self):
        # The relay never stores the raw nonce, so a replay cannot re-issue
        # one; the caller falls back to its own persisted copy.
        with _patch_urlopen(
            _FakeResp(200, _redeem_response(nonce=None, replayed=True))
        ):
            pending, hint, _is_repair = redeem_device_code(
                "wss://relay.test",
                "ff" * 32,
                code="WDJB-MJHT",
                redeemer_id="redeemer-1",
            )

        assert pending is None
        assert hint == "p***l@g***.com"

    def test_a_repair_redeem_is_flagged(self):
        with _patch_urlopen(_FakeResp(200, _redeem_response(is_repair=True))):
            _pending, _hint, is_repair = redeem_device_code(
                "wss://relay.test",
                "ff" * 32,
                code="WDJB-MJHT",
                redeemer_id="redeemer-1",
                current_agent_id="agent-old",
            )

        assert is_repair is True

    def test_a_rejected_code_surfaces_the_relay_s_own_wording(self):
        # Every redeem rejection is operator-facing, so replacing the
        # server's sentence with a status number loses the remedy.
        detail = "That pairing code is not valid. It may have expired."
        with patch(
            "mobaide._http.urlopen",
            side_effect=_http_error(410, json.dumps({"detail": detail})),
        ):
            with pytest.raises(DeviceCodeRejected) as excinfo:
                redeem_device_code(
                    "wss://relay.test",
                    "ff" * 32,
                    code="WDJB-MJHT",
                    redeemer_id="redeemer-1",
                )

        assert str(excinfo.value) == detail
        assert excinfo.value.status == 410

    def test_a_throttled_redeem_names_the_wait(self):
        body = json.dumps(
            {
                "detail": {
                    "error": "device_code_rate_limited",
                    "retry_after_seconds": 42,
                }
            }
        )
        with patch("mobaide._http.urlopen", side_effect=_http_error(429, body)):
            with pytest.raises(DeviceCodeRejected) as excinfo:
                redeem_device_code(
                    "wss://relay.test",
                    "ff" * 32,
                    code="WDJB-MJHT",
                    redeemer_id="redeemer-1",
                )

        assert "42s" in str(excinfo.value)
        assert excinfo.value.status == 429

    def test_a_non_json_error_body_falls_back_to_the_status(self):
        # A proxy's HTML error page must not crash the CLI on the way to
        # reporting the failure.
        with patch(
            "mobaide._http.urlopen",
            side_effect=_http_error(502, "<html>bad gateway</html>"),
        ):
            with pytest.raises(DeviceCodeRejected, match="HTTP 502"):
                redeem_device_code(
                    "wss://relay.test",
                    "ff" * 32,
                    code="WDJB-MJHT",
                    redeemer_id="redeemer-1",
                )
