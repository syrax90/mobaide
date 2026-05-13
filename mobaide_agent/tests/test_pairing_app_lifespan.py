"""Tests for the pairing-pending lifespan branch of ``create_app``.

The plan for offline-after-pairing first replaced ``sudo systemctl
restart`` with ``sys.exit(EXIT_RESTART_REQUESTED)``; a follow-up plan
then replaced the supervisor-restart dependency entirely with an
in-process state transition. The current ``_on_paired`` is an ``async``
callback that receives the validated ``PairingClaim``, builds a new
``RelayConfig`` (preserving ``server_url`` from the cold-boot config,
NOT from ``claim.relay_url`` — pinned by
``test_on_paired_preserves_boot_server_url``), and calls
``_start_relay_in_process`` so the running process becomes the
operational process within ~1-2s. The exit-75 path is now a fallback
that fires ONLY if in-process startup raises.

The test surface here pins:

* The lifespan instantiates ``run_pairing_monitor`` with an ``on_paired``
  callback (not ``None``).
* That callback is an async callable that takes a ``PairingClaim`` and,
  on the happy path, calls ``_start_relay_in_process`` rather than
  scheduling ``sys.exit``.
* On the happy path, no shell-out and no ``sys.exit`` — pins the
  in-process transition.
* On the failure path (``_start_relay_in_process`` raises), the
  fallback schedules ``sys.exit(EXIT_RESTART_REQUESTED)`` via
  ``loop.call_later``. This is the documented backup path so the
  operator does not have to wait for a manual restart.

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

import mobaide.agent.app as _agent_app
from mobaide.agent.app import create_app
from mobaide.agent.config import AgentConfig
from mobaide.agent.pairing_ws import PairingClaim
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


def _make_claim(
    *,
    relay_url: str = "https://different-relay.example.com",
) -> PairingClaim:
    return PairingClaim(
        agent_id="paired-agent-id",
        agent_token="paired-agent-token",
        relay_url=relay_url,
        agent_friendly_name="lab",
        issued_at="2026-05-07T00:00:00+00:00",
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


class TestOnPairedHappyPath:
    """The captured callback transitions in-process — no exit, no shell-out."""

    @pytest.mark.asyncio
    async def test_on_paired_calls_start_relay_in_process_with_claim(
        self, tmp_path: Path, captured_on_paired, monkeypatch,
    ):
        # Pins the post-7b9c581 contract: ``_on_paired`` is async, takes
        # a ``PairingClaim``, and routes the credentials into
        # ``_start_relay_in_process`` so the running process becomes
        # operational without a restart. A regression that reverts to a
        # no-arg sync callback (or that calls ``sys.exit`` on the happy
        # path) would fail this test.
        captured_calls: list[tuple] = []

        async def _stub_start(app, relay_cfg):
            captured_calls.append((app, relay_cfg))

        monkeypatch.setattr(
            _agent_app, "_start_relay_in_process", _stub_start,
        )

        app = create_app(config=_make_pairing_pending_config(tmp_path))
        claim = _make_claim()

        with TestClient(app):
            await asyncio.sleep(0)
            on_paired = captured_on_paired["on_paired"]
            await on_paired(claim)

        assert len(captured_calls) == 1, (
            f"expected exactly one _start_relay_in_process call on the "
            f"happy path; got {len(captured_calls)}"
        )
        _, new_relay_cfg = captured_calls[0]
        assert new_relay_cfg.agent_id == "paired-agent-id"
        assert new_relay_cfg.agent_token == "paired-agent-token"

    @pytest.mark.asyncio
    async def test_on_paired_preserves_boot_server_url(
        self, tmp_path: Path, captured_on_paired, monkeypatch,
    ):
        # Critical consistency invariant from the plan:
        # ``server_url`` in the in-process RelayConfig comes from the
        # cold-boot ``relay_cfg.server_url`` (loaded from .env), NOT
        # from ``claim.relay_url``. If the operator pinned a custom
        # RELAY_SERVER_URL, the running-process relay URL MUST match the
        # URL the next cold boot will read — otherwise the running
        # process and a fresh process would dial different relays and
        # the next supervisor restart would silently switch endpoints.
        # This mirrors ``write_credentials_to_env``'s "only backfill if
        # blank" rule for RELAY_SERVER_URL.
        captured_cfgs: list = []

        async def _stub_start(_app, relay_cfg):
            captured_cfgs.append(relay_cfg)

        monkeypatch.setattr(
            _agent_app, "_start_relay_in_process", _stub_start,
        )

        app = create_app(config=_make_pairing_pending_config(tmp_path))
        # The claim's relay_url is DIFFERENT from the .env server_url to
        # make the test fail if a regression starts using claim.relay_url.
        claim = _make_claim(
            relay_url="https://different-relay.example.com",
        )
        assert claim.relay_url != "wss://relay.example.com", (
            "test setup invariant: claim.relay_url must differ from "
            "the boot relay_cfg.server_url so we can prove the closure "
            "picked the right source"
        )

        with TestClient(app):
            await asyncio.sleep(0)
            await captured_on_paired["on_paired"](claim)

        assert len(captured_cfgs) == 1
        assert captured_cfgs[0].server_url == "wss://relay.example.com", (
            f"_on_paired must preserve boot relay_cfg.server_url for the "
            f"in-process RelayClient; got {captured_cfgs[0].server_url!r}, "
            f"which suggests the closure incorrectly used claim.relay_url "
            f"({claim.relay_url!r})"
        )

    @pytest.mark.asyncio
    async def test_on_paired_does_not_invoke_subprocess_or_sys_exit(
        self, tmp_path: Path, captured_on_paired, monkeypatch,
    ):
        # Combined regression guard for two prior designs:
        # (1) the original ``subprocess.run(["sudo", ...])`` path and
        # (2) the intermediate ``sys.exit(75) on every pair`` path.
        # The current happy path must do NEITHER: ``_start_relay_in_process``
        # owns the transition entirely. A regression that calls
        # ``sys.exit`` here would mask the in-process transition from
        # the operator (the process would die before the relay client
        # boots in-process) and the supposed "no restart" UX would
        # silently degrade.
        exit_calls: list[int] = []
        monkeypatch.setattr(
            _agent_app.sys, "exit",
            lambda code=0: exit_calls.append(code),
        )

        async def _stub_start(_app, _relay_cfg):
            return None

        monkeypatch.setattr(
            _agent_app, "_start_relay_in_process", _stub_start,
        )

        import os
        import subprocess

        def _trip(*args, **kwargs):
            raise AssertionError(
                f"_on_paired must not shell out on the happy path; got "
                f"args={args!r} kwargs={kwargs!r}"
            )

        monkeypatch.setattr(subprocess, "run", _trip)
        monkeypatch.setattr(subprocess, "Popen", _trip)
        monkeypatch.setattr(subprocess, "call", _trip)
        monkeypatch.setattr(subprocess, "check_call", _trip)
        monkeypatch.setattr(subprocess, "check_output", _trip)
        monkeypatch.setattr(os, "system", _trip)

        app = create_app(config=_make_pairing_pending_config(tmp_path))
        claim = _make_claim()

        with TestClient(app):
            await asyncio.sleep(0)
            await captured_on_paired["on_paired"](claim)
            # Yield past the 0.5s call_later window the fallback path
            # would use; if anything scheduled an exit we'd see it.
            await asyncio.sleep(0.6)

        assert exit_calls == [], (
            f"happy path must NOT call sys.exit; got {exit_calls!r}. "
            f"The exit-75 path is reserved for the in-process startup "
            f"failure fallback, not the success case"
        )

    @pytest.mark.asyncio
    async def test_on_paired_logs_relay_started_in_process(
        self, tmp_path: Path, captured_on_paired, monkeypatch, capsys,
    ):
        # Operators grep for ``agent.relay_started_in_process`` to
        # confirm the in-process transition fired. The log line is the
        # primary visibility surface for "pairing succeeded and the
        # agent is now operational without a restart" — the whole
        # point of the refactor. Pin the literal event name so a rename
        # forces an explicit test update.
        async def _stub_start(_app, _relay_cfg):
            return None

        monkeypatch.setattr(
            _agent_app, "_start_relay_in_process", _stub_start,
        )

        app = create_app(config=_make_pairing_pending_config(tmp_path))
        claim = _make_claim()

        with TestClient(app):
            await asyncio.sleep(0)
            await captured_on_paired["on_paired"](claim)

        out = capsys.readouterr().out
        assert "agent.relay_started_in_process" in out, (
            f"expected 'agent.relay_started_in_process' in stdout; "
            f"got: {out!r}"
        )
        # The log line carries the paired agent_id so operators can
        # correlate against the .env value.
        assert "paired-agent-id" in out, (
            f"expected paired agent_id in the log line; got: {out!r}"
        )


class TestOnPairedFailureFallback:
    """When ``_start_relay_in_process`` raises, fall back to exit-75.

    The whole point of the in-process transition is to AVOID the
    supervisor-restart dependency, but the plan explicitly preserves
    ``sys.exit(75)`` as a BACKUP: if in-process startup fails (e.g.
    central_server unreachable when the running process tries to dial
    it), ``.env`` is already correct, so the supervisor's next restart
    of the service will see the new credentials and start the relay
    client normally. The fallback ensures the operator does not have
    to wait for a manual restart on the rare failure case.
    """

    @pytest.mark.asyncio
    async def test_failure_schedules_sys_exit_with_restart_code(
        self, tmp_path: Path, captured_on_paired, monkeypatch,
    ):
        # In-process startup raises -> fallback schedules
        # ``sys.exit(EXIT_RESTART_REQUESTED)`` via ``loop.call_later``.
        exit_calls: list[int] = []
        monkeypatch.setattr(
            _agent_app.sys, "exit",
            lambda code=0: exit_calls.append(code),
        )

        async def _exploding_start(_app, _relay_cfg):
            raise RuntimeError("simulated in-process startup failure")

        monkeypatch.setattr(
            _agent_app, "_start_relay_in_process", _exploding_start,
        )

        app = create_app(config=_make_pairing_pending_config(tmp_path))
        claim = _make_claim()

        with TestClient(app):
            await asyncio.sleep(0)
            await captured_on_paired["on_paired"](claim)
            # The 0.5s defer mirrors the updater's flush window. Wait
            # slightly past it so ``loop.call_later`` fires before the
            # lifespan tears down.
            await asyncio.sleep(0.6)

        assert exit_calls == [EXIT_RESTART_REQUESTED], (
            f"expected exactly one sys.exit({EXIT_RESTART_REQUESTED}) "
            f"on the in-process-startup-failure path; got {exit_calls!r}"
        )

    @pytest.mark.asyncio
    async def test_failure_logs_in_process_relay_start_failed(
        self, tmp_path: Path, captured_on_paired, monkeypatch, capsys,
    ):
        # The fallback log line names the structured event and includes
        # the underlying exception so operators can tell why the
        # in-process transition failed (relay unreachable, TLS error,
        # etc.) without re-running the install. Pin the literal event
        # name so a rename forces an explicit test update.
        monkeypatch.setattr(
            _agent_app.sys, "exit", lambda code=0: None,
        )

        async def _exploding_start(_app, _relay_cfg):
            raise RuntimeError("simulated unique startup failure marker")

        monkeypatch.setattr(
            _agent_app, "_start_relay_in_process", _exploding_start,
        )

        app = create_app(config=_make_pairing_pending_config(tmp_path))
        claim = _make_claim()

        with TestClient(app):
            await asyncio.sleep(0)
            await captured_on_paired["on_paired"](claim)
            await asyncio.sleep(0.6)

        out = capsys.readouterr().out
        assert "agent.in_process_relay_start_failed" in out, (
            f"expected 'agent.in_process_relay_start_failed' in stdout; "
            f"got: {out!r}"
        )
        assert "simulated unique startup failure marker" in out, (
            f"expected underlying exception message in stdout so "
            f"operators can diagnose; got: {out!r}"
        )
        assert str(EXIT_RESTART_REQUESTED) in out, (
            f"expected exit code {EXIT_RESTART_REQUESTED} in fallback "
            f"log line; got: {out!r}"
        )
