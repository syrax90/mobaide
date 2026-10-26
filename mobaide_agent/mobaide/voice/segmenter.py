"""Pause-based segmentation of a dictation.

Audio is cut into segments at natural pauses while the user is still
speaking, and each segment is transcribed exactly once, so ``finish`` only
waits for the last few seconds instead of re-reading the whole recording.
A frame is quiet when its RMS is under
``max(ABS_QUIET_RMS, NOISE_FACTOR × noise floor)`` (the floor being the
quietest frame of the last few seconds) and also under ``PEAK_RATIO`` of the
loudest frame of the segment, so steady speech can never read as a pause.

A segment is skipped only when no frame is voiced: at least ``VOICE_RMS``, or
above ``SILENCE_RMS`` and ``VOICE_SNR`` times a noise floor that ignores the
digital-zero frames a microphone delivers while starting. Phones record without
gain, so quiet speech below ``VOICE_RMS`` is caught by the floor test.
"""
from __future__ import annotations

import collections
import math
import sys
from array import array
from dataclasses import dataclass

FRAME_MS = 20
MIN_SEGMENT_SECONDS = 5.0
MAX_SEGMENT_SECONDS = 25.0
PAUSE_MS = 500
CUT_SEARCH_SECONDS = 2.0
FLOOR_WINDOW_SECONDS = 3.0
ABS_QUIET_RMS = 60.0
NOISE_FACTOR = 2.5
PEAK_RATIO = 0.3
# About -44 dBFS: always voiced, whatever the noise floor.
VOICE_RMS = 200.0
# About -70 dBFS: only digital silence or a dead input stays below it.
SILENCE_RMS = 10.0
# Below it a frame is digital zeros, which no live microphone produces.
ZERO_RMS = 1.0
VOICE_SNR = 3.0


@dataclass(frozen=True)
class Segment:
    pcm: bytes
    # False when no frame is voiced: the segment is skipped, not transcribed.
    voiced: bool
    peak_rms: float


def _frame_rms(frame: bytes) -> float:
    samples = array("h")
    samples.frombytes(frame)
    if sys.byteorder == "big":
        samples.byteswap()
    return math.sqrt(math.sumprod(samples, samples) / len(samples))


class PauseSegmenter:
    def __init__(self, sample_rate: int = 16000) -> None:
        self._frame_bytes = sample_rate * FRAME_MS // 1000 * 2
        frames_per_second = 1000 // FRAME_MS
        self._min_frames = int(MIN_SEGMENT_SECONDS * frames_per_second)
        self._max_frames = int(MAX_SEGMENT_SECONDS * frames_per_second)
        self._pause_frames = PAUSE_MS // FRAME_MS
        self._search_frames = int(CUT_SEARCH_SECONDS * frames_per_second)
        self._recent: collections.deque[float] = collections.deque(
            maxlen=int(FLOOR_WINDOW_SECONDS * frames_per_second)
        )
        self._buf = bytearray()
        self._rms: list[float] = []
        self._quiet: list[bool] = []
        self._voiced: list[bool] = []
        self._quiet_run = 0
        self._peak = 0.0

    def feed(self, pcm: bytes) -> list[Segment]:
        """Add audio; return the segments it closed, in order."""
        self._buf += pcm
        closed: list[Segment] = []
        fb = self._frame_bytes
        while (len(self._rms) + 1) * fb <= len(self._buf):
            start = len(self._rms) * fb
            rms = _frame_rms(bytes(self._buf[start : start + fb]))
            self._recent.append(rms)
            self._peak = max(self._peak, rms)
            floor = min(self._recent)
            quiet = rms < max(ABS_QUIET_RMS, NOISE_FACTOR * floor) and (
                rms < PEAK_RATIO * self._peak
            )
            self._rms.append(rms)
            self._quiet.append(quiet)
            audible_floor = min((r for r in self._recent if r >= ZERO_RMS), default=math.inf)
            self._voiced.append(
                rms >= min(VOICE_RMS, max(SILENCE_RMS, VOICE_SNR * audible_floor))
            )
            self._quiet_run = self._quiet_run + 1 if quiet else 0
            frames = len(self._rms)
            if frames >= self._min_frames and self._quiet_run >= self._pause_frames:
                closed.append(self._cut(frames - self._pause_frames // 2))
            elif frames >= self._max_frames:
                window = range(frames - self._search_frames, frames)
                closed.append(self._cut(min(window, key=self._rms.__getitem__) + 1))
        return closed

    def flush(self) -> Segment | None:
        """The rest of the audio, or ``None`` when nothing is left."""
        if not self._buf:
            return None
        segment = Segment(
            pcm=bytes(self._buf[: len(self._buf) - len(self._buf) % 2]),
            voiced=any(self._voiced),
            peak_rms=max(self._rms, default=0.0),
        )
        self._buf = bytearray()
        self._rms.clear()
        self._quiet.clear()
        self._voiced.clear()
        self._quiet_run = 0
        self._peak = 0.0
        return segment

    def _cut(self, frames: int) -> Segment:
        size = frames * self._frame_bytes
        segment = Segment(
            pcm=bytes(self._buf[:size]),
            voiced=any(self._voiced[:frames]),
            peak_rms=max(self._rms[:frames], default=0.0),
        )
        del self._buf[:size]
        del self._rms[:frames]
        del self._quiet[:frames]
        del self._voiced[:frames]
        self._peak = max(self._rms, default=0.0)
        run = 0
        for quiet in reversed(self._quiet):
            if not quiet:
                break
            run += 1
        self._quiet_run = run
        return segment
