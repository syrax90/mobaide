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
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import mobaide.__main__ as _main
from mobaide.__main__ import (
    _report_permission_denied,
    _run_pair_qr,
    _run_re_pair,
    _run_show_qr,
    main,
)
from mobaide.agent._env_writer import env_path_for_prefix
from mobaide.agent.pairing_qr import (
    PAIRING_WEB_ORIGIN_ENV,
    PendingPairing,
    compute_trust_key_fp,
    format_pairing_link,
    save_pending_pairing,
)
from mobaide.agent.updater import InstallLayout

_REAL_LOCAL_AGENT_HEALTH = _main._local_agent_health


@pytest.fixture(autouse=True)
def _no_local_agent(monkeypatch):
    """No test may reach whatever agent really listens on this host's PORT."""
    monkeypatch.setattr(_main, "_local_agent_health", lambda _env_kv: None)


def _ns(**overrides) -> argparse.Namespace:
    defaults = {
        "prefix": None,
        "no_restart": True,
        "print": False,
        "wait": False,
        "require_pending": False,
        "fresh": False,
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


def _seed_pending(prefix: Path, trust_key_fp: str = "ee" * 32) -> str:
    """Write a pending-pairing file whose nonce is still far from expiry.

    ``load_pending_pairing`` drops an expired payload, so the exp has to sit
    well past any plausible test-run clock.
    """
    payload = f"MOBA1|https://relay.test|show-qr-nonce|{trust_key_fp}|2999999999"
    save_pending_pairing(
        InstallLayout(prefix=prefix),
        PendingPairing(
            nonce="show-qr-nonce",
            expires_at="2999-01-01T00:00:00+00:00",
            trust_key_fp=trust_key_fp,
            ws_url="wss://relay.test/ws/pairing/show-qr-nonce",
            relay_url="https://relay.test",
            qr_payload=payload,
        ),
    )
    return payload


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
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("DNS down"),
        ):
            rc = _run_pair_qr(_ns(prefix=str(tmp_path), print=True))

        assert rc == 3
        assert "central server" in capsys.readouterr().err.lower()

    def test_render_prints_payload_line_and_browser_link(
        self, tmp_path, capsys, monkeypatch
    ):
        # A pending file whose fingerprint already matches the pinned trust
        # key short-circuits ``initiate_pairing``, so the render path runs
        # with no network at all.
        _seed_env(tmp_path, RELAY_SERVER_URL="wss://relay.test")
        pub_b64 = _seed_trust_key(tmp_path)
        monkeypatch.delenv("MOBAIDE_AGENT_TRUST_KEY", raising=False)
        monkeypatch.delenv(PAIRING_WEB_ORIGIN_ENV, raising=False)
        payload = _seed_pending(
            tmp_path, trust_key_fp=compute_trust_key_fp(pub_b64)
        )

        with patch(
            "urllib.request.urlopen",
            side_effect=AssertionError(
                "a matching pending file must not trigger a new initiate"
            ),
        ):
            rc = _run_pair_qr(_ns(prefix=str(tmp_path), print=True))

        assert rc == 0
        lines = capsys.readouterr().out.split("\n")
        assert payload in lines
        assert format_pairing_link(payload) in lines


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

    def test_pending_render_prints_payload_line_and_browser_link(
        self, tmp_path, capsys, monkeypatch
    ):
        monkeypatch.delenv(PAIRING_WEB_ORIGIN_ENV, raising=False)
        payload = _seed_pending(tmp_path)

        rc = _run_show_qr(_ns(prefix=str(tmp_path)))

        assert rc == 0
        lines = capsys.readouterr().out.split("\n")
        # The whole point of the line: it is copied as-is, so it carries no
        # prefix and shares its line with nothing.
        assert payload in lines
        assert format_pairing_link(payload) in lines

    def test_already_paired_prints_no_link(self, tmp_path, capsys):
        # The early return is the one render site that must stay silent: an
        # agent that is already paired has no live nonce, so a printed link
        # would send the operator to a /pair screen that can only 410.
        _seed_env(
            tmp_path,
            RELAY_SERVER_URL="wss://relay.test",
            RELAY_AGENT_ID="already-paired-id",
            RELAY_AGENT_TOKEN="already-paired-token",
        )
        _seed_pending(tmp_path)

        rc = _run_show_qr(_ns(prefix=str(tmp_path)))

        assert rc == 0
        out = capsys.readouterr().out
        assert "Already paired" in out
        assert "/pair#" not in out
        assert "MOBA1|" not in out


