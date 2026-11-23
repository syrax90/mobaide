"""Tests for cloud voice input (PROTOCOL.md, "Delta 2026-11-22").

The speech service is an ``httpx.MockTransport``; no test opens
a socket.
"""
from __future__ import annotations

import asyncio
import base64
import dataclasses
import io
import json
import logging
import struct
import wave
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import httpx
import pytest
from starlette.testclient import TestClient

from central_server import app as app_module
from central_server.auth.models import UserTokenPayload
from central_server.cloud_voice import service as service_module
from central_server.cloud_voice.errors import CloudVoiceError
from central_server.cloud_voice.models import CloudVoiceStatus
from central_server.cloud_voice.service import MAX_OPEN_DICTATIONS, CloudVoiceService
from central_server.cloud_voice.speech_client import SpeechClient, Transcript
from central_server.config import CentralServerConfig
from central_server.db.database import Database
from central_server.relay.routing import RoutingTable, UserClient

URL = "/api/voice/cloud"
DICTATIONS = f"{URL}/dictations"

SPEECH_URL = "https://speech.test"
SPEECH_KEY = "test-speech-key-0123456789"
SPEECH_MODEL = "test-speech-model"
PROBE_USER = "health-check"

DAY = "2026-11-22"
YESTERDAY = "2026-11-21"
RESETS_AT = "2026-11-23T00:00:00Z"
# Usage rows are kept 35 days: the oldest day kept on DAY, and the one before.
OLDEST_KEPT_DAY = "2026-10-18"
EXPIRED_DAY = "2026-10-17"
LIMITS = {"sample_rate": 16000, "max_seconds": 120, "max_chunk_bytes": 65536}

OTHER_EMAIL = "other@test.local"
OTHER_PASSWORD = "otherpass123"

# Resolved from this file, never the CWD.
_FIXTURE_DIR = (
    Path(__file__).resolve().parents[2] / "tests" / "protocol_fixtures" / "dto" / "central"
)


def _pcm(seconds: float, amplitude: int = 8000) -> bytes:
    return struct.pack("<h", amplitude) * int(16000 * seconds)


SPEECH = _pcm(1.0)


def _transcript(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ]
        },
    )


def _status(code: int, **kwargs) -> Callable[[], httpx.Response]:
    return lambda: httpx.Response(code, **kwargs)


def _raises(error: Exception) -> Callable[[], httpx.Response]:
    def respond() -> httpx.Response:
        raise error

    return respond


class FakeSpeechService:
    """The hosted speech service, as an ``httpx.MockTransport`` handler."""

    def __init__(self) -> None:
        self.requests: list[SimpleNamespace] = []
        self.respond: Callable[[], httpx.Response] = lambda: _transcript("hello world")
        # While set to an un-set Event, every request waits for it.
        self.hold: asyncio.Event | None = None
        self.arrived = asyncio.Event()
        self.in_flight = 0
        self.max_in_flight = 0

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(
            SimpleNamespace(
                url=str(request.url),
                headers=request.headers,
                body=json.loads(request.content),
            )
        )
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self.arrived.set()
        try:
            if self.hold is not None:
                await self.hold.wait()
            return self.respond()
        finally:
            self.in_flight -= 1

    @property
    def probes(self) -> list[SimpleNamespace]:
        return [r for r in self.requests if r.body["user"] == PROBE_USER]

    @property
    def dictations(self) -> list[SimpleNamespace]:
        return [r for r in self.requests if r.body["user"] != PROBE_USER]


def _sent_pcm(request: SimpleNamespace) -> bytes:
    """The recording inside one request to the speech service."""
    _prompt, audio = request.body["messages"][0]["content"]
    prefix = "data:audio/wav;base64,"
    url = audio["audio_url"]["url"]
    assert url.startswith(prefix)
    with wave.open(io.BytesIO(base64.b64decode(url[len(prefix):]))) as recording:
        assert (
            recording.getnchannels(),
            recording.getsampwidth(),
            recording.getframerate(),
        ) == (1, 2, 16000)
        return recording.readframes(recording.getnframes())


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def speech() -> FakeSpeechService:
    return FakeSpeechService()


@pytest.fixture
def clock(monkeypatch) -> Clock:
    """Pins the service's clock and its UTC day."""
    pinned = Clock()
    monkeypatch.setattr(service_module, "time", pinned)
    monkeypatch.setattr(service_module, "_today", lambda: DAY)
    monkeypatch.setattr(service_module, "_resets_at", lambda: RESETS_AT)
    return pinned


@pytest.fixture
async def install(app, config, speech, clock):
    """Build a service that talks to the fake speech service and wire it
    into the app; keyword arguments override config fields."""
    built: list[CloudVoiceService] = []

    def _install(**overrides) -> CloudVoiceService:
        service = CloudVoiceService(
            app.state.db,
            app.state.routing,
            dataclasses.replace(config, **overrides),
            SpeechClient(
                base_url=SPEECH_URL,
                api_key=SPEECH_KEY,
                model=SPEECH_MODEL,
                transport=httpx.MockTransport(speech.handle),
            ),
        )
        app.state.cloud_voice_service = service
        built.append(service)
        return service

    yield _install
    for service in built:
        await service.aclose()


@pytest.fixture
def service(install) -> CloudVoiceService:
    return install()


@pytest.fixture
async def other_user(app) -> dict:
    await app.state.auth_service.register_user(OTHER_EMAIL, OTHER_PASSWORD)
    user = await app.state.db.get_user_by_email(OTHER_EMAIL)
    assert user is not None
    return user


@pytest.fixture
async def other_headers(app, other_user) -> dict[str, str]:
    _user_id, _role, token, _scheduled = await app.state.auth_service.login_user(
        OTHER_EMAIL, OTHER_PASSWORD
    )
    return {"Authorization": f"Bearer {token}"}


def _payload(user_id: str, email: str = "someone@test.local") -> UserTokenPayload:
    return UserTokenPayload(
        sub=user_id, email=email, role="viewer", token_type="dashboard", exp=None, iat=0
    )


def _make_user_client(user_id: str) -> UserClient:
    """Build a UserClient with no real WS — only the send_queue is used."""
    return UserClient(user_id=user_id, device_id="dev-x", websocket=None)  # type: ignore[arg-type]


