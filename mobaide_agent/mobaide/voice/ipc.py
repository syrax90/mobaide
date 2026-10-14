"""Frames between the agent and the STT worker over its stdin/stdout pipes.

A frame is ``>II`` (header length, payload length), a UTF-8 JSON header and a
raw payload (PCM for ``transcribe``). Both lengths are capped so a corrupted
stream fails fast instead of allocating gigabytes.
"""
from __future__ import annotations

import json
import struct
from typing import IO, Any

_PREFIX = struct.Struct(">II")
MAX_HEADER_BYTES = 1024 * 1024
MAX_PAYLOAD_BYTES = 16 * 1024 * 1024


class FrameError(Exception):
    pass


def encode_frame(header: dict[str, Any], payload: bytes = b"") -> bytes:
    head = json.dumps(header, separators=(",", ":")).encode("utf-8")
    if len(head) > MAX_HEADER_BYTES or len(payload) > MAX_PAYLOAD_BYTES:
        raise FrameError("frame too large")
    return _PREFIX.pack(len(head), len(payload)) + head + payload


def _read_exact(stream: IO[bytes], size: int) -> bytes | None:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(stream: IO[bytes]) -> tuple[dict[str, Any], bytes] | None:
    """Next frame, or ``None`` at a clean end of stream."""
    prefix = _read_exact(stream, _PREFIX.size)
    if prefix is None:
        return None
    head_len, payload_len = _PREFIX.unpack(prefix)
    if head_len > MAX_HEADER_BYTES or payload_len > MAX_PAYLOAD_BYTES:
        raise FrameError("frame too large")
    head = _read_exact(stream, head_len)
    payload = _read_exact(stream, payload_len) if payload_len else b""
    if head is None or payload is None:
        raise FrameError("truncated frame")
    try:
        header = json.loads(head)
    except ValueError as exc:
        raise FrameError("bad frame header") from exc
    if not isinstance(header, dict):
        raise FrameError("bad frame header")
    return header, payload