class TestRunShowQrRequirePending:
    """The ``--require-pending`` flag (added for the install-time flow).

    The installer now starts the agent service BEFORE rendering the
    QR, and lets the running service's pairing-monitor be the single
    initiator of the pairing nonce. The installer then calls
    ``mobaide-agent show-qr --require-pending`` to render the existing
    pending file — never initiate a new one. Two concurrent
    /ws/pairing/{nonce} dials for the same nonce would race; the
    central server rejects the loser with WS close 4001.
    """

    def test_no_pending_with_flag_returns_7_and_does_not_initiate(
        self, tmp_path, capsys
    ):
        # No env, no pending file → exit 7. CRITICAL: must NOT call
        # initiate_pairing or open any network connection. Stub urlopen
        # to assert it's never reached.
        with patch(
            "urllib.request.urlopen",
            side_effect=AssertionError(
                "show-qr --require-pending must not initiate a nonce"
            ),
        ):
            rc = _run_show_qr(
                _ns(prefix=str(tmp_path), require_pending=True)
            )

        assert rc == 7
        err = capsys.readouterr().err
        assert "--require-pending" in err
        # Exit code is dedicated (7) so the installer can distinguish
        # this precondition failure from other show-qr exit codes.

    def test_pending_file_with_flag_renders_existing_payload(
        self, tmp_path, capsys
    ):
        # A pending file written by the service's pairing-monitor →
        # show-qr --require-pending renders it and exits 0 without
        # initiating anything.
        from mobaide.agent.pairing_qr import (
            PendingPairing,
            save_pending_pairing,
        )
        from mobaide.agent.updater import InstallLayout

        layout = InstallLayout(prefix=tmp_path)
        save_pending_pairing(
            layout,
            PendingPairing(
                nonce="installer-nonce",
                expires_at="2099-01-01T00:00:00+00:00",
                trust_key_fp="ee" * 32,
                ws_url="wss://relay.test/ws/pairing/installer-nonce",
                relay_url="https://relay.test",
                qr_payload=(
                    f"MOBA1|https://relay.test|installer-nonce|"
                    f"{'ee' * 32}|2999999999"
                ),
            ),
        )

        with patch(
            "urllib.request.urlopen",
            side_effect=AssertionError(
                "show-qr --require-pending must not initiate when "
                "pending file exists"
            ),
        ):
            rc = _run_show_qr(
                _ns(prefix=str(tmp_path), require_pending=True)
            )

        assert rc == 0
        out = capsys.readouterr().out
        assert "Pending pairing QR" in out

    def test_already_paired_with_flag_returns_0_without_render(
        self, tmp_path, capsys
    ):
        # Re-install on an already-paired agent: env has both id and
        # token. show-qr --require-pending must exit 0 without
        # rendering (same as the no-flag form).
        _seed_env(
            tmp_path,
            RELAY_SERVER_URL="wss://relay.test",
            RELAY_AGENT_ID="x",
            RELAY_AGENT_TOKEN="y",
        )

        rc = _run_show_qr(
            _ns(prefix=str(tmp_path), require_pending=True)
        )

        assert rc == 0
        out = capsys.readouterr().out
        assert "Already paired" in out
        assert "Pairing QR" not in out


def _agent_reports(monkeypatch, health):
    monkeypatch.setattr(_main, "_local_agent_health", lambda _env_kv: health)


def _forbid_initiate():
    return patch(
        "mobaide.agent.pairing_monitor.initiate_pairing",
        side_effect=AssertionError("show-qr must not initiate its own nonce"),
    )


