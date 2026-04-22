"""Binary envelope codec for the agent-relay multiplexed tunnel.

Format:
    [1-byte channel_type][1-byte session_id_len][N-byte session_id][payload]

Channel types:
    0x01  session_io     — session_id present, payload = binary PTY frame
    0x02  sessions_list  — session_id_len=0, payload = JSON text
    0x03  events         — session_id_len=0, payload = JSON text
    0x10  control        — session_id_len=0, payload = JSON control message
"""
from __future__ import annotations

CH_SESSION_IO = 0x01
CH_SESSIONS_LIST = 0x02
CH_EVENTS = 0x03
CH_CONTROL = 0x10

_VALID_CHANNELS = {CH_SESSION_IO, CH_SESSIONS_LIST, CH_EVENTS, CH_CONTROL}


def encode(channel: int, session_id: str, payload: bytes) -> bytes:
    """Encode a relay envelope."""
    sid_bytes = session_id.encode("utf-8") if session_id else b""
    if len(sid_bytes) > 255:
        raise ValueError(f"session_id too long: {len(sid_bytes)} bytes (max 255)")
    return bytes([channel, len(sid_bytes)]) + sid_bytes + payload


def decode(data: bytes) -> tuple[int, str, bytes]:
    """Decode a relay envelope.

    Returns:
        (channel_type, session_id, payload)

    Raises:
        ValueError: If the envelope is malformed.
    """
    if len(data) < 2:
        raise ValueError("Envelope too short: need at least 2 bytes")
    channel = data[0]
    if channel not in _VALID_CHANNELS:
        raise ValueError(f"Unknown channel type: 0x{channel:02x}")
    sid_len = data[1]
    if len(data) < 2 + sid_len:
        raise ValueError(
            f"Envelope truncated: need {2 + sid_len} bytes, got {len(data)}"
        )
    session_id = data[2 : 2 + sid_len].decode("utf-8") if sid_len else ""
    payload = data[2 + sid_len :]
    return channel, session_id, payload
