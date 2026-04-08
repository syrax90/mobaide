"""Tests for the autoscan REST router additions in CLA-22."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from claude_mobile.server.autoscan.router import router


@pytest.fixture
def app_with_mock_manager():
    app = FastAPI()
    # Bypass auth dependency
    from claude_mobile.server.auth import dependencies as auth_deps
    app.dependency_overrides[auth_deps.get_current_user] = lambda: "test-user"
    app.include_router(router)
    mgr = MagicMock()
    mgr.get.return_value = None
    app.state.autoscan_manager = mgr
    return app, mgr


def test_run_now_404_when_not_running(app_with_mock_manager):
    app, mgr = app_with_mock_manager
    client = TestClient(app)
    resp = client.post("/api/autoscan/run-now", json={"project_name": "missing"})
    assert resp.status_code == 404


def test_run_now_calls_request_immediate_scan(app_with_mock_manager):
    app, mgr = app_with_mock_manager
    fake_inst = MagicMock()
    mgr.get.return_value = fake_inst
    client = TestClient(app)
    resp = client.post("/api/autoscan/run-now", json={"project_name": "p"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "project_name": "p"}
    fake_inst.request_immediate_scan.assert_called_once()