class TestRunShowQrAsksTheRunningAgent:
    """``show-qr`` checks the agent answering on PORT before rendering.

    After ``re-pair`` the operator may run ``show-qr`` while the service is
    still booting; a nonce initiated here would be replaced by the one the
    service initiates. And an agent that started before ``re-pair`` blanked
    the credentials keeps serving them, so a QR shown then is never answered.
    """

    def test_stale_paired_agent_refuses_to_render(self, tmp_path, capsys, monkeypatch):
        _seed_env(tmp_path, RELAY_SERVER_URL="wss://relay.test")
        _seed_pending(tmp_path)
        _agent_reports(monkeypatch, {"status": "ok", "pairing_pending": False})

        rc = _run_show_qr(_ns(prefix=str(tmp_path)))

        assert rc == 9
        captured = capsys.readouterr()
        assert "MOBA1|" not in captured.out
        assert "still paired" in captured.err
        assert "mobaide-agent re-pair" in captured.err

    def test_waits_for_the_service_to_publish_its_qr(self, tmp_path, capsys, monkeypatch):
        _seed_env(tmp_path, RELAY_SERVER_URL="wss://relay.test")
        _agent_reports(monkeypatch, {"status": "ok", "pairing_pending": True})
        published: list[str] = []
        monkeypatch.setattr(
            _main.time, "sleep", lambda _s: published.append(_seed_pending(tmp_path))
        )

        with _forbid_initiate():
            rc = _run_show_qr(_ns(prefix=str(tmp_path)))

        assert rc == 0
        lines = capsys.readouterr().out.split("\n")
        assert "Waiting for the agent to publish its pairing QR" in lines[0]
        assert published[0] in lines

    def test_service_that_never_publishes_returns_7(self, tmp_path, capsys, monkeypatch):
        _seed_env(tmp_path, RELAY_SERVER_URL="wss://relay.test")
        _agent_reports(monkeypatch, {"status": "ok", "pairing_pending": True})
        monkeypatch.setattr(_main, "_SERVICE_PENDING_TIMEOUT_S", 0.0)

        with _forbid_initiate():
            rc = _run_show_qr(_ns(prefix=str(tmp_path)))

        assert rc == 7
        assert "has not published a pairing QR" in capsys.readouterr().err

    def test_pairing_agent_with_pending_renders_without_waiting(
        self, tmp_path, capsys, monkeypatch
    ):
        _seed_env(tmp_path, RELAY_SERVER_URL="wss://relay.test")
        payload = _seed_pending(tmp_path)
        _agent_reports(monkeypatch, {"status": "ok", "pairing_pending": True})

        rc = _run_show_qr(_ns(prefix=str(tmp_path)))

        assert rc == 0
        out = capsys.readouterr().out
        assert payload in out.split("\n")
        assert "Waiting" not in out

    def test_no_agent_answering_keeps_the_initiate_fall_through(
        self, tmp_path, capsys
    ):
        # Nothing on PORT -> unchanged behaviour: initiate from this process,
        # here stopping at the missing trust key.
        _seed_env(tmp_path, RELAY_SERVER_URL="wss://relay.test")

        rc = _run_show_qr(_ns(prefix=str(tmp_path)))

        assert rc == 4
        assert "trust key not pinned" in capsys.readouterr().err

    def test_require_pending_never_asks_the_agent(self, tmp_path, monkeypatch):
        _seed_env(tmp_path, RELAY_SERVER_URL="wss://relay.test")
        _seed_pending(tmp_path)
        monkeypatch.setattr(
            _main,
            "_local_agent_health",
            lambda _env_kv: pytest.fail("installer path must not probe the agent"),
        )

        assert _run_show_qr(_ns(prefix=str(tmp_path), require_pending=True)) == 0

    def test_relay_mode_off_never_asks_the_agent(self, tmp_path, monkeypatch):
        _seed_env(tmp_path)
        monkeypatch.setattr(
            _main,
            "_local_agent_health",
            lambda _env_kv: pytest.fail("no relay configured, nothing to pair"),
        )

        assert _run_show_qr(_ns(prefix=str(tmp_path))) == 2


class TestLocalAgentHealth:
    @pytest.fixture
    def serve(self):
        import http.server
        import threading

        servers = []

        def start(status: int, body: bytes) -> int:
            class Handler(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(status)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, format, *args):
                    pass

            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            servers.append(server)
            return server.server_address[1]

        yield start
        for server in servers:
            server.shutdown()
            server.server_close()

    def test_returns_the_agent_health_body(self, serve):
        port = serve(200, b'{"status": "ok", "pairing_pending": true}')

        assert _REAL_LOCAL_AGENT_HEALTH({"PORT": str(port)}) == {
            "status": "ok",
            "pairing_pending": True,
        }

    @pytest.mark.parametrize(
        "status,body",
        [
            (200, b'{"status": "ok"}'),
            (200, b"<html>not an agent</html>"),
            (503, b'{"pairing_pending": true}'),
        ],
    )
    def test_ignores_answers_that_are_not_an_agent_health_body(
        self, serve, status, body
    ):
        port = serve(status, body)

        assert _REAL_LOCAL_AGENT_HEALTH({"PORT": str(port)}) is None

    def test_nothing_listening_returns_none(self):
        import socket

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        assert _REAL_LOCAL_AGENT_HEALTH({"PORT": str(port)}) is None

    @pytest.mark.parametrize("port", ["not-a-port", "0", "70000"])
    def test_unusable_port_returns_none(self, port):
        assert _REAL_LOCAL_AGENT_HEALTH({"PORT": port}) is None


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

    def test_fresh_blanks_both_id_and_token(self, tmp_path):
        # --fresh forgets RELAY_AGENT_ID too, so the next claim first-pairs
        # as a brand-new agent instead of re-sending an id the relay no
        # longer has — the recovery path for the HTTP 404 "Re-pair target
        # agent does not exist" dead-end (agent removed / relay redeployed).
        _seed_env(
            tmp_path,
            RELAY_SERVER_URL="wss://relay.test",
            RELAY_AGENT_ID="dead-agent",
            RELAY_AGENT_TOKEN="prior-token",
        )

        rc = _run_re_pair(
            _ns(prefix=str(tmp_path), no_restart=True, fresh=True)
        )

        assert rc == 0
        env_text = env_path_for_prefix(tmp_path).read_text(encoding="utf-8")
        for line in env_text.splitlines():
            if line.startswith("RELAY_AGENT_ID="):
                assert line == "RELAY_AGENT_ID="
            if line.startswith("RELAY_AGENT_TOKEN="):
                assert line == "RELAY_AGENT_TOKEN="
        # Relay URL is preserved — only the agent identity is dropped.
        assert "RELAY_SERVER_URL=wss://relay.test" in env_text

    def test_default_message_hints_fresh_recovery(self, tmp_path, capsys):
        # The same-owner default path tells the operator how to recover if
        # the relay 404s because the agent is gone: re-run with --fresh.
        _seed_env(
            tmp_path,
            RELAY_SERVER_URL="wss://relay.test",
            RELAY_AGENT_ID="x",
            RELAY_AGENT_TOKEN="y",
        )

        _run_re_pair(_ns(prefix=str(tmp_path), no_restart=True))

        assert "re-pair --fresh" in capsys.readouterr().out

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


