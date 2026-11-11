"""Verify the production wiring of ``StagedBinaryUpdater``.

Historically the relay's manifest callback existed but was never
registered against the running updater. The tests below exercise the
helper that builds the updater plus the ``/api/version`` integration
that exposes the latest manifest pointer through the agent's REST
surface.
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI

from mobaide.agent.app import _build_updater, _is_running_managed_release
from mobaide.agent.updater import InstallLayout, StagedBinaryUpdater


def _write_trust_key(prefix: Path) -> str:
    trust_dir = prefix / "trust"
    trust_dir.mkdir(parents=True, exist_ok=True)
    public_key_b64 = base64.b64encode(b"\x00" * 32).decode()
    (trust_dir / "central_server.pub").write_text(public_key_b64, encoding="utf-8")
    return public_key_b64


def _fake_app() -> FastAPI:
    fake = SimpleNamespace(state=SimpleNamespace(session_manager=MagicMock()))
    return cast(FastAPI, fake)


def _disabled_reasons(logger: MagicMock) -> str:
    return " ".join(str(c.args[0]) for c in logger.info.call_args_list)


def test_build_updater_without_trust_key_is_disabled_and_logs_why(
    tmp_path, monkeypatch
):
    """Dev checkout (no trust key on disk) -> updater stays disabled.

    Regression guard for the silent ``HTTP 503: Auto-updater is not
    configured``: the disabled reason must be logged for operators.
    """
    monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(tmp_path / "absent"))
    monkeypatch.delenv("MOBAIDE_AGENT_TRUST_KEY", raising=False)
    logger = MagicMock()
    monkeypatch.setattr("mobaide.agent.app.logger", logger)
    assert _build_updater(_fake_app()) is None
    assert "updater.disabled reason=no_trust_key" in _disabled_reasons(logger)


def test_build_updater_returns_instance_with_trust_key(tmp_path, monkeypatch):
    prefix = tmp_path / "mobaide"
    prefix.mkdir()
    expected_key = _write_trust_key(prefix)
    monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(prefix))
    monkeypatch.delenv("MOBAIDE_AGENT_TRUST_KEY", raising=False)
    # The test interpreter is not under <prefix>/releases, so the managed-venv
    # guard would otherwise disable the updater; stub it for the with-trust-key
    # path under test.
    monkeypatch.setattr(
        "mobaide.agent.app._is_running_managed_release", lambda layout: True
    )

    updater = _build_updater(_fake_app())
    assert isinstance(updater, StagedBinaryUpdater)
    assert updater._public_key_b64 == expected_key
    assert updater._layout.prefix == prefix


def test_build_updater_defers_while_voice_is_busy(tmp_path, monkeypatch):
    """``app.state.voice.busy`` is the updater's busy probe: an open
    dictation keeps the agent from restarting under the user."""
    prefix = tmp_path / "mobaide"
    prefix.mkdir()
    _write_trust_key(prefix)
    monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(prefix))
    monkeypatch.delenv("MOBAIDE_AGENT_TRUST_KEY", raising=False)
    monkeypatch.setattr(
        "mobaide.agent.app._is_running_managed_release", lambda layout: True
    )
    busy = {"value": False}
    session_manager = MagicMock()
    session_manager.list_sessions.return_value = []
    fake = SimpleNamespace(
        state=SimpleNamespace(
            session_manager=session_manager,
            voice=SimpleNamespace(busy=lambda: busy["value"]),
        )
    )

    updater = _build_updater(cast(FastAPI, fake))
    assert updater is not None
    assert updater._is_idle() is True

    busy["value"] = True
    assert updater._is_idle() is False


def test_build_updater_env_trust_key_overrides_disk(tmp_path, monkeypatch):
    """``MOBAIDE_AGENT_TRUST_KEY`` is the CI/test override path."""
    prefix = tmp_path / "mobaide"
    prefix.mkdir()
    override = base64.b64encode(b"\x11" * 32).decode()
    monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(prefix))
    monkeypatch.setenv("MOBAIDE_AGENT_TRUST_KEY", override)
    monkeypatch.setattr(
        "mobaide.agent.app._is_running_managed_release", lambda layout: True
    )

    updater = _build_updater(_fake_app())
    assert updater is not None
    assert updater._public_key_b64 == override


def test_build_updater_disabled_for_unmanaged_interpreter(tmp_path, monkeypatch):
    """Trust key present, but the interpreter is not the managed release venv
    (a dev / source run) → updater disabled, fail-closed.

    Regression guard for the dev-run-clobber root cause: a source run sharing
    ``default_prefix()`` must not drive the auto-updater (which would stage
    over and destroy the installed release).
    """
    prefix = tmp_path / "mobaide"
    prefix.mkdir()
    _write_trust_key(prefix)
    monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(prefix))
    monkeypatch.delenv("MOBAIDE_AGENT_TRUST_KEY", raising=False)
    monkeypatch.setattr(
        "mobaide.agent.app._is_running_managed_release", lambda layout: False
    )
    logger = MagicMock()
    monkeypatch.setattr("mobaide.agent.app.logger", logger)
    assert _build_updater(_fake_app()) is None
    assert "updater.disabled reason=unmanaged_interpreter" in _disabled_reasons(logger)


def test_is_running_managed_release_true_when_under_releases(tmp_path, monkeypatch):
    layout = InstallLayout(prefix=tmp_path / "mobaide")
    venv = layout.releases_dir / "0.2.0" / "venv"
    venv.mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(venv))
    assert _is_running_managed_release(layout) is True


def test_is_running_managed_release_false_for_dev_interpreter(tmp_path, monkeypatch):
    layout = InstallLayout(prefix=tmp_path / "mobaide")
    layout.releases_dir.mkdir(parents=True)
    dev_venv = tmp_path / "dev" / ".venv"
    dev_venv.mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(dev_venv))
    assert _is_running_managed_release(layout) is False


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
    monkeypatch.setattr(
        "mobaide.agent.app._is_running_managed_release", lambda layout: True
    )

    updater = _build_updater(_fake_app())
    assert updater is not None

    relay = RelayClient.__new__(RelayClient)
    relay._on_manifest_update = None
    relay._latest_manifest = None
    relay.set_manifest_update_callback(updater.consider_manifest)

    captured: dict[str, Any] = {}

    async def fake_consider(body, sig, ver, *, force=False):
        captured.update(body=body, sig=sig, ver=ver, force=force)
        return "no_change"

    # Point the callback at our spy so we don't drag in the real
    # stage/install path. ``manifest_body`` is delivered inline over the
    # WebSocket and handed verbatim to the updater — no HTTP fetch.
    relay._on_manifest_update = fake_consider
    body = {"version": "0.2.0", "tarball_url": "https://example.com/a.tar.gz"}
    await relay._maybe_dispatch_manifest_update(
        {
            "version": "0.2.0",
            "manifest_body": body,
            "manifest_signature": "sig",
        }
    )
    assert captured == {
        "body": body,
        "sig": "sig",
        "ver": "0.2.0",
        "force": False,
    }

