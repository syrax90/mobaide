"""Pause segmentation of a dictation: a segment closes in the middle of a
≥500 ms pause once it holds ≥5 s, is force-cut at the quietest frame of its
last 2 s at 25 s, steady speech never reads as a pause, and only a segment
with no frame above ``VOICE_RMS`` is marked unvoiced."""
from __future__ import annotations

import math
from array import array

from mobaide.voice.segmenter import PauseSegmenter

RATE = 16000
FRAME_BYTES = RATE * 20 // 1000 * 2


def _tone(seconds: float, amplitude: int = 3000) -> bytes:
    # 200 Hz divides a 20 ms frame exactly, so every frame has the same RMS.
    samples = array(
        "h",
        (int(amplitude * math.sin(2 * math.pi * 200 * i / RATE)) for i in range(int(seconds * RATE))),
    )
    return samples.tobytes()


def _silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(seconds * RATE)


def _ms(segment) -> int:
    return len(segment.pcm) // 2 * 1000 // RATE


def test_pause_after_five_seconds_closes_a_segment_mid_pause():
    seg = PauseSegmenter(RATE)

    closed = seg.feed(_tone(6.0) + _silence(0.6))

    assert len(closed) == 1
    assert _ms(closed[0]) == 6260
    assert closed[0].voiced


def test_pause_before_five_seconds_does_not_cut():
    seg = PauseSegmenter(RATE)

    assert seg.feed(_tone(3.0) + _silence(0.8) + _tone(1.0)) == []


def test_short_pause_does_not_cut():
    seg = PauseSegmenter(RATE)

    assert seg.feed(_tone(6.0) + _silence(0.4) + _tone(1.0)) == []


def test_steady_speech_never_reads_as_a_pause():
    seg = PauseSegmenter(RATE)

    assert seg.feed(_tone(20.0)) == []


def test_speech_without_a_pause_is_cut_at_the_quietest_frame_near_25_seconds():
    dip_frame = 1200  # 24.0 s, inside the last 2 s before the forced cut
    audio = bytearray(_tone(25.0))
    audio[dip_frame * FRAME_BYTES : (dip_frame + 1) * FRAME_BYTES] = _tone(0.02, amplitude=1500)
    seg = PauseSegmenter(RATE)

    closed = seg.feed(bytes(audio))

    assert len(closed) == 1
    assert len(closed[0].pcm) == (dip_frame + 1) * FRAME_BYTES
    tail = seg.flush()
    assert tail is not None
    assert len(closed[0].pcm) + len(tail.pcm) == len(audio)


def test_quiet_room_noise_segment_is_unvoiced():
    seg = PauseSegmenter(RATE)

    closed = seg.feed(_tone(6.0, amplitude=100) + _silence(0.6))

    assert len(closed) == 1
    assert not closed[0].voiced


def test_segments_after_a_cut_keep_counting_from_the_cut():
    seg = PauseSegmenter(RATE)

    closed = seg.feed(_tone(6.0) + _silence(0.6) + _tone(6.0) + _silence(0.6))

    assert [_ms(s) for s in closed] == [6260, 6600]
    assert all(s.voiced for s in closed)


def test_chunk_boundaries_do_not_change_the_cuts():
    audio = _tone(6.0) + _silence(0.6) + _tone(2.0)
    whole = PauseSegmenter(RATE)
    pieces = PauseSegmenter(RATE)

    expected = whole.feed(audio)
    got = []
    for offset in range(0, len(audio), 1001):
        got += pieces.feed(audio[offset : offset + 1001])

    assert got == expected
    assert pieces.flush() == whole.flush()


def test_flush_returns_the_remainder_then_nothing():
    seg = PauseSegmenter(RATE)
    seg.feed(_tone(1.0))

    tail = seg.flush()

    assert tail is not None
    assert _ms(tail) == 1000
    assert tail.voiced
    assert seg.flush() is None


def test_flush_of_silence_is_unvoiced():
    seg = PauseSegmenter(RATE)
    seg.feed(_silence(1.0))

    tail = seg.flush()

    assert tail is not None
    assert not tail.voiced


def test_flush_drops_a_trailing_odd_byte():
    seg = PauseSegmenter(RATE)
    seg.feed(_tone(0.01) + b"\x01")

    tail = seg.flush()

    assert tail is not None
    assert len(tail.pcm) % 2 == 0
