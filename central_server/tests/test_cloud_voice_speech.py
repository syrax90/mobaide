"""Pure tests of the cloud voice speech client and its transcript tidying.

The speech service is an ``httpx.MockTransport``: no network, no credentials.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import struct
import unicodedata
import wave

import httpx
import pytest

from central_server.cloud_voice.audio import tidy_transcript
from central_server.cloud_voice.speech_client import SpeechClient


@pytest.mark.parametrize(
    "base_url, expected",
    [
        ("https://speech.test", "https://speech.test/v1/chat/completions"),
        ("https://speech.test/", "https://speech.test/v1/chat/completions"),
        ("https://speech.test/v1", "https://speech.test/v1/chat/completions"),
        ("https://speech.test/v1/", "https://speech.test/v1/chat/completions"),
        (
            "https://speech.test/gateway/v1",
            "https://speech.test/gateway/v1/chat/completions",
        ),
    ],
)
async def test_base_url_with_or_without_v1_reaches_chat_completions(base_url, expected):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        )

    client = SpeechClient(
        base_url=base_url,
        api_key="test-key",
        model="test-model",
        transport=httpx.MockTransport(handler),
    )
    try:
        transcript = await client.transcribe(
            bytes(3200), user="u1", max_tokens=8, timeout=1.0
        )
    finally:
        await client.aclose()

    assert seen == [expected]
    assert transcript.text == "ok"


async def test_speech_service_url_never_reaches_the_server_log(caplog):
    """httpx logs every request line, URL included, at INFO; the service's
    address is private and must stay out of the server log."""
    caplog.set_level(logging.INFO)
    host = "private-speech-host.invalid"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        )

    client = SpeechClient(
        base_url=f"https://{host}",
        api_key="test-key",
        model="test-model",
        transport=httpx.MockTransport(handler),
    )
    try:
        await client.transcribe(bytes(3200), user="u1", max_tokens=8, timeout=1.0)
    finally:
        await client.aclose()

    assert host not in caplog.text


async def test_request_body_is_valid_json_whatever_the_settings_hold():
    """The body is put together as bytes around the base64 audio instead of
    by one ``json.dumps``, so nothing but this test proves it stays JSON
    when a configured prompt or model name needs escaping."""
    model = 'test-"model"\\v1'
    prompt = 'Type "exactly" what is said.\nNo more \\ no less — überprüft, 確認.'
    user = 'account-"1"\\'
    pcm = struct.pack("<h", -1234) * 1600
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        )

    client = SpeechClient(
        base_url="https://speech.test",
        api_key="test-key",
        model=model,
        prompt=prompt,
        transport=httpx.MockTransport(handler),
    )
    try:
        await client.transcribe(pcm, user=user, max_tokens=80, timeout=1.0)
    finally:
        await client.aclose()

    [request] = seen
    assert request.headers["content-type"] == "application/json"
    body = json.loads(request.content)
    [message] = body.pop("messages")
    assert body == {
        "model": model,
        "temperature": 0,
        "seed": 0,
        "max_tokens": 80,
        "user": user,
    }
    assert set(message) == {"role", "content"}
    assert message["role"] == "user"
    text_part, audio_part = message["content"]
    assert text_part == {"type": "text", "text": prompt}
    assert set(audio_part) == {"type", "audio_url"}
    assert audio_part["type"] == "audio_url"
    assert set(audio_part["audio_url"]) == {"url"}
    prefix = "data:audio/wav;base64,"
    url = audio_part["audio_url"]["url"]
    assert url.startswith(prefix)
    with wave.open(io.BytesIO(base64.b64decode(url[len(prefix):]))) as recording:
        assert (
            recording.getnchannels(),
            recording.getsampwidth(),
            recording.getframerate(),
        ) == (1, 2, 16000)
        assert recording.readframes(recording.getnframes()) == pcm


def test_tidy_transcript_leaves_no_control_or_line_separator_character():
    """Dictated text is typed into a terminal: an Enter or an escape sequence
    in it would run something the user never said."""
    codes = [*range(0x00, 0x20), *range(0x7F, 0xA0), 0x2028, 0x2029]
    raw = "".join(f"w{code:x}{chr(code)}" for code in codes)

    tidy = tidy_transcript(raw)

    assert [c for c in tidy if unicodedata.category(c) in ("Cc", "Zl", "Zp")] == []
    assert tidy == " ".join(f"w{code:x}" for code in codes)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("thank you " * 40, "thank you"),
        ("a" * 200, "a"),
        ("no no no, that is wrong", "no no no, that is wrong"),
        ("set it to 1000000000 now", "set it to 1000000000 now"),
    ],
    ids=["phrase_loop", "character_loop", "spoken_repeat_stays", "number_stays"],
)
def test_tidy_transcript_collapses_only_a_runaway_repetition(raw, expected):
    assert tidy_transcript(raw) == expected