def _unknown_owner(_path: Path) -> str:
    raise KeyError(1234)


class TestPermissionDenied:
    """``mobaide-agent`` is on PATH for every account but the install belongs
    to one, so a PermissionError from a verb becomes advice and exit 13
    instead of a traceback."""

    _ENV_FILE = "/opt/mobaide/mobaide_agent/.env"

    @pytest.fixture
    def denied(self) -> PermissionError:
        return PermissionError(13, "Permission denied", self._ENV_FILE)

    @pytest.fixture
    def posix(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("mobaide.__main__._current_user", lambda: "alice")

    @pytest.mark.usefixtures("posix")
    def test_main_exits_13_with_the_command_to_run_as_the_owner(
        self, monkeypatch, capsys, denied
    ):
        def refuse(_args: argparse.Namespace) -> int:
            raise denied

        monkeypatch.setattr("mobaide.__main__._run_re_pair", refuse)
        monkeypatch.setattr(Path, "owner", lambda self: "mobaide")

        with pytest.raises(SystemExit) as excinfo:
            main(["re-pair", "--prefix", "/opt/mobaide", "--fresh"])

        assert excinfo.value.code == 13
        err = capsys.readouterr().err
        assert f"mobaide-agent: permission denied: {self._ENV_FILE}" in err
        assert (
            "sudo -u mobaide mobaide-agent re-pair --prefix /opt/mobaide --fresh"
            in err
        )

    @pytest.mark.usefixtures("posix")
    def test_without_prefix_flag_asks_who_owns_the_default_install(
        self, monkeypatch, capsys, denied
    ):
        monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", "/srv/mobaide")
        asked: list[Path] = []

        def owner(path: Path) -> str:
            asked.append(path)
            return "mobaide"

        monkeypatch.setattr(Path, "owner", owner)

        rc = _report_permission_denied(_ns(prefix=None), denied, ["show-qr"])

        assert rc == 13
        assert asked == [Path("/srv/mobaide")]
        assert "sudo -u mobaide mobaide-agent show-qr" in capsys.readouterr().err

    @pytest.mark.usefixtures("posix")
    @pytest.mark.parametrize(
        "owner",
        [lambda self: "alice", _unknown_owner],
        ids=["same-user", "unknown-owner"],
    )
    def test_without_another_owner_points_at_the_file(
        self, monkeypatch, capsys, denied, owner
    ):
        monkeypatch.setattr(Path, "owner", owner)

        rc = _report_permission_denied(
            _ns(prefix="/opt/mobaide"), denied, ["re-pair"]
        )

        assert rc == 13
        err = capsys.readouterr().err
        assert f"Check the ownership and permissions of {self._ENV_FILE}" in err
        assert "sudo" not in err

    def test_windows_asks_for_an_elevated_terminal(
        self, monkeypatch, capsys, denied
    ):
        monkeypatch.setattr(sys, "platform", "win32")

        rc = _report_permission_denied(
            _ns(prefix=r"C:\Program Files\MobaideAgent"), denied, ["re-pair"]
        )

        assert rc == 13
        err = capsys.readouterr().err
        assert f"mobaide-agent: permission denied: {self._ENV_FILE}" in err
        assert "elevated terminal" in err
        assert "sudo" not in err
