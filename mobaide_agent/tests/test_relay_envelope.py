"""Tests for the relay binary envelope codec (agent-side copy).

Mirrors the central_server's test_envelope.py coverage and verifies that the
agent-side copy produces bit-identical output for the same inputs.
"""
from __future__ import annotations

import pytest

from mobaide.relay.envelope import (
    CH_AUTOSCAN,
    CH_SESSION_IO,
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

    def test_autoscan_roundtrip(self):
        """CH_AUTOSCAN carries a per-mobile subscription_id in session_id slot."""
        payload = b'{"type":"event","project_name":"p","event":{"seq":1}}'
        subscription_id = "abc123def456"
        data = encode(CH_AUTOSCAN, subscription_id, payload)
        channel, session_id, out_payload = decode(data)
        assert channel == CH_AUTOSCAN
        assert session_id == subscription_id
        assert out_payload == payload

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


class TestEnvelopeErrors:
    def test_session_id_too_long(self):
        with pytest.raises(ValueError, match="too long"):
            encode(CH_SESSION_IO, "x" * 256, b"payload")

    def test_decode_too_short_single_byte(self):
        with pytest.raises(ValueError, match="too short"):
            decode(b"\x01")

    def test_decode_unknown_channel(self):
        with pytest.raises(ValueError, match="Unknown channel"):
            decode(bytes([0xFF, 0x00]))

    def test_decode_truncated_session_id(self):
        """Header says 10-byte session_id but data is shorter."""
        with pytest.raises(ValueError, match="truncated"):
            decode(bytes([0x01, 10]) + b"short")
