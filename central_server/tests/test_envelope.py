"""Tests for the binary envelope codec."""
from __future__ import annotations

import pytest

from central_server.relay.envelope import (
    CH_CONTROL,
    CH_EVENTS,
    CH_SESSION_IO,
    CH_SESSIONS_LIST,
    decode,
    encode,
)


class TestEnvelope:
    def test_session_io_roundtrip(self):
        payload = b"o\x1b[31mhello\x1b[0m"
        data = encode(CH_SESSION_IO, "sess-abc-123", payload)
        channel, session_id, out_payload = decode(data)
        assert channel == CH_SESSION_IO
        assert session_id == "sess-abc-123"
        assert out_payload == payload

    def test_sessions_list_roundtrip(self):
        payload = b'[{"session_id":"s1"}]'
        data = encode(CH_SESSIONS_LIST, "", payload)
        channel, session_id, out_payload = decode(data)
        assert channel == CH_SESSIONS_LIST
        assert session_id == ""
        assert out_payload == payload

    def test_events_roundtrip(self):
        payload = b'{"type":"sessions.changed"}'
        data = encode(CH_EVENTS, "", payload)
        channel, session_id, out_payload = decode(data)
        assert channel == CH_EVENTS
        assert session_id == ""
        assert out_payload == payload

    def test_control_roundtrip(self):
        payload = b'{"type":"heartbeat"}'
        data = encode(CH_CONTROL, "", payload)
        channel, session_id, out_payload = decode(data)
        assert channel == CH_CONTROL
        assert session_id == ""
        assert out_payload == payload

    def test_empty_payload(self):
        data = encode(CH_SESSION_IO, "s1", b"")
        channel, sid, payload = decode(data)
        assert channel == CH_SESSION_IO
        assert sid == "s1"
        assert payload == b""

    def test_binary_payload(self):
        """Binary payload with bytes 0x00-0xFF passes through."""
        raw = bytes(range(256))
        data = encode(CH_SESSION_IO, "s1", raw)
        _, _, out = decode(data)
        assert out == raw

    def test_session_id_too_long(self):
        with pytest.raises(ValueError, match="too long"):
            encode(CH_SESSION_IO, "x" * 256, b"payload")

    def test_decode_too_short(self):
        with pytest.raises(ValueError, match="too short"):
            decode(b"\x01")

    def test_decode_unknown_channel(self):
        with pytest.raises(ValueError, match="Unknown channel"):
            decode(bytes([0xFF, 0x00]))

    def test_decode_truncated(self):
        # Header says 10-byte session_id but data is shorter
        with pytest.raises(ValueError, match="truncated"):
            decode(bytes([0x01, 10]) + b"short")

    def test_envelope_structure(self):
        """Verify the raw byte layout."""
        data = encode(CH_SESSION_IO, "ab", b"\xDE\xAD")
        assert data[0] == CH_SESSION_IO
        assert data[1] == 2  # session_id length
        assert data[2:4] == b"ab"
        assert data[4:] == b"\xDE\xAD"
