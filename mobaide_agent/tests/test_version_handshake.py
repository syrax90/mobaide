"""Cover ``GET /api/version`` on the agent and the ``server_info`` text frame
emitted by ``/ws/session/<id>``."""
from __future__ import annotations

import pytest

from mobaide import __version__
from mobaide.relay.envelope import (
    MIN_CLIENT_PROTOCOL_VERSION,
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
    # _pin_claude_binary / _pin_codex_binary / _pin_antigravity_binary fixtures
    # make all three resolve, so all report available. The entries stay bare
    # {"available": bool} because the ASGITransport fixtures never run the app
    # lifespan, so the model-cache warm task never fires.
    assert body["capabilities"] == {
        "cli": {
            "claude": {"available": True},
            "codex": {"available": True},
            "antigravity": {"available": True},
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
    assert cli["antigravity"] == {"available": True}


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
    assert cli["antigravity"] == {"available": True}


async def test_get_version_antigravity_missing_reports_unavailable(
    client, monkeypatch
) -> None:
    """When the ``agy`` binary can't be resolved, ``capabilities.cli.antigravity``
    flips to ``available: false`` while Claude/Codex stay available — the gate is
    per-CLI, not all-or-nothing."""
    from mobaide.core import antigravity_launcher

    def _raise(*_a, **_kw):
        raise antigravity_launcher.AntigravityBinaryNotFoundError(["PATH:agy"])

    monkeypatch.setattr(antigravity_launcher, "_resolve_agy_binary", _raise)

    response = await client.get("/api/version")
    assert response.status_code == 200
    cli = response.json()["capabilities"]["cli"]
    assert cli["antigravity"] == {"available": False}
    assert cli["claude"] == {"available": True}
    assert cli["codex"] == {"available": True}


async def test_get_version_all_clis_missing_still_returns_200(
    client, monkeypatch
) -> None:
    """A host with no managed CLI installed must not error the version endpoint —
    all report unavailable and the client falls back to its optimistic default
    plus the create-time 503 backstop."""
    from mobaide.core import antigravity_launcher, claude_launcher, codex_launcher

    def _claude_raise(*_a, **_kw):
        raise claude_launcher.ClaudeBinaryNotFoundError(["PATH:claude"])

    def _codex_raise(*_a, **_kw):
        raise codex_launcher.CodexBinaryNotFoundError(["PATH:codex"])

    def _antigravity_raise(*_a, **_kw):
        raise antigravity_launcher.AntigravityBinaryNotFoundError(["PATH:agy"])

    monkeypatch.setattr(claude_launcher, "_resolve_claude_binary", _claude_raise)
    monkeypatch.setattr(codex_launcher, "_resolve_codex_binary", _codex_raise)
    monkeypatch.setattr(
        antigravity_launcher, "_resolve_agy_binary", _antigravity_raise
    )

    response = await client.get("/api/version")
    assert response.status_code == 200
    assert response.json()["capabilities"]["cli"] == {
        "claude": {"available": False},
        "codex": {"available": False},
        "antigravity": {"available": False},
    }


async def test_get_version_reports_models_for_warm_cache(app, client) -> None:
    """A launcher whose model cache was warmed carries ``models`` in its
    capability entry, while cold-cache launchers keep the bare
    ``{"available": bool}`` shape. The cache is seeded directly because the
    ASGITransport fixtures never run the lifespan ``warm_cli_model_caches``
    task."""
    app.state.antigravity_launcher._cached_models = (
        "Gemini 3.1 Pro (Low)",
        "GPT-OSS 120B (Medium)",
    )

    response = await client.get("/api/version")
    assert response.status_code == 200
    cli = response.json()["capabilities"]["cli"]
    assert cli["antigravity"] == {
        "available": True,
        "models": ["Gemini 3.1 Pro (Low)", "GPT-OSS 120B (Medium)"],
    }
    assert "models" not in cli["claude"]
