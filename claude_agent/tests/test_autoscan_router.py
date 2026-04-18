"""Tests for the autoscan REST router additions in CLA-22."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from claude_mobile.core.claude_launcher import ClaudeOptions
from claude_mobile.linear.scanner import (
    AutoScanAlreadyRunning,
    AutoScanCapacityReached,
)
from claude_mobile.agent.autoscan.router import router


@pytest.fixture
def app_with_mock_manager():
    app = FastAPI()
    # Bypass auth dependency
    from claude_mobile.agent.auth import dependencies as auth_deps
    app.dependency_overrides[auth_deps.get_current_user] = lambda: "test-user"
    app.include_router(router)
    mgr = MagicMock()
    mgr.get.return_value = None
    app.state.autoscan_manager = mgr
    return app, mgr


def test_run_now_404_when_not_running(app_with_mock_manager):
    app, mgr = app_with_mock_manager
    del mgr  # fixture default mgr.get returns None — no local reference needed
    client = TestClient(app)
    resp = client.post("/api/autoscan/run-now", json={"project_name": "missing"})
    assert resp.status_code == 404


def test_run_now_calls_request_immediate_scan(app_with_mock_manager):
    app, mgr = app_with_mock_manager
    fake_inst = MagicMock()
    fake_inst.to_dto.return_value = {"next_scan_at": "2026-04-08T15:00:00Z"}
    mgr.get.return_value = fake_inst
    client = TestClient(app)
    resp = client.post("/api/autoscan/run-now", json={"project_name": "p"})
    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True,
        "project_name": "p",
        "next_scan_at": "2026-04-08T15:00:00Z",
    }
    fake_inst.request_immediate_scan.assert_called_once()


# --------------------------------------------------------------------------- #
# Helpers / fixtures for REST coverage below.
# --------------------------------------------------------------------------- #


def _make_status_dict(project_name: str = "p") -> dict:
    """Minimal dict matching every required field on ``AutoScanInstanceDto``
    plus the ``recent_events`` list from ``AutoScanInstanceStatusResponse``.
    """
    return {
        "project_name": project_name,
        "status": "running",
        "started_at": 1712000000.0,
        "last_activity_at": 1712000100.0,
        "processed_count": 3,
        "last_scan_time": 1712000050.0,
        "scan_interval_seconds": 60,
        "next_scan_at": "2026-04-08T15:00:00Z",
        "cycle_number": 2,
        "current_seq": 7,
        "recent_events": [],
    }


def _make_dto_dict(project_name: str = "p") -> dict:
    """Minimal dict matching every required field on ``AutoScanInstanceDto``.

    Used for ``list_response`` scans entries (validated by the
    ``AutoScanListResponse`` pydantic model on the response).
    """
    return {
        "project_name": project_name,
        "status": "running",
        "started_at": 1712000000.0,
        "last_activity_at": 1712000100.0,
        "processed_count": 3,
        "last_scan_time": 1712000050.0,
        "scan_interval_seconds": 60,
        "next_scan_at": "2026-04-08T15:00:00Z",
        "cycle_number": 2,
        "current_seq": 7,
    }


# --------------------------------------------------------------------------- #
# Common gating — 401 when unauthenticated.
#
# Post-CLA-39: ``autoscan_manager`` is always instantiated in ``app.py`` — the
# legacy "501 not configured" branch is unreachable and those tests were
# deleted. Empty ``available_projects`` is the new signal that no projects
# have a Linear API key.
# --------------------------------------------------------------------------- #


def test_list_requires_auth_when_override_absent():
    """Without the auth override, the router-level
    ``Depends(get_current_user)`` should 401 on a bare request.
    """
    app = FastAPI()
    app.include_router(router)
    # Deliberately no dependency_overrides and no autoscan_manager — the
    # auth dependency runs first, so the manager state is irrelevant.
    client = TestClient(app)
    resp = client.get("/api/autoscan/list")
    assert resp.status_code == 401
    # The WWW-Authenticate header is part of the router's 401 contract.
    assert resp.headers.get("WWW-Authenticate") == "Bearer"


# --------------------------------------------------------------------------- #
# POST /api/autoscan/start
# --------------------------------------------------------------------------- #


class TestStartScan:
    def test_happy_path_without_claude_options(self, app_with_mock_manager):
        app, mgr = app_with_mock_manager
        fake_inst = MagicMock()
        fake_inst.project_name = "p"
        mgr.start = AsyncMock(return_value=fake_inst)

        client = TestClient(app)
        resp = client.post("/api/autoscan/start", json={"project_name": "p"})

        assert resp.status_code == 200
        assert resp.json() == {"message": "Auto scan started for project 'p'"}
        mgr.start.assert_awaited_once_with("p", client_options=None)

    def test_happy_path_with_claude_options(self, app_with_mock_manager):
        app, mgr = app_with_mock_manager
        fake_inst = MagicMock()
        fake_inst.project_name = "proj"
        mgr.start = AsyncMock(return_value=fake_inst)

        client = TestClient(app)
        body = {
            "project_name": "proj",
            "claude_options": {
                "plan_mode": True,
                "no_memory": False,
                "initial_prompt": "hello",
                "worktree": True,
                "branch_name": "feature/x",
                "identifier": "id-1",
                "title": "Some title",
            },
        }
        resp = client.post("/api/autoscan/start", json=body)

        assert resp.status_code == 200
        assert resp.json() == {"message": "Auto scan started for project 'proj'"}
        mgr.start.assert_awaited_once()
        # Positional arg: project_name, kwarg: client_options as a real
        # ClaudeOptions dataclass populated from the incoming model.
        call = mgr.start.await_args
        assert call is not None
        assert call.args == ("proj",)
        opts = call.kwargs["client_options"]
        assert isinstance(opts, ClaudeOptions)
        assert opts.plan_mode is True
        assert opts.no_memory is False
        assert opts.initial_prompt == "hello"
        assert opts.worktree is True
        assert opts.branch_name == "feature/x"
        assert opts.identifier == "id-1"
        assert opts.title == "Some title"

    def test_happy_path_skip_permissions_false(self, app_with_mock_manager):
        """Explicit ``skip_permissions: false`` in the request body must
        propagate through to the ClaudeOptions dataclass passed to the
        scan manager."""
        app, mgr = app_with_mock_manager
        fake_inst = MagicMock()
        fake_inst.project_name = "proj"
        mgr.start = AsyncMock(return_value=fake_inst)

        client = TestClient(app)
        body = {
            "project_name": "proj",
            "claude_options": {
                "plan_mode": True,
                "skip_permissions": False,
            },
        }
        resp = client.post("/api/autoscan/start", json=body)

        assert resp.status_code == 200
        call = mgr.start.await_args
        assert call is not None
        opts = call.kwargs["client_options"]
        assert isinstance(opts, ClaudeOptions)
        assert opts.skip_permissions is False
        assert opts.plan_mode is True

    def test_start_409_when_already_running(self, app_with_mock_manager):
        app, mgr = app_with_mock_manager
        mgr.start = AsyncMock(side_effect=AutoScanAlreadyRunning("already going"))
        client = TestClient(app)
        resp = client.post("/api/autoscan/start", json={"project_name": "p"})
        assert resp.status_code == 409
        assert "already going" in resp.json()["detail"]

    def test_start_429_when_capacity_reached(self, app_with_mock_manager):
        app, mgr = app_with_mock_manager
        mgr.start = AsyncMock(side_effect=AutoScanCapacityReached("at cap"))
        client = TestClient(app)
        resp = client.post("/api/autoscan/start", json={"project_name": "p"})
        assert resp.status_code == 429
        assert "at cap" in resp.json()["detail"]

    def test_start_404_when_value_error(self, app_with_mock_manager):
        app, mgr = app_with_mock_manager
        mgr.start = AsyncMock(side_effect=ValueError("unknown project"))
        client = TestClient(app)
        resp = client.post("/api/autoscan/start", json={"project_name": "nope"})
        assert resp.status_code == 404
        assert "unknown project" in resp.json()["detail"]

    def test_start_422_when_project_name_missing(self, app_with_mock_manager):
        app, _mgr = app_with_mock_manager
        del _mgr
        client = TestClient(app)
        resp = client.post("/api/autoscan/start", json={})
        assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# POST /api/autoscan/stop
# --------------------------------------------------------------------------- #


class TestStopScan:
    def test_happy_path(self, app_with_mock_manager):
        app, mgr = app_with_mock_manager
        mgr.stop = AsyncMock(return_value=None)
        client = TestClient(app)
        resp = client.post("/api/autoscan/stop", json={"project_name": "p"})
        assert resp.status_code == 200
        assert resp.json() == {"message": "Auto scan stopped for project 'p'"}
        mgr.stop.assert_awaited_once_with("p")

    def test_stop_404_when_key_error(self, app_with_mock_manager):
        app, mgr = app_with_mock_manager
        mgr.stop = AsyncMock(side_effect=KeyError("p"))
        client = TestClient(app)
        resp = client.post("/api/autoscan/stop", json={"project_name": "p"})
        assert resp.status_code == 404

    def test_stop_422_when_project_name_missing(self, app_with_mock_manager):
        app, _mgr = app_with_mock_manager
        del _mgr
        client = TestClient(app)
        resp = client.post("/api/autoscan/stop", json={})
        assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# POST /api/autoscan/run-now  (extra case not covered by the existing two)
# --------------------------------------------------------------------------- #


class TestRunNowExtra:
    def test_run_now_next_scan_at_missing_returns_null(self, app_with_mock_manager):
        """If ``instance.to_dto()`` omits ``next_scan_at``, the endpoint
        still returns 200 with ``next_scan_at: null`` in the response body.
        """
        app, mgr = app_with_mock_manager
        fake_inst = MagicMock()
        fake_inst.to_dto.return_value = {}  # no next_scan_at at all
        mgr.get.return_value = fake_inst

        client = TestClient(app)
        resp = client.post("/api/autoscan/run-now", json={"project_name": "p"})
        assert resp.status_code == 200
        assert resp.json() == {
            "ok": True,
            "project_name": "p",
            "next_scan_at": None,
        }
        fake_inst.request_immediate_scan.assert_called_once()


# --------------------------------------------------------------------------- #
# GET /api/autoscan/list
# --------------------------------------------------------------------------- #


class TestListScans:
    def test_list_empty(self, app_with_mock_manager):
        app, mgr = app_with_mock_manager
        mgr.list_response.return_value = {
            "scans": [],
            "capacity": 5,
            "max_scans": 5,
            "available_projects": ["A"],
            "auto_scan_prompt": "prompt",
        }
        client = TestClient(app)
        resp = client.get("/api/autoscan/list")
        assert resp.status_code == 200
        assert resp.json() == {
            "scans": [],
            "capacity": 5,
            "max_scans": 5,
            "available_projects": ["A"],
            "auto_scan_prompt": "prompt",
        }
        mgr.list_response.assert_called_once_with()

    def test_list_with_scans(self, app_with_mock_manager):
        app, mgr = app_with_mock_manager
        dto = _make_dto_dict("alpha")
        mgr.list_response.return_value = {
            "scans": [dto],
            "capacity": 2,
            "max_scans": 5,
            "available_projects": ["alpha", "beta"],
            "auto_scan_prompt": None,
        }
        client = TestClient(app)
        resp = client.get("/api/autoscan/list")
        assert resp.status_code == 200
        body = resp.json()
        assert body["capacity"] == 2
        assert body["max_scans"] == 5
        assert body["available_projects"] == ["alpha", "beta"]
        assert body["auto_scan_prompt"] is None
        assert len(body["scans"]) == 1
        assert body["scans"][0]["project_name"] == "alpha"
        assert body["scans"][0]["status"] == "running"
        assert body["scans"][0]["processed_count"] == 3


# --------------------------------------------------------------------------- #
# GET /api/autoscan/status
# --------------------------------------------------------------------------- #


class TestScanStatus:
    def test_happy_path(self, app_with_mock_manager):
        app, mgr = app_with_mock_manager
        fake_inst = MagicMock()
        fake_inst.get_status.return_value = _make_status_dict("p")
        mgr.get.return_value = fake_inst

        client = TestClient(app)
        resp = client.get("/api/autoscan/status", params={"project_name": "p"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["project_name"] == "p"
        assert body["status"] == "running"
        assert body["processed_count"] == 3
        assert body["scan_interval_seconds"] == 60
        assert body["recent_events"] == []
        mgr.get.assert_called_once_with("p")
        fake_inst.get_status.assert_called_once_with()

    def test_status_404_when_not_running(self, app_with_mock_manager):
        app, mgr = app_with_mock_manager
        mgr.get.return_value = None
        client = TestClient(app)
        resp = client.get("/api/autoscan/status", params={"project_name": "ghost"})
        assert resp.status_code == 404
        assert "ghost" in resp.json()["detail"]

    def test_status_422_when_project_name_missing(self, app_with_mock_manager):
        app, _mgr = app_with_mock_manager
        del _mgr
        client = TestClient(app)
        resp = client.get("/api/autoscan/status")
        assert resp.status_code == 422
