"""Tests for the new ``mobaide-agent`` QR-pairing CLI verbs.

The full ``--wait`` happy path (HTTP initiate + WebSocket claim
delivery) is integration-shaped and out of scope here; these tests
focus on the argv-parsing dispatch, the early-exit error paths the
operator most commonly hits (no .env, blank RELAY_SERVER_URL, no
trust key), and the env-rewrite behaviour of ``re-pair``.
"""
from __future__ import annotations

import argparse
import base64
import io
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mobaide.__main__ import _run_pair_qr, _run_re_pair, _run_show_qr
from mobaide.agent._env_writer import env_path_for_prefix


def _ns(**overrides) -> argparse.Namespace:
    defaults = {
        "prefix": None,
        "no_restart": True,
        "print": False,
        "wait": False,
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
    """Pin a valid Ed25519 public key under ``<prefix>/trust/``."""
    pub_b64 = base64.b64encode(
        Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    ).decode()
    trust_dir = prefix / "trust"
    trust_dir.mkdir(parents=True, exist_ok=True)
    (trust_dir / "central_server.pub").write_text(
        pub_b64 + "\n", encoding="utf-8",
    )
    return pub_b64


class TestRunPairQrEarlyExits:
    def test_missing_env_returns_2(self, tmp_path, capsys):
        # No .env file at all → exit 2 with an init-env hint.
        rc = _run_pair_qr(_ns(prefix=str(tmp_path), print=True))

        assert rc == 2
        err = capsys.readouterr().err
        assert "init-env" in err

    def test_blank_relay_server_url_returns_2(self, tmp_path, capsys):
        _seed_env(tmp_path, RELAY_SERVER_URL="")

        rc = _run_pair_qr(_ns(prefix=str(tmp_path), print=True))

        assert rc == 2
        assert "RELAY_SERVER_URL" in capsys.readouterr().err

    def test_no_trust_key_returns_4(self, tmp_path, capsys, monkeypatch):
        # .env is fine but the trust key has not been pinned.
        _seed_env(tmp_path, RELAY_SERVER_URL="wss://relay.test")
        # Defensive: kill any host env override for the trust key so the
        # test relies on the file-based check (which is what the agent
        # sees in production).
        monkeypatch.delenv("MOBAIDE_AGENT_TRUST_KEY", raising=False)

        rc = _run_pair_qr(_ns(prefix=str(tmp_path), print=True))

        assert rc == 4
        assert "fetch-trust-key" in capsys.readouterr().err

    def test_initiate_network_failure_returns_3(
        self, tmp_path, capsys, monkeypatch
    ):
        import urllib.error

        _seed_env(tmp_path, RELAY_SERVER_URL="wss://relay.test")
        _seed_trust_key(tmp_path)
        monkeypatch.delenv("MOBAIDE_AGENT_TRUST_KEY", raising=False)

        # initiate_pairing's underlying urlopen raises URLError → exit 3.
        with patch(
            "mobaide.agent.pairing_monitor.urllib.request.urlopen",
            side_effect=urllib.error.URLError("DNS down"),
        ):
            rc = _run_pair_qr(_ns(prefix=str(tmp_path), print=True))

        assert rc == 3
        assert "central server" in capsys.readouterr().err.lower()


class TestRunShowQr:
    def test_already_paired_returns_0_without_render(
        self, tmp_path, capsys
    ):
        # Both id and token set → no QR rendered, hint to run re-pair.
        _seed_env(
            tmp_path,
            RELAY_SERVER_URL="wss://relay.test",
            RELAY_AGENT_ID="already-paired-id",
            RELAY_AGENT_TOKEN="already-paired-token",
        )

        rc = _run_show_qr(_ns(prefix=str(tmp_path)))

        assert rc == 0
        out = capsys.readouterr().out
        assert "Already paired" in out
        # No QR header emitted.
        assert "Pairing QR" not in out

    def test_no_pending_falls_through_to_pair_qr_print(
        self, tmp_path, capsys, monkeypatch
    ):
        # No env at all → falls through to _run_pair_qr(--print), which
        # immediately exits 2 (init-env not run).
        rc = _run_show_qr(_ns(prefix=str(tmp_path)))

        assert rc == 2


class TestRunRePair:
    def test_missing_env_returns_2(self, tmp_path, capsys):
        rc = _run_re_pair(_ns(prefix=str(tmp_path)))

        assert rc == 2
        assert "init-env" in capsys.readouterr().err

    def test_blanks_only_token_preserves_agent_id(self, tmp_path):
        # Re-pair preserves RELAY_AGENT_ID and blanks RELAY_AGENT_TOKEN
        # only. The boot autorun then carries the prior agent_id into the
        # next initiate request via ``current_agent_id`` so the server
        # takes the re-pair branch (transfer + cascade) instead of
        # registering a fresh agent.
        _seed_env(
            tmp_path,
            RELAY_SERVER_URL="wss://relay.test",
            RELAY_AGENT_ID="prior-agent",
            RELAY_AGENT_TOKEN="prior-token",
        )

        rc = _run_re_pair(_ns(prefix=str(tmp_path), no_restart=True))

        assert rc == 0
        env_text = env_path_for_prefix(tmp_path).read_text(encoding="utf-8")
        for line in env_text.splitlines():
            if line.startswith("RELAY_AGENT_ID="):
                # The agent_id is preserved so the boot autorun can drive
                # a re-pair claim against the existing agent row.
                assert line == "RELAY_AGENT_ID=prior-agent"
            if line.startswith("RELAY_AGENT_TOKEN="):
                assert line == "RELAY_AGENT_TOKEN="
        # Relay URL is preserved.
        assert "RELAY_SERVER_URL=wss://relay.test" in env_text

    def test_clears_pending_pairing_file(self, tmp_path):
        # Any cached pending nonce is dropped — the next show-qr must
        # initiate a fresh one.
        _seed_env(
            tmp_path,
            RELAY_SERVER_URL="wss://relay.test",
            RELAY_AGENT_ID="x",
            RELAY_AGENT_TOKEN="y",
        )
        # Pre-create a pending file.
        from mobaide.agent.pairing_qr import (
            PendingPairing,
            pending_pairing_path,
            save_pending_pairing,
        )
        from mobaide.agent.updater import InstallLayout

        layout = InstallLayout(prefix=tmp_path)
        save_pending_pairing(
            layout,
            PendingPairing(
                nonce="n1",
                expires_at="2099-01-01T00:00:00+00:00",
                trust_key_fp="ff" * 32,
                ws_url="wss://relay.test/ws/pairing/n1",
                relay_url="https://relay.test",
                qr_payload=f"MOBA1|https://relay.test|n1|{'ff' * 32}|2999999999",
            ),
        )
        assert pending_pairing_path(layout).exists()

        _run_re_pair(_ns(prefix=str(tmp_path), no_restart=True))

        assert not pending_pairing_path(layout).exists()
