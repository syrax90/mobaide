"""Pause segmentation of a dictation: a segment closes in the middle of a
≥500 ms pause once it holds ≥5 s, is force-cut at the quietest frame of its
last 2 s at 25 s, steady speech and a quieter word above the noise floor
never read as a pause, a segment is unvoiced only when no frame reaches
``VOICE_RMS`` or ``VOICE_SNR`` times the non-zero noise floor, and each segment
carries its peak RMS."""
from __future__ import annotations

import math
from array import array

import pytest

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


def _bursts(seconds: float, speech: int, noise: int) -> bytes:
    """Speech-like audio: 0.3 s words at ``speech`` amplitude between 0.3 s
    gaps of ``noise`` amplitude, starting with a gap."""
    out = b""
    while len(out) < seconds * RATE * 2:
        out += _tone(0.3, amplitude=noise) + _tone(0.3, amplitude=speech)
    return out


@pytest.mark.parametrize("noise", [5, 28], ids=["low-hiss", "room-noise-rms-20"])
def test_quiet_speech_below_the_voice_level_is_voiced(noise):
    seg = PauseSegmenter(RATE)

    assert seg.feed(_bursts(4.0, speech=113, noise=noise)) == []
    tail = seg.flush()

    assert tail is not None
    assert tail.voiced


def test_room_noise_fluctuating_under_the_snr_is_unvoiced():
    noise = b"".join(_tone(0.1, amplitude=85) + _tone(0.1, amplitude=212) for _ in range(20))
    seg = PauseSegmenter(RATE)

    assert seg.feed(noise) == []
    tail = seg.flush()

    assert tail is not None
    assert not tail.voiced


def test_hiss_below_the_silence_level_after_digital_silence_is_unvoiced():
    seg = PauseSegmenter(RATE)

    seg.feed(_silence(1.0) + _tone(2.0, amplitude=12))
    tail = seg.flush()

    assert tail is not None
    assert not tail.voiced


def test_room_noise_right_after_a_zero_frame_is_unvoiced():
    seg = PauseSegmenter(RATE)

    assert seg.feed(_silence(0.02) + _tone(2.5, amplitude=71)) == []
    tail = seg.flush()

    assert tail is not None
    assert not tail.voiced


def test_quiet_speech_over_a_low_hiss_after_zeros_is_voiced():
    seg = PauseSegmenter(RATE)

    assert seg.feed(_silence(0.5) + _bursts(4.0, speech=113, noise=5)) == []
    tail = seg.flush()

    assert tail is not None
    assert tail.voiced


def test_a_faint_frame_before_long_digital_silence_does_not_lower_the_floor_for_later_noise():
    seg = PauseSegmenter(RATE)

    closed = seg.feed(_tone(0.02, amplitude=4) + _silence(4.0) + _tone(2.0, amplitude=71))
    tail = seg.flush()

    assert tail is not None
    assert not any(s.voiced for s in [*closed, tail])


def test_a_quieter_word_above_the_noise_floor_is_not_a_pause():
    speech = b"".join(_tone(0.4) + _silence(0.1) for _ in range(12))
    seg = PauseSegmenter(RATE)

    assert seg.feed(speech + _tone(0.6, amplitude=141) + _tone(1.0)) == []


def test_each_segment_carries_its_own_peak_rms():
    seg = PauseSegmenter(RATE)

    closed = seg.feed(_tone(6.0, amplitude=3000) + _silence(0.6) + _tone(1.0, amplitude=1000))
    tail = seg.flush()

    assert len(closed) == 1
    assert closed[0].peak_rms == pytest.approx(3000 / math.sqrt(2), rel=0.01)
    assert tail is not None
    assert tail.peak_rms == pytest.approx(1000 / math.sqrt(2), rel=0.01)
