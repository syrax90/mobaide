"""Binary envelope codec for the agent-relay multiplexed tunnel.

Format:
    [1-byte channel_type][1-byte session_id_len][N-byte session_id][payload]

Channel types:
    0x01  session_io     -- session_id present, payload = binary PTY frame
    0x02  sessions_list  -- session_id_len=0, payload = JSON text
    0x03  events         -- session_id_len=0, payload = JSON text
    0x04  autoscan       -- session_id = per-mobile subscription id, payload = JSON text
    0x05  session_io_targeted -- agent->server only; payload =
          [1-byte client_id_len][client_id][binary PTY frame], delivered to the
          one attached mobile client whose conn_id matches (proto-v8)
    0x10  control        -- session_id_len=0, payload = JSON control message

Note: intentionally duplicated — the two packages are independent and cannot share code.
"""
from __future__ import annotations

CH_SESSION_IO = 0x01
CH_SESSIONS_LIST = 0x02
CH_EVENTS = 0x03
CH_AUTOSCAN = 0x04
CH_SESSION_IO_TARGETED = 0x05
CH_CONTROL = 0x10

# Wire-contract version. Single integer, monotonically incremented on any
# breaking change to the JSON shapes carried over CH_CONTROL or CH_EVENTS,
# or to the binary frame layout on CH_SESSION_IO. Mirrored in the matching
# central_server envelope module — CI enforces equality.
PROTOCOL_VERSION: int = 8
MIN_CLIENT_PROTOCOL_VERSION: int = 6
SUPPORTED_PROTOCOL_VERSIONS: tuple[int, ...] = (6, 7, 8)

# Application-private close codes (RFC 6455 §7.4.2).
PROTOCOL_INCOMPATIBLE_CLOSE_CODE = 4002

_VALID_CHANNELS = {
    CH_SESSION_IO,
    CH_SESSIONS_LIST,
    CH_EVENTS,
    CH_AUTOSCAN,
    CH_SESSION_IO_TARGETED,
    CH_CONTROL,
}


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


def encode_targeted(session_id: str, client_id: str, frame: bytes) -> bytes:
    """Encode a CH_SESSION_IO_TARGETED envelope addressed at one mobile client."""
    cid_bytes = client_id.encode("utf-8")
    if not cid_bytes or len(cid_bytes) > 255:
        raise ValueError(f"client_id length out of range: {len(cid_bytes)} bytes")
    return encode(
        CH_SESSION_IO_TARGETED,
        session_id,
        bytes([len(cid_bytes)]) + cid_bytes + frame,
    )


def split_targeted(payload: bytes) -> tuple[str, bytes]:
    """Split a CH_SESSION_IO_TARGETED payload into (client_id, inner frame).

    Raises:
        ValueError: If the payload is empty or truncated.
    """
    if not payload:
        raise ValueError("Targeted payload empty")
    cid_len = payload[0]
    if cid_len == 0 or len(payload) < 1 + cid_len:
        raise ValueError("Targeted payload truncated")
    client_id = payload[1 : 1 + cid_len].decode("utf-8")
    return client_id, payload[1 + cid_len :]
