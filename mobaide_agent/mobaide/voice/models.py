"""Pydantic wire models of the voice API.

Every key is always emitted (nulls included). ``state``, ``recommendation``,
``reasons[].code`` and ``error.code`` are open vocabularies: clients treat a
value they do not know as "voice unavailable" and show the message.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from .dictation import MAX_CHUNK_BYTES, MAX_SECONDS, SAMPLE_RATE

# Advertised as ``capabilities.voice`` on ``GET /api/version`` (both
# transports) whenever this agent build supports voice, whatever the install
# state; ``GET /api/voice/status`` says whether it is installed.
VOICE_CAPABILITIES: dict[str, object] = {
    "dictation": True,
    "sample_rate": SAMPLE_RATE,
    "max_seconds": MAX_SECONDS,
    "max_chunk_bytes": MAX_CHUNK_BYTES,
}


class VoiceModelInfo(BaseModel):
    id: str
    name: str
    languages: list[str]
    download_bytes: int
    disk_bytes: int
    ram_bytes: int


class VoiceProgress(BaseModel):
    done_bytes: int
    total_bytes: int


class VoiceHostReason(BaseModel):
    code: str
    message: str


class VoiceHostInfo(BaseModel):
    ram_total_bytes: int | None
    disk_free_bytes: int | None
    cpu_count: int | None
    recommendation: str
    reasons: list[VoiceHostReason]


class VoiceErrorInfo(BaseModel):
    code: str
    message: str


class VoiceStatusResponse(BaseModel):
    """``GET /api/voice/status`` and the ``voice.changed`` event payload.

    ``state``: ``unsupported`` | ``not_installed`` | ``queued`` |
    ``downloading`` | ``verifying`` | ``ready`` | ``failed``. ``progress`` is
    set while ``downloading``; ``error`` while ``failed``; ``speed_factor``
    (seconds of speech transcribed per second of CPU time, from the install
    self-test) once ``ready``.
    """

    state: str
    enabled: bool
    model: VoiceModelInfo
    progress: VoiceProgress | None
    host: VoiceHostInfo
    speed_factor: float | None
    error: VoiceErrorInfo | None


class VoiceInstallRequest(BaseModel):
    model_id: str | None = None


class VoiceDictationCreateRequest(BaseModel):
    # Reserved for repository hotwords; accepted and currently unused.
    session_id: str | None = None


class VoiceDictationResponse(BaseModel):
    dictation_id: str
    sample_rate: int
    max_seconds: int
    max_chunk_bytes: int


class VoiceDictationAudioRequest(BaseModel):
    seq: int = Field(ge=0)
    pcm16_b64: str


class VoiceDictationAudioResponse(BaseModel):
    seq: int
    duration_ms: int


class VoiceDictationFinishRequest(BaseModel):
    # The highest ``seq`` the agent accepted (answered 200); a chunk refused
    # with 4xx does not count. -1 when none was accepted.
    last_seq: int = Field(ge=-1)


class VoiceDictationResultResponse(BaseModel):
    """``state`` is ``done`` (``text`` set) or ``transcribing`` (call
    ``finish`` again)."""

    dictation_id: str
    state: str
    text: str | None
    duration_ms: int
