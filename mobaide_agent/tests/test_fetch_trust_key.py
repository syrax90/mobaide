"""Tests for the ``mobaide-agent fetch-trust-key`` CLI verb.

Drives ``_run_fetch_trust_key`` directly with a hand-built
``argparse.Namespace`` so each test targets a per-test temporary
install prefix. The HTTP fetch is stubbed via ``urllib.request.urlopen``
to keep tests hermetic.
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mobaide.__main__ import (
    _run_fetch_trust_key,
    _trust_key_url_from_relay_server_url,
)
from mobaide.agent._env_writer import env_path_for_prefix


def _ns(**overrides) -> argparse.Namespace:
    defaults = {
        "from_url": None,
        "prefix": None,
        "no_restart": True,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _seed_env(tmp_path: Path, relay_server_url: str) -> Path:
    env_file = env_path_for_prefix(tmp_path)
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(
        f"JWT_SECRET_KEY=test-secret\nRELAY_SERVER_URL={relay_server_url}\n",
        encoding="utf-8",
    )
    return env_file


def _valid_key_b64() -> str:
    pk = Ed25519PrivateKey.generate().public_key()
    return base64.b64encode(pk.public_bytes_raw()).decode()


class _FakeResponse:
    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _patch_urlopen(payload):
    if isinstance(payload, str):
        body = payload
    elif isinstance(payload, dict):
        body = json.dumps(payload)
    else:
        raise TypeError("payload must be str or dict")
    return patch(
        "urllib.request.urlopen",
        return_value=_FakeResponse(body),
    )


class TestUrlDerivation:
    def test_wss_becomes_https(self):
        assert (
            _trust_key_url_from_relay_server_url("wss://example.com")
            == "https://example.com/api/v1/agent/trust_key"
        )

    def test_ws_becomes_http_when_explicitly_allowed(self, monkeypatch):
        monkeypatch.setenv("MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH", "1")
        assert (
            _trust_key_url_from_relay_server_url("ws://example.com")
            == "http://example.com/api/v1/agent/trust_key"
        )

    def test_path_component_is_dropped(self):
        assert (
            _trust_key_url_from_relay_server_url("wss://example.com/relay/path")
            == "https://example.com/api/v1/agent/trust_key"
        )

    def test_port_preserved(self):
        assert (
            _trust_key_url_from_relay_server_url("wss://example.com:9000")
            == "https://example.com:9000/api/v1/agent/trust_key"
        )

    def test_https_scheme_passthrough(self):
        assert (
            _trust_key_url_from_relay_server_url("https://example.com")
            == "https://example.com/api/v1/agent/trust_key"
        )

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError):
            _trust_key_url_from_relay_server_url("not-a-url")

    def test_unsupported_scheme_raises(self):
        with pytest.raises(ValueError):
            _trust_key_url_from_relay_server_url("ftp://example.com")

    def test_userinfo_in_url_rejected(self):
        # urlparse('wss://api.mobaide.com@evil.example').netloc would
        # otherwise smuggle the attacker host into the trust-key URL.
        # Reject up front so the post-fetch validator never has to see
        # the spoofed key.
        with pytest.raises(ValueError, match="user-info"):
            _trust_key_url_from_relay_server_url(
                "wss://api.mobaide.com@evil.example"
            )

    def test_userinfo_with_password_rejected(self):
        with pytest.raises(ValueError, match="user-info"):
            _trust_key_url_from_relay_server_url(
                "wss://user:pass@example.com"
            )

    def test_cleartext_http_refused_by_default(self, monkeypatch):
        monkeypatch.delenv(
            "MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH", raising=False,
        )
        with pytest.raises(ValueError, match="cleartext"):
            _trust_key_url_from_relay_server_url("ws://example.com")

    def test_cleartext_https_passthrough_unaffected_by_insecure_flag(
        self, monkeypatch,
    ):
        # The insecure flag is a refuse-cleartext escape hatch; it must
        # not change behaviour for HTTPS URLs.
        monkeypatch.setenv("MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH", "1")
        assert (
            _trust_key_url_from_relay_server_url("wss://example.com")
            == "https://example.com/api/v1/agent/trust_key"
        )

    def test_cleartext_explicit_http_refused_by_default(self, monkeypatch):
        monkeypatch.delenv(
            "MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH", raising=False,
        )
        with pytest.raises(ValueError, match="cleartext"):
            _trust_key_url_from_relay_server_url("http://example.com")

    def test_cleartext_http_allowed_when_flag_set(self, monkeypatch):
        monkeypatch.setenv("MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH", "1")
        assert (
            _trust_key_url_from_relay_server_url("http://example.com")
            == "http://example.com/api/v1/agent/trust_key"
        )

    def test_insecure_flag_ignored_unless_value_is_one(self, monkeypatch):
        # Only the literal "1" opts in; "true"/"yes"/"0" do not.
        for raw in ("0", "true", "yes", ""):
            monkeypatch.setenv(
                "MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH", raw,
            )
            with pytest.raises(ValueError, match="cleartext"):
                _trust_key_url_from_relay_server_url("ws://example.com")


class TestFetchHappyPath:
    def test_writes_trust_key_file(self, tmp_path):
        _seed_env(tmp_path, "wss://example.com")
        key_b64 = _valid_key_b64()

        with _patch_urlopen({"public_key_b64": key_b64}):
            rc = _run_fetch_trust_key(_ns(prefix=str(tmp_path)))

        assert rc == 0
        trust_file = tmp_path / "trust" / "central_server.pub"
        assert trust_file.exists()
        assert trust_file.read_text(encoding="utf-8").strip() == key_b64

    def test_from_url_overrides_env_derivation(self, tmp_path):
        # No env file at all — only --from-url is set.
        key_b64 = _valid_key_b64()

        with _patch_urlopen({"public_key_b64": key_b64}) as urlopen:
            rc = _run_fetch_trust_key(
                _ns(
                    prefix=str(tmp_path),
                    from_url="https://override.example/api/v1/agent/trust_key",
                )
            )

        assert rc == 0
        urlopen.assert_called_once()
        called_url = urlopen.call_args[0][0]
        assert called_url == "https://override.example/api/v1/agent/trust_key"


class TestFetchEnvFileMissing:
    def test_returns_2_when_env_missing(self, tmp_path, capsys):
        rc = _run_fetch_trust_key(_ns(prefix=str(tmp_path)))

        assert rc == 2
        err = capsys.readouterr().err
        assert "does not exist" in err

    def test_returns_2_when_relay_server_url_empty(self, tmp_path, capsys):
        _seed_env(tmp_path, "")

        rc = _run_fetch_trust_key(_ns(prefix=str(tmp_path)))

        assert rc == 2
        err = capsys.readouterr().err
        assert "RELAY_SERVER_URL" in err


class TestFetchNetworkErrors:
    def test_returns_3_when_endpoint_unreachable(self, tmp_path, capsys):
        _seed_env(tmp_path, "wss://example.com")

        with patch(
            "urllib.request.urlopen",
            side_effect=OSError("connection refused"),
        ):
            rc = _run_fetch_trust_key(_ns(prefix=str(tmp_path)))

        assert rc == 3
        err = capsys.readouterr().err
        assert "cannot reach" in err

    def test_returns_3_on_malformed_json(self, tmp_path, capsys):
        _seed_env(tmp_path, "wss://example.com")

        with _patch_urlopen("not json"):
            rc = _run_fetch_trust_key(_ns(prefix=str(tmp_path)))

        assert rc == 3
        err = capsys.readouterr().err
        assert "malformed" in err

    def test_returns_3_when_key_field_missing(self, tmp_path):
        _seed_env(tmp_path, "wss://example.com")

        with _patch_urlopen({"unrelated": "field"}):
            rc = _run_fetch_trust_key(_ns(prefix=str(tmp_path)))

        assert rc == 3

    def test_returns_3_when_key_empty(self, tmp_path, capsys):
        _seed_env(tmp_path, "wss://example.com")

        with _patch_urlopen({"public_key_b64": "  "}):
            rc = _run_fetch_trust_key(_ns(prefix=str(tmp_path)))

        assert rc == 3
        err = capsys.readouterr().err
        assert "empty" in err


class TestFetchValidation:
    def test_returns_4_on_invalid_base64(self, tmp_path, capsys):
        _seed_env(tmp_path, "wss://example.com")

        with _patch_urlopen({"public_key_b64": "not-valid-base64!!!"}):
            rc = _run_fetch_trust_key(_ns(prefix=str(tmp_path)))

        assert rc == 4
        err = capsys.readouterr().err
        assert "Ed25519" in err

    def test_returns_4_on_wrong_key_length(self, tmp_path):
        _seed_env(tmp_path, "wss://example.com")
        # Valid base64 but only 16 bytes — Ed25519 needs exactly 32.
        wrong_length = base64.b64encode(b"\x00" * 16).decode()

        with _patch_urlopen({"public_key_b64": wrong_length}):
            rc = _run_fetch_trust_key(_ns(prefix=str(tmp_path)))

        assert rc == 4

    def test_invalid_key_does_not_create_trust_file(self, tmp_path):
        _seed_env(tmp_path, "wss://example.com")
        trust_file = tmp_path / "trust" / "central_server.pub"
        assert not trust_file.exists()

        with _patch_urlopen({"public_key_b64": "garbage"}):
            _run_fetch_trust_key(_ns(prefix=str(tmp_path)))

        assert not trust_file.exists()


class TestNoRestartFlag:
    def test_no_restart_skips_supervisor_call(self, tmp_path, capsys):
        _seed_env(tmp_path, "wss://example.com")
        key_b64 = _valid_key_b64()

        with _patch_urlopen({"public_key_b64": key_b64}), patch(
            "mobaide.__main__._try_restart_service"
        ) as restart:
            rc = _run_fetch_trust_key(
                _ns(prefix=str(tmp_path), no_restart=True),
            )

        assert rc == 0
        restart.assert_not_called()
        out = capsys.readouterr().out
        assert "Skipping restart" in out

    def test_default_invokes_supervisor_call(self, tmp_path):
        _seed_env(tmp_path, "wss://example.com")
        key_b64 = _valid_key_b64()

        with _patch_urlopen({"public_key_b64": key_b64}), patch(
            "mobaide.__main__._try_restart_service",
            return_value=0,
        ) as restart:
            rc = _run_fetch_trust_key(
                _ns(prefix=str(tmp_path), no_restart=False),
            )

        assert rc == 0
        restart.assert_called_once()
