"""Cover ``GET /api/version`` on the agent and the ``server_info`` text frame
emitted by ``/ws/session/<id>``."""
from __future__ import annotations

import sys

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
        },
        "git": {
            "changes": True,
            "stage": True,
            "discard": True,
            "commit": True,
            "fetch": True,
            "pull": True,
            "push": True,
            "branches": True,
            "rebase": True,
            "resolve": True,
            "delete_remote": True,
            "conflicts": True,
            "worktree_base": True,
        },
        "voice": {
            "dictation": True,
            "sample_rate": 16000,
            "max_seconds": 120,
            "max_chunk_bytes": 65536,
            "cleanup": True,
            "cleanup_kinds": ["claude", "codex", "antigravity"],
        },
        "fs": {"dirs": True, "windows_paths": sys.platform == "win32"},
        "chat": {
            "claude": {"resume": True, "live": False},
            "codex": {"resume": True, "live": sys.platform != "win32"},
        },
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


async def test_get_version_reports_model_details_for_warm_codex_cache(
    app, client
) -> None:
    """A Codex launcher whose catalog enumeration captured per-model reasoning
    levels carries ``model_details`` next to ``models``; ``default_effort`` is
    omitted — never null — when unknown. Seeded directly for the same
    no-lifespan reason as the ``models`` test above."""
    from mobaide.core.codex_launcher import CodexModelInfo

    app.state.codex_launcher._cached_models = ("gpt-5.6-sol", "gpt-5.4-mini")
    app.state.codex_launcher._cached_model_details = (
        CodexModelInfo(
            id="gpt-5.6-sol", efforts=("low", "high", "ultra"), default_effort="low"
        ),
        CodexModelInfo(id="gpt-5.4-mini", efforts=("low",), default_effort=None),
    )

    response = await client.get("/api/version")
    assert response.status_code == 200
    cli = response.json()["capabilities"]["cli"]
    assert cli["codex"] == {
        "available": True,
        "models": ["gpt-5.6-sol", "gpt-5.4-mini"],
        "model_details": [
            {
                "id": "gpt-5.6-sol",
                "efforts": ["low", "high", "ultra"],
                "default_effort": "low",
            },
            {"id": "gpt-5.4-mini", "efforts": ["low"]},
        ],
    }
    assert "model_details" not in cli["antigravity"]


async def test_get_version_marks_models_pending_while_warmup_outlasts_the_wait(
    app, client, monkeypatch
) -> None:
    """A warm task still running after the bounded wait (a legitimately slow
    probe — each subprocess attempt may take 10s, more than the wait) must be
    reported via ``models_pending`` on every enumeration-capable entry, so
    clients treat the cold shape as provisional instead of reconciling
    persisted catalog efforts away. The awaiter is stubbed out so the test
    exercises the marker, not the (separately tested) wait."""
    import asyncio

    import mobaide.agent.app as app_module

    async def _no_wait(*_a, **_kw) -> None:
        return None

    monkeypatch.setattr(app_module, "await_model_cache_warmup", _no_wait)
    app.state.cli_models_warmup = asyncio.create_task(asyncio.sleep(60))
    try:
        response = await client.get("/api/version")

        assert response.status_code == 200
        cli = response.json()["capabilities"]["cli"]
        assert cli["codex"]["models_pending"] is True
        assert cli["antigravity"]["models_pending"] is True
        # Claude never enumerates, so its entry must not read as "still warming".
        assert "models_pending" not in cli["claude"]

        # The warm task probes sequentially: once a CLI's own enumeration has
        # memoized (Codex always does), its entry must stop reading as pending
        # even while a sibling's probe keeps the task running — otherwise its
        # final answer would sit un-reconciled behind an unrelated probe.
        app.state.codex_launcher._cached_models = ("gpt-5.6-sol",)
        response = await client.get("/api/version")
        cli = response.json()["capabilities"]["cli"]
        assert cli["codex"] == {"available": True, "models": ["gpt-5.6-sol"]}
        assert cli["antigravity"]["models_pending"] is True
    finally:
        app.state.cli_models_warmup.cancel()


async def test_get_version_waits_for_a_pending_model_warmup(app, client) -> None:
    """A probe landing mid-warmup must see the warmed caches, not the cold
    ones — a cold response is indistinguishable from an old agent without
    enumeration and would make clients reconcile persisted catalog efforts
    away. The warm task here only releases after the request is already in
    flight, so a handler that did not wait would answer with no ``models``."""
    import asyncio

    release = asyncio.Event()

    async def _warm() -> None:
        await release.wait()
        app.state.codex_launcher._cached_models = ("gpt-5.6-sol",)

    app.state.cli_models_warmup = asyncio.create_task(_warm())
    request = asyncio.create_task(client.get("/api/version"))
    await asyncio.sleep(0.05)
    release.set()

    response = await request
    assert response.status_code == 200
    codex = response.json()["capabilities"]["cli"]["codex"]
    assert codex["models"] == ["gpt-5.6-sol"]
