"""``/api/voice/*`` through the FastAPI app and the relay ``_dispatch_voice``
dispatcher (byte-identical answers on both transports), ``capabilities.voice``
on ``/api/version``, the ``voice.changed`` connect snapshots, relay voice
requests running off the tunnel reader, and the invariant that a broken
voice root never breaks the app."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from mobaide.agent.app import create_app
from mobaide.agent.config import AgentConfig
from mobaide.core.claude_launcher import ClaudeBinaryNotFoundError
from mobaide.core.git_exec import CmdResult
from mobaide.relay import client as relay_module
from mobaide.relay.client import RelayClient
from mobaide.relay.envelope import CH_CONTROL, CH_SESSIONS_LIST, decode
from mobaide.voice import host
from mobaide.voice import service as service_module
from mobaide.voice.cleanup import (
    MAX_CLEANUP_CHARS,
    ClaudeCleanup,
    CodexCleanup,
    TranscriptCleaner,
)
from mobaide.voice.models import VOICE_CAPABILITIES, VoiceDictationResultResponse
from mobaide.voice.service import VoiceService

from .conftest import TEST_JWT_SECRET, TEST_PASSWORD, TEST_USERNAME

STATUS = "/api/voice/status"
INSTALL = "/api/voice/install"
MODEL = "/api/voice/model"
DICTATIONS = "/api/voice/dictations"
CLEANUP = "/api/voice/cleanup"
CLEANUP_SETTINGS = "/api/voice/cleanup/settings"
ONE_SECOND_B64 = base64.b64encode(b"\x10\x27" * 16000).decode()


@pytest.fixture(autouse=True)
def _pin_host_probes(monkeypatch):
    """Free disk moves between two status reads; pin it so both transports
    are compared on the same host snapshot."""
    monkeypatch.setattr(host, "total_ram_bytes", lambda: 16 * host.GIB)
    monkeypatch.setattr(host, "free_disk_bytes", lambda _path: 100 * host.GIB)


def _config(tmp_path, **overrides) -> AgentConfig:
    fields = {
        "jwt_secret_key": TEST_JWT_SECRET,
        "user_store_path": str(tmp_path / "users.json"),
        "projects_store_path": str(tmp_path / "projects.json"),
        "voice_dir": str(tmp_path / "voice"),
        "voice_fake": True,
        "max_sessions": 4,
        "log_level": "DEBUG",
    }
    fields.update(overrides)
    return AgentConfig(**fields)


@pytest.fixture
async def voice_app(tmp_path):
    application = create_app(config=_config(tmp_path))
    application.state.user_store.add_user(TEST_USERNAME, TEST_PASSWORD)
    yield application
    await application.state.voice.shutdown()
    await application.state.session_manager.shutdown()


@pytest.fixture
async def http(voice_app):
    async with AsyncClient(transport=ASGITransport(app=voice_app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def headers(http) -> dict[str, str]:
    resp = await http.post(
        "/api/auth/token", json={"username": TEST_USERNAME, "password": TEST_PASSWORD}
    )
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
def relay(voice_app, relay_config, mock_session_manager, mock_event_bus, mock_broadcaster,
          mock_app_state):
    mock_app_state.voice = voice_app.state.voice
    mock_app_state.cli_launchers = {}
    mock_app_state.cli_models_warmup = None
    return RelayClient(
        config=relay_config,
        session_manager=mock_session_manager,
        event_bus=mock_event_bus,
        broadcaster=mock_broadcaster,
        app_state=mock_app_state,
    )


async def _assert_parity(http, headers, relay, method, url, body=None):
    resp = await http.request(method, url, headers=headers, json=body)
    dispatched = await relay._dispatch_voice(method, url, body)
    expected = None if resp.status_code == 204 else resp.json()
    assert dispatched == {"status": resp.status_code, "body": expected}
    return resp


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)


class TestRouting:
    async def test_status_requires_auth(self, http):
        assert (await http.get(STATUS)).status_code == 401

    async def test_fresh_status_on_both_transports(self, http, headers, relay):
        resp = await _assert_parity(http, headers, relay, "GET", STATUS)

        assert resp.status_code == 200
        assert resp.json()["state"] == "not_installed"
        assert resp.json()["model"]["id"] == "fake-stt"

    async def test_unknown_model_is_422_on_both_transports(self, http, headers, relay):
        resp = await _assert_parity(http, headers, relay, "POST", INSTALL, {"model_id": "nope"})

        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "unknown_model"

    async def test_dictation_before_install_is_409_on_both_transports(self, http, headers, relay):
        resp = await _assert_parity(http, headers, relay, "POST", DICTATIONS, {})

        assert resp.status_code == 409
        assert resp.json() == {
            "detail": {
                "code": "voice_not_ready",
                "message": "Voice input is not installed on this computer.",
            }
        }

    @pytest.mark.parametrize(
        ("url", "body"),
        [
            (f"{DICTATIONS}/nope/audio", {"seq": 0, "pcm16_b64": "AAA="}),
            (f"{DICTATIONS}/nope/finish", {"last_seq": -1}),
        ],
        ids=["audio", "finish"],
    )
    async def test_unknown_dictation_is_404_on_both_transports(self, http, headers, relay, url, body):
        resp = await _assert_parity(http, headers, relay, "POST", url, body)

        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "dictation_not_found"

    async def test_deleting_an_unknown_dictation_is_204_on_both_transports(self, http, headers, relay):
        resp = await _assert_parity(http, headers, relay, "DELETE", f"{DICTATIONS}/nope")

        assert resp.status_code == 204

    @pytest.mark.parametrize(
        ("url", "body"),
        [
            (f"{DICTATIONS}/d/audio", {"seq": -1, "pcm16_b64": ""}),
            (f"{DICTATIONS}/d/audio", {"pcm16_b64": ""}),
            (f"{DICTATIONS}/d/finish", {"last_seq": -2}),
        ],
    )
    async def test_invalid_bodies_are_422_on_both_transports(self, http, headers, relay, url, body):
        resp = await http.post(url, headers=headers, json=body)
        dispatched = await relay._dispatch_voice("POST", url, body)

        assert resp.status_code == dispatched["status"] == 422

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", INSTALL),
            ("PUT", STATUS),
            ("GET", f"{DICTATIONS}/d/finish"),
            ("POST", f"{DICTATIONS}/d/rewind"),
            ("GET", CLEANUP),
            ("GET", "/api/voice"),
            ("GET", "/api/sessions"),
        ],
    )
    async def test_relay_ignores_other_methods_and_routes(self, relay, method, path):
        assert await relay._dispatch_voice(method, path, None) is None

    async def test_service_missing_is_503_on_both_transports(self, voice_app, http, headers, relay):
        service = voice_app.state.voice
        voice_app.state.voice = None
        relay._app_state.voice = None
        try:
            resp = await _assert_parity(http, headers, relay, "GET", STATUS)
        finally:
            voice_app.state.voice = service

        assert resp.status_code == 503
        assert resp.json()["detail"]["code"] == "voice_unavailable"

    async def test_voice_routes_are_reachable_through_dispatch_proxy(self, relay):
        result = await relay._dispatch_proxy("GET", STATUS, None, None, AsyncMock())

        assert result["status"] == 200
        assert result["body"]["state"] == "not_installed"


class TestFullFlow:
    async def test_enable_dictate_remove_over_the_relay_matches_direct(self, voice_app, http, headers, relay):
        started = await relay._dispatch_voice("POST", INSTALL, None)
        assert started["status"] == 200
        assert started["body"]["enabled"] is True
        await asyncio.wait_for(voice_app.state.voice.wait_for_install(), 20)

        ready = await _assert_parity(http, headers, relay, "GET", STATUS)
        assert ready.json()["state"] == "ready"

        created = await relay._dispatch_voice("POST", DICTATIONS, {"session_id": "s-1"})
        assert created["status"] == 200
        assert created["body"]["sample_rate"] == VOICE_CAPABILITIES["sample_rate"]
        dictation = created["body"]["dictation_id"]
        audio = await relay._dispatch_voice(
            "POST", f"{DICTATIONS}/{dictation}/audio", {"seq": 0, "pcm16_b64": ONE_SECOND_B64}
        )
        assert audio == {"status": 200, "body": {"seq": 0, "duration_ms": 1000}}
        finished = await relay._dispatch_voice(
            "POST", f"{DICTATIONS}/{dictation}/finish", {"last_seq": 0}
        )
        assert finished == {
            "status": 200,
            "body": {
                "dictation_id": dictation,
                "state": "done",
                "text": "fake transcript 1000 ms",
                "duration_ms": 1000,
            },
        }

        direct = (await http.post(DICTATIONS, headers=headers, json={})).json()["dictation_id"]
        gap = await _assert_parity(
            http, headers, relay, "POST", f"{DICTATIONS}/{direct}/audio",
            {"seq": 1, "pcm16_b64": ONE_SECOND_B64},
        )
        assert gap.status_code == 409
        assert gap.json()["detail"]["expected_seq"] == 0

        removed = await http.delete(MODEL, headers=headers)
        assert removed.status_code == 200
        assert removed.json()["state"] == "not_installed"
        await _assert_parity(http, headers, relay, "GET", STATUS)


class _Claude:
    """``claude -p`` or ``codex exec`` as the cleaner runs them, answering
    ``text`` and recording each argv; with ``installed`` off the Claude
    binary does not resolve."""

    def __init__(self) -> None:
        self.text = "Fix the login bug."
        self.installed = True
        self.argvs: list[list[str]] = []

    def resolve(self) -> str:
        if not self.installed:
            raise ClaudeBinaryNotFoundError([])
        return "claude"

    def __call__(self, argv, **_kwargs) -> CmdResult:
        self.argvs.append(list(argv))
        if argv[0] == "codex":
            item = {"type": "item.completed", "item": {"type": "agent_message", "text": self.text}}
            return CmdResult(rc=0, stdout=json.dumps(item).encode() + b"\n", stderr=b"")
        payload = {"type": "result", "subtype": "success", "is_error": False, "result": self.text}
        return CmdResult(rc=0, stdout=json.dumps(payload).encode(), stderr=b"")


async def _signed_in(_binary, _env) -> bool:
    return True


class TestCleanup:
    @pytest.fixture
    async def claude(self, voice_app, relay, tmp_path):
        """Both transports get a voice service whose cleaner runs the fake."""
        claude = _Claude()
        service = VoiceService(
            voice_dir=tmp_path / "voice",
            fake=True,
            cleaner=TranscriptCleaner(
                clis={
                    "claude": ClaudeCleanup(claude.resolve),
                    "codex": CodexCleanup(lambda: "codex", lambda: None, auth_present=_signed_in),
                },
                work_dir=tmp_path / "voice-cleanup",
                run=claude,
            ),
        )
        original = voice_app.state.voice
        voice_app.state.voice = service
        relay._app_state.voice = service
        yield claude
        voice_app.state.voice = original
        await service.shutdown()

    @pytest.mark.parametrize("text", ["um fix the uh login bug", "x" * MAX_CLEANUP_CHARS])
    async def test_cleaned_text_on_both_transports(self, http, headers, relay, claude, text):
        resp = await _assert_parity(http, headers, relay, "POST", CLEANUP, {"text": text})

        assert resp.status_code == 200
        assert resp.json() == {"text": "Fix the login bug."}

    async def test_session_kind_picks_the_cli_on_both_transports(self, http, headers, relay, claude):
        body = {"text": "um fix the uh login bug", "kind": "codex"}

        resp = await _assert_parity(http, headers, relay, "POST", CLEANUP, body)

        assert resp.json() == {"text": "Fix the login bug."}
        assert [argv[:2] for argv in claude.argvs] == [["codex", "exec"], ["codex", "exec"]]

    async def test_no_kind_means_claude_code(self, http, headers, relay, claude):
        await _assert_parity(http, headers, relay, "POST", CLEANUP, {"text": "hello"})

        assert [argv[0] for argv in claude.argvs] == ["claude", "claude"]

    async def test_missing_claude_code_is_503_on_both_transports(self, http, headers, relay, claude):
        claude.installed = False

        resp = await _assert_parity(http, headers, relay, "POST", CLEANUP, {"text": "hello"})

        assert resp.status_code == 503
        assert resp.json()["detail"]["code"] == "cleanup_unavailable"

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"text": ""},
            {"text": "x" * (MAX_CLEANUP_CHARS + 1)},
            {"text": 42},
            {"text": "hello", "kind": "shell"},
            {"text": "hello", "kind": None},
        ],
        ids=["missing", "empty", "too_long", "not_text", "unknown_kind", "null_kind"],
    )
    async def test_invalid_bodies_are_422_on_both_transports(self, http, headers, relay, claude, body):
        resp = await http.post(CLEANUP, headers=headers, json=body)
        dispatched = await relay._dispatch_voice("POST", CLEANUP, body)

        assert resp.status_code == dispatched["status"] == 422


class TestCleanupSettings:
    async def test_stored_on_both_transports(self, http, headers, relay):
        resp = await _assert_parity(http, headers, relay, "PUT", CLEANUP_SETTINGS, {"enabled": True})

        assert resp.status_code == 200
        assert resp.json()["cleanup_enabled"] is True
        assert (await http.get(STATUS, headers=headers)).json()["cleanup_enabled"] is True

        resp = await _assert_parity(http, headers, relay, "PUT", CLEANUP_SETTINGS, {"enabled": False})

        assert resp.json()["cleanup_enabled"] is False

    @pytest.mark.parametrize("body", [{}, {"enabled": "yes"}], ids=["missing", "not_bool"])
    async def test_invalid_bodies_are_422_on_both_transports(self, http, headers, relay, body):
        resp = await http.put(CLEANUP_SETTINGS, headers=headers, json=body)
        dispatched = await relay._dispatch_voice("PUT", CLEANUP_SETTINGS, body)

        assert resp.status_code == dispatched["status"] == 422


class TestVersionCapability:
    async def test_direct_and_relay_advertise_the_voice_capability(self, http, headers, relay):
        direct = await http.get("/api/version", headers=headers)
        relayed = await relay._dispatch_proxy("GET", "/api/version", None, None, AsyncMock())

        assert direct.json()["capabilities"]["voice"] == VOICE_CAPABILITIES
        assert relayed["body"]["capabilities"]["voice"] == VOICE_CAPABILITIES


class TestBrokenVoiceRootNeverBreaksTheApp:
    async def _version_and_status(self, application) -> tuple[dict, dict]:
        application.state.user_store.add_user(TEST_USERNAME, TEST_PASSWORD)
        async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as c:
            token = (await c.post(
                "/api/auth/token", json={"username": TEST_USERNAME, "password": TEST_PASSWORD}
            )).json()["access_token"]
            auth = {"Authorization": f"Bearer {token}"}
            version = await c.get("/api/version", headers=auth)
            status = await c.get(STATUS, headers=auth)
        assert version.status_code == 200
        assert status.status_code == 200
        return version.json(), status.json()

    async def test_corrupt_settings(self, tmp_path):
        (tmp_path / "voice").mkdir()
        (tmp_path / "voice" / "settings.json").write_text('{"enabled": tru', encoding="utf-8")
        application = create_app(config=_config(tmp_path))
        try:
            await application.state.voice.start()
            version, status = await self._version_and_status(application)
        finally:
            await application.state.voice.shutdown()

        assert version["capabilities"]["voice"] == VOICE_CAPABILITIES
        assert status["state"] == "not_installed"

    @pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="POSIX non-root permissions")
    async def test_unreadable_voice_dir(self, tmp_path):
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o000)
        try:
            application = create_app(config=_config(tmp_path, voice_dir=str(locked / "voice")))
            await application.state.voice.start()
            version, status = await self._version_and_status(application)
            await application.state.voice.shutdown()
        finally:
            locked.chmod(0o700)

        assert version["capabilities"]["voice"] == VOICE_CAPABILITIES
        assert status["state"] == "not_installed"

    async def test_no_runtime_for_this_host(self, tmp_path, monkeypatch):
        monkeypatch.setattr(service_module, "resolve_runtime", lambda: (None, "python_unsupported"))
        (tmp_path / "voice").mkdir()
        (tmp_path / "voice" / "settings.json").write_text(
            json.dumps({"enabled": True, "phase": "ready"}), encoding="utf-8"
        )
        application = create_app(config=_config(tmp_path, voice_fake=False))
        try:
            await application.state.voice.start()
            version, status = await self._version_and_status(application)
        finally:
            await application.state.voice.shutdown()

        assert version["capabilities"]["voice"] == VOICE_CAPABILITIES
        assert status["state"] == "unsupported"


class TestConnectSnapshots:
    def test_events_websocket_sends_a_voice_snapshot_on_connect(self, app):
        token = app.state.jwt_service.create_access_token(TEST_USERNAME)

        with TestClient(app).websocket_connect(f"/ws/events?token={token}") as ws:
            types = [ws.receive_json() for _ in range(4)]

        assert [t["type"] for t in types] == [
            "sessions.changed", "projects.changed", "autoscan.list.changed", "voice.changed",
        ]
        assert types[3]["payload"] == app.state.voice.status().model_dump(mode="json")

    async def test_relay_snapshot_publishes_voice_changed(
        self, tmp_path, relay_config, mock_session_manager, mock_event_bus, mock_broadcaster,
        mock_app_state,
    ):
        voice = VoiceService(voice_dir=tmp_path / "voice", fake=True)
        mock_app_state.voice = voice
        mock_session_manager.list_sessions = MagicMock(return_value=[])
        client = RelayClient(
            config=relay_config, session_manager=mock_session_manager, event_bus=mock_event_bus,
            broadcaster=mock_broadcaster, app_state=mock_app_state,
        )

        await client._handle_control(json.dumps({"type": "request_events_snapshot"}).encode(), AsyncMock())

        voice_calls = [c for c in mock_event_bus.publish.call_args_list if c[0][0] == "voice.changed"]
        assert len(voice_calls) == 1
        assert voice_calls[0][0][1] == voice.status().model_dump(mode="json")

    async def test_relay_snapshot_skips_voice_when_the_app_has_none(
        self, relay_config, mock_session_manager, mock_event_bus, mock_broadcaster, mock_app_state,
    ):
        mock_session_manager.list_sessions = MagicMock(return_value=[])
        client = RelayClient(
            config=relay_config, session_manager=mock_session_manager, event_bus=mock_event_bus,
            broadcaster=mock_broadcaster, app_state=mock_app_state,
        )

        await client._handle_control(json.dumps({"type": "request_events_snapshot"}).encode(), AsyncMock())

        assert "voice.changed" not in [c[0][0] for c in mock_event_bus.publish.call_args_list]

    async def test_failing_voice_status_does_not_abort_the_relay_snapshot(
        self, tmp_path, relay_config, mock_session_manager, mock_event_bus, mock_broadcaster,
        mock_app_state,
    ):
        class _BrokenVoice(VoiceService):
            def status(self):
                raise RuntimeError("disk on fire")

        mock_app_state.voice = _BrokenVoice(voice_dir=tmp_path / "voice", fake=True)
        mock_session_manager.list_sessions = MagicMock(return_value=[])
        client = RelayClient(
            config=relay_config, session_manager=mock_session_manager, event_bus=mock_event_bus,
            broadcaster=mock_broadcaster, app_state=mock_app_state,
        )
        ws = AsyncMock()

        await client._handle_control(json.dumps({"type": "request_events_snapshot"}).encode(), ws)

        assert [decode(c[0][0])[0] for c in ws.send.call_args_list].count(CH_SESSIONS_LIST) == 1


class _SlowVoice(VoiceService):
    """``finish`` waits on a gate, like a long transcription; records
    cancellation."""

    def __init__(self, voice_dir) -> None:
        super().__init__(voice_dir=voice_dir, fake=True)
        self.gate = asyncio.Event()
        self.entered = 0
        self.cancelled = 0

    async def finish_dictation(self, dictation_id, request):
        self.entered += 1
        try:
            await self.gate.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return VoiceDictationResultResponse(
            dictation_id=dictation_id, state="done", text="hello", duration_ms=10
        )


def _proxy(request_id: str, method: str, path: str, body: dict | None = None) -> bytes:
    return json.dumps(
        {"type": "proxy_request", "request_id": request_id, "method": method, "path": path, "body": body}
    ).encode()


def _finish(request_id: str) -> bytes:
    return _proxy(request_id, "POST", f"{DICTATIONS}/d-1/finish", {"last_seq": -1})


def _responses(ws) -> list[dict]:
    out = []
    for call in ws.send.call_args_list:
        channel, _sid, payload = decode(call[0][0])
        if channel == CH_CONTROL:
            out.append(json.loads(payload))
    return out


class TestRelayVoiceDispatchIsOffTheReader:
    @pytest.fixture
    def slow_voice(self, tmp_path) -> _SlowVoice:
        return _SlowVoice(tmp_path / "voice")

    @pytest.fixture
    async def tunnel(self, slow_voice, relay_config, mock_session_manager, mock_event_bus,
                     mock_broadcaster, mock_app_state):
        mock_app_state.voice = slow_voice
        client = RelayClient(
            config=relay_config, session_manager=mock_session_manager, event_bus=mock_event_bus,
            broadcaster=mock_broadcaster, app_state=mock_app_state,
        )
        ws = AsyncMock()
        client._ws = ws
        yield client, ws
        slow_voice.gate.set()
        await client.stop()

    async def test_slow_finish_does_not_hold_up_later_requests(self, tunnel, slow_voice):
        client, ws = tunnel

        await asyncio.wait_for(client._handle_control(_finish("r-finish"), ws), 1)
        await _wait_for(lambda: slow_voice.entered == 1)
        await asyncio.wait_for(client._handle_control(_proxy("r-status", "GET", STATUS), ws), 1)
        await _wait_for(lambda: len(_responses(ws)) == 1)

        assert _responses(ws)[0]["request_id"] == "r-status"
        assert _responses(ws)[0]["status"] == 200

        slow_voice.gate.set()
        await _wait_for(lambda: len(_responses(ws)) == 2)
        assert _responses(ws)[1] == {
            "type": "proxy_response",
            "request_id": "r-finish",
            "status": 200,
            "body": {"dictation_id": "d-1", "state": "done", "text": "hello", "duration_ms": 10},
        }

    async def test_reply_goes_out_on_the_tunnel_that_is_live_when_it_finishes(self, tunnel, slow_voice):
        client, old_ws = tunnel
        await client._handle_control(_finish("r-1"), old_ws)
        await _wait_for(lambda: slow_voice.entered == 1)
        client._cancel_all_subtasks()
        new_ws = AsyncMock()
        client._ws = new_ws

        slow_voice.gate.set()
        await _wait_for(lambda: len(_responses(new_ws)) == 1)

        assert _responses(new_ws)[0]["request_id"] == "r-1"
        assert _responses(old_ws) == []
        assert slow_voice.cancelled == 0

    async def test_reply_without_a_tunnel_is_dropped_quietly(self, tunnel, slow_voice):
        client, ws = tunnel
        await client._handle_control(_finish("r-1"), ws)
        await _wait_for(lambda: slow_voice.entered == 1)
        client._ws = None

        slow_voice.gate.set()
        await _wait_for(lambda: not client._voice_proxy_tasks)

        assert _responses(ws) == []

    async def test_requests_beyond_the_in_flight_cap_are_503_voice_busy(self, tunnel, slow_voice):
        client, ws = tunnel
        cap = relay_module.MAX_VOICE_PROXY_TASKS
        for i in range(cap):
            await client._handle_control(_finish(f"r-{i}"), ws)
        await _wait_for(lambda: slow_voice.entered == cap)

        await client._handle_control(_proxy("r-over", "GET", STATUS), ws)
        await _wait_for(lambda: len(_responses(ws)) == 1)

        (reply,) = _responses(ws)
        assert reply["request_id"] == "r-over"
        assert reply["status"] == 503
        assert reply["body"]["detail"]["code"] == "voice_busy"

    async def test_stop_cancels_in_flight_voice_requests(self, tunnel, slow_voice):
        client, ws = tunnel
        await client._handle_control(_finish("r-1"), ws)
        await _wait_for(lambda: slow_voice.entered == 1)

        await client.stop()

        await _wait_for(lambda: slow_voice.cancelled == 1)

    async def test_unknown_voice_route_is_answered_404(self, tunnel):
        client, ws = tunnel

        await client._handle_control(_proxy("r-1", "PATCH", STATUS), ws)
        await _wait_for(lambda: len(_responses(ws)) == 1)

        assert _responses(ws)[0]["status"] == 404

    async def test_dispatch_crash_is_answered_500(self, tunnel, slow_voice, monkeypatch):
        client, ws = tunnel

        def explode():
            raise RuntimeError("boom")

        monkeypatch.setattr(slow_voice, "status", explode)

        await client._handle_control(_proxy("r-1", "GET", STATUS), ws)
        await _wait_for(lambda: len(_responses(ws)) == 1)

        assert _responses(ws)[0]["status"] == 500
        assert _responses(ws)[0]["request_id"] == "r-1"