async def _open(client, headers) -> str:
    resp = await client.post(DICTATIONS, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["dictation_id"]


async def _audio(client, headers, dictation_id: str, seq: int, pcm: bytes):
    return await client.post(
        f"{DICTATIONS}/{dictation_id}/audio",
        json={"seq": seq, "pcm16_b64": base64.b64encode(pcm).decode("ascii")},
        headers=headers,
    )


async def _finish(client, headers, dictation_id: str, last_seq: int):
    return await client.post(
        f"{DICTATIONS}/{dictation_id}/finish",
        json={"last_seq": last_seq},
        headers=headers,
    )


async def _record(client, headers, pcm: bytes = SPEECH) -> str:
    """Open a dictation and upload ``pcm`` as chunk 0."""
    dictation_id = await _open(client, headers)
    resp = await _audio(client, headers, dictation_id, 0, pcm)
    assert resp.status_code == 200, resp.text
    return dictation_id


async def _state(client, headers) -> str:
    resp = await client.get(URL, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["state"]


def _refusal(resp) -> tuple[int, str]:
    return resp.status_code, resp.json()["detail"]["code"]


async def _usage_rows(db: Database, user_id: str) -> dict[str, tuple[int, int]]:
    """The stored (seconds, requests) of each day the user has a row for."""
    cursor = await db.conn.execute(
        "SELECT day, seconds, requests FROM cloud_voice_usage WHERE user_id = ?",
        (user_id,),
    )
    return {row[0]: (row[1], row[2]) for row in await cursor.fetchall()}


class TestAccess:
    @pytest.mark.parametrize(
        "method, path, body",
        [
            ("GET", URL, None),
            ("PUT", f"{URL}/engines/a1b2c3d4e5f6", {"engine": "local"}),
            ("POST", DICTATIONS, None),
            ("POST", f"{DICTATIONS}/d1/audio", {"seq": 0, "pcm16_b64": ""}),
            ("POST", f"{DICTATIONS}/d1/finish", {"last_seq": 0}),
            ("DELETE", f"{DICTATIONS}/d1", None),
        ],
    )
    async def test_every_route_needs_a_signed_in_user(
        self, client, service, method, path, body
    ):
        resp = await client.request(method, path, json=body)
        assert resp.status_code == 401

    async def test_admin_cannot_dictate(
        self, client, service, speech, admin_api_auth_headers
    ):
        resp = await client.post(DICTATIONS, headers=admin_api_auth_headers)

        assert resp.status_code == 403
        assert speech.requests == []

    async def test_dictation_is_invisible_to_another_account(
        self, client, service, speech, api_auth_headers, other_headers
    ):
        dictation_id = await _record(client, api_auth_headers)
        await _open(client, other_headers)

        assert _refusal(
            await _audio(client, other_headers, dictation_id, 1, SPEECH)
        ) == (404, "dictation_not_found")
        assert _refusal(await _finish(client, other_headers, dictation_id, 0)) == (
            404,
            "dictation_not_found",
        )
        resp = await client.delete(
            f"{DICTATIONS}/{dictation_id}", headers=other_headers
        )
        assert resp.status_code == 204

        # The owner's recording is neither grown, finished nor dropped.
        assert speech.dictations == []
        done = await _finish(client, api_auth_headers, dictation_id, 0)
        assert done.json()["state"] == "done"
        assert done.json()["duration_ms"] == 1000
        assert [_sent_pcm(r) for r in speech.dictations] == [SPEECH]


class TestFeatureOff:
    """The default test app: no speech service is configured."""

    async def test_status_says_off_in_the_golden_shape(self, client, api_auth_headers):
        resp = await client.get(URL, headers=api_auth_headers)

        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "off"
        assert body["limits"] == LIMITS
        assert body["engines"] == {}
        assert _shape(body) == _shape(_golden_case("CloudVoiceStatus", "off"))

    async def test_dictation_and_cloud_choice_are_refused(
        self, client, api_auth_headers, registered_agent
    ):
        agent_id, _token = registered_agent

        assert _refusal(await client.post(DICTATIONS, headers=api_auth_headers)) == (
            403,
            "cloud_disabled",
        )
        assert _refusal(
            await client.put(
                f"{URL}/engines/{agent_id}",
                json={"engine": "cloud"},
                headers=api_auth_headers,
            )
        ) == (403, "cloud_disabled")
        resp = await client.get(URL, headers=api_auth_headers)
        assert resp.json()["engines"] == {}


class TestRollout:
    async def test_only_listed_accounts_are_offered(
        self, client, install, speech, api_auth_headers, other_headers
    ):
        install(cloud_voice_allowed_emails=(OTHER_EMAIL,))

        assert await _state(client, api_auth_headers) == "off"
        assert _refusal(await client.post(DICTATIONS, headers=api_auth_headers)) == (
            403,
            "cloud_disabled",
        )
        assert speech.requests == []
        assert await _state(client, other_headers) == "ready"

    @pytest.mark.parametrize(
        "overrides, offered",
        [
            ({}, True),
            ({"cloud_voice_url": None}, False),
            ({"cloud_voice_api_key": None}, False),
            ({"cloud_voice_model": None}, False),
            ({"cloud_voice_enabled": False}, False),
        ],
        ids=["all_set", "no_url", "no_key", "no_model", "kill_switch"],
    )
    async def test_feature_is_on_only_with_url_key_and_model(
        self, db, config, clock, monkeypatch, overrides, offered
    ):
        built: list[dict] = []

        class RecordingClient:
            def __init__(self, **kwargs) -> None:
                built.append(kwargs)

            async def transcribe(self, pcm, *, user, max_tokens, timeout) -> Transcript:
                return Transcript(text="", truncated=False)

            async def aclose(self) -> None:
                pass

        monkeypatch.setattr(service_module, "SpeechClient", RecordingClient)
        settings = {
            "cloud_voice_url": SPEECH_URL,
            "cloud_voice_api_key": SPEECH_KEY,
            "cloud_voice_model": SPEECH_MODEL,
            **overrides,
        }
        user_id = await db.create_user(email="owner@test.local", password_hash="h")

        service = CloudVoiceService.from_config(
            db, RoutingTable(), dataclasses.replace(config, **settings)
        )
        status = await service.status(_payload(user_id))
        await service.aclose()

        if offered:
            assert status["state"] == "ready"
            assert built == [
                {
                    "base_url": SPEECH_URL,
                    "api_key": SPEECH_KEY,
                    "model": SPEECH_MODEL,
                    "prompt": None,
                }
            ]
        else:
            assert status["state"] == "off"
            assert built == []


class TestDictation:
    async def test_recording_is_transcribed_and_counted(
        self, client, service, speech, api_auth_headers, viewer_user
    ):
        first, second = _pcm(1.0), _pcm(0.5, amplitude=-9000)

        opened = await client.post(DICTATIONS, headers=api_auth_headers)
        assert opened.status_code == 200
        dictation_id = opened.json()["dictation_id"]
        assert opened.json() == {"dictation_id": dictation_id, **LIMITS}
        resp = await _audio(client, api_auth_headers, dictation_id, 0, first)
        assert resp.json() == {"seq": 0, "duration_ms": 1000}
        resp = await _audio(client, api_auth_headers, dictation_id, 1, second)
        assert resp.json() == {"seq": 1, "duration_ms": 1500}
        done = await _finish(client, api_auth_headers, dictation_id, 1)

        assert done.status_code == 200
        assert done.json() == {
            "dictation_id": dictation_id,
            "state": "done",
            "text": "hello world",
            "duration_ms": 1500,
        }
        [sent] = speech.dictations
        assert sent.url == f"{SPEECH_URL}/v1/chat/completions"
        assert sent.headers["authorization"] == f"Bearer {SPEECH_KEY}"
        assert sent.body["model"] == SPEECH_MODEL
        assert _sent_pcm(sent) == first + second
        # The speech service tells users apart by the opaque account id; it
        # gets neither the e-mail address nor this server's key in the body.
        assert sent.body["user"] == viewer_user["id"]
        body_text = json.dumps(sent.body)
        assert viewer_user["email"] not in body_text
        assert SPEECH_KEY not in body_text
        # 1.5 s of audio counts as 2 s of today's use.
        status = (await client.get(URL, headers=api_auth_headers)).json()
        assert status["state"] == "ready"
        assert status["usage"] == {
            "used_seconds": 2,
            "limit_seconds": 0,
            "resets_at": RESETS_AT,
        }

    async def test_repeated_chunk_is_acknowledged_without_being_appended(
        self, client, service, speech, api_auth_headers
    ):
        dictation_id = await _record(client, api_auth_headers)

        again = await _audio(client, api_auth_headers, dictation_id, 0, SPEECH)

        assert again.status_code == 200
        assert again.json() == {"seq": 0, "duration_ms": 1000}
        await _finish(client, api_auth_headers, dictation_id, 0)
        assert [_sent_pcm(r) for r in speech.dictations] == [SPEECH]

    async def test_missing_chunk_is_refused_with_the_expected_seq(
        self, client, service, speech, api_auth_headers
    ):
        dictation_id = await _record(client, api_auth_headers)

        skipped = await _audio(client, api_auth_headers, dictation_id, 2, SPEECH)
        early = await _finish(client, api_auth_headers, dictation_id, 1)

        for resp in (skipped, early):
            assert _refusal(resp) == (409, "seq_gap")
            assert resp.json()["detail"]["expected_seq"] == 1
        assert speech.dictations == []

    @pytest.mark.parametrize(
        "pcm16_b64, refusal",
        [
            ("not base64 !!", (422, "bad_audio")),
            (base64.b64encode(b"\x01\x02\x03").decode("ascii"), (422, "bad_audio")),
            (base64.b64encode(bytes(65538)).decode("ascii"), (413, "chunk_too_large")),
        ],
        ids=["not_base64", "odd_byte_count", "over_chunk_limit"],
    )
    async def test_bad_chunk_is_refused_and_not_counted(
        self, client, service, api_auth_headers, pcm16_b64, refusal
    ):
        dictation_id = await _open(client, api_auth_headers)

        resp = await client.post(
            f"{DICTATIONS}/{dictation_id}/audio",
            json={"seq": 0, "pcm16_b64": pcm16_b64},
            headers=api_auth_headers,
        )

        assert _refusal(resp) == refusal
        accepted = await _audio(client, api_auth_headers, dictation_id, 0, SPEECH)
        assert accepted.json() == {"seq": 0, "duration_ms": 1000}

    async def test_audio_past_two_minutes_is_refused_and_the_rest_still_finishes(
        self, client, service, speech, api_auth_headers
    ):
        chunk = struct.pack("<h", 8000) * 32768
        dictation_id = await _open(client, api_auth_headers)
        for seq in range(58):
            resp = await _audio(client, api_auth_headers, dictation_id, seq, chunk)
            assert resp.status_code == 200

        over = await _audio(client, api_auth_headers, dictation_id, 58, chunk)

        assert _refusal(over) == (409, "too_long")
        assert over.json()["detail"]["max_seconds"] == 120
        done = await _finish(client, api_auth_headers, dictation_id, 57)
        assert done.json()["state"] == "done"
        [sent] = speech.dictations
        assert len(_sent_pcm(sent)) == 58 * 65536

    async def test_failed_finish_keeps_the_audio_and_the_result_is_kept(
        self, client, service, speech, clock, api_auth_headers
    ):
        dictation_id = await _record(client, api_auth_headers)
        speech.respond = _status(500)
        failed = await _finish(client, api_auth_headers, dictation_id, 0)
        assert _refusal(failed) == (503, "cloud_unavailable")

        speech.respond = lambda: _transcript("second try")
        # The failure marked the service down: it is asked again 20 s later,
        # which is also past the wait before the recording is sent again.
        clock.advance(20)
        done = await _finish(client, api_auth_headers, dictation_id, 0)
        again = await _finish(client, api_auth_headers, dictation_id, 0)

        assert done.json()["text"] == "second try"
        assert again.json() == done.json()
        assert [_sent_pcm(r) for r in speech.dictations] == [SPEECH, SPEECH]
        # Only the attempt that produced text counts against the allowance.
        status = (await client.get(URL, headers=api_auth_headers)).json()
        assert status["usage"]["used_seconds"] == 1

    async def test_finished_dictation_takes_no_more_audio(
        self, client, service, api_auth_headers
    ):
        dictation_id = await _record(client, api_auth_headers)
        await _finish(client, api_auth_headers, dictation_id, 0)

        resp = await _audio(client, api_auth_headers, dictation_id, 1, SPEECH)

        assert _refusal(resp) == (409, "dictation_finished")

    async def test_slow_transcription_answers_transcribing_and_runs_once(
        self, client, service, speech, api_auth_headers, monkeypatch
    ):
        dictation_id = await _record(client, api_auth_headers)
        speech.hold = asyncio.Event()
        monkeypatch.setattr(service_module, "FINISH_WAIT_SECONDS", 0.01)

        for _ in range(2):
            waiting = await _finish(client, api_auth_headers, dictation_id, 0)
            assert waiting.json() == {
                "dictation_id": dictation_id,
                "state": "transcribing",
                "text": None,
                "duration_ms": 1000,
            }
        late = await _audio(client, api_auth_headers, dictation_id, 1, SPEECH)
        assert _refusal(late) == (409, "dictation_finished")

        monkeypatch.setattr(service_module, "FINISH_WAIT_SECONDS", 20.0)
        speech.hold.set()
        done = await _finish(client, api_auth_headers, dictation_id, 0)

        assert done.json()["state"] == "done"
        assert done.json()["text"] == "hello world"
        assert len(speech.dictations) == 1

    async def test_new_dictation_replaces_the_open_one(
        self, client, service, api_auth_headers
    ):
        abandoned = await _record(client, api_auth_headers)

        current = await _open(client, api_auth_headers)

        assert _refusal(
            await _audio(client, api_auth_headers, abandoned, 1, SPEECH)
        ) == (404, "dictation_not_found")
        resp = await _audio(client, api_auth_headers, current, 0, SPEECH)
        assert resp.status_code == 200

    async def test_abort_drops_the_dictation_and_is_idempotent(
        self, client, service, speech, api_auth_headers
    ):
        dictation_id = await _record(client, api_auth_headers)

        for _ in range(2):
            resp = await client.delete(
                f"{DICTATIONS}/{dictation_id}", headers=api_auth_headers
            )
            assert resp.status_code == 204

        assert _refusal(await _finish(client, api_auth_headers, dictation_id, 0)) == (
            404,
            "dictation_not_found",
        )
        assert speech.dictations == []

    @pytest.mark.parametrize(
        "pcm",
        [_pcm(1.0, amplitude=0), _pcm(1.0, amplitude=99), _pcm(0.2)],
        ids=["silent", "below_the_speech_level", "too_short"],
    )
    async def test_recording_without_speech_is_empty_text_and_never_sent(
        self, client, service, speech, api_auth_headers, pcm
    ):
        dictation_id = await _record(client, api_auth_headers, pcm)

        done = await _finish(client, api_auth_headers, dictation_id, 0)

        assert done.status_code == 200
        assert done.json()["state"] == "done"
        assert done.json()["text"] == ""
        assert speech.dictations == []
        status = (await client.get(URL, headers=api_auth_headers)).json()
        assert status["usage"]["used_seconds"] == 0

    async def test_quiet_speech_at_the_speech_level_is_sent(
        self, client, service, speech, api_auth_headers
    ):
        quiet = _pcm(1.0, amplitude=-100)
        dictation_id = await _record(client, api_auth_headers, quiet)

        done = await _finish(client, api_auth_headers, dictation_id, 0)

        assert done.json()["text"] == "hello world"
        assert [_sent_pcm(r) for r in speech.dictations] == [quiet]

    async def test_transcript_cannot_carry_control_characters(
        self, client, service, speech, api_auth_headers
    ):
        speech.respond = lambda: _transcript("git status\r\n\x1b[2Jrm -rf\x00 \x9b31m")
        dictation_id = await _record(client, api_auth_headers)

        done = await _finish(client, api_auth_headers, dictation_id, 0)

        assert done.json()["text"] == "git status [2Jrm -rf 31m"

    async def test_logs_hold_no_text_no_audio_and_no_key(
        self, client, service, speech, api_auth_headers, caplog
    ):
        caplog.set_level(logging.DEBUG)
        speech.respond = lambda: _transcript("zebra umbrella seventeen")
        dictation_id = await _record(client, api_auth_headers)

        done = await _finish(client, api_auth_headers, dictation_id, 0)

        assert done.json()["text"] == "zebra umbrella seventeen"
        logged = caplog.text
        assert "cloud_voice.transcribed" in logged
        assert "zebra" not in logged
        assert SPEECH_KEY not in logged
        assert base64.b64encode(SPEECH).decode("ascii")[:64] not in logged


class TestThrottles:
    async def test_more_than_30_dictations_a_minute_is_told_busy(
        self, client, service, api_auth_headers, other_headers
    ):
        for _ in range(30):
            await _open(client, api_auth_headers)

        resp = await client.post(DICTATIONS, headers=api_auth_headers)

        assert _refusal(resp) == (503, "voice_busy")
        # The count is per account.
        await _open(client, other_headers)

    async def test_open_dictations_of_all_accounts_are_bounded(self, service):
        opened = [
            await service.create(_payload(f"user-{n}"))
            for n in range(MAX_OPEN_DICTATIONS)
        ]

        with pytest.raises(CloudVoiceError) as refused:
            await service.create(_payload("one-too-many"))
        assert (refused.value.status, refused.value.code) == (503, "voice_busy")

        await service.abort("user-0", opened[0])
        await service.create(_payload("one-too-many"))

    async def test_finished_dictation_holds_no_slot_and_keeps_its_text(
        self, service, db, monkeypatch
    ):
        monkeypatch.setattr(service_module, "MAX_OPEN_DICTATIONS", 2)
        users = [
            await db.create_user(email=f"user{n}@test.local", password_hash="h")
            for n in range(4)
        ]
        finished = await service.create(_payload(users[0]))
        await service.append(
            users[0], finished, 0, base64.b64encode(SPEECH).decode("ascii")
        )
        assert (await service.finish(users[0], finished, 0)).state == "done"

        # Two more recordings fit next to the finished one; a third does not.
        await service.create(_payload(users[1]))
        await service.create(_payload(users[2]))
        with pytest.raises(CloudVoiceError) as refused:
            await service.create(_payload(users[3]))

        assert (refused.value.status, refused.value.code) == (503, "voice_busy")
        again = await service.finish(users[0], finished, 0)
        assert (again.state, again.text) == ("done", "hello world")

    async def test_requests_beyond_the_concurrency_limit_are_told_busy(
        self, client, install, speech, clock, api_auth_headers, other_headers,
        monkeypatch,
    ):
        install(cloud_voice_max_concurrency=1)
        monkeypatch.setattr(service_module, "_GATE_WAIT_SECONDS", 0.01)
        mine = await _record(client, api_auth_headers)
        theirs = await _record(client, other_headers)
        speech.hold = asyncio.Event()
        speech.arrived.clear()

        first = asyncio.ensure_future(_finish(client, api_auth_headers, mine, 0))
        await asyncio.wait_for(speech.arrived.wait(), 5)
        refused = await _finish(client, other_headers, theirs, 0)
        speech.hold.set()

        assert _refusal(refused) == (503, "voice_busy")
        assert (await first).json()["state"] == "done"
        # The refused recording was kept, and the slot is free again once
        # the wait before another attempt is over.
        clock.advance(2)
        retried = await _finish(client, other_headers, theirs, 0)
        assert retried.json()["state"] == "done"
        assert speech.max_in_flight == 1

    @pytest.mark.parametrize(
        "respond, refusal",
        [
            (_status(429), (503, "voice_busy")),
            (_status(422), (503, "cloud_unavailable")),
        ],
        ids=["rate_limited", "rejected"],
    )
    async def test_refused_recording_is_not_sent_again_before_two_seconds(
        self, client, service, speech, clock, db, viewer_user, api_auth_headers,
        respond, refusal,
    ):
        """Each attempt uploads the whole recording: a client that asks again
        at once gets the first refusal back, and causes no request."""
        dictation_id = await _record(client, api_auth_headers)
        speech.respond = respond
        first = await _finish(client, api_auth_headers, dictation_id, 0)
        assert _refusal(first) == refusal
        sent = len(speech.requests)
        # From here on the speech service would answer: an attempt that got
        # through would end as "done".
        speech.respond = lambda: _transcript("hello world")

        hurried = [
            await _finish(client, api_auth_headers, dictation_id, 0) for _ in range(4)
        ]
        clock.advance(1)
        hurried.append(await _finish(client, api_auth_headers, dictation_id, 0))

        for resp in hurried:
            assert resp.status_code == first.status_code
            assert resp.json() == first.json()
        assert len(speech.requests) == sent
        assert len(speech.dictations) == 1

        # The refusals in between did not push the next attempt away.
        clock.advance(1)
        done = await _finish(client, api_auth_headers, dictation_id, 0)

        assert done.json()["text"] == "hello world"
        assert [_sent_pcm(r) for r in speech.dictations] == [SPEECH, SPEECH]
        assert await _usage_rows(db, viewer_user["id"]) == {DAY: (1, 1)}

    async def test_wait_between_attempts_doubles_up_to_16_seconds(
        self, client, service, speech, clock, api_auth_headers
    ):
        dictation_id = await _record(client, api_auth_headers)
        # Refused alone: the service stays up, so only the wait holds a
        # retry back.
        speech.respond = _status(422)

        for attempt, wait in enumerate((2, 4, 8, 16, 16), start=1):
            failed = await _finish(client, api_auth_headers, dictation_id, 0)
            assert _refusal(failed) == (503, "cloud_unavailable")
            assert len(speech.dictations) == attempt
            clock.advance(wait - 1)
            await _finish(client, api_auth_headers, dictation_id, 0)
            assert len(speech.dictations) == attempt, (
                f"sent again {wait - 1} s after failure {attempt}"
            )
            clock.advance(1)

        await _finish(client, api_auth_headers, dictation_id, 0)
        assert len(speech.dictations) == 6


class TestDailyLimits:
    async def test_account_at_its_limit_is_refused_until_the_reset(
        self, client, install, speech, db, viewer_user, other_user,
        api_auth_headers, other_headers,
    ):
        install(cloud_voice_user_daily_seconds=60)
        await db.add_cloud_voice_usage(viewer_user["id"], DAY, 60)
        await db.add_cloud_voice_usage(other_user["id"], YESTERDAY, 60)

        status = (await client.get(URL, headers=api_auth_headers)).json()
        refused = await client.post(DICTATIONS, headers=api_auth_headers)

        assert status["state"] == "limit_reached"
        assert status["usage"] == {
            "used_seconds": 60,
            "limit_seconds": 60,
            "resets_at": RESETS_AT,
        }
        assert _refusal(refused) == (429, "cloud_limit_reached")
        assert refused.json()["detail"]["resets_at"] == RESETS_AT
        # Another account, and yesterday's use, are not counted.
        assert await _state(client, other_headers) == "ready"
        await _open(client, other_headers)

    async def test_open_dictation_finishes_and_then_the_limit_applies(
        self, client, install, api_auth_headers
    ):
        install(cloud_voice_user_daily_seconds=2)
        dictation_id = await _record(client, api_auth_headers, _pcm(2.0))

        done = await _finish(client, api_auth_headers, dictation_id, 0)

        assert done.json()["state"] == "done"
        assert await _state(client, api_auth_headers) == "limit_reached"
        assert _refusal(await client.post(DICTATIONS, headers=api_auth_headers)) == (
            429,
            "cloud_limit_reached",
        )

    async def test_limit_of_all_accounts_reads_as_unavailable(
        self, client, install, speech, db, other_user, api_auth_headers
    ):
        install(cloud_voice_global_daily_seconds=100)
        await db.add_cloud_voice_usage(other_user["id"], DAY, 100)

        assert await _state(client, api_auth_headers) == "unavailable"
        assert _refusal(await client.post(DICTATIONS, headers=api_auth_headers)) == (
            503,
            "cloud_unavailable",
        )
        assert speech.requests == []


    async def test_zero_limits_mean_no_limit(
        self, client, install, speech, db, viewer_user, other_user, api_auth_headers
    ):
        install(cloud_voice_user_daily_seconds=0, cloud_voice_global_daily_seconds=0)
        await db.add_cloud_voice_usage(viewer_user["id"], DAY, 86_400)
        await db.add_cloud_voice_usage(other_user["id"], DAY, 10_000_000)

        status = (await client.get(URL, headers=api_auth_headers)).json()

        assert status["state"] == "ready"
        assert status["usage"]["limit_seconds"] == 0
        await _open(client, api_auth_headers)


class TestUsageCharge:
    """A recording is charged before it is sent, and given back only when the
    speech service refuses it or cannot be reached: a client must not get free
    requests by abandoning each one, or by sending what is never answered in
    time."""

    @pytest.mark.parametrize("abandon", ["delete", "new_dictation"])
    async def test_recording_abandoned_during_the_request_stays_charged(
        self, client, install, speech, api_auth_headers, abandon
    ):
        install(cloud_voice_max_concurrency=1)
        dictation_id = await _record(client, api_auth_headers)
        speech.hold = asyncio.Event()
        speech.arrived.clear()
        finishing = asyncio.ensure_future(
            _finish(client, api_auth_headers, dictation_id, 0)
        )
        await asyncio.wait_for(speech.arrived.wait(), 5)

        if abandon == "delete":
            resp = await client.delete(
                f"{DICTATIONS}/{dictation_id}", headers=api_auth_headers
            )
            assert resp.status_code == 204
        else:
            await _open(client, api_auth_headers)

        assert _refusal(await finishing) == (404, "dictation_not_found")
        status = (await client.get(URL, headers=api_auth_headers)).json()
        assert status["usage"]["used_seconds"] == 1
        # The abandoned request gave its slot back: the next one is served,
        # and charged on top.
        speech.hold = None
        done = await _finish(
            client, api_auth_headers, await _record(client, api_auth_headers), 0
        )
        assert done.json()["state"] == "done"
        status = (await client.get(URL, headers=api_auth_headers)).json()
        assert status["usage"]["used_seconds"] == 2

    @pytest.mark.parametrize(
        "respond, refusal",
        [
            (_status(500), (503, "cloud_unavailable")),
            (_raises(httpx.ConnectError("refused")), (503, "cloud_unavailable")),
            (_status(429), (503, "voice_busy")),
            (_status(422), (503, "cloud_unavailable")),
            (
                _status(403, json={"error": {"message": "This user is blocked."}}),
                (403, "cloud_disabled"),
            ),
        ],
        ids=["http_500", "unreachable", "rate_limited", "rejected", "user_blocked"],
    )
    async def test_refused_request_is_given_back(
        self, client, service, speech, db, viewer_user, api_auth_headers,
        respond, refusal,
    ):
        await db.add_cloud_voice_usage(viewer_user["id"], DAY, 7)
        dictation_id = await _record(client, api_auth_headers, _pcm(2.0))
        speech.respond = respond

        failed = await _finish(client, api_auth_headers, dictation_id, 0)

        assert _refusal(failed) == refusal
        assert len(speech.dictations) == 1
        # Earlier use of the day is untouched; the refused request is gone.
        assert await _usage_rows(db, viewer_user["id"]) == {DAY: (7, 1)}

    async def test_request_the_service_does_not_answer_in_time_stays_charged(
        self, client, service, speech, db, viewer_user, api_auth_headers
    ):
        await db.add_cloud_voice_usage(viewer_user["id"], DAY, 7)
        dictation_id = await _record(client, api_auth_headers, _pcm(2.0))
        speech.respond = _raises(httpx.ReadTimeout("timed out"))

        failed = await _finish(client, api_auth_headers, dictation_id, 0)

        assert _refusal(failed) == (503, "cloud_unavailable")
        assert await _usage_rows(db, viewer_user["id"]) == {DAY: (9, 2)}

    async def test_request_past_the_overall_deadline_fails_and_stays_charged(
        self, client, install, speech, clock, db, viewer_user, api_auth_headers,
        monkeypatch,
    ):
        install(cloud_voice_max_concurrency=1)
        monkeypatch.setattr(service_module, "_TRANSCRIBE_DEADLINE_SECONDS", 0.01)
        dictation_id = await _record(client, api_auth_headers)
        # A speech service that accepts the request and never answers.
        speech.hold = asyncio.Event()

        failed = await _finish(client, api_auth_headers, dictation_id, 0)

        assert _refusal(failed) == (503, "cloud_unavailable")
        assert await _state(client, api_auth_headers) == "unavailable"
        assert await _usage_rows(db, viewer_user["id"]) == {DAY: (1, 1)}
        # The recording and the slot are both still there for another try,
        # once the service that was marked down is asked again.
        monkeypatch.setattr(service_module, "_TRANSCRIBE_DEADLINE_SECONDS", 75.0)
        speech.hold = None
        clock.advance(20)
        done = await _finish(client, api_auth_headers, dictation_id, 0)
        assert done.json()["text"] == "hello world"
        assert await _usage_rows(db, viewer_user["id"]) == {DAY: (2, 2)}


class TestSpeechServiceHealth:
    async def test_one_answer_is_trusted_five_minutes_for_status_one_for_a_dictation(
        self, service, speech, clock, viewer_user
    ):
        user = _payload(viewer_user["id"])

        assert (await service.status(user))["state"] == "ready"
        [probe] = speech.probes
        assert probe.body["max_tokens"] == 1
        assert speech.dictations == []

        clock.advance(299)
        assert (await service.status(user))["state"] == "ready"
        assert len(speech.probes) == 1

        # A recording is about to start: the answer must be a minute fresh.
        await service.create(user)
        assert len(speech.probes) == 2

        clock.advance(301)
        assert (await service.status(user))["state"] == "ready"
        assert len(speech.probes) == 3

    async def test_down_service_is_asked_at_most_every_5_seconds(
        self, service, speech, clock, viewer_user
    ):
        user = _payload(viewer_user["id"])
        speech.respond = _status(503)

        assert (await service.status(user))["state"] == "unavailable"
        with pytest.raises(CloudVoiceError) as refused:
            await service.create(user)
        assert (refused.value.status, refused.value.code) == (503, "cloud_unavailable")
        clock.advance(4)
        assert (await service.status(user))["state"] == "unavailable"
        assert len(speech.probes) == 1

        speech.respond = lambda: _transcript("")
        clock.advance(2)
        assert (await service.status(user))["state"] == "ready"
        assert len(speech.probes) == 2
        await service.create(user)

    async def test_concurrent_callers_share_one_probe(
        self, install, speech, db, viewer_user, monkeypatch
    ):
        # A global limit, so that each caller reads the total.
        service = install(cloud_voice_global_daily_seconds=72_000)
        user = _payload(viewer_user["id"])
        callers = 5
        asked = 0
        all_asked = asyncio.Event()
        read_total = db.get_cloud_voice_seconds_total

        # The last read before the health check: a caller that returns from
        # it runs on to the probe without yielding to the loop again.
        async def counted(day: str) -> int:
            nonlocal asked
            total = await read_total(day)
            asked += 1
            if asked == callers:
                all_asked.set()
            return total

        monkeypatch.setattr(db, "get_cloud_voice_seconds_total", counted)
        speech.hold = asyncio.Event()

        waiting = [asyncio.ensure_future(service.status(user)) for _ in range(callers)]
        await asyncio.wait_for(all_asked.wait(), 5)
        await asyncio.wait_for(speech.arrived.wait(), 5)
        speech.hold.set()
        states = [status["state"] for status in await asyncio.gather(*waiting)]

        assert states == ["ready"] * callers
        assert len(speech.probes) == 1

    async def test_status_answers_from_the_last_known_state_when_the_probe_hangs(
        self, service, speech, viewer_user, monkeypatch
    ):
        user = _payload(viewer_user["id"])
        monkeypatch.setattr(service_module, "_STATUS_PROBE_WAIT_SECONDS", 0.01)
        speech.hold = asyncio.Event()
        speech.respond = _status(503)

        for _ in range(2):
            status = await asyncio.wait_for(service.status(user), 5)
            assert status["state"] == "ready"
        assert len(speech.probes) == 1

        # The probe ran on: the next request gets its result without another.
        monkeypatch.setattr(service_module, "_STATUS_PROBE_WAIT_SECONDS", 2.0)
        speech.hold.set()
        assert (await service.status(user))["state"] == "unavailable"
        assert len(speech.probes) == 1

    @pytest.mark.parametrize("code", [400, 413, 422])
    async def test_recording_the_speech_service_rejects_fails_alone(
        self, client, service, speech, api_auth_headers, other_headers, code
    ):
        dictation_id = await _record(client, api_auth_headers)
        speech.respond = _status(code)

        failed = await _finish(client, api_auth_headers, dictation_id, 0)

        assert _refusal(failed) == (503, "cloud_unavailable")
        speech.respond = lambda: _transcript("hello world")
        # No outage for anyone; the next status asks the service afresh
        # instead of trusting the answer it gave before the rejection.
        assert await _state(client, other_headers) == "ready"
        assert await _state(client, api_auth_headers) == "ready"
        assert len(speech.probes) == 2
        await _open(client, other_headers)

    async def test_dictation_that_keeps_failing_does_not_postpone_the_next_probe(
        self, client, service, speech, clock, api_auth_headers
    ):
        dictation_id = await _record(client, api_auth_headers)
        speech.respond = _status(500)

        first = await _finish(client, api_auth_headers, dictation_id, 0)
        clock.advance(3)
        second = await _finish(client, api_auth_headers, dictation_id, 0)

        for failed in (first, second):
            assert _refusal(failed) == (503, "cloud_unavailable")
        # 6 s after the service went down, whatever failed in between.
        speech.respond = lambda: _transcript("hello world")
        clock.advance(3)
        assert await _state(client, api_auth_headers) == "ready"
        assert len(speech.probes) == 2

    async def test_repeated_finish_while_down_sends_no_recording_and_one_probe_per_5_seconds(
        self, client, service, speech, clock, api_auth_headers
    ):
        dictation_id = await _record(client, api_auth_headers)
        speech.respond = _status(500)
        failed = await _finish(client, api_auth_headers, dictation_id, 0)
        assert _refusal(failed) == (503, "cloud_unavailable")
        assert (len(speech.dictations), len(speech.probes)) == (1, 1)

        # Every step is past the 2 s wait of the recording itself: only the
        # outage holds these back.
        for seconds, probes in ((4, 1), (1, 2), (4, 2)):
            clock.advance(seconds)
            for _ in range(3):
                refused = await _finish(client, api_auth_headers, dictation_id, 0)
                assert _refusal(refused) == (503, "cloud_unavailable")
            assert (len(speech.dictations), len(speech.probes)) == (1, probes)

        # The probe that finds the service back lets the same finish through.
        speech.respond = lambda: _transcript("hello world")
        clock.advance(1)
        done = await _finish(client, api_auth_headers, dictation_id, 0)

        assert done.json()["text"] == "hello world"
        assert (len(speech.dictations), len(speech.probes)) == (2, 3)

    async def test_probe_that_never_completes_is_cut_off_and_marks_the_service_down(
        self, service, speech, clock, viewer_user, monkeypatch
    ):
        user = _payload(viewer_user["id"])
        monkeypatch.setattr(service_module, "_PROBE_DEADLINE_SECONDS", 0.01)
        # A speech service that accepts the probe and never answers.
        speech.hold = asyncio.Event()

        with pytest.raises(CloudVoiceError) as refused:
            await asyncio.wait_for(service.create(user), 5)

        assert (refused.value.status, refused.value.code) == (503, "cloud_unavailable")
        assert speech.in_flight == 0
        assert (await service.status(user))["state"] == "unavailable"
        assert len(speech.probes) == 1

        # The cut-off probe does not stand in the way of the next one.
        speech.hold = None
        clock.advance(20)
        assert (await service.status(user))["state"] == "ready"
        assert len(speech.probes) == 2

    async def test_rate_limited_probe_counts_as_alive(
        self, service, speech, viewer_user
    ):
        speech.respond = _status(429)

        assert (await service.status(_payload(viewer_user["id"])))["state"] == "ready"
        assert len(speech.probes) == 1

    @pytest.mark.parametrize(
        "respond, refusal, state_after",
        [
            (_status(500), (503, "cloud_unavailable"), "unavailable"),
            (_raises(httpx.ReadTimeout("timed out")), (503, "cloud_unavailable"), "unavailable"),
            (_raises(httpx.ConnectError("refused")), (503, "cloud_unavailable"), "unavailable"),
            (_status(401), (503, "cloud_unavailable"), "unavailable"),
            (_status(403, json={"error": "forbidden"}), (503, "cloud_unavailable"), "unavailable"),
            (
                _status(403, json={"error": "Request blocked by policy."}),
                (503, "cloud_unavailable"),
                "unavailable",
            ),
            (
                _status(302, headers={"Location": "https://elsewhere.test/v1/chat/completions"}),
                (503, "cloud_unavailable"),
                "unavailable",
            ),
            (_status(200, text="<html>"), (503, "cloud_unavailable"), "unavailable"),
            (_status(200, json={"choices": []}), (503, "cloud_unavailable"), "unavailable"),
            (_status(429), (503, "voice_busy"), "ready"),
        ],
        ids=[
            "http_500", "timeout", "unreachable", "key_rejected", "forbidden",
            "blocked_but_not_this_user",
            "redirect_not_followed", "not_json", "no_choices", "rate_limited",
        ],
    )
    async def test_speech_service_failure_reaches_the_client_as(
        self, client, service, speech, api_auth_headers, respond, refusal, state_after
    ):
        dictation_id = await _record(client, api_auth_headers)
        speech.respond = respond

        failed = await _finish(client, api_auth_headers, dictation_id, 0)

        assert _refusal(failed) == refusal
        assert len(speech.dictations) == 1
        # Without another request: a failure marks the service down at once,
        # and a rate limit leaves it up.
        assert await _state(client, api_auth_headers) == state_after
        assert len(speech.requests) == 2

    async def test_user_the_speech_service_blocks_is_switched_off_alone(
        self, client, service, speech, clock, api_auth_headers, other_headers
    ):
        dictation_id = await _record(client, api_auth_headers)
        speech.respond = _status(
            403, json={"error": {"message": "This user is blocked."}}
        )

        refused = await _finish(client, api_auth_headers, dictation_id, 0)

        assert _refusal(refused) == (403, "cloud_disabled")
        assert await _state(client, api_auth_headers) == "off"
        assert _refusal(await client.post(DICTATIONS, headers=api_auth_headers)) == (
            403,
            "cloud_disabled",
        )
        speech.respond = lambda: _transcript("hello world")
        assert await _state(client, other_headers) == "ready"
        await _open(client, other_headers)
        assert len(speech.dictations) == 1

        # Remembered for ten minutes, then the speech service decides again.
        clock.advance(599)
        assert await _state(client, api_auth_headers) == "off"
        clock.advance(2)
        assert await _state(client, api_auth_headers) == "ready"


class TestEngineChoice:
    async def test_choice_is_stored_and_returned_with_the_status(
        self, client, service, api_auth_headers, other_headers, registered_agent
    ):
        agent_id, _token = registered_agent

        saved = await client.put(
            f"{URL}/engines/{agent_id}",
            json={"engine": "cloud"},
            headers=api_auth_headers,
        )

        assert saved.status_code == 200
        assert saved.json()["state"] == "ready"
        assert saved.json()["engines"] == {agent_id: "cloud"}
        mine = (await client.get(URL, headers=api_auth_headers)).json()
        theirs = (await client.get(URL, headers=other_headers)).json()
        assert mine["engines"] == {agent_id: "cloud"}
        assert theirs["engines"] == {}

    async def test_saving_a_choice_never_asks_the_speech_service(
        self, client, service, speech, api_auth_headers, registered_agent
    ):
        agent_id, _token = registered_agent
        # A speech service that never answers must not hold the save back.
        speech.hold = asyncio.Event()

        saved = await asyncio.wait_for(
            client.put(
                f"{URL}/engines/{agent_id}",
                json={"engine": "cloud"},
                headers=api_auth_headers,
            ),
            5,
        )

        assert saved.status_code == 200
        assert saved.json()["engines"] == {agent_id: "cloud"}
        assert speech.requests == []

    async def test_agent_of_another_account_is_not_found(
        self, client, service, db, api_auth_headers, viewer_user, other_user
    ):
        their_agent, _token = await db.register_agent("their-agent", other_user["id"])

        for agent_id in (their_agent, "000000000000"):
            resp = await client.put(
                f"{URL}/engines/{agent_id}",
                json={"engine": "local"},
                headers=api_auth_headers,
            )
            assert _refusal(resp) == (404, "agent_not_found")

        assert await db.get_voice_engines(viewer_user["id"]) == {}
        assert await db.get_voice_engines(other_user["id"]) == {}

    async def test_change_is_pushed_once_to_the_owners_devices_only(
        self, app, service, viewer_user, other_user, registered_agent
    ):
        agent_id, _token = registered_agent
        routing: RoutingTable = app.state.routing
        owner_clients = [_make_user_client(viewer_user["id"]) for _ in range(2)]
        other_client = _make_user_client(other_user["id"])
        for c in owner_clients:
            routing.register_user_client(viewer_user["id"], c)
        routing.register_user_client(other_user["id"], other_client)
        user = _payload(viewer_user["id"])

        await service.set_engine(user, agent_id, "cloud")
        await service.set_engine(user, agent_id, "cloud")

        for c in owner_clients:
            envelope = json.loads(c.send_queue.get_nowait())
            assert envelope == {
                "type": "cloud_voice.changed",
                "payload": {"engines": {agent_id: "cloud"}},
            }
            assert set(envelope["payload"]) == set(
                _golden_case("CloudVoiceChangedPayload", "two_agents")
            )
            with pytest.raises(asyncio.QueueEmpty):
                c.send_queue.get_nowait()
        with pytest.raises(asyncio.QueueEmpty):
            other_client.send_queue.get_nowait()


def _golden(name: str) -> dict:
    doc = json.loads((_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    assert doc["type"] == name
    assert doc["cases"], f"{name} has no cases — the golden would assert nothing"
    return doc


def _golden_case(name: str, case: str) -> dict:
    [wire] = [c["wire"] for c in _golden(name)["cases"] if c["name"] == case]
    return wire


def _shape(value):
    """The keys of a JSON value, recursively, with leaf types."""
    if isinstance(value, dict):
        return {key: _shape(item) for key, item in value.items()}
    return type(value).__name__


@pytest.mark.parametrize(
    "case",
    [pytest.param(c["wire"], id=c["name"]) for c in _golden("CloudVoiceStatus")["cases"]],
)
def test_status_golden_fixture_round_trips_through_the_server_model(case):
    assert CloudVoiceStatus.model_validate(case).model_dump(mode="json") == case


class TestDatabase:
    async def test_engine_choice_reports_a_change_only_once(self, db: Database):
        owner = await db.create_user(email="owner@test.local", password_hash="h")
        agent_id, _token = await db.register_agent("pc", owner)

        assert await db.set_voice_engine(owner, agent_id, "cloud") is True
        assert await db.set_voice_engine(owner, agent_id, "cloud") is False
        assert await db.set_voice_engine(owner, agent_id, "local") is True
        assert await db.get_voice_engines(owner) == {agent_id: "local"}

    async def test_usage_adds_up_per_account_and_day(self, db: Database):
        first = await db.create_user(email="first@test.local", password_hash="h")
        second = await db.create_user(email="second@test.local", password_hash="h")
        await db.add_cloud_voice_usage(first, DAY, 30)
        await db.add_cloud_voice_usage(first, DAY, 12)
        await db.add_cloud_voice_usage(second, DAY, 5)
        await db.add_cloud_voice_usage(first, YESTERDAY, 99)

        assert await db.get_cloud_voice_seconds(first, DAY) == 42
        assert await db.get_cloud_voice_seconds_total(DAY) == 47

        await db.purge_cloud_voice_usage(DAY)

        assert await db.get_cloud_voice_seconds(first, YESTERDAY) == 0
        assert await db.get_cloud_voice_seconds(first, DAY) == 42

    async def test_refund_takes_back_one_request_and_stops_at_zero(self, db: Database):
        first = await db.create_user(email="first@test.local", password_hash="h")
        second = await db.create_user(email="second@test.local", password_hash="h")
        await db.add_cloud_voice_usage(first, DAY, 30)
        await db.add_cloud_voice_usage(first, DAY, 12)
        await db.add_cloud_voice_usage(first, YESTERDAY, 9)
        await db.add_cloud_voice_usage(second, DAY, 5)

        await db.refund_cloud_voice_usage(first, DAY, 12)

        assert await _usage_rows(db, first) == {DAY: (30, 1), YESTERDAY: (9, 1)}
        assert await _usage_rows(db, second) == {DAY: (5, 1)}

        # More than is left (the row was purged and written again meanwhile),
        # and a day with no row at all.
        await db.refund_cloud_voice_usage(second, DAY, 60)
        await db.refund_cloud_voice_usage(second, DAY, 60)
        await db.refund_cloud_voice_usage(second, YESTERDAY, 60)

        assert await _usage_rows(db, second) == {DAY: (0, 0)}
        assert await db.get_cloud_voice_seconds_total(DAY) == 30

    async def test_delete_user_purges_choices_and_usage(self, db: Database):
        owner = await db.create_user(email="owner@test.local", password_hash="h")
        survivor = await db.create_user(email="other@test.local", password_hash="h")
        owner_agent, _token = await db.register_agent("pc", owner)
        survivor_agent, _token = await db.register_agent("pc", survivor)
        for user_id, agent_id in ((owner, owner_agent), (survivor, survivor_agent)):
            await db.set_voice_engine(user_id, agent_id, "cloud")
            await db.add_cloud_voice_usage(user_id, DAY, 30)

        await db.delete_user(owner)

        assert await db.get_voice_engines(owner) == {}
        assert await db.get_cloud_voice_seconds(owner, DAY) == 0
        assert await db.get_voice_engines(survivor) == {survivor_agent: "cloud"}
        assert await db.get_cloud_voice_seconds_total(DAY) == 30

    async def test_delete_agent_purges_its_choice(self, db: Database):
        owner = await db.create_user(email="owner@test.local", password_hash="h")
        gone, _token = await db.register_agent("old-pc", owner)
        kept, _token = await db.register_agent("new-pc", owner)
        await db.set_voice_engine(owner, gone, "cloud")
        await db.set_voice_engine(owner, kept, "local")

        await db.delete_agent(gone)

        assert await db.get_voice_engines(owner) == {kept: "local"}


class _NoonOfDay(datetime):
    """``datetime`` with the clock stopped at noon UTC of ``DAY``."""

    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 22, 12, tzinfo=tz)


class TestUsageRetention:
    """The privacy policy promises 35 days, whether or not cloud voice input
    is still on: the sweep belongs to the server, not to the feature."""

    @pytest.fixture(autouse=True)
    def _pinned_day(self, monkeypatch):
        monkeypatch.setattr(service_module, "datetime", _NoonOfDay)

    @staticmethod
    async def _seed(db: Database) -> str:
        owner = await db.create_user(email="owner@test.local", password_hash="h")
        for day in (EXPIRED_DAY, OLDEST_KEPT_DAY, DAY):
            await db.add_cloud_voice_usage(owner, day, 30)
        return owner

    async def test_startup_drops_expired_usage_with_the_feature_off(self, tmp_path):
        config = CentralServerConfig(
            jwt_secret_key="test-secret-key-for-usage-retention",
            db_path=str(tmp_path / "startup.db"),
            manifest_store_path=str(tmp_path / "manifest.json"),
            email_verification_required=False,
        )
        assert config.cloud_voice_missing
        db = Database(config.db_path)
        await db.connect()
        try:
            owner = await self._seed(db)
        finally:
            await db.close()

        # TestClient as a context manager runs the real lifespan.
        with TestClient(app_module.create_app(config=config)):
            pass

        db = Database(config.db_path)
        await db.connect()
        try:
            assert sorted(await _usage_rows(db, owner)) == [OLDEST_KEPT_DAY, DAY]
        finally:
            await db.close()

    async def test_hourly_sweep_drops_expired_usage(self, db: Database, monkeypatch):
        owner = await self._seed(db)
        sleeps = 0

        # One pass of the loop: the first sleep returns, the second ends it.
        async def one_pass(_seconds: float) -> None:
            nonlocal sleeps
            sleeps += 1
            if sleeps > 1:
                raise asyncio.CancelledError

        monkeypatch.setattr(app_module.asyncio, "sleep", one_pass)

        with pytest.raises(asyncio.CancelledError):
            await app_module._retention_purge_loop(db)

        assert sorted(await _usage_rows(db, owner)) == [OLDEST_KEPT_DAY, DAY]


class TestMigration:
    async def test_v24_creates_tables_on_existing_db(self, tmp_path):
        db_path = str(tmp_path / "pre_v24.db")
        db = Database(db_path)
        await db.connect()
        owner = await db.create_user(email="owner@test.local", password_hash="h")
        agent_id, _token = await db.register_agent("pc", owner)
        cursor = await db.conn.execute("PRAGMA user_version")
        head = (await cursor.fetchone())[0]
        # Simulate a pre-v24 on-disk schema: drop the new tables and roll
        # user_version back so reopening replays the v24 block.
        await db.conn.execute("DROP TABLE voice_engine_choices")
        await db.conn.execute("DROP TABLE cloud_voice_usage")
        await db.conn.execute("PRAGMA user_version = 23")
        await db.conn.commit()
        await db.close()

        db = Database(db_path)
        await db.connect()
        cursor = await db.conn.execute("PRAGMA user_version")
        assert (await cursor.fetchone())[0] == head
        assert head >= 24
        assert await db.set_voice_engine(owner, agent_id, "cloud") is True
        await db.add_cloud_voice_usage(owner, DAY, 30)
        await db.close()

        # Idempotent: a second reopen at head keeps the rows.
        db = Database(db_path)
        await db.connect()
        cursor = await db.conn.execute("PRAGMA user_version")
        assert (await cursor.fetchone())[0] == head
        assert await db.get_voice_engines(owner) == {agent_id: "cloud"}
        assert await db.get_cloud_voice_seconds(owner, DAY) == 30
        await db.close()
