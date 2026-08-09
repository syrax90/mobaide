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
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mobaide.__main__ import (
    _confirm_device_pairing,
    _run_pair,
    _run_show_trust_fp,
)
from mobaide.agent._env_writer import env_path_for_prefix
from mobaide.agent.pairing_qr import compute_trust_key_fp


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
