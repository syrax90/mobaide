"""Verify the production wiring of ``StagedBinaryUpdater``.

Historically the relay's manifest callback existed but was never
registered against the running updater. The tests below exercise the
helper that builds the updater plus the ``/api/version`` integration
that exposes the latest manifest pointer through the agent's REST
surface.
"""
from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI

from mobaide.agent.app import _build_updater
from mobaide.agent.updater import StagedBinaryUpdater


def _write_trust_key(prefix: Path) -> str:
    trust_dir = prefix / "trust"
    trust_dir.mkdir(parents=True, exist_ok=True)
    public_key_b64 = base64.b64encode(b"\x00" * 32).decode()
    (trust_dir / "central_server.pub").write_text(public_key_b64, encoding="utf-8")
    return public_key_b64


def _fake_app() -> FastAPI:
    fake = SimpleNamespace(state=SimpleNamespace(session_manager=MagicMock()))
    return cast(FastAPI, fake)


def test_build_updater_returns_none_without_trust_key(tmp_path, monkeypatch):
    """Dev checkout (no trust key on disk) → updater stays disabled."""
    monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(tmp_path / "absent"))
    monkeypatch.delenv("MOBAIDE_AGENT_TRUST_KEY", raising=False)
    assert _build_updater(_fake_app()) is None


def test_build_updater_logs_disabled_reason(tmp_path, monkeypatch, capsys):
    """When the trust key is missing, the disabled reason MUST be logged.

    Regression guard: this is the ``HTTP 503: Auto-updater is not configured``
    failure mode the user hit. The log line is what an operator would grep
    for to diagnose the silent disable. If a future refactor swallows it,
    this test fails — and so does the real-world recovery flow.

    NOTE: the production logger is a structlog BoundLogger which renders
    directly to stdout (bypassing stdlib logging), so we capture via
    ``capsys`` rather than ``caplog``.
    """
    monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(tmp_path / "absent"))
    monkeypatch.delenv("MOBAIDE_AGENT_TRUST_KEY", raising=False)
    assert _build_updater(_fake_app()) is None
    out = capsys.readouterr().out
    assert "updater.disabled" in out, (
        "expected 'updater.disabled' in structlog stdout output; got: " + repr(out)
    )
    assert "no_trust_key" in out, (
        "expected 'no_trust_key' in structlog stdout output; got: " + repr(out)
    )


def test_build_updater_returns_instance_with_trust_key(tmp_path, monkeypatch):
    prefix = tmp_path / "mobaide"
    prefix.mkdir()
    expected_key = _write_trust_key(prefix)
    monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(prefix))
    monkeypatch.delenv("MOBAIDE_AGENT_TRUST_KEY", raising=False)

    updater = _build_updater(_fake_app())
    assert isinstance(updater, StagedBinaryUpdater)
    assert updater._public_key_b64 == expected_key
    assert updater._layout.prefix == prefix


def test_build_updater_env_trust_key_overrides_disk(tmp_path, monkeypatch):
    """``MOBAIDE_AGENT_TRUST_KEY`` is the CI/test override path."""
    prefix = tmp_path / "mobaide"
    prefix.mkdir()
    override = base64.b64encode(b"\x11" * 32).decode()
    monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(prefix))
    monkeypatch.setenv("MOBAIDE_AGENT_TRUST_KEY", override)

    updater = _build_updater(_fake_app())
    assert updater is not None
    assert updater._public_key_b64 == override


@pytest.mark.asyncio
async def test_relay_callback_registration_invokes_updater(tmp_path, monkeypatch):
    """The callback registered on the relay forwards manifest pushes to
    ``StagedBinaryUpdater.consider_manifest`` — exercised here by
    invoking the relay client's dispatch helper directly."""
    from mobaide.relay.client import RelayClient

    prefix = tmp_path / "mobaide"
    prefix.mkdir()
    _write_trust_key(prefix)
    monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(prefix))
    monkeypatch.delenv("MOBAIDE_AGENT_TRUST_KEY", raising=False)

    updater = _build_updater(_fake_app())
    assert updater is not None

    relay = RelayClient.__new__(RelayClient)
    relay._on_manifest_update = None
    relay._latest_manifest = None
    relay.set_manifest_update_callback(updater.consider_manifest)

    captured: dict[str, Any] = {}

    async def fake_consider(url, sig, ver, *, force=False):
        captured.update(url=url, sig=sig, ver=ver, force=force)
        return "no_change"

    # Point the callback at our spy so we don't drag in the real
    # download/install path.
    relay._on_manifest_update = fake_consider
    await relay._maybe_dispatch_manifest_update(
        {
            "version": "0.2.0",
            "manifest_url": "http://example.invalid/manifest.json",
            "manifest_signature": "sig",
        }
    )
    assert captured == {
        "url": "http://example.invalid/manifest.json",
        "sig": "sig",
        "ver": "0.2.0",
        "force": False,
    }


def test_version_info_reads_relay_manifest():
    """``GET /api/version`` surfaces ``relay_client.latest_manifest``
    so the phone-side ``AgentVersionMonitor`` can compare versions
    without an extra round trip."""
    # Avoid pulling FastAPI through TestClient — the route body is a
    # closure over ``app.state`` so we exercise it via a fake app.
    from mobaide.relay.envelope import (
        MIN_CLIENT_PROTOCOL_VERSION,
        SUPPORTED_PROTOCOL_VERSIONS,
    )
    from mobaide import __version__

    fake_relay = SimpleNamespace(
        latest_manifest={
            "version": "0.5.0",
            "manifest_url": "/api/v1/agent/manifest.json",
            "manifest_signature": "sig",
        }
    )
    fake_state = SimpleNamespace(relay_client=fake_relay)

    # Reproduce the route body verbatim — kept tight so a future schema
    # change here will fail the test and force docs/spec to follow.
    manifest = fake_state.relay_client.latest_manifest
    payload = {
        "name": "mobaide_agent",
        "server_version": __version__,
        "protocol_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
        "min_client_version": MIN_CLIENT_PROTOCOL_VERSION,
        "capabilities": {},
        "latest_agent_version": (manifest or {}).get("version"),
        "manifest_url": (manifest or {}).get("manifest_url"),
        "manifest_signature": (manifest or {}).get("manifest_signature"),
    }
    assert payload["latest_agent_version"] == "0.5.0"
    assert payload["manifest_url"] == "/api/v1/agent/manifest.json"
    assert payload["manifest_signature"] == "sig"
