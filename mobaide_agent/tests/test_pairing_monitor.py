"""Tests for ``mobaide.agent.pairing_monitor``.

Targets:

* ``initiate_pairing`` — synchronous urllib call. Mocks ``urlopen``
  to validate happy path, fingerprint mismatch (PairingMonitorError),
  malformed-payload rejection, and HTTP-level non-200 surfacing.
* ``write_credentials_to_env`` — atomic .env write that backfills
  RELAY_SERVER_URL only when missing.
* ``run_pairing_monitor`` — only the ``OSError``-around-write defence:
  the rest of the loop (initiate / WS / restart) remains integration-
  shaped and intentionally out of scope. The defence path is in-scope
  because it pins a specific failure-mode contract that, if regressed,
  silently kills the pairing-monitor task and leaves the agent stuck
  in ``pairing_pending``.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

import mobaide.agent.pairing_monitor as _pm
from mobaide.agent.pairing_monitor import (
    PairingMonitorError,
    _pairing_ws_url,
    initiate_pairing,
    run_pairing_monitor,
    write_credentials_to_env,
)
from mobaide.agent.pairing_qr import PendingPairing
from mobaide.agent.pairing_ws import PairingClaim
from mobaide.agent.updater import InstallLayout


def _initiate_response(**overrides) -> dict:
    # Note: no ``ws_url`` field. The agent derives the pairing WS URL
    # locally from RELAY_SERVER_URL + nonce; the server does not return
    # it. See ``_pairing_ws_url`` in ``mobaide.agent.pairing_monitor``.
    base = {
        "nonce": "n1",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "trust_key_fp": "ff" * 32,
        "relay_url": "https://relay.test",
        "qr_payload": f"MOBA1|https://relay.test|n1|{'ff' * 32}|2999999999",
    }
    base.update(overrides)
    return base


class _FakeResp:
    def __init__(self, status: int, body: dict | str):
        self.status = status
        if isinstance(body, dict):
            self._body = json.dumps(body).encode("utf-8")
        else:
            self._body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _patch_urlopen(resp):
    return patch("urllib.request.urlopen", return_value=resp)


class TestInitiatePairing:
    def test_happy_path_returns_pending_pairing(self):
        # Pinned fp matches server response → initiate succeeds.
        with _patch_urlopen(_FakeResp(200, _initiate_response())):
            pending = initiate_pairing(
                "wss://relay.test",
                pinned_trust_key_fp="ff" * 32,
                hostname="alpha",
                os_name="Linux",
            )

        assert pending.nonce == "n1"
        assert pending.relay_url == "https://relay.test"
        assert pending.trust_key_fp == "ff" * 32
        assert pending.qr_payload.startswith("MOBA1|")
        # WS URL is derived from RELAY_SERVER_URL (the wss:// arg above) +
        # the response nonce — never read from the server response. Pin
        # the exact value so a regression that re-introduces a
        # server-supplied ws_url field gets caught here.
        assert pending.ws_url == "wss://relay.test/ws/pairing/n1"

    def test_ws_url_ignores_buggy_server_supplied_https(self):
        # Regression guard for the offline-after-pairing bug: a deployed
        # central_server that erroneously hands back ``https://`` as
        # ``ws_url`` would break the websockets client. The agent now
        # derives the URL locally from RELAY_SERVER_URL + nonce, so a
        # buggy server-side field is simply ignored.
        buggy = _initiate_response(
            ws_url="https://relay.test/ws/pairing/n1",  # Pretend stale server.
        )
        with _patch_urlopen(_FakeResp(200, buggy)):
            pending = initiate_pairing(
                "wss://relay.test",
                pinned_trust_key_fp="ff" * 32,
            )

        assert pending.ws_url == "wss://relay.test/ws/pairing/n1"
        assert pending.ws_url.startswith("wss://")

    def test_fp_mismatch_raises_pairing_monitor_error(self):
        # Server announces a different fingerprint than the agent has
        # pinned → fail-closed; do not render a QR a mobile can't
        # safely scan.
        with _patch_urlopen(
            _FakeResp(200, _initiate_response(trust_key_fp="aa" * 32))
        ):
            with pytest.raises(
                PairingMonitorError, match="fingerprint mismatch"
            ):
                initiate_pairing(
                    "wss://relay.test",
                    pinned_trust_key_fp="ff" * 32,
                )

    def test_malformed_qr_payload_raises(self):
        with _patch_urlopen(
            _FakeResp(
                200, _initiate_response(qr_payload="MOBA0|bad|payload")
            )
        ):
            with pytest.raises(
                PairingMonitorError, match="malformed QR payload"
            ):
                initiate_pairing(
                    "wss://relay.test",
                    pinned_trust_key_fp="ff" * 32,
                )

    def test_includes_metadata_in_request_body(self):
        captured: dict = {}

        def _fake_urlopen(req, timeout=None):
            captured["data"] = json.loads(req.data.decode("utf-8"))
            return _FakeResp(200, _initiate_response())

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            initiate_pairing(
                "wss://relay.test",
                pinned_trust_key_fp="ff" * 32,
                hostname="alpha",
                os_name="Linux",
                current_agent_id="agent-prior",
            )

        assert captured["data"] == {
            "hostname": "alpha",
            "os": "Linux",
            "current_agent_id": "agent-prior",
        }

    def test_omits_unset_metadata_fields(self):
        # None values are dropped; only the keys the agent actually
        # supplied land in the JSON body.
        captured: dict = {}

        def _fake_urlopen(req, timeout=None):
            captured["data"] = json.loads(req.data.decode("utf-8"))
            return _FakeResp(200, _initiate_response())

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            initiate_pairing(
                "wss://relay.test",
                pinned_trust_key_fp="ff" * 32,
            )

        assert captured["data"] == {}

    def test_outbound_request_carries_mobaide_user_agent(self):
        # Regression guard for the Cloudflare 1010 UA block: ``initiate_pairing`` must route through
        # ``mobaide._http.urlopen`` so Cloudflare's default Bot Fight Mode
        # doesn't 403 the request based on the ``Python-urllib/X.Y`` UA.
        captured: dict = {}

        def _fake_urlopen(req, timeout=None):
            captured["request"] = req
            return _FakeResp(200, _initiate_response())

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            initiate_pairing(
                "wss://relay.test",
                pinned_trust_key_fp="ff" * 32,
            )

        ua = captured["request"].get_header("User-agent")
        assert ua is not None and ua.startswith("mobaide-agent/")
        # Caller-supplied Content-Type must not have been clobbered by the
        # wrapper's header merge.
        assert (
            captured["request"].get_header("Content-type")
            == "application/json"
        )


class TestPairingWsUrlHelper:
    @pytest.mark.parametrize(
        "relay_url,expected",
        [
            ("wss://relay.example.com",
             "wss://relay.example.com/ws/pairing/N1"),
            ("wss://relay.example.com:9000",
             "wss://relay.example.com:9000/ws/pairing/N1"),
            ("https://relay.example.com",
             "wss://relay.example.com/ws/pairing/N1"),
            ("https://relay.example.com:9000",
             "wss://relay.example.com:9000/ws/pairing/N1"),
        ],
    )
    def test_secure_scheme_translation(self, relay_url, expected):
        assert _pairing_ws_url(relay_url, "N1") == expected

    def test_path_query_fragment_dropped(self):
        # WS endpoint is pinned at /ws/pairing/{nonce}; any path on the
        # input relay URL is intentionally discarded — the relay tunnel
        # path and the WS endpoint live at the same origin root.
        out = _pairing_ws_url(
            "https://relay.example.com/some/path?q=1#frag", "abc",
        )

        assert out == "wss://relay.example.com/ws/pairing/abc"

    def test_unsupported_scheme_raises(self):
        with pytest.raises(ValueError, match="unsupported scheme"):
            _pairing_ws_url("ftp://relay.example.com", "n")

    def test_missing_host_raises(self):
        with pytest.raises(ValueError, match="not a valid URL"):
            _pairing_ws_url("https://", "n")

    def test_user_info_rejected(self):
        # urlparse('wss://api@evil.example') resolves to evil.example;
        # refuse upfront to mirror _trust_key_url_from_relay_server_url.
        with pytest.raises(ValueError, match="user-info"):
            _pairing_ws_url(
                "wss://attacker@relay.example.com", "n",
            )

    def test_cleartext_refused_by_default(self, monkeypatch):
        monkeypatch.delenv(
            "MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH",
            raising=False,
        )
        with pytest.raises(ValueError, match="cleartext"):
            _pairing_ws_url("ws://relay.example.com", "n")
        with pytest.raises(ValueError, match="cleartext"):
            _pairing_ws_url("http://localhost:8080", "n")

    def test_cleartext_allowed_with_insecure_env_var(self, monkeypatch):
        monkeypatch.setenv(
            "MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH", "1",
        )
        assert (
            _pairing_ws_url("ws://localhost", "N1")
            == "ws://localhost/ws/pairing/N1"
        )
        assert (
            _pairing_ws_url("http://localhost:8080", "N1")
            == "ws://localhost:8080/ws/pairing/N1"
        )


class TestWriteCredentialsToEnv:
    @pytest.fixture
    def env_path(self, tmp_path) -> Path:
        path = tmp_path / "agent.env"
        return path

    async def test_writes_id_and_token_overriding_existing(
        self, env_path: Path
    ):
        env_path.write_text(
            "RELAY_AGENT_ID=old-id\n"
            "RELAY_AGENT_TOKEN=old-token\n"
            "RELAY_SERVER_URL=wss://relay.existing\n",
            encoding="utf-8",
        )
        claim = PairingClaim(
            agent_id="new-id",
            agent_token="new-token",
            relay_url="https://relay.test",
            agent_friendly_name="lab",
            issued_at="2026-05-07T00:00:00+00:00",
        )

        await write_credentials_to_env(env_path, claim)

        contents = env_path.read_text(encoding="utf-8")
        assert "RELAY_AGENT_ID=new-id" in contents
        assert "RELAY_AGENT_TOKEN=new-token" in contents
        # Did NOT overwrite a non-empty RELAY_SERVER_URL.
        assert "RELAY_SERVER_URL=wss://relay.existing" in contents

    async def test_backfills_relay_server_url_when_missing(
        self, env_path: Path
    ):
        env_path.write_text(
            "RELAY_AGENT_ID=\nRELAY_AGENT_TOKEN=\nRELAY_SERVER_URL=\n",
            encoding="utf-8",
        )
        claim = PairingClaim(
            agent_id="id1",
            agent_token="tok1",
            relay_url="https://relay.test",
            agent_friendly_name="lab",
            issued_at="2026-05-07T00:00:00+00:00",
        )

        await write_credentials_to_env(env_path, claim)

        contents = env_path.read_text(encoding="utf-8")
        assert "RELAY_SERVER_URL=https://relay.test" in contents

    async def test_writes_to_nonexistent_env_file(self, env_path: Path):
        # No pre-existing file: the writer creates one carrying the new
        # credentials. (init-env is a separate prerequisite, but the
        # writer itself must not crash on a missing target.)
        claim = PairingClaim(
            agent_id="i", agent_token="t",
            relay_url="https://relay.test",
            agent_friendly_name="x",
            issued_at="2026-05-07T00:00:00+00:00",
        )

        await write_credentials_to_env(env_path, claim)

        text = env_path.read_text(encoding="utf-8")
        assert "RELAY_AGENT_ID=i" in text
        assert "RELAY_AGENT_TOKEN=t" in text


class TestRunPairingMonitorWriteFailureHandling:
    """Pin the defence-in-depth ``try/except OSError`` around the
    ``write_credentials_to_env`` call inside ``run_pairing_monitor``.

    The original Windows pairing bug surfaced precisely because an
    unhandled ``PermissionError`` (from ``os.replace`` on a
    ``SYSTEM:R``-locked ``.env``) escaped the monitor task. The task is
    spawned via ``asyncio.create_task`` from ``app.py`` and never
    awaited, so the exception silently killed the task — the agent
    stayed in ``pairing_pending`` forever and operators saw no
    traceback in ``agent.err.log``.

    The fix wraps the call in ``except OSError`` and emits a structured
    ``pairing_monitor.write_failed`` log line plus a backoff-and-retry.
    These tests pin that contract so a future refactor that drops or
    narrows the handler reintroduces a visible test failure rather than
    a silent regression of the original bug.
    """

    @pytest.fixture(autouse=True)
    def _fast_backoff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The retry path sleeps for ``_BACKOFF_FLOOR_S`` (2s in prod).
        # Compress to make the test fast without changing the
        # behavioural contract we're pinning.
        monkeypatch.setattr(_pm, "_BACKOFF_FLOOR_S", 0.01)

    def _make_layout(self, tmp_path: Path) -> InstallLayout:
        prefix = tmp_path / "prefix"
        (prefix / "mobaide_agent").mkdir(parents=True, exist_ok=True)
        return InstallLayout(prefix=prefix)

    def _make_pending(self) -> PendingPairing:
        # ``exp_unix`` far in the future so ``parse_qr_payload``'s
        # validity check in the loop is satisfied without freezing time.
        return PendingPairing(
            nonce="n1",
            expires_at="2999-01-01T00:00:00+00:00",
            trust_key_fp="ff" * 32,
            ws_url="wss://relay.test/ws/pairing/n1",
            relay_url="https://relay.test",
            qr_payload=f"MOBA1|https://relay.test|n1|{'ff' * 32}|2999999999",
        )

    def _make_claim(self) -> PairingClaim:
        return PairingClaim(
            agent_id="new-agent-id",
            agent_token="new-agent-token",
            relay_url="https://relay.test",
            agent_friendly_name="lab",
            issued_at="2026-05-07T00:00:00+00:00",
        )

    def _install_loop_stubs(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        write_side_effects: list,
    ) -> dict:
        """Stub every collaborator ``run_pairing_monitor`` reaches so the
        loop body can be driven deterministically.

        ``write_side_effects`` is consumed one entry per
        ``write_credentials_to_env`` call: ``None`` => success (write
        finishes), an exception instance => raise it. The list is the
        contract: as many cycles as the test expects, in order.
        """
        recorder: dict = {
            "clear_calls": 0,
            "on_paired_calls": 0,
            "write_calls": 0,
            "save_calls": 0,
        }
        pending = self._make_pending()
        claim = self._make_claim()

        async def _stub_initiate(
            relay_server_url, layout, *,
            hostname, os_name, current_agent_id,
        ):
            return pending

        def _stub_load(_layout):
            # Always start fresh — force the initiate path through
            # ``_stub_initiate`` each iteration. Verifies the loop
            # actually re-enters the cycle on the OSError branch.
            return None

        def _stub_save(_layout, _pending):
            recorder["save_calls"] += 1

        def _stub_clear(_layout):
            recorder["clear_calls"] += 1

        async def _stub_render(_pending):
            return None

        class _StubWsClient:
            async def wait_for_claim(self, **_kwargs):
                return claim

        async def _stub_write(_env_path, _claim):
            recorder["write_calls"] += 1
            if not write_side_effects:
                raise AssertionError(
                    "write_side_effects exhausted: the loop called "
                    "write_credentials_to_env more times than the test "
                    "configured"
                )

            effect = write_side_effects.pop(0)
            if isinstance(effect, BaseException):
                raise effect
            # None => success path

        def _on_paired() -> None:
            recorder["on_paired_calls"] += 1

        recorder["on_paired"] = _on_paired

        monkeypatch.setattr(
            _pm, "read_trust_public_key", lambda _layout: "stub-pub-key"
        )
        monkeypatch.setattr(_pm, "_initiate_with_metadata", _stub_initiate)
        monkeypatch.setattr(_pm, "load_pending_pairing", _stub_load)
        monkeypatch.setattr(_pm, "save_pending_pairing", _stub_save)
        monkeypatch.setattr(_pm, "clear_pending_pairing", _stub_clear)
        monkeypatch.setattr(_pm, "_render_pending_to_stdout", _stub_render)
        monkeypatch.setattr(_pm, "PairingWsClient", _StubWsClient)
        monkeypatch.setattr(_pm, "write_credentials_to_env", _stub_write)
        return recorder

    async def test_oserror_during_write_does_not_crash_task(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # First cycle: write raises PermissionError (the exact original
        # bug surface on Windows under ``SYSTEM:R``). Second cycle:
        # write succeeds, ``on_paired`` fires, loop exits. The task
        # must complete without re-raising — that's the contract the
        # ``except OSError`` clause provides.
        recorder = self._install_loop_stubs(
            monkeypatch,
            write_side_effects=[
                PermissionError("simulated SYSTEM:R blocking os.replace"),
                None,
            ],
        )
        layout = self._make_layout(tmp_path)

        with caplog.at_level(logging.ERROR, logger=_pm.__name__):
            await asyncio.wait_for(
                run_pairing_monitor(
                    layout=layout,
                    relay_server_url="wss://relay.test",
                    on_paired=recorder["on_paired"],
                ),
                timeout=5.0,
            )

        # The loop entered the write step twice (first raised, second
        # succeeded). If the OSError had escaped, the second call
        # would never have happened.
        assert recorder["write_calls"] == 2, (
            f"expected exactly two write attempts (raise + succeed); "
            f"got {recorder['write_calls']}"
        )
        # Final cycle's success path called ``on_paired`` exactly once.
        assert recorder["on_paired_calls"] == 1

    async def test_oserror_during_write_logs_structured_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # The handler MUST emit a ``pairing_monitor.write_failed`` log
        # line carrying the env_path and the exception message. The
        # whole point of the try/except is to make the original silent-
        # death failure mode visible in ``agent.err.log``; if the log
        # is dropped, the visibility guarantee is dropped.
        recorder = self._install_loop_stubs(
            monkeypatch,
            write_side_effects=[
                PermissionError("simulated permission denied"),
                None,
            ],
        )
        layout = self._make_layout(tmp_path)

        with caplog.at_level(logging.ERROR, logger=_pm.__name__):
            await asyncio.wait_for(
                run_pairing_monitor(
                    layout=layout,
                    relay_server_url="wss://relay.test",
                    on_paired=recorder["on_paired"],
                ),
                timeout=5.0,
            )

        error_messages = [
            r.getMessage()
            for r in caplog.records
            if r.levelno >= logging.ERROR
        ]
        # The token "pairing_monitor.write_failed" is the structured
        # event name operators grep for; pin the literal string so a
        # rename of the event needs an explicit test update (which
        # forces a conversation about whether the dashboards that key
        # off the name also need updating).
        assert any(
            "pairing_monitor.write_failed" in msg
            for msg in error_messages
        ), (
            f"expected 'pairing_monitor.write_failed' in ERROR logs; "
            f"got: {error_messages!r}"
        )
        assert any(
            "simulated permission denied" in msg
            for msg in error_messages
        ), (
            "the exception message must be propagated into the log "
            "line so operators can see the actual cause (was the "
            "ACL? a disk-full? a read-only mount?) — without it the "
            "fix only converts one silent failure into another"
        )

    async def test_oserror_during_write_clears_pending_before_retry(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The retry contract is "clear the pending-pairing record so
        # the next cycle re-initiates with a fresh nonce". If we
        # retried with the SAME pending record, the WS would
        # eventually reject the now-expired nonce and the user would
        # have to wait through the longer initiate-backoff cycle
        # before another QR appeared. ``clear_pending_pairing`` on
        # the failure path is the user-visible recovery handle.
        recorder = self._install_loop_stubs(
            monkeypatch,
            write_side_effects=[
                OSError("simulated disk-full"),
                None,
            ],
        )
        layout = self._make_layout(tmp_path)

        await asyncio.wait_for(
            run_pairing_monitor(
                layout=layout,
                relay_server_url="wss://relay.test",
                on_paired=recorder["on_paired"],
            ),
            timeout=5.0,
        )

        # First cycle: failure path -> clear (1). Second cycle:
        # success path -> clear (2). Two clears total proves the
        # failure path performed its own clear instead of relying on
        # the success path's clear that it would never reach.
        assert recorder["clear_calls"] == 2, (
            f"expected clear_pending_pairing on both cycles (failure "
            f"+ success), got {recorder['clear_calls']} calls"
        )

    async def test_oserror_during_write_skips_on_paired_for_that_cycle(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # ``on_paired`` triggers ``sys.exit(EXIT_RESTART_REQUESTED)``
        # in production — if the failure path accidentally called
        # ``on_paired`` AND THEN retried, the service would exit
        # before the retry could run, leaving the agent in the same
        # unpaired-credentials state but now with the service down.
        # The handler's ``continue`` (not ``break``, not fall-through)
        # is load-bearing; this test pins it.
        #
        # We let the loop run for a short bounded window with the
        # write ALWAYS failing — that way we can prove ``on_paired``
        # never fired even given several retries. A timeout is the
        # expected exit signal here, not a failure.
        recorder = self._install_loop_stubs(
            monkeypatch,
            # Effectively-infinite supply of OSError side-effects.
            # The loop runs until the asyncio timeout below.
            write_side_effects=[OSError("simulated failure")] * 1000,
        )
        layout = self._make_layout(tmp_path)

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                run_pairing_monitor(
                    layout=layout,
                    relay_server_url="wss://relay.test",
                    on_paired=recorder["on_paired"],
                ),
                timeout=0.1,
            )

        # Several write attempts happened, all raised — and yet
        # ``on_paired`` must not have fired even once. A regression
        # that drops the ``continue`` in the OSError branch would let
        # control fall through to the ``on_paired()`` call below the
        # try/except in production and this assertion would break.
        assert recorder["write_calls"] >= 1, (
            "loop should have reached the write step at least once"
        )
        assert recorder["on_paired_calls"] == 0, (
            f"on_paired must NOT be called on the OSError branch; "
            f"got {recorder['on_paired_calls']} invocations"
        )

    async def test_non_oserror_during_write_still_propagates(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The handler is narrow on purpose: ``except OSError`` covers
        # the filesystem failures the defence is designed for
        # (PermissionError, FileNotFoundError, IsADirectoryError, ...)
        # and explicitly does NOT swallow programmer errors like
        # AssertionError, TypeError, ValueError. Swallowing those
        # would hide real bugs (e.g. a renamed field on
        # ``PairingClaim``) behind a generic "will retry" log line
        # that keeps the agent stuck without surfacing the actual
        # cause.
        recorder = self._install_loop_stubs(
            monkeypatch,
            write_side_effects=[
                ValueError("simulated programmer error"),
            ],
        )
        layout = self._make_layout(tmp_path)

        with pytest.raises(ValueError, match="simulated programmer error"):
            await asyncio.wait_for(
                run_pairing_monitor(
                    layout=layout,
                    relay_server_url="wss://relay.test",
                    on_paired=recorder["on_paired"],
                ),
                timeout=5.0,
            )

        # Single write attempt: the ValueError escaped immediately,
        # the loop did not retry.
        assert recorder["write_calls"] == 1
        assert recorder["on_paired_calls"] == 0
