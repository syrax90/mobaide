"""Wire models of the cloud voice routes (PROTOCOL.md, "cloud voice input").

The dictation bodies mirror the agent's ``/api/voice/dictations`` models field
for field, so a client uploads to either with the same code.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CloudVoiceLimits(BaseModel):
    sample_rate: int
    max_seconds: int
    max_chunk_bytes: int


class CloudVoiceUsage(BaseModel):
    """The caller's audio transcribed today (UTC). ``limit_seconds`` 0: no daily limit."""

    used_seconds: int
    limit_seconds: int
    resets_at: str


class CloudVoiceStatus(BaseModel):
    """``GET /api/voice/cloud``.

    ``state`` is an open vocabulary: ``off`` (not offered to this account),
    ``ready``, ``unavailable`` (the speech service does not answer) or
    ``limit_reached`` (the caller's daily limit, until ``usage.resets_at``).
    A client reads a value it does not know as ``off``.
    """

    state: str
    limits: CloudVoiceLimits
    usage: CloudVoiceUsage
    engines: dict[str, str]


class CloudVoiceEngineRequest(BaseModel):
    engine: Literal["cloud", "local"]


class CloudVoiceDictationResponse(BaseModel):
    dictation_id: str
    sample_rate: int
    max_seconds: int
    max_chunk_bytes: int


class CloudVoiceAudioRequest(BaseModel):
    seq: int = Field(ge=0)
    pcm16_b64: str


class CloudVoiceAudioResponse(BaseModel):
    seq: int
    duration_ms: int


class CloudVoiceFinishRequest(BaseModel):
    # The highest ``seq`` this server accepted; -1 when none was.
    last_seq: int = Field(ge=-1)


class CloudVoiceResultResponse(BaseModel):
    """``state`` is ``done`` (``text`` set) or ``transcribing`` (call
    ``finish`` again)."""

    dictation_id: str
    state: str
    text: str | None
    duration_ms: int
