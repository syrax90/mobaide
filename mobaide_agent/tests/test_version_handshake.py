"""Cover ``GET /api/version`` on the agent and the ``server_info`` text frame
emitted by ``/ws/session/<id>``."""
from __future__ import annotations

import pytest

from mobaide import __version__
from mobaide.relay.envelope import (
    MIN_CLIENT_PROTOCOL_VERSION,
    PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
)


async def test_get_version(client) -> None:
    response = await client.get("/api/version")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "mobaide_agent"
    assert body["server_version"] == __version__
    assert body["protocol_versions"] == list(SUPPORTED_PROTOCOL_VERSIONS)
    assert body["min_client_version"] == MIN_CLIENT_PROTOCOL_VERSION
    assert body["latest_agent_version"] is None
    # capabilities.cli now reports per-CLI binary availability so the Android
    # mode-picker can gray out a CLI that isn't installed. The autouse
    # _pin_claude_binary / _pin_codex_binary fixtures make both resolve, so
    # both report available.
    assert body["capabilities"] == {
        "cli": {
            "claude": {"available": True},
            "codex": {"available": True},
        }
    }
    # The signed manifest body now travels inline over the relay WebSocket
    # — it is not exposed via this REST surface.
    assert "manifest_url" not in body
    assert "manifest_signature" not in body


async def test_get_version_claude_missing_reports_unavailable(
    client, monkeypatch
) -> None:
    """When the Claude binary can't be resolved, ``capabilities.cli.claude``
    flips to ``available: false`` while the endpoint stays 200 and Codex (still
    pinned) stays available — the gate is per-CLI, not all-or-nothing."""
    from mobaide.core import claude_launcher

    def _raise(*_a, **_kw):
        raise claude_launcher.ClaudeBinaryNotFoundError(["PATH:claude"])

    monkeypatch.setattr(claude_launcher, "_resolve_claude_binary", _raise)

    response = await client.get("/api/version")
    assert response.status_code == 200
    cli = response.json()["capabilities"]["cli"]
    assert cli["claude"] == {"available": False}
    assert cli["codex"] == {"available": True}


async def test_get_version_codex_missing_reports_unavailable(
    client, monkeypatch
) -> None:
    from mobaide.core import codex_launcher

    def _raise(*_a, **_kw):
        raise codex_launcher.CodexBinaryNotFoundError(["PATH:codex"])

    monkeypatch.setattr(codex_launcher, "_resolve_codex_binary", _raise)

    response = await client.get("/api/version")
    assert response.status_code == 200
    cli = response.json()["capabilities"]["cli"]
    assert cli["codex"] == {"available": False}
    assert cli["claude"] == {"available": True}


async def test_get_version_both_clis_missing_still_returns_200(
    client, monkeypatch
) -> None:
    """A host with neither CLI installed must not error the version endpoint —
    both report unavailable and the client falls back to its optimistic default
    plus the create-time 503 backstop."""
    from mobaide.core import claude_launcher, codex_launcher

    def _claude_raise(*_a, **_kw):
        raise claude_launcher.ClaudeBinaryNotFoundError(["PATH:claude"])

    def _codex_raise(*_a, **_kw):
        raise codex_launcher.CodexBinaryNotFoundError(["PATH:codex"])

    monkeypatch.setattr(claude_launcher, "_resolve_claude_binary", _claude_raise)
    monkeypatch.setattr(codex_launcher, "_resolve_codex_binary", _codex_raise)

    response = await client.get("/api/version")
    assert response.status_code == 200
    assert response.json()["capabilities"]["cli"] == {
        "claude": {"available": False},
        "codex": {"available": False},
    }


def test_protocol_version_constants() -> None:
    # proto-v5: additive bump adding the create-session ``cli`` discriminator
    # and ``codex_options`` for Codex CLI support (see PROTOCOL.md). This is a
    # deliberate tripwire — bumping the constants must consciously update it.
    assert PROTOCOL_VERSION == 5
    assert MIN_CLIENT_PROTOCOL_VERSION == 5
    assert tuple(SUPPORTED_PROTOCOL_VERSIONS) == (5,)
