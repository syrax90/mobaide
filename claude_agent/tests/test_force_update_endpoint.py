"""Cover ``POST /api/agent/update/force_now``.

The endpoint backs the phone-side "Install now" CTA in
``AgentOutdatedBanner``. It bypasses idle-deferral by routing through
``StagedBinaryUpdater.force_update_now`` and returns the verbatim
outcome string the updater produced.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from claude_mobile.agent.updater_router import router as updater_router
from claude_mobile.agent.auth.dependencies import get_current_user


def _build_app(updater: Any | None, manifest: dict | None) -> FastAPI:
    app = FastAPI()
    app.include_router(updater_router)
    app.state.updater = updater
    app.state.relay_client = SimpleNamespace(latest_manifest=manifest)
    # Authentication is orthogonal to this endpoint's behaviour — stub
    # it out so the tests focus on the updater dispatch logic.
    app.dependency_overrides[get_current_user] = lambda: "test-user"
    return app


class _SpyUpdater:
    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, str, str, bool]] = []

    async def force_update_now(
        self, manifest_url, signature_b64, latest_version, *, exit_after_stage=True,
    ):
        self.calls.append(
            (manifest_url, signature_b64, latest_version, exit_after_stage)
        )
        return self.outcome


@pytest.mark.asyncio
async def test_force_now_invokes_updater_with_relay_manifest(monkeypatch):
    spy = _SpyUpdater("staged")
    manifest = {
        "version": "0.2.0",
        "manifest_url": "/api/v1/agent/manifest.json",
        "manifest_signature": "sig",
    }
    app = _build_app(spy, manifest)

    # The router schedules sys.exit via BackgroundTasks when the outcome is
    # "staged" — patch it in the router module so the background task fires
    # without terminating the test process.
    import claude_mobile.agent.updater_router as _router_mod
    exit_calls: list[int] = []
    monkeypatch.setattr(_router_mod.sys, "exit", lambda code=0: exit_calls.append(code))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/agent/update/force_now")
    assert resp.status_code == 200
    assert resp.json() == {"outcome": "staged"}
    # The HTTP route must call with exit_after_stage=False so the response is
    # flushed before the supervisor restart fires.
    assert spy.calls == [
        ("/api/v1/agent/manifest.json", "sig", "0.2.0", False)
    ]
    # The background task must have scheduled a supervisor restart.
    from claude_mobile.agent.updater import EXIT_RESTART_REQUESTED
    assert exit_calls == [EXIT_RESTART_REQUESTED]


@pytest.mark.asyncio
async def test_force_now_503_when_updater_disabled():
    app = _build_app(None, None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/agent/update/force_now")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_force_now_409_when_no_manifest():
    """Updater is wired but no manifest has been received yet — the
    server has no rollout pointer to apply, so a force-update is a
    semantically invalid request."""
    spy = _SpyUpdater("no_change")
    app = _build_app(spy, None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/agent/update/force_now")
    assert resp.status_code == 409
    assert spy.calls == []


@pytest.mark.asyncio
async def test_force_now_409_when_manifest_missing_required_fields():
    spy = _SpyUpdater("staged")
    incomplete = {"version": None, "manifest_url": None, "manifest_signature": None}
    app = _build_app(spy, incomplete)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/agent/update/force_now")
    assert resp.status_code == 409
    assert spy.calls == []
