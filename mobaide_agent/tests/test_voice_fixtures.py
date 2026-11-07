"""Python arm of the voice golden fixtures shared with the iOS, Android and
web clients.

Strict rule: every case survives
``model_validate`` → ``model_dump(mode="json")`` unchanged, and
``decoded_canonical`` (the Swift re-encode, nil optionals dropped) equals the
wire with null-valued keys recursively stripped. ``capabilities.voice`` in
the ``VersionInfoResponse`` golden must be exactly what the agent advertises.
Every chunk the clients would upload for ``voice/pcm16_chunks.json`` decodes
through the agent's own chunk decoder to the pinned bytes.
"""
from __future__ import annotations

import base64
import hashlib
import json
import struct
from pathlib import Path

import pytest

from mobaide.voice.dictation import MAX_CHUNK_BYTES, SAMPLE_RATE, _decode_chunk
from mobaide.voice.models import (
    VOICE_CAPABILITIES,
    VoiceCleanupRequest,
    VoiceCleanupResponse,
    VoiceCleanupSettingsRequest,
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
_PCM16_CHUNKS = (
    Path(__file__).resolve().parents[2] / "tests" / "protocol_fixtures" / "voice" / "pcm16_chunks.json"
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
    "VoiceCleanupRequest": VoiceCleanupRequest,
    "VoiceCleanupResponse": VoiceCleanupResponse,
    "VoiceCleanupSettingsRequest": VoiceCleanupSettingsRequest,
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


def _pcm16_doc() -> dict:
    doc = json.loads(_PCM16_CHUNKS.read_text(encoding="utf-8"))
    assert doc["type"] == "Pcm16Chunks"
    return doc


def _chunk_cases():
    for case in _pcm16_doc()["chunk_cases"]:
        yield pytest.param(case, id=case["name"])


def _generated_samples(generator: dict) -> list[int]:
    out = []
    for i in range(generator["count"]):
        value = (i * generator["multiplier"]) % 65536
        out.append(value - 65536 if value >= 32768 else value)
    return out


def test_pcm16_chunk_size_matches_what_the_agent_announces():
    doc = _pcm16_doc()
    rows = {row["max_chunk_bytes"]: row["chunk_bytes"] for row in doc["chunk_size"]}

    assert doc["sample_rate"] == SAMPLE_RATE
    assert rows[MAX_CHUNK_BYTES] == SAMPLE_RATE * 2


@pytest.mark.parametrize("case", list(_chunk_cases()))
def test_every_golden_chunk_decodes_to_its_bytes(case):
    for chunk in case["open"] + case["final"]:
        if "bytes_hex" not in chunk:
            continue
        assert "\n" not in chunk["pcm16_b64"]
        assert _decode_chunk(chunk["pcm16_b64"]) == bytes.fromhex(chunk["bytes_hex"])


@pytest.mark.parametrize("case", list(_chunk_cases()))
def test_final_chunks_carry_every_sample_little_endian_in_order(case):
    samples = case["samples"] if "samples" in case else _generated_samples(case["generator"])
    expected = struct.pack(f"<{len(samples)}h", *samples)
    final = case["final"]

    assert [chunk["seq"] for chunk in final] == list(range(len(final)))
    if "generator" in case:
        assert sum(chunk["byte_length"] for chunk in final) == len(expected)
    else:
        assert b"".join(bytes.fromhex(chunk["bytes_hex"]) for chunk in final) == expected


def test_generated_case_hashes_match_the_agent_decoder():
    case = next(c for c in _pcm16_doc()["chunk_cases"] if "generator" in c)
    pcm = struct.pack(f"<{case['generator']['count']}h", *_generated_samples(case["generator"]))
    offset = 0
    for chunk in case["final"]:
        piece = pcm[offset : offset + chunk["byte_length"]]
        offset += chunk["byte_length"]
        text = base64.b64encode(piece).decode("ascii")

        assert len(text) == chunk["b64_length"]
        assert hashlib.sha256(text.encode("ascii")).hexdigest() == chunk["b64_sha256"]
        assert _decode_chunk(text) == piece
    assert offset == len(pcm)
