"""The agent↔worker frame codec and the worker's request loop, driven
in-process over byte streams (the subprocess side is covered by
``test_voice_helper.py``)."""
from __future__ import annotations

import io
import struct

import pytest

from mobaide.voice.ipc import MAX_PAYLOAD_BYTES, FrameError, encode_frame, read_frame
from mobaide.voice.worker import serve


def _replies(out: io.BytesIO) -> list[dict]:
    out.seek(0)
    replies = []
    while (frame := read_frame(out)) is not None:
        replies.append(frame[0])
    return replies


def _load(request_id: int = 1, engine: str = "fake") -> bytes:
    return encode_frame(
        {"op": "load", "id": request_id, "engine": engine, "model_dir": "/nowhere", "files": {}}
    )


def _transcribe(request_id: int, pcm: bytes) -> bytes:
    return encode_frame({"op": "transcribe", "id": request_id, "sample_rate": 16000}, pcm)


class TestFrames:
    def test_round_trip_with_payload(self):
        stream = io.BytesIO(encode_frame({"op": "transcribe", "id": 7}, b"\x01\x02\x03"))

        assert read_frame(stream) == ({"op": "transcribe", "id": 7}, b"\x01\x02\x03")
        assert read_frame(stream) is None

    def test_truncated_frame_is_an_error(self):
        frame = encode_frame({"op": "load"}, b"payload")

        with pytest.raises(FrameError):
            read_frame(io.BytesIO(frame[:-3]))

    def test_oversized_length_prefix_fails_before_allocating(self):
        prefix = struct.pack(">II", 10, MAX_PAYLOAD_BYTES + 1)

        with pytest.raises(FrameError):
            read_frame(io.BytesIO(prefix + b"{}"))

    @pytest.mark.parametrize("head", [b"not json!!", b"[1, 2, 3]"])
    def test_header_that_is_not_a_json_object_is_an_error(self, head):
        stream = io.BytesIO(struct.pack(">II", len(head), 0) + head)

        with pytest.raises(FrameError):
            read_frame(stream)

    def test_encoding_an_oversized_payload_is_refused(self):
        with pytest.raises(FrameError):
            encode_frame({"op": "transcribe"}, b"\x00" * (MAX_PAYLOAD_BYTES + 1))


class TestServe:
    def test_load_then_transcribe_with_the_fake_engine(self):
        out = io.BytesIO()

        code = serve(io.BytesIO(_load() + _transcribe(2, b"\x00\x00" * 8000)), out)

        load, result = _replies(out)
        assert code == 0
        assert load["id"] == 1 and load["ok"] is True
        assert isinstance(load["load_ms"], int) and isinstance(load["pid"], int)
        assert result == {
            "id": 2,
            "ok": True,
            "text": "fake transcript 500 ms",
            "decode_ms": result["decode_ms"],
            "peak_rss_bytes": result["peak_rss_bytes"],
        }

    def test_transcribe_before_load_is_refused(self):
        out = io.BytesIO()

        serve(io.BytesIO(_transcribe(1, b"\x00\x00")), out)

        (reply,) = _replies(out)
        assert reply["ok"] is False
        assert reply["error"]["code"] == "not_loaded"

    def test_unknown_engine_fails_the_load_and_keeps_serving(self, capsys):
        out = io.BytesIO()

        serve(io.BytesIO(_load(engine="nope") + _transcribe(2, b"\x00\x00")), out)

        load, result = _replies(out)
        assert load["ok"] is False and load["error"]["code"] == "load_failed"
        assert result["error"]["code"] == "not_loaded"

    def test_shutdown_stops_before_later_frames(self):
        out = io.BytesIO()

        code = serve(io.BytesIO(encode_frame({"op": "shutdown"}) + _load()), out)

        assert code == 0
        assert _replies(out) == []
