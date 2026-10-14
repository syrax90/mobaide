"""End-to-end voice input over the real relay: enable → ready → dictate →
finish → remove, from the headless mobile client through the central server
to the agent subprocess.

The harness agent runs with ``MOBAIDE_VOICE_FAKE=1``: the
real install state machine, lock and helper worker process run against a
tiny generated model and the deterministic fake engine, so nothing is
downloaded.
"""
from __future__ import annotations

import base64
import time

import pytest

from mobaide.voice.models import VOICE_CAPABILITIES

pytestmark = pytest.mark.timeout(180)

# One second of a constant, clearly voiced 16 kHz PCM16 level.
ONE_SECOND_B64 = base64.b64encode(b"\x10\x27" * 16000).decode()


def _poll(fetch, done, *, timeout: float = 60.0, interval: float = 0.2) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        last = fetch()
        if done(last):
            return last
        if time.monotonic() > deadline:
            raise TimeoutError(f"gave up waiting; last answer: {last!r}")
        time.sleep(interval)


def test_voice_enable_dictate_remove_over_the_relay(e2e_stack):
    client = e2e_stack.client
    agent = e2e_stack.agent_id
    token = client.mint_mobile_token(agent)

    def call(method: str, path: str, body: dict | None = None):
        return client.proxy_request(agent, token, method, f"api/voice/{path}", body)

    version = client.proxy_request(agent, token, "GET", "api/version")
    assert version.status_code == 200
    assert version.json()["capabilities"]["voice"] == VOICE_CAPABILITIES

    status = call("GET", "status")
    assert status.status_code == 200
    assert status.json()["state"] == "not_installed"
    assert status.json()["model"]["id"] == "fake-stt"

    refused = call("POST", "dictations", {})
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "voice_not_ready"

    started = call("POST", "install")
    assert started.status_code == 200
    assert started.json()["enabled"] is True

    ready = _poll(
        lambda: call("GET", "status").json(),
        lambda s: s["state"] in ("ready", "failed"),
    )
    assert ready["state"] == "ready", ready
    assert ready["speed_factor"] is not None

    created = call("POST", "dictations", {})
    assert created.status_code == 200
    dictation = created.json()["dictation_id"]

    chunk = {"seq": 0, "pcm16_b64": ONE_SECOND_B64}
    first = call("POST", f"dictations/{dictation}/audio", chunk)
    retried = call("POST", f"dictations/{dictation}/audio", chunk)
    assert first.status_code == retried.status_code == 200
    assert first.json() == retried.json() == {"seq": 0, "duration_ms": 1000}

    gap = call("POST", f"dictations/{dictation}/audio", {"seq": 5, "pcm16_b64": ONE_SECOND_B64})
    assert gap.status_code == 409
    assert gap.json()["detail"] == {
        "code": "seq_gap",
        "message": "An earlier audio chunk is missing.",
        "expected_seq": 1,
    }

    result = _poll(
        lambda: call("POST", f"dictations/{dictation}/finish", {"last_seq": 0}).json(),
        lambda r: r.get("state") != "transcribing",
    )
    assert result == {
        "dictation_id": dictation,
        "state": "done",
        "text": "fake transcript 1000 ms",
        "duration_ms": 1000,
    }

    deleted = call("DELETE", f"dictations/{dictation}")
    assert deleted.status_code == 204
    assert deleted.content == b""
    gone = call("POST", f"dictations/{dictation}/finish", {"last_seq": 0})
    assert gone.status_code == 404
    assert gone.json()["detail"]["code"] == "dictation_not_found"

    removed = call("DELETE", "model")
    assert removed.status_code == 200
    assert removed.json()["state"] == "not_installed"
    assert call("GET", "status").json()["enabled"] is False
