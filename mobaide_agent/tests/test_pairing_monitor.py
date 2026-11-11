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
import contextlib
import json
import logging
import time
import types
from pathlib import Path
from unittest.mock import patch

import pytest
from websockets.datastructures import Headers
from websockets.exceptions import InvalidHandshake, InvalidMessage, InvalidStatus
from websockets.http11 import Response

import mobaide.agent.pairing_monitor as _pm
from mobaide.agent.pairing_monitor import (
    PairingMonitorError,
    _handshake_status,
    _pairing_ws_url,
    initiate_pairing,
    run_pairing_monitor,
    write_credentials_to_env,
)
from mobaide.agent.pairing_qr import PendingPairing
from mobaide.agent.pairing_ws import PairingClaim, PairingWsError
from mobaide.agent.updater import InstallLayout


def _make_two_phase_ws_client(claim_or_exc):
    """Build a stub ``PairingWsClient`` honouring the two-phase API.

    Production code uses ``async with PairingWsClient.connect(url) as conn``
    then ``await conn.wait_for_claim(...)``. Tests parametrise the
    inner ``wait_for_claim`` behaviour by passing either a ready
    ``PairingClaim`` (returned from the connection) or a callable
    consumed once per call.
    """

    class _StubConn:
        async def wait_for_claim(self, **_kwargs):
            if callable(claim_or_exc):
                return claim_or_exc()
            if isinstance(claim_or_exc, BaseException):
                raise claim_or_exc
            return claim_or_exc

    class _StubWsClient:
        @classmethod
        @contextlib.asynccontextmanager
        async def connect(cls, _ws_url):
            yield _StubConn()

    return _StubWsClient


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

        def _fake_urlopen(req, timeout=None, **_kwargs):
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

    def test_outbound_request_carries_mobaide_user_agent(self):
        # Regression guard for the Cloudflare 1010 UA block: ``initiate_pairing`` must route through
        # ``mobaide._http.urlopen`` so Cloudflare's default Bot Fight Mode
        # doesn't 403 the request based on the ``Python-urllib/X.Y`` UA.
        captured: dict = {}

        def _fake_urlopen(req, timeout=None, **_kwargs):
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


class _LegacyInvalidStatusCode(InvalidHandshake):
    """Stand-in for websockets 13 ``InvalidStatusCode`` (``status_code`` attr)."""

    def __init__(self, status_code) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


