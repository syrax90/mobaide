"""CH_SESSION_IO_TARGETED helpers on the agent-side envelope copy (proto-v8).

Mirrors ``central_server/tests/test_envelope_targeted.py`` — the two envelope
modules are intentionally duplicated and their test suites track each other.
"""
from __future__ import annotations

import pytest

from mobaide.relay.envelope import (
    CH_SESSION_IO_TARGETED,
    decode,
    encode_targeted,
    split_targeted,
)


class TestTargetedRoundtrip:
    def test_roundtrip_through_decode_and_split(self):
        frame = b"s" + b'{"status":"running"}'
        data = encode_targeted("sess-abc-123", "conn-1", frame)

        channel, session_id, payload = decode(data)
        assert channel == CH_SESSION_IO_TARGETED
        assert session_id == "sess-abc-123"

        client_id, inner = split_targeted(payload)
        assert client_id == "conn-1"
        assert inner == frame

    def test_binary_frame_payload_survives_verbatim(self):
        # PTY frames are raw bytes; the cid-length prefix must not disturb
        # high-bit or NUL bytes in the inner frame.
        frame = b"o\x00\xff\x1b[31m"
        _, _, payload = decode(encode_targeted("s1", "c", frame))
        assert split_targeted(payload) == ("c", frame)

    def test_multibyte_client_id_length_is_counted_in_bytes(self):
        # The 1-byte prefix stores the UTF-8 *byte* length, not the character
        # count — a non-ASCII cid decodes wrong (or truncates) otherwise.
        client_id = "клиент-1"
        _, _, payload = decode(encode_targeted("s1", client_id, b"e"))
        assert split_targeted(payload) == (client_id, b"e")

    def test_empty_inner_frame_roundtrips(self):
        # Marker frames ('b' alone was already 1 byte; the degenerate empty
        # frame must still split cleanly rather than raise.
        _, _, payload = decode(encode_targeted("s1", "c1", b""))
        assert split_targeted(payload) == ("c1", b"")


class TestEncodeTargetedValidation:
    def test_empty_client_id_raises(self):
        with pytest.raises(ValueError):
            encode_targeted("s1", "", b"e")

    def test_client_id_over_255_bytes_raises(self):
        with pytest.raises(ValueError):
            encode_targeted("s1", "c" * 256, b"e")

    def test_client_id_of_exactly_255_bytes_roundtrips(self):
        client_id = "c" * 255
        _, _, payload = decode(encode_targeted("s1", client_id, b"e"))
        assert split_targeted(payload) == (client_id, b"e")


class TestSplitTargetedValidation:
    def test_empty_payload_raises(self):
        with pytest.raises(ValueError):
            split_targeted(b"")

    def test_zero_cid_length_raises(self):
        with pytest.raises(ValueError):
            split_targeted(b"\x00" + b"e")

    def test_truncated_cid_raises(self):
        # Prefix promises 8 cid bytes but only 4 follow.
        with pytest.raises(ValueError):
            split_targeted(b"\x08" + b"conn")
