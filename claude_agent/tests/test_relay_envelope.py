"""Tests for the relay binary envelope codec (agent-side copy).

Mirrors the central_server's test_envelope.py coverage and verifies that the
agent-side copy produces bit-identical output for the same inputs.
"""
from __future__ import annotations

import pytest

from claude_mobile.relay.envelope import (
    CH_AUTOSCAN,
    CH_CONTROL,
    CH_EVENTS,
    CH_SESSION_IO,
    CH_SESSIONS_LIST,
    decode,
    encode,
)


class TestEnvelopeRoundtrip:
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

    def test_autoscan_roundtrip(self):
        """CH_AUTOSCAN carries a per-mobile subscription_id in session_id slot."""
        payload = b'{"type":"event","project_name":"p","event":{"seq":1}}'
        subscription_id = "abc123def456"
        data = encode(CH_AUTOSCAN, subscription_id, payload)
        channel, session_id, out_payload = decode(data)
        assert channel == CH_AUTOSCAN
        assert session_id == subscription_id
        assert out_payload == payload

    def test_autoscan_with_uuid_hex_subscription_id(self):
        """The central server uses ``uuid4().hex`` (32 chars) as subscription_id."""
        import uuid

        sub_id = uuid.uuid4().hex
        assert len(sub_id) == 32
        data = encode(CH_AUTOSCAN, sub_id, b'{"type":"caught_up"}')
        channel, decoded_sid, payload = decode(data)
        assert channel == CH_AUTOSCAN
        assert decoded_sid == sub_id
        assert payload == b'{"type":"caught_up"}'

    def test_empty_payload(self):
        data = encode(CH_SESSION_IO, "s1", b"")
        channel, sid, payload = decode(data)
        assert channel == CH_SESSION_IO
        assert sid == "s1"
        assert payload == b""

    def test_binary_payload_full_byte_range(self):
        """Binary payload with bytes 0x00-0xFF passes through intact."""
        raw = bytes(range(256))
        data = encode(CH_SESSION_IO, "s1", raw)
        _, _, out = decode(data)
        assert out == raw

    def test_empty_session_id_with_session_io(self):
        """CH_SESSION_IO with empty session_id is valid at codec level."""
        data = encode(CH_SESSION_IO, "", b"payload")
        ch, sid, pl = decode(data)
        assert ch == CH_SESSION_IO
        assert sid == ""
        assert pl == b"payload"

    def test_max_length_session_id(self):
        """Session ID at exactly 255 bytes must succeed."""
        sid = "a" * 255
        data = encode(CH_SESSION_IO, sid, b"\x00")
        _, decoded_sid, _ = decode(data)
        assert decoded_sid == sid


class TestEnvelopeStructure:
    def test_wire_layout(self):
        """Verify the raw byte layout: [channel][sid_len][sid][payload]."""
        data = encode(CH_SESSION_IO, "ab", b"\xDE\xAD")
        assert data[0] == CH_SESSION_IO
        assert data[1] == 2  # session_id length
        assert data[2:4] == b"ab"
        assert data[4:] == b"\xDE\xAD"

    def test_empty_session_id_produces_zero_length_byte(self):
        data = encode(CH_CONTROL, "", b"x")
        assert data[0] == CH_CONTROL
        assert data[1] == 0
        assert data[2:] == b"x"


class TestEnvelopeErrors:
    def test_session_id_too_long(self):
        with pytest.raises(ValueError, match="too long"):
            encode(CH_SESSION_IO, "x" * 256, b"payload")

    def test_decode_too_short_single_byte(self):
        with pytest.raises(ValueError, match="too short"):
            decode(b"\x01")

    def test_decode_too_short_empty(self):
        with pytest.raises(ValueError, match="too short"):
            decode(b"")

    def test_decode_unknown_channel(self):
        with pytest.raises(ValueError, match="Unknown channel"):
            decode(bytes([0xFF, 0x00]))

    def test_decode_truncated_session_id(self):
        """Header says 10-byte session_id but data is shorter."""
        with pytest.raises(ValueError, match="truncated"):
            decode(bytes([0x01, 10]) + b"short")


class TestChannelConstants:
    def test_channel_values(self):
        """Channel constants must match the central server protocol."""
        assert CH_SESSION_IO == 0x01
        assert CH_SESSIONS_LIST == 0x02
        assert CH_EVENTS == 0x03
        assert CH_AUTOSCAN == 0x04
        assert CH_CONTROL == 0x10
