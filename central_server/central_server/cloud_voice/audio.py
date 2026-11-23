"""Audio and text helpers of cloud voice input: no I/O, no state."""
from __future__ import annotations

import base64
import binascii
import re
import struct
import sys

from .errors import CloudVoiceError

SAMPLE_RATE = 16000
MAX_SECONDS = 120
MAX_CHUNK_BYTES = 65536
MAX_BYTES = MAX_SECONDS * SAMPLE_RATE * 2
_MAX_B64_CHARS = (MAX_CHUNK_BYTES + 2) // 3 * 4

# A recording whose loudest sample stays below this (about -50 dBFS) holds no
# speech. It is answered as empty text without a request to the speech
# service, which tends to invent words for silence.
SILENCE_PEAK = 100
MIN_SPEECH_BYTES = SAMPLE_RATE * 2 * 3 // 10

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029]")
_CHAR_RUN = re.compile(r"(.)\1{20,}", re.DOTALL)
_BLOCK_RUN = re.compile(r"(.{2,80}?)\1{7,}", re.DOTALL)


def decode_chunk(pcm16_b64: str) -> bytes:
    """One uploaded chunk as PCM16 bytes; refusals use the agent's codes."""
    if len(pcm16_b64) > _MAX_B64_CHARS:
        raise _too_large()
    try:
        pcm = base64.b64decode(pcm16_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CloudVoiceError(422, "bad_audio", "Audio must be base64-encoded PCM16.") from exc
    if len(pcm) > MAX_CHUNK_BYTES:
        raise _too_large()
    if len(pcm) % 2:
        raise CloudVoiceError(
            422, "bad_audio", "PCM16 audio must have an even number of bytes."
        )
    return pcm


def _too_large() -> CloudVoiceError:
    return CloudVoiceError(
        413,
        "chunk_too_large",
        f"An audio chunk can hold at most {MAX_CHUNK_BYTES} bytes.",
        max_chunk_bytes=MAX_CHUNK_BYTES,
    )


def peak(pcm: bytes) -> int:
    """The loudest sample of little-endian PCM16, as an absolute value."""
    if not pcm:
        return 0
    samples = memoryview(pcm).cast("h")
    if sys.byteorder == "big":
        return max(abs(value) for (value,) in struct.iter_unpack("<h", pcm))
    return max(max(samples), -min(samples))


def wav(pcm: bytes) -> bytes:
    """``pcm`` (16 kHz mono PCM16) as a WAV file."""
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(pcm),
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        SAMPLE_RATE,
        SAMPLE_RATE * 2,
        2,
        16,
        b"data",
        len(pcm),
    )
    return header + pcm


def duration_ms(byte_count: int) -> int:
    return byte_count // 2 * 1000 // SAMPLE_RATE


def tidy_transcript(text: str) -> str:
    """The speech service's answer as plain dictated text.

    Control characters go, so the text can never carry an Enter or an escape
    sequence into a terminal, and a runaway repetition (one character or one
    short phrase, over and over) collapses to a single copy.
    """
    text = _CONTROL_CHARS.sub(" ", text)
    text = _CHAR_RUN.sub(lambda match: match.group(1), text)
    text = _BLOCK_RUN.sub(lambda match: match.group(1), text)
    return " ".join(text.split())
