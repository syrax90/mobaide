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
        assert captured_cfgs[0].agent_id == "paired-agent-id"
        assert captured_cfgs[0].agent_token == "paired-agent-token"
        assert captured_cfgs[0].server_url == "wss://relay.example.com", (
            f"_on_paired must preserve boot relay_cfg.server_url for the "
            f"in-process RelayClient; got {captured_cfgs[0].server_url!r}, "
            f"which suggests the closure incorrectly used claim.relay_url "
            f"({claim.relay_url!r})"
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


class TestPairingTaskDoneCallback:
    """Pin the ``_pairing_task_done`` callback contract in ``app.py``.

    The pairing task is scheduled with ``asyncio.create_task`` and is
    not awaited until lifespan shutdown. Without the done-callback added
    in this change, an uncaught exception inside the task only surfaces
    via Python's "Task exception was never retrieved" GC log, which on a
    long-running service may never trigger — the failure mode that
    motivated RC-1 in the plan. ``run_pairing_monitor`` already has its
    own outer-loop ``try/except Exception`` (tested in
    ``TestRunPairingMonitorBroadenedExceptionHandling`` in
    ``test_pairing_monitor.py``), so this callback is the last-line
    defence: it fires only if the monitor returns normally, is
    cancelled, or escapes a ``BaseException`` the inner catch-all does
    not swallow. Pinning each branch keeps a future refactor that
    removes either layer from silently regressing.
    """

    @pytest.fixture
    def _stub_layout(self, monkeypatch):
        import mobaide.agent.pairing_monitor as _pm

        monkeypatch.setattr(_pm, "default_layout", lambda: object())
        return _pm

    @pytest.mark.asyncio
    async def test_task_died_unexpectedly_is_logged_when_monitor_raises(
        self, tmp_path: Path, monkeypatch, capsys, _stub_layout,
    ):
        # Simulate the silent-task-death failure mode: the monitor
        # escapes ``BaseException`` (or a regression removes the outer
        # ``except Exception``) and the task ends with an exception.
        # The done-callback MUST log
        # ``pairing_monitor.task_died_unexpectedly`` so an operator
        # grepping ``agent.err.log`` sees the failure within seconds
        # rather than at process shutdown 24 hours later.
        async def _exploding_monitor(**_kwargs: Any) -> None:
            raise SystemError("simulated unanticipated monitor death")

        monkeypatch.setattr(
            _stub_layout, "run_pairing_monitor", _exploding_monitor,
        )

        app = create_app(config=_make_pairing_pending_config(tmp_path))

        with TestClient(app):
            # The task must (a) be scheduled, (b) raise, (c) trigger the
            # done-callback. Yielding two rounds covers both schedule and
            # done-callback dispatch on the same loop.
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        out = capsys.readouterr().out
        assert "pairing_monitor.task_died_unexpectedly" in out, (
            f"expected 'pairing_monitor.task_died_unexpectedly' in "
            f"stdout when the monitor raises — this is the defence-"
            f"in-depth log that surfaces silent task death within "
            f"seconds. Got: {out!r}"
        )
        # The structured log carries the exception type so operators
        # can route to the right cause without re-running the install.
        assert "SystemError" in out, (
            f"expected exception type 'SystemError' in the structured "
            f"log line; without ``exc_type=%s`` an operator cannot "
            f"distinguish a JSON parse failure from a permission denial "
            f"from a panicked relay client. Got: {out!r}"
        )

    @pytest.mark.asyncio
    async def test_task_cancellation_at_shutdown_is_silent(
        self, tmp_path: Path, monkeypatch, capsys, _stub_layout,
    ):
        # Lifespan shutdown cancels the pairing task — that is the
        # designed path, not a failure. The done-callback must
        # short-circuit on ``task.cancelled()`` and NOT log a failure;
        # otherwise every clean shutdown would emit a false-positive
        # ``task_died_unexpectedly`` and operators would learn to
        # ignore the alarm that exists to surface RC-1.
        async def _sleep_forever_monitor(**_kwargs: Any) -> None:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise

        monkeypatch.setattr(
            _stub_layout, "run_pairing_monitor", _sleep_forever_monitor,
        )

        app = create_app(config=_make_pairing_pending_config(tmp_path))

        with TestClient(app):
            await asyncio.sleep(0)
        # ``with TestClient(app):`` exit triggers lifespan shutdown
        # which cancels the task. Drain one more event-loop tick so the
        # done-callback runs.
        await asyncio.sleep(0)

        out = capsys.readouterr().out
        assert "pairing_monitor.task_died_unexpectedly" not in out, (
            f"a shutdown-driven cancellation must NOT be reported as "
            f"unexpected death — the done-callback's first branch "
            f"explicitly returns on ``task.cancelled()``. Got: {out!r}"
        )
