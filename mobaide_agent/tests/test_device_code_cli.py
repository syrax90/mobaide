"""Tests for the ``mobaide-agent pair <code>`` / ``show-trust-fp`` verbs.

The redeem-then-wait happy path is integration-shaped and covered at the
unit level in ``test_device_code_pairing.py``; what matters here is the
refusal ladder, because every one of its rungs guards something: the
already-paired check stops a code from moving a working host, the
non-interactive check runs *before* the redeem so a run that could never
show the prompt cannot spend the code, and the prompt itself is the
reversed flow's primary safeguard rather than a courtesy.
"""
from __future__ import annotations

import argparse
import base64
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mobaide.__main__ import (
    _confirm_device_pairing,
    _run_pair,
    _run_show_trust_fp,
)
from mobaide.agent._env_writer import env_path_for_prefix
from mobaide.agent.pairing_qr import (
    PendingDeviceCode,
    PendingPairing,
    compute_trust_key_fp,
    device_code_fingerprint,
    encode_qr_payload,
    save_pending_device_code,
)
from mobaide.agent.pairing_ws import PairingWsError
from mobaide.agent.updater import InstallLayout


def _ns(**overrides) -> argparse.Namespace:
    defaults = {
        "code": "WDJB-MJHT",
        "yes": True,
        "force": False,
        "prefix": None,
        "no_restart": True,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _seed_env(prefix: Path, **kv: str) -> Path:
    env_file = env_path_for_prefix(prefix)
    env_file.parent.mkdir(parents=True, exist_ok=True)
    body = "JWT_SECRET_KEY=test-secret\n"
    for key, value in kv.items():
        body += f"{key}={value}\n"
    env_file.write_text(body, encoding="utf-8")
    return env_file


def _seed_trust_key(prefix: Path) -> str:
    pub_b64 = base64.b64encode(
        Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    ).decode()
    trust_dir = prefix / "trust"
    trust_dir.mkdir(parents=True, exist_ok=True)
    (trust_dir / "central_server.pub").write_text(
        pub_b64 + "\n", encoding="utf-8"
    )
    return pub_b64


def _seed_pending_device_code(
    prefix: Path, *, code: str = "WDJB-MJHT", confirmed: bool
) -> None:
    """Persist a redeemed-but-not-yet-claimed record ``_run_pair`` can resume.

    Mirrors the shape ``_run_pair`` itself writes after a successful redeem:
    a nonce on disk with ``confirmed`` set independently, so a test can drive
    the resume branch without going through a real (mocked) redeem call.
    """
    nonce = "nonce-abc"
    exp = int(time.time()) + 10_000
    pending = PendingPairing(
        nonce=nonce,
        expires_at="2099-01-01T00:00:00+00:00",
        trust_key_fp="ff" * 32,
        ws_url=f"wss://relay.test/ws/pairing/{nonce}",
        relay_url="https://relay.test",
        qr_payload=encode_qr_payload("https://relay.test", nonce, "ff" * 32, exp),
    )
    save_pending_device_code(
        InstallLayout(prefix=prefix),
        PendingDeviceCode(
            redeemer_id="redeemer-1",
            code_fp=device_code_fingerprint(code),
            pending=pending,
            account_hint="p***l@g***.com",
            confirmed=confirmed,
        ),
    )


class TestRunPairRefusals:
    def test_missing_env_returns_2(self, tmp_path, capsys):
        rc = _run_pair(_ns(prefix=str(tmp_path)))

        assert rc == 2
        assert "init-env" in capsys.readouterr().err

    def test_blank_relay_server_url_returns_2(self, tmp_path, capsys):
        _seed_env(tmp_path, RELAY_SERVER_URL="")

        rc = _run_pair(_ns(prefix=str(tmp_path)))

        assert rc == 2
        assert "RELAY_SERVER_URL" in capsys.readouterr().err

    def test_an_already_paired_host_refuses_without_force(self, tmp_path, capsys):
        _seed_env(
            tmp_path,
            RELAY_SERVER_URL="wss://relay.test",
            RELAY_AGENT_ID="agent-1",
            RELAY_AGENT_TOKEN="tok",
        )
        _seed_trust_key(tmp_path)

        rc = _run_pair(_ns(prefix=str(tmp_path)))

        # Without this, a code someone else minted could silently move a
        # working host to their account.
        assert rc == 9
        assert "--force" in capsys.readouterr().err

    def test_force_gets_past_the_already_paired_guard(self, tmp_path):
        _seed_env(
            tmp_path,
            RELAY_SERVER_URL="wss://relay.test",
            RELAY_AGENT_ID="agent-1",
            RELAY_AGENT_TOKEN="tok",
        )

        rc = _run_pair(_ns(prefix=str(tmp_path), force=True))

        # No trust key seeded, so reaching the unpinned refusal (4) rather
        # than the already-paired one (9) is what proves the guard yielded.
        assert rc == 4

    def test_a_half_written_credential_pair_is_not_treated_as_paired(
        self, tmp_path
    ):
        # An id with no token is an interrupted install, not a paired host;
        # refusing it would leave the operator with no way forward.
        _seed_env(
            tmp_path,
            RELAY_SERVER_URL="wss://relay.test",
            RELAY_AGENT_ID="agent-1",
        )

        rc = _run_pair(_ns(prefix=str(tmp_path)))

        assert rc == 4

    def test_a_non_interactive_run_refuses_before_spending_the_code(
        self, tmp_path, capsys
    ):
        _seed_env(tmp_path, RELAY_SERVER_URL="wss://relay.test")

        with patch("sys.stdin.isatty", return_value=False):
            rc = _run_pair(_ns(prefix=str(tmp_path), yes=False))

        # No trust key is pinned either, so returning 9 rather than 4 pins
        # the ordering: the check that the prompt is showable happens
        # before anything that could burn the code.
        assert rc == 9
        assert "--yes" in capsys.readouterr().err

    def test_an_unpinned_trust_key_returns_4(self, tmp_path, capsys):
        _seed_env(tmp_path, RELAY_SERVER_URL="wss://relay.test")

        rc = _run_pair(_ns(prefix=str(tmp_path)))

        assert rc == 4
        assert "fetch-trust-key" in capsys.readouterr().err


class TestRunPairResume:
    """The nonce is now persisted before the confirm prompt, not after — so
    these pin the guarantee that makes that safe: a resume only ever skips
    the redeem call, never the approval gate.
    """

    def test_an_unconfirmed_resume_is_prompted_again(self, tmp_path, capsys):
        # Persisting early must not let an interrupted run come back with a
        # nonce on disk and slip past the agent-side approval — the
        # reversed flow's primary security control.
        _seed_env(tmp_path, RELAY_SERVER_URL="wss://relay.test")
        _seed_trust_key(tmp_path)
        _seed_pending_device_code(tmp_path, confirmed=False)

        with patch("sys.stdin.isatty", return_value=True), patch(
            "builtins.input", return_value="n"
        ) as mock_input:
            rc = _run_pair(_ns(prefix=str(tmp_path), yes=False))

        mock_input.assert_called_once()
        out = capsys.readouterr().out
        assert "Resuming the pairing request" in out
        assert "p***l@g***.com" in out
        # Declined: the code is spent and the record must not linger.
        assert rc == 9
        assert not (tmp_path / "run" / "pending_device_code.json").exists()

    def test_a_confirmed_resume_is_not_re_prompted(self, tmp_path, capsys):
        _seed_env(tmp_path, RELAY_SERVER_URL="wss://relay.test")
        _seed_trust_key(tmp_path)
        _seed_pending_device_code(tmp_path, confirmed=True)

        with patch("sys.stdin.isatty", return_value=True), patch(
            "mobaide.agent.pairing_ws.PairingWsClient"
        ) as mock_ws_cls, patch("builtins.input") as mock_input:
            mock_ws_cls.return_value.wait_for_claim = AsyncMock(
                side_effect=PairingWsError("boom")
            )
            rc = _run_pair(_ns(prefix=str(tmp_path), yes=False))

        # Never asked — the whole point of ``confirmed`` is that a second
        # approval is not required to continue waiting on the same request.
        mock_input.assert_not_called()
        out = capsys.readouterr().out
        assert "Resuming the pairing request" in out
        # Reaching (and failing) the WS wait proves the code got past the
        # confirm gate without a prompt, not just that nothing crashed.
        assert rc == 6

    def test_a_dropped_websocket_keeps_the_record_for_a_reconnect(
        self, tmp_path, capsys
    ):
        # The relay parks the claimed credentials in ``claimed_token_raw``
        # for a reconnect on the same nonce, and that nonce exists nowhere
        # but this file — clearing it on a transport failure would turn a
        # dropped connection into a code that can never be completed.
        _seed_env(tmp_path, RELAY_SERVER_URL="wss://relay.test")
        _seed_trust_key(tmp_path)
        _seed_pending_device_code(tmp_path, confirmed=True)

        with patch("sys.stdin.isatty", return_value=True), patch(
            "mobaide.agent.pairing_ws.PairingWsClient"
        ) as mock_ws_cls:
            mock_ws_cls.return_value.wait_for_claim = AsyncMock(
                side_effect=PairingWsError("connection reset")
            )
            rc = _run_pair(_ns(prefix=str(tmp_path), yes=False))

        assert rc == 6
        assert (tmp_path / "run" / "pending_device_code.json").exists()
        # And the operator is told how to use it.
        assert "still on file" in capsys.readouterr().err

    def test_a_code_typed_with_a_discarded_character_resumes_the_record(
        self, tmp_path, capsys
    ):
        # "WDJB0MJHT" and "WDJB-MJHT" fingerprint identically (0 is outside
        # the relay's alphabet, same as the hyphen) — retyping the code
        # slightly differently on a resume must not make the CLI treat it as
        # a fresh, unrelated request.
        _seed_env(tmp_path, RELAY_SERVER_URL="wss://relay.test")
        _seed_trust_key(tmp_path)
        _seed_pending_device_code(tmp_path, code="WDJB-MJHT", confirmed=True)

        with patch("sys.stdin.isatty", return_value=True), patch(
            "mobaide.agent.pairing_ws.PairingWsClient"
        ) as mock_ws_cls, patch("builtins.input") as mock_input:
            mock_ws_cls.return_value.wait_for_claim = AsyncMock(
                side_effect=PairingWsError("boom")
            )
            rc = _run_pair(_ns(prefix=str(tmp_path), code="WDJB0MJHT", yes=False))

        mock_input.assert_not_called()
        assert "Resuming the pairing request" in capsys.readouterr().out
        assert rc == 6


class TestConfirmDevicePairing:
    @pytest.mark.parametrize("answer", ["y", "Y", "yes", " yes "])
    def test_accepts_an_affirmative_answer(self, answer):
        with patch("builtins.input", return_value=answer):
            assert _confirm_device_pairing("p***l@g***.com", "alpha") is True

    @pytest.mark.parametrize("answer", ["", "n", "no", "sure", "yeah"])
    def test_anything_else_declines(self, answer):
        # y/N, so silence and ambiguity both mean no — the cost of a false
        # accept is handing an attacker shell sessions on this machine.
        with patch("builtins.input", return_value=answer):
            assert _confirm_device_pairing("p***l@g***.com", "alpha") is False

    def test_a_closed_stdin_declines(self):
        with patch("builtins.input", side_effect=EOFError):
            assert _confirm_device_pairing("p***l@g***.com", "alpha") is False

    def test_the_prompt_names_the_account_and_the_machine(self, capsys):
        # This text is the reversed flow's primary safeguard: the browser
        # confirm is answered by whoever minted the code, so the account
        # has to be readable on the terminal of the machine being handed
        # over.
        with patch("builtins.input", return_value="n"):
            _confirm_device_pairing("p***l@g***.com", "alpha")

        out = capsys.readouterr().out
        assert "p***l@g***.com" in out
        assert "alpha" in out
        assert "terminal" in out


class TestRunShowTrustFp:
    def test_prints_the_pinned_fingerprint(self, tmp_path, capsys):
        pub_b64 = _seed_trust_key(tmp_path)

        rc = _run_show_trust_fp(argparse.Namespace(prefix=str(tmp_path)))

        assert rc == 0
        out = capsys.readouterr().out
        # The whole point of the verb is comparing this against the app,
        # so the digest has to be on its own first line.
        assert out.splitlines()[0] == compute_trust_key_fp(pub_b64)

    def test_an_unpinned_host_returns_4(self, tmp_path, capsys):
        rc = _run_show_trust_fp(argparse.Namespace(prefix=str(tmp_path)))

        assert rc == 4
        assert "fetch-trust-key" in capsys.readouterr().err
