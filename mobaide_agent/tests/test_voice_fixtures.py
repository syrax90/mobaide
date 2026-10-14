"""Python arm of the voice golden fixtures shared with the iOS, Android and
web clients.

Strict rule: every case survives
``model_validate`` → ``model_dump(mode="json")`` unchanged, and
``decoded_canonical`` (the Swift re-encode, nil optionals dropped) equals the
wire with null-valued keys recursively stripped. ``capabilities.voice`` in
the ``VersionInfoResponse`` golden must be exactly what the agent advertises.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mobaide.voice.models import (
    VOICE_CAPABILITIES,
    VoiceDictationAudioRequest,
    VoiceDictationAudioResponse,
    VoiceDictationCreateRequest,
    VoiceDictationFinishRequest,
    VoiceDictationResponse,
    VoiceDictationResultResponse,
    VoiceInstallRequest,
    VoiceStatusResponse,
)

# Resolved from this file, never the CWD.
_FIXTURE_DIR = (
    Path(__file__).resolve().parents[2] / "tests" / "protocol_fixtures" / "dto" / "agent"
)
_MODELS = {
    "VoiceStatusResponse": VoiceStatusResponse,
    "VoiceInstallRequest": VoiceInstallRequest,
    "VoiceDictationCreateRequest": VoiceDictationCreateRequest,
    "VoiceDictationResponse": VoiceDictationResponse,
    "VoiceDictationAudioRequest": VoiceDictationAudioRequest,
    "VoiceDictationAudioResponse": VoiceDictationAudioResponse,
    "VoiceDictationFinishRequest": VoiceDictationFinishRequest,
    "VoiceDictationResultResponse": VoiceDictationResultResponse,
}


def _load(name: str) -> dict:
    doc = json.loads((_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    assert doc["type"] == name
    assert doc["cases"], f"{name} has no cases — the golden would assert nothing"
    return doc


def _cases():
    for name, model in _MODELS.items():
        for case in _load(name)["cases"]:
            yield pytest.param(model, case, id=f"{name}:{case['name']}")


def _strip_nulls(value):
    if isinstance(value, dict):
        return {k: _strip_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_strip_nulls(v) for v in value]
    return value


@pytest.mark.parametrize(("model", "case"), list(_cases()))
def test_wire_round_trips_through_model(model, case):
    decoded = model.model_validate(case["wire"])

    assert decoded.model_dump(mode="json") == case["wire"]


@pytest.mark.parametrize(("model", "case"), list(_cases()))
def test_decoded_canonical_is_wire_without_nulls(model, case):
    expected = _strip_nulls(case["wire"])

    if "decoded_canonical" in case:
        assert case["decoded_canonical"] == expected
    else:
        assert expected == case["wire"], "a wire with nulls needs decoded_canonical"


def test_status_fixture_covers_every_state_clients_must_render():
    states = {case["wire"]["state"] for case in _load("VoiceStatusResponse")["cases"]}

    assert {"not_installed", "downloading", "ready", "failed", "unsupported"} <= states


def test_version_fixture_voice_capability_is_what_the_agent_advertises():
    cases = {case["name"]: case for case in _load("VersionInfoResponse")["cases"]}

    assert cases["voice_capability"]["wire"]["capabilities"]["voice"] == VOICE_CAPABILITIES
