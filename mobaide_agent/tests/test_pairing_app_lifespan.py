"""Tests for the pairing-pending lifespan branch of ``create_app``.

The plan for offline-after-pairing replaced the legacy
``sudo -n systemctl restart mobaide-agent`` path in ``_on_paired`` with a
``sys.exit(EXIT_RESTART_REQUESTED)`` deferral — the same exit-with-75
contract the auto-updater already uses. The test surface here pins the
new wiring:

* The lifespan instantiates ``run_pairing_monitor`` with an ``on_paired``
  callback (not ``None``).
* That callback, when invoked from the running event loop, schedules
  ``sys.exit(75)`` via ``loop.call_later`` rather than shelling out to
  ``sudo`` / ``systemctl``.
* No ``subprocess.Popen`` / ``os.system`` / ``sudo`` is involved — a
  regression that re-introduces the privileged-restart path is caught by
  the absence of those side effects under the lifespan.

The full ``run_pairing_monitor`` body is exercised by
``test_pairing_monitor.py``; here we only care about the ``app.py`` <->
``pairing_monitor.py`` integration seam.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mobaide.agent.app import create_app
from mobaide.agent.config import AgentConfig
from mobaide.agent.updater import EXIT_RESTART_REQUESTED
from mobaide.relay.config import RelayConfig

_TEST_JWT_SECRET = "test-secret-key-for-jwt-signing-do-not-use-in-prod"


def _make_pairing_pending_config(tmp_path: Path) -> AgentConfig:
    relay = RelayConfig(
        server_url="wss://relay.example.com",
        agent_id="",
        agent_token="",
    )
    assert relay.pairing_pending is True
    return AgentConfig(
        jwt_secret_key=_TEST_JWT_SECRET,
        user_store_path=str(tmp_path / "users.json"),
        projects_store_path=str(tmp_path / "projects.json"),
        max_sessions=2,
        log_level="DEBUG",
        relay_config=relay,
    )


@pytest.fixture
def captured_on_paired(monkeypatch):
    """Patch ``run_pairing_monitor`` to capture the ``on_paired`` arg.

    The lifespan does a deferred ``from mobaide.agent.pairing_monitor
    import run_pairing_monitor, default_layout``; replacing the module
    attributes before the lifespan runs intercepts both the stale-name
    lookup at import time and any future direct call site.
    """
    captured: dict[str, Any] = {}

    async def _stub_monitor(
        *,
        layout: Any,
        relay_server_url: str,
        current_agent_id: str | None,
        on_paired: Any,
    ) -> None:
        captured["on_paired"] = on_paired
        captured["layout"] = layout
        captured["relay_server_url"] = relay_server_url
        captured["current_agent_id"] = current_agent_id
        # Sleep until cancelled so the lifespan teardown's ``cancel()``
        # path is exercised as well.
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            raise

    def _stub_layout() -> Any:
        # ``default_layout`` reads the runtime prefix from disk; the
        # stubbed monitor never uses the layout, but production wiring
        # passes whatever this returns through unchanged.
        return object()

    import mobaide.agent.pairing_monitor as _pm

    monkeypatch.setattr(_pm, "run_pairing_monitor", _stub_monitor)
    monkeypatch.setattr(_pm, "default_layout", _stub_layout)
    return captured


class TestPairingLifespanWiresOnPaired:
    """The pairing-pending branch must pass a real callback, not None."""

    @pytest.mark.asyncio
    async def test_on_paired_callback_is_passed_to_monitor(
        self, tmp_path: Path, captured_on_paired,
    ):
        app = create_app(config=_make_pairing_pending_config(tmp_path))

        with TestClient(app):
            # Yielding control to the event loop lets the
            # ``asyncio.create_task(run_pairing_monitor(...))`` actually
            # schedule and call the stub.
            await asyncio.sleep(0)

            assert "on_paired" in captured_on_paired, (
                "lifespan did not call run_pairing_monitor — "
                "pairing-pending branch is unwired"
            )
            assert callable(captured_on_paired["on_paired"])

    @pytest.mark.asyncio
    async def test_relay_server_url_threaded_from_relay_config(
        self, tmp_path: Path, captured_on_paired,
    ):
        # Pin: ``relay_cfg.server_url`` is forwarded verbatim. A regression
        # that derives this from somewhere else (host header, env var, or
        # a stale cached config) is caught here.
        cfg = _make_pairing_pending_config(tmp_path)
        app = create_app(config=cfg)

        with TestClient(app):
            await asyncio.sleep(0)

        assert (
            captured_on_paired["relay_server_url"]
            == "wss://relay.example.com"
        )


class TestOnPairedSchedulesExit:
    """The captured callback exits with EXIT_RESTART_REQUESTED, no sudo."""

    @pytest.mark.asyncio
    async def test_on_paired_calls_sys_exit_with_restart_code(
        self, tmp_path: Path, captured_on_paired, monkeypatch,
    ):
        # Spy on ``sys.exit`` at the call site — the closure imports
        # ``sys`` from ``mobaide.agent.app``, so patching the attribute
        # there intercepts the call without terminating the test process.
        import mobaide.agent.app as _app_mod
        exit_calls: list[int] = []
        monkeypatch.setattr(
            _app_mod.sys, "exit", lambda code=0: exit_calls.append(code)
        )

        app = create_app(config=_make_pairing_pending_config(tmp_path))

        with TestClient(app):
            await asyncio.sleep(0)
            on_paired = captured_on_paired["on_paired"]
            on_paired()
            # The 0.5s defer mirrors the updater's flush window. Wait
            # slightly past it so ``loop.call_later`` fires before the
            # lifespan tears down.
            await asyncio.sleep(0.6)

        assert exit_calls == [EXIT_RESTART_REQUESTED], (
            f"expected exactly one sys.exit({EXIT_RESTART_REQUESTED}); "
            f"got {exit_calls!r}"
        )

    @pytest.mark.asyncio
    async def test_on_paired_does_not_invoke_subprocess(
        self, tmp_path: Path, captured_on_paired, monkeypatch,
    ):
        # Regression guard: the legacy implementation called
        # ``subprocess.run(["sudo", "-n", "systemctl", "restart", ...])``.
        # The new implementation must not shell out at all — pin the
        # absence by tripwiring every plausible launch primitive.
        import mobaide.agent.app as _app_mod
        monkeypatch.setattr(
            _app_mod.sys, "exit", lambda code=0: None
        )

        import subprocess
        import os

        def _trip(*args, **kwargs):
            raise AssertionError(
                f"_on_paired must not shell out; got "
                f"args={args!r} kwargs={kwargs!r}"
            )

        monkeypatch.setattr(subprocess, "run", _trip)
        monkeypatch.setattr(subprocess, "Popen", _trip)
        monkeypatch.setattr(subprocess, "call", _trip)
        monkeypatch.setattr(subprocess, "check_call", _trip)
        monkeypatch.setattr(subprocess, "check_output", _trip)
        monkeypatch.setattr(os, "system", _trip)

        app = create_app(config=_make_pairing_pending_config(tmp_path))

        with TestClient(app):
            await asyncio.sleep(0)
            on_paired = captured_on_paired["on_paired"]
            on_paired()
            await asyncio.sleep(0.6)

        # If we got here, no tripwire fired.

    @pytest.mark.asyncio
    async def test_on_paired_emits_structured_log_with_exit_code(
        self, tmp_path: Path, captured_on_paired, monkeypatch, capsys,
    ):
        # Operators grep for ``agent.pairing_complete`` in the journal to
        # confirm the supervisor restart path actually fired. The log
        # line MUST contain the exit code so a future refactor that
        # changes the contract is also visible in ops dashboards.
        #
        # Like ``test_app_updater_wiring.test_build_updater_logs_disabled_reason``,
        # the production logger is structlog-backed and renders to stdout —
        # capture via ``capsys``.
        import mobaide.agent.app as _app_mod
        monkeypatch.setattr(
            _app_mod.sys, "exit", lambda code=0: None
        )

        app = create_app(config=_make_pairing_pending_config(tmp_path))

        with TestClient(app):
            await asyncio.sleep(0)
            captured_on_paired["on_paired"]()
            await asyncio.sleep(0.6)

        out = capsys.readouterr().out
        assert "agent.pairing_complete" in out, (
            f"expected 'agent.pairing_complete' in stdout; got: {out!r}"
        )
        assert str(EXIT_RESTART_REQUESTED) in out, (
            f"expected exit code {EXIT_RESTART_REQUESTED} in log line; "
            f"got: {out!r}"
        )