class TestHandshakeStatusHelper:
    @pytest.mark.parametrize(
        ("cause", "expected"),
        [
            pytest.param(
                InvalidStatus(Response(403, "Forbidden", Headers(), b"")),
                403,
                id="invalid-status-response",
            ),
            pytest.param(
                _LegacyInvalidStatusCode(502), 502, id="legacy-status-code"
            ),
            pytest.param(
                _LegacyInvalidStatusCode("403"), None, id="non-int-status"
            ),
            pytest.param(
                InvalidMessage("did not receive a valid HTTP response"),
                None,
                id="handshake-without-status",
            ),
            pytest.param(OSError("connection refused"), None, id="not-handshake"),
        ],
    )
    def test_reads_the_rejected_handshake_status(self, cause, expected):
        assert _handshake_status(cause) == expected


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

    async def test_preserves_claude_bin_when_rewriting_credentials(
        self, env_path: Path
    ):
        # Critical contract for the Windows-service flow: the installer
        # writes ``MOBAIDE_CLAUDE_BIN=<path>`` BEFORE pairing happens
        # (init-env --claude-bin), then the mobile-app pairing flow
        # later rewrites the same .env to land RELAY_AGENT_ID/TOKEN.
        # ``write_credentials_to_env`` MUST NOT drop the claude_bin
        # line — otherwise pairing would silently un-set the resolver
        # override and session creation would 503 with
        # claude_cli_missing on the next launch. The rewrite is a full
        # render_template walk (pairing_monitor.py:222); set-env-style
        # surgical updates do not apply here.
        env_path.write_text(
            "JWT_SECRET_KEY=existing-secret\n"
            "MOBAIDE_CLAUDE_BIN=/abs/path/to/claude\n"
            "RELAY_SERVER_URL=wss://relay.existing\n"
            "RELAY_AGENT_ID=\n"
            "RELAY_AGENT_TOKEN=\n",
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
        # Pairing creds landed.
        assert "RELAY_AGENT_ID=new-id" in contents
        assert "RELAY_AGENT_TOKEN=new-token" in contents
        # And the claude binary override survived, byte-for-byte.
        assert "MOBAIDE_CLAUDE_BIN=/abs/path/to/claude" in contents
        # The pre-existing JWT secret stays put too (the rewrite must
        # not rotate it; the same pattern as RELAY_SERVER_URL).
        assert "JWT_SECRET_KEY=existing-secret" in contents


class _RunPairingMonitorTestBase:
    """Shared scaffolding for the two ``run_pairing_monitor`` integration
    test classes (write-failure and ws-failure).

    Each child class supplies its own ``_install_*_stubs`` helper that
    constructs the scenario-specific WS client class and write stub,
    then delegates the seven monkeypatched collaborator hooks plus the
    shared layout / pending fixtures to this base. The shared helpers
    avoid drifting the seven ``monkeypatch.setattr`` lines across two
    classes; the scenario-specific bits stay in the children where the
    difference is locally visible.
    """

    @pytest.fixture(autouse=True)
    def _fast_backoff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The retry path sleeps for ``_BACKOFF_FLOOR_S`` (2s in prod).
        # Compress to make the test fast without changing the
        # behavioural contract we're pinning. ``_BACKOFF_CEIL_S`` is
        # left at the production value (60s) because no test exercises
        # the ceiling-clamped path; bringing it down would erase the
        # cap-vs-uncapped distinction the perf tests rely on.
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

    def _wire_base_collaborators(
        self,
        monkeypatch: pytest.MonkeyPatch,
        recorder: dict,
        *,
        pending: PendingPairing,
        ws_client_class: type,
        write_stub,
    ) -> None:
        """Install the seven scenario-independent monkeypatches.

        The five inner stubs (initiate, load, save, clear, render) plus
        the trust-key lambda and the on_paired closure are byte-identical
        across the write-failure and ws-failure scenarios; only the WS
        client class and the write stub differ. Children pass those in
        and this method installs the full set.

        Mutates ``recorder`` to attach the on_paired closure as
        ``recorder["on_paired"]`` so the test body has a callable to
        forward into ``run_pairing_monitor``.
        """

        async def _stub_initiate(
            relay_server_url, layout, *,
            hostname, os_name, current_agent_id,
        ):
            return pending

        def _stub_load(_layout):
            # Always start fresh — force the initiate path through
            # ``_stub_initiate`` each iteration. The save_calls counter
            # then ticks once per outer-loop pass, which both child
            # scenarios use as a proxy for "the loop actually cycled".
            return None

        def _stub_save(_layout, _pending):
            recorder["save_calls"] = recorder.get("save_calls", 0) + 1

        def _stub_clear(_layout):
            recorder["clear_calls"] = recorder.get("clear_calls", 0) + 1

        async def _stub_render(_pending):
            return None

        async def _on_paired(received_claim) -> None:
            recorder["on_paired_calls"] = (
                recorder.get("on_paired_calls", 0) + 1
            )
            recorder["on_paired_claim"] = received_claim
        recorder["on_paired"] = _on_paired

        monkeypatch.setattr(
            _pm, "read_trust_public_key", lambda _layout: "stub-pub-key"
        )
        monkeypatch.setattr(_pm, "_initiate_with_metadata", _stub_initiate)
        monkeypatch.setattr(_pm, "load_pending_pairing", _stub_load)
        monkeypatch.setattr(_pm, "save_pending_pairing", _stub_save)
        monkeypatch.setattr(_pm, "clear_pending_pairing", _stub_clear)
        monkeypatch.setattr(_pm, "_render_pending_to_stdout", _stub_render)
        monkeypatch.setattr(_pm, "PairingWsClient", ws_client_class)
        monkeypatch.setattr(_pm, "write_credentials_to_env", write_stub)


class TestRunPairingMonitorWriteFailureHandling(_RunPairingMonitorTestBase):
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
            "on_paired_claim": None,
            "write_calls": 0,
            "save_calls": 0,
        }
        pending = self._make_pending()
        claim = self._make_claim()

        _StubWsClient = _make_two_phase_ws_client(claim)

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

        self._wire_base_collaborators(
            monkeypatch, recorder,
            pending=pending,
            ws_client_class=_StubWsClient,
            write_stub=_stub_write,
        )
        return recorder

    async def test_write_failure_retries_inline_and_does_not_clear_pending(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The post-fix retry contract is "hold the claim in memory and
        # retry the WRITE in an inner loop — never fall back to the outer
        # WS loop on write failure". The claim is single-use server-side:
        # ``claim_pairing_nonce`` consumed the nonce and stored
        # ``claimed_token_raw``, then the WS push (or recovery read)
        # delivered the payload and cleared ``claimed_token_raw``. After
        # that point the server has no way to re-deliver — a fresh WS
        # dial on a new nonce would orphan the just-received credentials.
        #
        # Concrete pin: ``clear_pending_pairing`` is called EXACTLY ONCE
        # on the success path (after the inner retry breaks out), never
        # on the write-failure path. The prior contract (clear-then-
        # re-initiate on write failure) caused the user-reported bug:
        # on a Cloudflare-induced WS drop the agent moved to a new nonce
        # while the mobile-supplied credentials sat stranded on the old
        # one, and ``.env`` stayed empty forever.
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

        # Exactly one clear, on the success path. A regression that
        # re-introduces ``clear_pending_pairing`` in the write-failure
        # branch would push this to 2; a regression that drops the
        # success-path clear entirely would push this to 0 (and the
        # next cold boot would re-dial the just-paired nonce, getting
        # a server-side 4410 "already delivered" close). Both are
        # independently load-bearing.
        assert recorder["clear_calls"] == 1, (
            f"expected exactly one clear_pending_pairing on the success "
            f"path; got {recorder['clear_calls']}"
        )
        # The WS was dialled exactly once: the inner retry never falls
        # back to a fresh wait-for-claim, so no second initiate happens.
        # ``_install_loop_stubs`` records ``save_calls`` per
        # ``save_pending_pairing`` invocation, which is the per-iteration
        # signal of "the outer loop initiated a new nonce".
        assert recorder["save_calls"] == 1, (
            f"expected exactly one save_pending_pairing (one outer loop "
            f"iteration); got {recorder['save_calls']} — a regression "
            f"that re-enters the WS loop on write failure would push "
            f"this above 1 and orphan the stranded server-side claim"
        )
        # Both writes executed: the failure path retried in-place.
        assert recorder["write_calls"] == 2, (
            f"expected 2 write attempts (one failure + one success in "
            f"the inner retry loop); got {recorder['write_calls']}"
        )
        # The success-path handoff fired exactly once.
        assert recorder["on_paired_calls"] == 1, (
            f"expected exactly one on_paired call on the successful "
            f"write; got {recorder['on_paired_calls']}"
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

    async def test_non_oserror_during_write_is_caught_and_retried(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # The handler is intentionally broad: ``except Exception`` catches
        # every non-Cancellation failure so the pairing-monitor task
        # never dies silently. ``OSError`` covers the filesystem failure
        # modes the defence was originally designed for, but
        # ``write_credentials_to_env`` can also raise
        # ``UnicodeDecodeError`` on a corrupt-encoding ``.env`` or
        # ``ValueError`` from ``render_template``'s control-character
        # injection guard — neither of which is an ``OSError`` subclass.
        # Narrowing the handler to ``OSError`` previously let those
        # propagate and silently killed the asyncio task (because the
        # task is ``asyncio.create_task``'d but never awaited), which
        # was exactly the silent-failure mode the wrapper exists to
        # eliminate.
        recorder = self._install_loop_stubs(
            monkeypatch,
            write_side_effects=[
                ValueError("simulated control-character guard fired"),
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

        # Two attempts: the first raised ValueError, the loop caught
        # it via the broadened handler, retried, and the second
        # succeeded. If the handler were still narrow on OSError, the
        # ValueError would have escaped immediately, the pairing-monitor
        # task would have died, and the second write attempt would
        # never have happened.
        assert recorder["write_calls"] == 2, (
            f"expected exactly two write attempts (raise + succeed); "
            f"got {recorder['write_calls']} — a narrowed handler would "
            f"have produced exactly 1"
        )
        assert recorder["on_paired_calls"] == 1, (
            "second write succeeded, so on_paired must have fired once"
        )

        # The structured log line names the actual exception type
        # (``ValueError`` here), so an operator grepping
        # ``agent.err.log`` can tell ``ValueError`` from
        # ``PermissionError`` from ``UnicodeDecodeError`` and route to
        # the right cause without re-running the install.
        error_messages = [
            r.getMessage()
            for r in caplog.records
            if r.levelno >= logging.ERROR
        ]
        assert any(
            "pairing_monitor.write_failed" in msg
            and "ValueError" in msg
            for msg in error_messages
        ), (
            f"expected 'pairing_monitor.write_failed ... ValueError' "
            f"in ERROR logs; got: {error_messages!r}"
        )

    async def test_on_paired_async_callback_exception_does_not_crash_loop(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # The callback runs INSIDE the pairing-monitor task. If the
        # production callback raises (e.g. relay-client construction
        # fails because central_server is unreachable), the task itself
        # must not die — the ``except Exception`` around the await is
        # what makes the in-process transition's "fall back to
        # sys.exit(75)" branch in ``app.py``'s ``_on_paired`` reachable.
        # If a future refactor drops the wrapper, the task would die
        # silently between writes and the user-visible symptom would be
        # "QR scan succeeds but agent never comes online" — silently.
        captured: list[BaseException] = []

        async def _exploding_on_paired(_claim) -> None:
            exc = RuntimeError("simulated callback failure")
            captured.append(exc)
            raise exc

        # We install the stubs only to satisfy run_pairing_monitor's
        # collaborators — the on_paired callback is supplied explicitly
        # below, so the recorder's _on_paired entry is irrelevant here.
        self._install_loop_stubs(
            monkeypatch,
            write_side_effects=[None],
        )
        layout = self._make_layout(tmp_path)

        with caplog.at_level(logging.WARNING, logger=_pm.__name__):
            # The function MUST return cleanly — the failure is logged
            # and swallowed. ``asyncio.wait_for`` raises on timeout, so
            # a hanging task would fail the test loudly.
            await asyncio.wait_for(
                run_pairing_monitor(
                    layout=layout,
                    relay_server_url="wss://relay.test",
                    on_paired=_exploding_on_paired,
                ),
                timeout=5.0,
            )

        assert len(captured) == 1, (
            "exploding on_paired must have been called exactly once"
        )
        # The exception is logged at WARNING via
        # ``pairing_monitor.on_paired_callback_failed`` — pins the
        # operator-visibility contract.
        warning_messages = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.WARNING
        ]
        assert any(
            "pairing_monitor.on_paired_callback_failed" in msg
            for msg in warning_messages
        ), (
            f"expected 'pairing_monitor.on_paired_callback_failed' "
            f"WARNING log; got: {warning_messages!r}"
        )

    async def test_write_failure_backoff_grows_between_attempts(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The inner write-retry loop applies a GROWING backoff between
        # successive failed write attempts. The pre-growth code slept
        # exactly ``_BACKOFF_FLOOR_S`` (2 s) between every retry, which
        # emits ~30 ERROR lines/min under a persistent ACL or disk-full
        # condition — enough to fill the operator's log disk overnight.
        # The post-fix loop doubles the write-retry sleep on each
        # failure (capped at ``_BACKOFF_CEIL_S``) so steady-state error
        # log volume is bounded.
        #
        # We pin the GROWTH DIRECTION (strictly increasing successive
        # sleeps) rather than the exact 2x factor or cap value, so a
        # future tuning of the growth rate doesn't force a test edit.
        # The signal that's load-bearing is "growth is happening at all".
        captured_sleeps: list[float] = []
        real_sleep = asyncio.sleep

        async def _capturing_sleep(duration: float) -> None:
            captured_sleeps.append(duration)
            # Yield control without burning real wall-clock time so
            # the bounded run can collect many iterations quickly.
            await real_sleep(0)

        # Replace the module-bound ``asyncio`` reference with a
        # SimpleNamespace shim. The production code does
        # ``await asyncio.sleep(...)`` and ``await asyncio.to_thread(...)``
        # via attribute lookup on the module-level ``asyncio`` name, so
        # patching ``_pm.asyncio`` redirects only this module's sleeps
        # without mutating the global ``asyncio`` module (which would
        # also break ``asyncio.wait_for`` below).
        fake_asyncio = types.SimpleNamespace(
            **{
                name: getattr(asyncio, name)
                for name in dir(asyncio)
                if not name.startswith("__")
            }
        )
        fake_asyncio.sleep = _capturing_sleep
        monkeypatch.setattr(_pm, "asyncio", fake_asyncio)

        # Five write failures + one success — enough to observe four
        # growth steps (sleep is captured AFTER each failure, so 5
        # failures yield 5 captured write-retry sleeps).
        recorder = self._install_loop_stubs(
            monkeypatch,
            write_side_effects=[
                OSError("simulated transient failure 1"),
                OSError("simulated transient failure 2"),
                OSError("simulated transient failure 3"),
                OSError("simulated transient failure 4"),
                OSError("simulated transient failure 5"),
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

        # Sanity check: every captured sleep happened inside the inner
        # write-retry loop because the WS path delivers a claim on the
        # first call (no WS-failure backoff captured) and the success
        # path doesn't sleep. So all captures are write-retry sleeps.
        assert recorder["write_calls"] == 6, (
            f"expected 6 write attempts (5 failures + 1 success); "
            f"got {recorder['write_calls']}"
        )
        assert len(captured_sleeps) >= 4, (
            f"expected at least 4 captured write-retry sleeps "
            f"(one per failed write attempt); got {len(captured_sleeps)}: "
            f"{captured_sleeps!r}"
        )

        # Smoking-gun pin: take the first five captured sleeps (one per
        # failure) and assert each is strictly greater than its
        # predecessor. A regression that drops the growth math
        # (``write_backoff = min(_BACKOFF_CEIL_S, write_backoff * 2)``)
        # and reverts to a fixed-floor sleep would produce equal values
        # and trip this assertion immediately. We deliberately don't
        # assert the exact ratio (e.g. 2.0x) so future tuning of the
        # growth rate (1.5x, 1.8x, …) doesn't require a test edit.
        retry_sleeps = captured_sleeps[:5]
        for i in range(1, len(retry_sleeps)):
            assert retry_sleeps[i] > retry_sleeps[i - 1], (
                f"write-retry sleep #{i + 1} ({retry_sleeps[i]}) "
                f"must be strictly greater than #{i} "
                f"({retry_sleeps[i - 1]}); full sequence: "
                f"{retry_sleeps!r}. A regression that drops the "
                f"exponential growth math would emit a constant sleep "
                f"of _BACKOFF_FLOOR_S and produce equal values here."
            )


class TestRunPairingMonitorWsFailureHandling(_RunPairingMonitorTestBase):
    """Pin the post-fix WS-failure branch contract.

    Before the fix, a transient WS drop (CDN idle-close, TLS reset,
    proxy hiccup) eagerly invoked ``clear_pending_pairing(layout)``
    and forced the next iteration to initiate a brand-new nonce.
    Under realistic timing — a mobile that scanned the QR during the
    WS drop — this orphaned the credentials server-side: the central
    server's ``claim_pairing_nonce`` persisted ``claimed_token_raw``
    and returned 200 to the mobile (so the mobile showed "Paired"),
    but the agent's next WS connect was for a different nonce, so the
    server's ``read_and_clear_claimed_credentials`` recovery path never
    fired against the right nonce.

    The post-fix contract is: on ``PairingWsError`` the pending file is
    RETAINED so the next outer-loop iteration reconnects to the SAME
    nonce, giving the server's recovery path a chance to deliver any
    stranded claim. The natural TTL check in ``load_pending_pairing``
    evicts the entry once the nonce truly expires.

    These tests pin "WS failure does not call clear_pending_pairing"
    so a regression that re-introduces the eager-clear bug fails loudly.
    """

    def _install_ws_failure_stubs(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> dict:
        """Drive the loop deterministically with a WS that always raises.

        Mirrors ``_install_loop_stubs`` in the sibling class but swaps
        the WS stub for one that raises ``PairingWsError`` on every
        attempt — so the loop never reaches the write path and the
        bounded test window observes only the WS-failure branch.
        """
        recorder: dict = {
            "clear_calls": 0,
            "save_calls": 0,
            "ws_calls": 0,
            "write_calls": 0,
            "on_paired_calls": 0,
        }
        pending = self._make_pending()

        class _FailingConn:
            async def wait_for_claim(self, **_kwargs):
                # Connection's wait_for_claim is never reached in this
                # scenario — the ``connect`` ctx manager raises before
                # yielding (matching real Cloudflare-idle / TLS-reset
                # symptoms where the WS handshake itself fails).
                raise AssertionError(
                    "wait_for_claim must not be reached when connect() raises"
                )

        class _FailingWsClient:
            @classmethod
            @contextlib.asynccontextmanager
            async def connect(cls, _ws_url):
                recorder["ws_calls"] += 1
                raise PairingWsError("simulated transport failure")
                yield _FailingConn()  # pragma: no cover — make this a generator

        async def _stub_write(_env_path, _claim):
            # Should never be reached — the WS always raises before the
            # write step. Assert loudly if it does to catch a regression
            # that lets PairingWsError fall through to the write branch.
            recorder["write_calls"] += 1
            raise AssertionError(
                "write_credentials_to_env reached on the WS-failure "
                "path; the post-fix code must continue to the next "
                "outer-loop iteration without writing"
            )

        self._wire_base_collaborators(
            monkeypatch, recorder,
            pending=pending,
            ws_client_class=_FailingWsClient,
            write_stub=_stub_write,
        )
        return recorder

    async def test_ws_failure_does_not_clear_pending(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Bound the run with a short timeout — the loop is infinite as
        # long as the WS keeps raising. A TimeoutError on
        # ``asyncio.wait_for`` is the EXPECTED exit signal here: the
        # behaviour we pin is "did the loop call clear_pending_pairing
        # during this bounded window?" not "did the loop terminate".
        recorder = self._install_ws_failure_stubs(monkeypatch)
        layout = self._make_layout(tmp_path)

        with caplog.at_level(logging.WARNING, logger=_pm.__name__):
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    run_pairing_monitor(
                        layout=layout,
                        relay_server_url="wss://relay.test",
                        on_paired=recorder["on_paired"],
                    ),
                    timeout=0.1,
                )

        # The loop reached the WS dial at least once during the window.
        # If it didn't, the test isn't actually exercising the branch
        # we claim to be pinning.
        assert recorder["ws_calls"] >= 1, (
            f"loop never reached PairingWsClient.wait_for_claim; "
            f"test is not exercising the WS-failure branch "
            f"(ws_calls={recorder['ws_calls']})"
        )
        # The smoking-gun pin: on the WS-failure path,
        # clear_pending_pairing MUST NOT be called. A regression that
        # re-introduces the eager-clear bug — which is the exact
        # user-reported failure mode where the agent moves to a new
        # nonce while the mobile-supplied credentials sit stranded on
        # the old one — would push this above 0.
        assert recorder["clear_calls"] == 0, (
            f"clear_pending_pairing was called {recorder['clear_calls']} "
            f"times on the WS-failure branch; the post-fix contract is "
            f"that the pending file is RETAINED so the next iteration "
            f"can reconnect to the same nonce and trigger the server's "
            f"recovery path"
        )
        # The write path must not have been reached on the WS-failure
        # branch (the stub raises if it is, but the explicit assert
        # makes the contract obvious).
        assert recorder["write_calls"] == 0, (
            f"write_credentials_to_env must not be called on the "
            f"WS-failure path; got {recorder['write_calls']} calls"
        )
        # ``on_paired`` must not fire on a WS failure — the claim was
        # never delivered.
        assert recorder["on_paired_calls"] == 0, (
            f"on_paired must not fire on a WS-failure-only run; "
            f"got {recorder['on_paired_calls']} calls"
        )
        # Operator-visibility check: every WS failure emits the
        # ``pairing_monitor.ws_failed`` structured log line. Pin the
        # event name so ops dashboards keying off this string keep
        # working across refactors.
        warning_messages = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.WARNING
        ]
        assert any(
            "pairing_monitor.ws_failed" in msg
            for msg in warning_messages
        ), (
            f"expected 'pairing_monitor.ws_failed' WARNING log on the "
            f"WS-failure branch; got: {warning_messages!r}"
        )

    async def test_ws_failure_applies_jitter(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The WS-failure branch adds ``random.uniform(0, 1)`` to the
        # sleep duration. The jitter de-synchronises a synchronised
        # fleet retry after a global event like a CDN restart —
        # without jitter, every agent that reconnected at T+0 would
        # also reconnect at T+2, T+4, … in lockstep, peaking the
        # central server's connect rate.
        #
        # Pin the existence of the jitter call rather than its
        # statistical distribution: a regression that hard-codes the
        # jitter to a constant 0 (or removes the addition entirely)
        # will not invoke ``random.uniform(0, 1)`` and this test will
        # fail with a precise diagnostic. We verify the call shape
        # (``(0, 1)``) so a regression that calls ``random.uniform``
        # with a degenerate range like ``(0, 0)`` is also caught.
        uniform_calls: list[tuple] = []

        def _capturing_uniform(a: float, b: float) -> float:
            uniform_calls.append((a, b))
            return 0.0

        fake_random = types.SimpleNamespace(uniform=_capturing_uniform)
        monkeypatch.setattr(_pm, "random", fake_random)

        # Patch asyncio.sleep to yield without real waits so the bounded
        # run iterates several times within the 0.1 s timeout.
        real_sleep = asyncio.sleep

        async def _instant_sleep(_duration: float) -> None:
            await real_sleep(0)

        fake_asyncio = types.SimpleNamespace(
            **{
                name: getattr(asyncio, name)
                for name in dir(asyncio)
                if not name.startswith("__")
            }
        )
        fake_asyncio.sleep = _instant_sleep
        monkeypatch.setattr(_pm, "asyncio", fake_asyncio)

        recorder = self._install_ws_failure_stubs(monkeypatch)
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

        assert recorder["ws_calls"] >= 1, (
            f"loop never reached the WS-failure branch "
            f"(ws_calls={recorder['ws_calls']}); test is not "
            f"exercising the path it claims to pin"
        )
        # At least one ``random.uniform(0, 1)`` call must have happened
        # — one per WS failure. A regression that drops the jitter
        # addition entirely would produce zero calls here.
        assert len(uniform_calls) >= 1, (
            f"expected ``random.uniform`` to be called at least once "
            f"on the WS-failure branch (one per retry); got "
            f"{len(uniform_calls)} calls. A regression that drops the "
            f"jitter addition would produce zero calls — re-synchronising "
            f"a fleet retry storm against the central server."
        )
        # Every captured call uses the (0, 1) range — pins the jitter
        # window so a regression that narrows it to a degenerate
        # ``(0, 0)`` (effectively no jitter) is also caught.
        assert all(call == (0, 1) for call in uniform_calls), (
            f"expected every ``random.uniform`` call to use the (0, 1) "
            f"range; got: {uniform_calls!r}. A degenerate range like "
            f"(0, 0) silently disables jitter without removing the call."
        )


class TestRunPairingMonitorWsBeforeSaveOrdering(_RunPairingMonitorTestBase):
    """Pin the new ordering: ``pending_pairing.json`` is written ONLY
    after ``PairingWsClient.connect`` yields (i.e. after the server's
    ``"ready"`` frame has been received).

    Before the fix, the monitor saved the pending file immediately
    after the HTTP initiate call. ``install.ps1``'s
    ``Show-PairingQrFromService`` polls that file and renders the QR
    to the user as soon as it appears. The user scanned within a
    1-2 second window while the agent's WS handshake was still
    pending; the mobile claim arrived before
    ``register_waiter(nonce)`` had run, so the server's claim handler
    stashed ``claimed_token_raw`` and fell through to the recovery
    branch on the agent's later WS connect. The recovery branch
    re-signed the push with a different ``relay_url`` than the
    initiate handler returned (one came from ``websocket.url.scheme``,
    the other from ``request.base_url``), and the agent's strict-
    equality check on ``relay_url`` rejected its own credentials.
    The single-use server-side claim was then lost; ``.env`` stayed
    blank forever.

    The fix moves ``save_pending_pairing`` INSIDE the
    ``async with PairingWsClient.connect(...) as conn`` block.
    ``connect`` only returns after ``"ready"`` is received, which is
    exactly when the server's ``register_waiter`` has run and the
    dispatch path can win against any concurrent mobile claim.
    """

    async def test_save_pending_pairing_happens_inside_connect_block(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Order recorder: collect the literal names in the exact
        # sequence the monitor calls them. The expected sequence is
        # ``connect_entered`` first, then ``save_pending_pairing``,
        # then ``connect_exited``. A regression that moves
        # ``save_pending_pairing`` back outside the ctx manager
        # surfaces as ``save_pending_pairing`` BEFORE
        # ``connect_entered`` in the list.
        order: list[str] = []
        claim = self._make_claim()
        pending = self._make_pending()

        recorder: dict = {
            "clear_calls": 0,
            "save_calls": 0,
            "on_paired_calls": 0,
            "on_paired_claim": None,
            "write_calls": 0,
        }

        async def _stub_initiate(
            relay_server_url, layout, *,
            hostname, os_name, current_agent_id,
        ):
            return pending

        def _stub_load(_layout):
            return None

        def _stub_save(_layout, _pending):
            order.append("save_pending_pairing")
            recorder["save_calls"] += 1

        def _stub_clear(_layout):
            order.append("clear_pending_pairing")
            recorder["clear_calls"] += 1

        async def _stub_render(_pending):
            order.append("render_qr")

        async def _stub_write(_env_path, _claim):
            order.append("write_credentials_to_env")
            recorder["write_calls"] += 1

        async def _on_paired(received):
            order.append("on_paired")
            recorder["on_paired_calls"] += 1
            recorder["on_paired_claim"] = received

        class _OrderingConn:
            async def wait_for_claim(self, **_kwargs):
                order.append("wait_for_claim")
                return claim

        class _OrderingWsClient:
            @classmethod
            @contextlib.asynccontextmanager
            async def connect(cls, _ws_url):
                order.append("connect_entered")
                try:
                    yield _OrderingConn()
                finally:
                    order.append("connect_exited")

        monkeypatch.setattr(
            _pm, "read_trust_public_key", lambda _layout: "stub-pub-key"
        )
        monkeypatch.setattr(_pm, "_initiate_with_metadata", _stub_initiate)
        monkeypatch.setattr(_pm, "load_pending_pairing", _stub_load)
        monkeypatch.setattr(_pm, "save_pending_pairing", _stub_save)
        monkeypatch.setattr(_pm, "clear_pending_pairing", _stub_clear)
        monkeypatch.setattr(_pm, "_render_pending_to_stdout", _stub_render)
        monkeypatch.setattr(_pm, "PairingWsClient", _OrderingWsClient)
        monkeypatch.setattr(_pm, "write_credentials_to_env", _stub_write)

        layout = self._make_layout(tmp_path)

        await asyncio.wait_for(
            run_pairing_monitor(
                layout=layout,
                relay_server_url="wss://relay.test",
                on_paired=_on_paired,
            ),
            timeout=5.0,
        )

        # Smoking-gun: ``connect_entered`` MUST appear before
        # ``save_pending_pairing``. If a regression moves the save
        # back outside the context manager, this assertion is the
        # earliest visible signal.
        assert "connect_entered" in order, (
            f"PairingWsClient.connect was never entered; "
            f"order={order!r}"
        )
        assert "save_pending_pairing" in order, (
            f"save_pending_pairing was never called; order={order!r}"
        )
        connect_idx = order.index("connect_entered")
        save_idx = order.index("save_pending_pairing")
        assert connect_idx < save_idx, (
            f"save_pending_pairing happened BEFORE the WS connect "
            f"handshake completed — this is the QR-before-WS race "
            f"the fix is meant to close. The install.ps1 polling "
            f"loop would see the pending file before the agent's "
            f"WS waiter was registered, so a fast mobile scan could "
            f"deliver the claim into the server's recovery path and "
            f"the agent would reject its own credentials on the "
            f"resulting relay_url mismatch. Order: {order!r}"
        )

        # Reinforcing: save → render → wait_for_claim → connect_exited
        # → write → clear → on_paired
        render_idx = order.index("render_qr")
        wait_idx = order.index("wait_for_claim")
        write_idx = order.index("write_credentials_to_env")
        on_paired_idx = order.index("on_paired")
        assert save_idx < render_idx < wait_idx, (
            f"expected save → render → wait_for_claim ordering inside "
            f"the connect block; got: {order!r}"
        )
        assert wait_idx < write_idx < on_paired_idx, (
            f"expected wait → write → on_paired ordering after the "
            f"claim is received; got: {order!r}"
        )


class _StopLoop(BaseException):
    """Ends a bounded ``run_pairing_monitor`` run from the sleep stub.

    A ``BaseException`` so the loop's ``except Exception`` safety net
    cannot swallow it.
    """


class TestRunPairingMonitorRetryPacing(_RunPairingMonitorTestBase):
    """Pin how fast an unpaired agent retries when pairing keeps failing.

    The production incident: backoff reset to the floor on every
    successful initiate, and a fresh nonce lost to a pre-``ready`` WS
    failure was re-minted on the next pass, so a stuck agent hit
    ``POST /api/pairing/initiate`` every ~4 s forever.
    """

    @pytest.fixture(autouse=True)
    def _no_jitter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            _pm, "random", types.SimpleNamespace(uniform=lambda _a, _b: 0.0)
        )

    def _capture_sleeps(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        stop_after: int | None,
        on_sleep=None,
    ) -> list[float]:
        sleeps: list[float] = []
        real_sleep = asyncio.sleep

        async def _capturing_sleep(duration: float) -> None:
            sleeps.append(duration)
            if on_sleep is not None:
                on_sleep()
            if stop_after is not None and len(sleeps) >= stop_after:
                raise _StopLoop
            await real_sleep(0)

        fake_asyncio = types.SimpleNamespace(
            **{
                name: getattr(asyncio, name)
                for name in dir(asyncio)
                if not name.startswith("__")
            }
        )
        fake_asyncio.sleep = _capturing_sleep
        monkeypatch.setattr(_pm, "asyncio", fake_asyncio)
        return sleeps

    def _pending(self, nonce: str, exp_unix: int) -> PendingPairing:
        return PendingPairing(
            nonce=nonce,
            expires_at="2999-01-01T00:00:00+00:00",
            trust_key_fp="ff" * 32,
            ws_url=f"wss://relay.test/ws/pairing/{nonce}",
            relay_url="https://relay.test",
            qr_payload=f"MOBA1|https://relay.test|{nonce}|{'ff' * 32}|{exp_unix}",
        )

    def _wire(
        self,
        monkeypatch: pytest.MonkeyPatch,
        recorder: dict,
        *,
        initiate,
        ws_client_class: type,
        load=lambda _layout: None,
    ) -> None:
        async def _stub_initiate(
            relay_server_url, layout, *,
            hostname, os_name, current_agent_id,
        ):
            recorder["initiate_calls"] += 1
            return initiate()

        def _stub_save(_layout, pending):
            recorder["saved"].append(pending)

        async def _stub_render(_pending):
            return None

        async def _stub_write(_env_path, _claim):
            return None

        monkeypatch.setattr(
            _pm, "read_trust_public_key", lambda _layout: "stub-pub-key"
        )
        monkeypatch.setattr(_pm, "_initiate_with_metadata", _stub_initiate)
        monkeypatch.setattr(_pm, "load_pending_pairing", load)
        monkeypatch.setattr(_pm, "save_pending_pairing", _stub_save)
        monkeypatch.setattr(_pm, "clear_pending_pairing", lambda _layout: None)
        monkeypatch.setattr(_pm, "_render_pending_to_stdout", _stub_render)
        monkeypatch.setattr(_pm, "PairingWsClient", ws_client_class)
        monkeypatch.setattr(_pm, "write_credentials_to_env", _stub_write)

    def _pre_ready_failing_ws(self, recorder: dict) -> type:
        class _PreReadyFailingWsClient:
            @classmethod
            @contextlib.asynccontextmanager
            async def connect(cls, ws_url):
                recorder["ws_urls"].append(ws_url)
                raise PairingWsError("no ready frame")
                yield  # pragma: no cover — make this an async generator

        return _PreReadyFailingWsClient

    async def test_pre_ready_ws_failure_reuses_the_nonce_and_backs_off(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sleeps = self._capture_sleeps(monkeypatch, stop_after=4)
        recorder: dict = {"initiate_calls": 0, "saved": [], "ws_urls": []}
        pending = self._pending("n1", 2999999999)
        self._wire(
            monkeypatch, recorder,
            initiate=lambda: pending,
            ws_client_class=self._pre_ready_failing_ws(recorder),
        )

        with pytest.raises(_StopLoop):
            await asyncio.wait_for(
                run_pairing_monitor(
                    layout=self._make_layout(tmp_path),
                    relay_server_url="wss://relay.test",
                ),
                timeout=5.0,
            )

        assert recorder["initiate_calls"] == 1
        assert recorder["ws_urls"] == [pending.ws_url] * 4
        floor = _pm._BACKOFF_FLOOR_S
        assert sleeps == pytest.approx([floor, floor * 2, floor * 4, floor * 8])
        assert recorder["saved"] == []

    async def test_rejected_handshake_drops_the_unsaved_nonce_and_backs_off(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Pass 1 is a plain transport failure, so n1 is reused. Pass 2 is
        # the relay's 403 ``duplicate`` for n1, so pass 3 must mint n2.
        sleeps = self._capture_sleeps(monkeypatch, stop_after=3)
        recorder: dict = {"initiate_calls": 0, "saved": [], "ws_urls": []}

        def _initiate() -> PendingPairing:
            return self._pending(f"n{recorder['initiate_calls']}", 2999999999)

        class _RejectedAfterFirstWsClient:
            @classmethod
            @contextlib.asynccontextmanager
            async def connect(cls, ws_url):
                recorder["ws_urls"].append(ws_url)
                if len(recorder["ws_urls"]) == 1:
                    raise PairingWsError("no ready frame")
                rejected = InvalidStatus(
                    Response(403, "Forbidden", Headers(), b"duplicate")
                )
                raise PairingWsError(
                    f"pairing ws transport failure: {rejected}"
                ) from rejected
                yield  # pragma: no cover — make this an async generator

        self._wire(
            monkeypatch, recorder,
            initiate=_initiate,
            ws_client_class=_RejectedAfterFirstWsClient,
        )

        with pytest.raises(_StopLoop):
            await asyncio.wait_for(
                run_pairing_monitor(
                    layout=self._make_layout(tmp_path),
                    relay_server_url="wss://relay.test",
                ),
                timeout=5.0,
            )

        assert recorder["initiate_calls"] == 2
        assert recorder["ws_urls"] == [
            "wss://relay.test/ws/pairing/n1",
            "wss://relay.test/ws/pairing/n1",
            "wss://relay.test/ws/pairing/n2",
        ]
        floor = _pm._BACKOFF_FLOOR_S
        assert sleeps == pytest.approx([floor, floor * 2, floor * 4])
        assert recorder["saved"] == []

    @pytest.mark.parametrize(
        "cause",
        [
            pytest.param(
                InvalidStatus(Response(502, "Bad Gateway", Headers(), b"")),
                id="proxy-502",
            ),
            pytest.param(
                InvalidMessage("did not receive a valid HTTP response"),
                id="eof-mid-upgrade",
            ),
        ],
    )
    async def test_non_403_handshake_failure_reuses_the_unsaved_nonce(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        cause: InvalidHandshake,
    ) -> None:
        sleeps = self._capture_sleeps(monkeypatch, stop_after=3)
        recorder: dict = {"initiate_calls": 0, "saved": [], "ws_urls": []}

        def _initiate() -> PendingPairing:
            return self._pending(f"n{recorder['initiate_calls']}", 2999999999)

        class _HandshakeFailingWsClient:
            @classmethod
            @contextlib.asynccontextmanager
            async def connect(cls, ws_url):
                recorder["ws_urls"].append(ws_url)
                raise PairingWsError(
                    f"pairing ws transport failure: {cause}"
                ) from cause
                yield  # pragma: no cover — make this an async generator

        self._wire(
            monkeypatch, recorder,
            initiate=_initiate,
            ws_client_class=_HandshakeFailingWsClient,
        )

        with pytest.raises(_StopLoop):
            await asyncio.wait_for(
                run_pairing_monitor(
                    layout=self._make_layout(tmp_path),
                    relay_server_url="wss://relay.test",
                ),
                timeout=5.0,
            )

        assert recorder["initiate_calls"] == 1
        assert recorder["ws_urls"] == ["wss://relay.test/ws/pairing/n1"] * 3
        floor = _pm._BACKOFF_FLOOR_S
        assert sleeps == pytest.approx([floor, floor * 2, floor * 4])

    async def test_repeated_403_drops_only_one_unsaved_nonce(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A proxy that refuses every upgrade with 403: n1 is dropped once,
        # then n2 is kept while it is still valid by the local clock.
        sleeps = self._capture_sleeps(monkeypatch, stop_after=4)
        recorder: dict = {"initiate_calls": 0, "saved": [], "ws_urls": []}

        def _initiate() -> PendingPairing:
            return self._pending(f"n{recorder['initiate_calls']}", 2999999999)

        class _AlwaysRejectedWsClient:
            @classmethod
            @contextlib.asynccontextmanager
            async def connect(cls, ws_url):
                recorder["ws_urls"].append(ws_url)
                rejected = InvalidStatus(
                    Response(403, "Forbidden", Headers(), b"")
                )
                raise PairingWsError(
                    f"pairing ws transport failure: {rejected}"
                ) from rejected
                yield  # pragma: no cover — make this an async generator

        self._wire(
            monkeypatch, recorder,
            initiate=_initiate,
            ws_client_class=_AlwaysRejectedWsClient,
        )

        with pytest.raises(_StopLoop):
            await asyncio.wait_for(
                run_pairing_monitor(
                    layout=self._make_layout(tmp_path),
                    relay_server_url="wss://relay.test",
                ),
                timeout=5.0,
            )

        assert recorder["initiate_calls"] == 2
        assert recorder["ws_urls"] == [
            "wss://relay.test/ws/pairing/n1",
            "wss://relay.test/ws/pairing/n2",
            "wss://relay.test/ws/pairing/n2",
            "wss://relay.test/ws/pairing/n2",
        ]
        floor = _pm._BACKOFF_FLOOR_S
        assert sleeps == pytest.approx([floor, floor * 2, floor * 4, floor * 8])
        assert recorder["saved"] == []

    async def test_reused_nonce_is_saved_once_the_ws_reaches_ready(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._capture_sleeps(monkeypatch, stop_after=None)
        recorder: dict = {"initiate_calls": 0, "saved": [], "ws_urls": []}
        pending = self._pending("n1", 2999999999)
        claim = self._make_claim()
        paired: list[PairingClaim] = []

        class _Conn:
            async def wait_for_claim(self, **_kwargs):
                return claim

        class _FailsTwiceWsClient:
            @classmethod
            @contextlib.asynccontextmanager
            async def connect(cls, ws_url):
                recorder["ws_urls"].append(ws_url)
                if len(recorder["ws_urls"]) <= 2:
                    raise PairingWsError("no ready frame")
                yield _Conn()

        self._wire(
            monkeypatch, recorder,
            initiate=lambda: pending,
            ws_client_class=_FailsTwiceWsClient,
        )

        async def _on_paired(received: PairingClaim) -> None:
            paired.append(received)

        await asyncio.wait_for(
            run_pairing_monitor(
                layout=self._make_layout(tmp_path),
                relay_server_url="wss://relay.test",
                on_paired=_on_paired,
            ),
            timeout=5.0,
        )

        assert recorder["initiate_calls"] == 1
        assert recorder["ws_urls"] == [pending.ws_url] * 3
        assert recorder["saved"] == [pending]
        assert paired == [claim]

    async def test_unsaved_nonce_expired_by_the_local_clock_is_replaced(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        start = 2_000_000_000
        wall = [float(start)]

        def _advance_wall() -> None:
            wall[0] += 60

        monkeypatch.setattr(
            _pm,
            "time",
            types.SimpleNamespace(time=lambda: wall[0], monotonic=lambda: 0.0),
        )
        sleeps = self._capture_sleeps(
            monkeypatch, stop_after=3, on_sleep=_advance_wall
        )
        recorder: dict = {"initiate_calls": 0, "saved": [], "ws_urls": []}

        def _initiate() -> PendingPairing:
            n = recorder["initiate_calls"]
            return self._pending(f"n{n}", int(wall[0]) + 100)

        self._wire(
            monkeypatch, recorder,
            initiate=_initiate,
            ws_client_class=self._pre_ready_failing_ws(recorder),
        )

        with pytest.raises(_StopLoop):
            await asyncio.wait_for(
                run_pairing_monitor(
                    layout=self._make_layout(tmp_path),
                    relay_server_url="wss://relay.test",
                ),
                timeout=5.0,
            )

        # n1 (exp start+100) is reused at start+60 and dropped at start+120.
        assert recorder["initiate_calls"] == 2
        assert recorder["ws_urls"] == [
            "wss://relay.test/ws/pairing/n1",
            "wss://relay.test/ws/pairing/n1",
            "wss://relay.test/ws/pairing/n2",
        ]
        floor = _pm._BACKOFF_FLOOR_S
        assert sleeps == pytest.approx([floor, floor * 2, floor * 4])

    async def test_clock_skew_backs_off_and_logs_a_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Agent clock ahead of the relay: every fresh nonce is already
        # expired locally, the claim wait times out within a second, and
        # the saved file is evicted as expired, so each pass re-initiates.
        sleeps = self._capture_sleeps(monkeypatch, stop_after=3)
        recorder: dict = {"initiate_calls": 0, "saved": [], "ws_urls": []}

        def _initiate() -> PendingPairing:
            return self._pending(
                f"skewed{recorder['initiate_calls']}", 1_000_000_000
            )

        class _Conn:
            async def wait_for_claim(self, **_kwargs):
                raise PairingWsError("timed out waiting for claim")

        class _ReadyWsClient:
            @classmethod
            @contextlib.asynccontextmanager
            async def connect(cls, ws_url):
                recorder["ws_urls"].append(ws_url)
                yield _Conn()

        self._wire(
            monkeypatch, recorder,
            initiate=_initiate,
            ws_client_class=_ReadyWsClient,
        )

        with caplog.at_level(logging.WARNING, logger=_pm.__name__):
            with pytest.raises(_StopLoop):
                await asyncio.wait_for(
                    run_pairing_monitor(
                        layout=self._make_layout(tmp_path),
                        relay_server_url="wss://relay.test",
                    ),
                    timeout=5.0,
                )

        assert recorder["initiate_calls"] == 3
        floor = _pm._BACKOFF_FLOOR_S
        assert sleeps == pytest.approx([floor, floor * 2, floor * 4])
        skew_warnings = [
            r.getMessage()
            for r in caplog.records
            if "pairing_monitor.clock_skew" in r.getMessage()
        ]
        assert len(skew_warnings) == 3, skew_warnings
        assert "nonce=skewed1" in skew_warnings[0]

    @pytest.mark.parametrize(
        ("wait_s", "expected_multipliers"),
        [
            pytest.param(_pm._HEALTHY_WAIT_S, [1, 1, 1], id="healthy-wait-resets"),
            pytest.param(
                _pm._HEALTHY_WAIT_S - 1, [1, 2, 4], id="short-wait-keeps-growing"
            ),
        ],
    )
    async def test_backoff_resets_only_after_a_healthy_claim_wait(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        wait_s: float,
        expected_multipliers: list[int],
    ) -> None:
        mono = [1000.0]
        monkeypatch.setattr(
            _pm,
            "time",
            types.SimpleNamespace(time=time.time, monotonic=lambda: mono[0]),
        )
        sleeps = self._capture_sleeps(monkeypatch, stop_after=3)
        recorder: dict = {"initiate_calls": 0, "saved": [], "ws_urls": []}
        stable_pending = self._pending("n1", 2999999999)

        class _Conn:
            async def wait_for_claim(self, **_kwargs):
                mono[0] += wait_s
                raise PairingWsError("connection dropped")

        class _ReadyWsClient:
            @classmethod
            @contextlib.asynccontextmanager
            async def connect(cls, ws_url):
                recorder["ws_urls"].append(ws_url)
                yield _Conn()

        self._wire(
            monkeypatch, recorder,
            initiate=lambda: stable_pending,
            ws_client_class=_ReadyWsClient,
            load=lambda _layout: stable_pending,
        )

        with pytest.raises(_StopLoop):
            await asyncio.wait_for(
                run_pairing_monitor(
                    layout=self._make_layout(tmp_path),
                    relay_server_url="wss://relay.test",
                ),
                timeout=5.0,
            )

        assert recorder["initiate_calls"] == 0
        floor = _pm._BACKOFF_FLOOR_S
        assert sleeps == pytest.approx([floor * m for m in expected_multipliers])


class TestRunPairingMonitorBroadenedExceptionHandling(_RunPairingMonitorTestBase):
    """Pin the broadened-exception contract on the outer loop.

    Before this fix, the initiate branch caught only
    ``(PairingMonitorError, urllib.error.URLError, OSError)``. A
    ``json.JSONDecodeError`` (server returned an HTML proxy error
    page), a ``KeyError`` (server response missing a required field),
    or a ``ValueError`` from URL parsing would escape the loop and
    kill the asyncio task — silently, because ``app.py``'s
    ``asyncio.create_task`` never ``await``s the task. The fix
    broadens the inner except to ``Exception`` and wraps the whole
    loop body in a catch-all ``except Exception`` so any unanticipated
    raise still results in a logged warning + backoff + retry instead
    of a dead task.

    Two tests pin two different escape routes (initiate, outer loop)
    to avoid a single regression silently re-opening either side.
    """

    async def test_json_decode_error_during_initiate_is_caught_and_retries(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # First initiate raises JSONDecodeError (proxy returned an HTML
        # error page); second initiate succeeds. The pre-fix code would
        # crash on the first call; the post-fix code retries and reaches
        # the happy path.
        pending = self._make_pending()
        claim = self._make_claim()
        recorder = {"initiate_calls": 0, "save_calls": 0}

        async def _flaky_initiate(*args, **kwargs):
            recorder["initiate_calls"] += 1
            if recorder["initiate_calls"] == 1:
                raise json.JSONDecodeError("not json", "<html>", 0)
            return pending

        def _stub_load(_layout):
            return None

        def _stub_save(_layout, _pending):
            recorder["save_calls"] += 1

        def _stub_clear(_layout):
            pass

        async def _stub_render(_pending):
            pass

        async def _stub_write(_env_path, _claim):
            pass

        ws_client_class = _make_two_phase_ws_client(claim)

        monkeypatch.setattr(
            _pm, "read_trust_public_key", lambda _layout: "stub-pub-key"
        )
        monkeypatch.setattr(_pm, "_initiate_with_metadata", _flaky_initiate)
        monkeypatch.setattr(_pm, "load_pending_pairing", _stub_load)
        monkeypatch.setattr(_pm, "save_pending_pairing", _stub_save)
        monkeypatch.setattr(_pm, "clear_pending_pairing", _stub_clear)
        monkeypatch.setattr(_pm, "_render_pending_to_stdout", _stub_render)
        monkeypatch.setattr(_pm, "PairingWsClient", ws_client_class)
        monkeypatch.setattr(_pm, "write_credentials_to_env", _stub_write)

        layout = self._make_layout(tmp_path)
        await asyncio.wait_for(
            run_pairing_monitor(
                layout=layout,
                relay_server_url="wss://relay.test",
                on_paired=None,
            ),
            timeout=5.0,
        )

        assert recorder["initiate_calls"] >= 2, (
            "expected the loop to retry after JSONDecodeError; "
            f"only got {recorder['initiate_calls']} initiate call(s) — "
            "regression: the inner except may have been narrowed back "
            "to (PairingMonitorError, URLError, OSError), letting "
            "JSONDecodeError kill the task on the first failure"
        )
        assert recorder["save_calls"] == 1, (
            "expected pairing to succeed on the retry — got "
            f"{recorder['save_calls']} save_pending_pairing calls"
        )

    async def test_unexpected_exception_in_loop_body_does_not_kill_task(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # An unanticipated exception from a code path NOT wrapped by the
        # inner try/except blocks (e.g. ``parse_qr_payload`` raising
        # instead of returning None) must still result in a logged
        # warning + retry, not a silent task death.
        pending = self._make_pending()
        claim = self._make_claim()
        recorder = {"parse_calls": 0}

        original_parse = _pm.parse_qr_payload

        def _flaky_parse(text):
            recorder["parse_calls"] += 1
            if recorder["parse_calls"] == 1:
                # parse_qr_payload's contract is "return None on
                # malformedness"; a raise here is unanticipated by the
                # narrow inner handlers and is exactly the kind of
                # surface the outer catch-all is designed for.
                raise ValueError("synthetic unexpected exception")
            return original_parse(text)

        async def _stub_initiate(*args, **kwargs):
            return pending

        def _stub_load(_layout):
            return None

        recorder["save_calls"] = 0

        def _stub_save(_layout, _pending):
            recorder["save_calls"] += 1

        def _stub_clear(_layout):
            pass

        async def _stub_render(_pending):
            pass

        async def _stub_write(_env_path, _claim):
            pass

        ws_client_class = _make_two_phase_ws_client(claim)

        monkeypatch.setattr(
            _pm, "read_trust_public_key", lambda _layout: "stub-pub-key"
        )
        monkeypatch.setattr(_pm, "_initiate_with_metadata", _stub_initiate)
        monkeypatch.setattr(_pm, "load_pending_pairing", _stub_load)
        monkeypatch.setattr(_pm, "save_pending_pairing", _stub_save)
        monkeypatch.setattr(_pm, "clear_pending_pairing", _stub_clear)
        monkeypatch.setattr(_pm, "_render_pending_to_stdout", _stub_render)
        monkeypatch.setattr(_pm, "PairingWsClient", ws_client_class)
        monkeypatch.setattr(_pm, "write_credentials_to_env", _stub_write)
        monkeypatch.setattr(_pm, "parse_qr_payload", _flaky_parse)

        layout = self._make_layout(tmp_path)
        with caplog.at_level(logging.WARNING, logger=_pm.logger.name):
            await asyncio.wait_for(
                run_pairing_monitor(
                    layout=layout,
                    relay_server_url="wss://relay.test",
                    on_paired=None,
                ),
                timeout=5.0,
            )

        assert recorder["parse_calls"] >= 2, (
            "expected the loop to retry after the unexpected ValueError; "
            f"only got {recorder['parse_calls']} parse call(s)"
        )
        assert recorder["save_calls"] == 1, (
            "expected pairing to succeed on the retry"
        )
        assert any(
            "loop_iter_unexpected" in rec.getMessage() for rec in caplog.records
        ), (
            "expected the outer catch-all to log "
            "``pairing_monitor.loop_iter_unexpected``; if this assertion "
            "fires, the catch-all may have been removed or moved — "
            "regression risk is silent task death"
        )


class TestRenderPendingToStdout:
    """Safety-net behaviour of ``_render_pending_to_stdout``.

    The stdout QR is a convenience — the cross-process pairing signal
    is ``pending_pairing.json`` (already on disk before this is called),
    and the central server's mobile-claim flow is independent. So a
    rendering failure MUST NOT propagate out of this helper, because it
    is awaited from inside the surrounding ``async with
    PairingWsClient.connect(...)`` block in ``run_pairing_monitor``: a
    raised exception there would tear down the WS before
    ``wait_for_claim`` could run, the same nonce would then be rejected
    with HTTP 403 on every reconnect, and pairing would silently fail.

    This is exactly the failure mode that the original ``agent.out.log``
    captured — segno raised ``UnicodeEncodeError`` on cp1252-redirected
    stdout, the WS dropped, and the agent looped on HTTP 403 until the
    nonce TTL expired. These tests pin the contract that any exception
    from the renderer is swallowed and logged as
    ``pairing_monitor.qr_render_skipped``.
    """

    def _make_pending(self) -> PendingPairing:
        return PendingPairing(
            nonce="n-render-1",
            expires_at="2999-01-01T00:00:00+00:00",
            trust_key_fp="ff" * 32,
            ws_url="wss://relay.test/ws/pairing/n-render-1",
            relay_url="https://relay.test",
            qr_payload=(
                f"MOBA1|https://relay.test|n-render-1|{'ff' * 32}|2999999999"
            ),
        )

    async def test_swallows_unicode_encode_error(
        self, monkeypatch, caplog
    ) -> None:
        def _raise_unicode(*_args, **_kwargs):
            raise UnicodeEncodeError(
                "cp1252", "▀", 0, 1, "character maps to <undefined>"
            )

        monkeypatch.setattr(_pm, "render_qr_to_terminal", _raise_unicode)
        caplog.set_level(logging.WARNING, logger="mobaide.agent.pairing_monitor")

        await _pm._render_pending_to_stdout(self._make_pending())

        assert any(
            "qr_render_skipped" in rec.getMessage()
            and "UnicodeEncodeError" in rec.getMessage()
            for rec in caplog.records
        ), (
            "expected the safety net to log "
            "``pairing_monitor.qr_render_skipped`` with the exception "
            "type; if this fires, the try/except around the QR render "
            "may have been removed — regression risk is the original "
            "Windows-service pairing crash (UnicodeEncodeError tears "
            "down the WS before ``wait_for_claim``)"
        )

    async def test_swallows_pairing_hint_write_error(
        self, monkeypatch, caplog
    ) -> None:
        # ``write_pairing_hints`` runs after the QR on the same stdout, so it
        # inherits the identical hazard: a cp1252-redirected service log
        # raises UnicodeEncodeError on write. It must be *inside* the same
        # try, not after it — a raise here reaches the surrounding ``async
        # with PairingWsClient.connect(...)``, tears the socket down before
        # ``wait_for_claim``, and the relay then rejects that nonce forever.
        def _raise_unicode(*_args, **_kwargs):
            raise UnicodeEncodeError(
                "cp1252", "—", 0, 1, "character maps to <undefined>"
            )

        monkeypatch.setattr(_pm, "write_pairing_hints", _raise_unicode)
        caplog.set_level(logging.WARNING, logger="mobaide.agent.pairing_monitor")

        await _pm._render_pending_to_stdout(self._make_pending())

        assert any(
            "qr_render_skipped" in rec.getMessage()
            and "UnicodeEncodeError" in rec.getMessage()
            for rec in caplog.records
        ), (
            "expected the pairing-hint write to sit inside the same "
            "must-not-raise try as the QR render; if this fires, a failed "
            "hint write kills pairing permanently"
        )

    async def test_writes_the_pairing_hints_after_the_qr(
        self, monkeypatch, capsys
    ) -> None:
        # The safety net above is only worth having if the call actually
        # happens on this path — an operator tailing the service log is the
        # main audience for the pasteable line.
        calls: list[str] = []
        monkeypatch.setattr(_pm, "render_qr_to_terminal", lambda *_a, **_k: None)
        monkeypatch.setattr(
            _pm, "write_pairing_hints", lambda payload, **_k: calls.append(payload)
        )
        pending = self._make_pending()

        await _pm._render_pending_to_stdout(pending)

        assert calls == [pending.qr_payload]
