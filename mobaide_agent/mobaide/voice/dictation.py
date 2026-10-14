"""Dictations: numbered PCM chunks in, one transcript out.

A client opens a dictation, uploads 16 kHz mono PCM16 chunks with ``seq``
0, 1, 2… (one request in flight at a time; a retried ``seq`` is acknowledged
without being appended again) and calls ``finish`` with the last ``seq`` it
sent. ``finish`` waits at most ``FINISH_WAIT_SECONDS`` (below the relay's
30 s proxy timeout) and otherwise answers ``transcribing``; calling it again
is safe.

Audio lives only in memory and is dropped as soon as the text is known;
neither audio nor text is ever logged or written to disk.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .errors import VoiceError, dictation_not_found
from .helper import HelperError, LoadSpec, SttHelper, consume_exception
from .segmenter import PauseSegmenter, Segment

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
MAX_SECONDS = 120
MAX_CHUNK_BYTES = 65536
MAX_ACTIVE_DICTATIONS = 2
FINISH_WAIT_SECONDS = 20.0
IDLE_EXPIRE_SECONDS = 60.0
# Wall-clock bound on an open dictation, so a client trickling tiny chunks
# cannot hold it (and the auto-updater's deferral) open forever.
MAX_LIFETIME_SECONDS = 2 * MAX_SECONDS + 60
RESULT_TTL_SECONDS = 120.0
_REAP_INTERVAL_SECONDS = 10.0
_MAX_BYTES = MAX_SECONDS * SAMPLE_RATE * 2
_MAX_B64_CHARS = (MAX_CHUNK_BYTES + 2) // 3 * 4
_RETRYABLE = frozenset({"helper_crashed", "helper_timeout"})

STATE_DONE = "done"
STATE_TRANSCRIBING = "transcribing"


@dataclass
class _Dictation:
    id: str
    load: LoadSpec
    segmenter: PauseSegmenter
    created_at: float
    last_activity: float
    next_seq: int = 0
    received_bytes: int = 0
    parts: list[asyncio.Task[str]] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    collect: asyncio.Task[str] | None = None
    finished_at: float | None = None
    # Set by the first segment that fails for good: the dictation's result is
    # already decided, so later segments must not respawn the worker and
    # reload the model just to fail again.
    failure: VoiceError | None = None

    @property
    def duration_ms(self) -> int:
        return self.received_bytes // 2 * 1000 // SAMPLE_RATE


@dataclass(frozen=True)
class DictationResult:
    state: str
    text: str | None
    duration_ms: int


class DictationManager:
    def __init__(self, helper: SttHelper, ready: Callable[[], LoadSpec]) -> None:
        self._helper = helper
        self._ready = ready
        self._dictations: dict[str, _Dictation] = {}
        self._reaper: asyncio.Task[None] | None = None
        self._background: set[asyncio.Task[Any]] = set()

    def busy(self) -> bool:
        if self._helper.busy:
            return True
        return any(d.collect is None or not d.collect.done() for d in self._dictations.values())

    async def create(self) -> str:
        load = self._ready()
        # A finished dictation still holds its queued audio until it is
        # transcribed, so it keeps its slot until then.
        active = sum(
            1 for d in self._dictations.values() if d.collect is None or not d.collect.done()
        )
        if active >= MAX_ACTIVE_DICTATIONS:
            raise VoiceError(
                409, "voice_busy", "Another dictation is in progress on this computer."
            )
        now = time.monotonic()
        dictation = _Dictation(
            id=uuid.uuid4().hex,
            load=load,
            segmenter=PauseSegmenter(SAMPLE_RATE),
            created_at=now,
            last_activity=now,
        )
        self._dictations[dictation.id] = dictation
        self._ensure_reaper()
        self._spawn(self._preload(dictation))
        logger.info("voice.dictation_started dictation_id=%s", dictation.id)
        return dictation.id

    async def append(self, dictation_id: str, seq: int, pcm16_b64: str) -> int:
        """Append chunk ``seq``; return the audio duration received so far."""
        dictation = self._get(dictation_id)
        async with dictation.lock:
            if dictation.collect is not None:
                raise VoiceError(409, "dictation_finished", "This dictation is already finished.")
            if dictation.failure is not None:
                # Tell the client now, while it is still recording, instead
                # of letting it upload audio nobody can transcribe.
                raise _fresh(dictation.failure)
            if seq < dictation.next_seq:
                return dictation.duration_ms
            if seq > dictation.next_seq:
                raise VoiceError(
                    409, "seq_gap", "An earlier audio chunk is missing.",
                    expected_seq=dictation.next_seq,
                )
            pcm = _decode_chunk(pcm16_b64)
            if dictation.received_bytes + len(pcm) > _MAX_BYTES:
                raise VoiceError(
                    409, "too_long", f"A dictation can last at most {MAX_SECONDS} seconds.",
                    max_seconds=MAX_SECONDS,
                )
            dictation.received_bytes += len(pcm)
            dictation.next_seq += 1
            dictation.last_activity = time.monotonic()
            for segment in dictation.segmenter.feed(pcm):
                dictation.parts.append(self._start_part(dictation, segment))
            return dictation.duration_ms

    async def finish(self, dictation_id: str, last_seq: int) -> DictationResult:
        dictation = self._get(dictation_id)
        async with dictation.lock:
            if dictation.collect is None:
                if dictation.failure is not None:
                    raise _fresh(dictation.failure)
                if last_seq >= dictation.next_seq:
                    raise VoiceError(
                        409, "seq_gap", "An audio chunk has not arrived yet.",
                        expected_seq=dictation.next_seq,
                    )
                tail = dictation.segmenter.flush()
                if tail is not None:
                    dictation.parts.append(self._start_part(dictation, tail))
                dictation.collect = asyncio.ensure_future(self._collect(dictation))
                dictation.collect.add_done_callback(consume_exception)
            dictation.last_activity = time.monotonic()
            collect = dictation.collect
        done, _ = await asyncio.wait({collect}, timeout=FINISH_WAIT_SECONDS)
        if not done:
            return DictationResult(STATE_TRANSCRIBING, None, dictation.duration_ms)
        if collect.cancelled():
            raise dictation_not_found()
        error = collect.exception()
        if error is not None:
            raise _fresh(error) if isinstance(error, VoiceError) else error
        return DictationResult(STATE_DONE, collect.result(), dictation.duration_ms)

    async def abort(self, dictation_id: str) -> None:
        dictation = self._dictations.pop(dictation_id, None)
        if dictation is None:
            return
        for part in dictation.parts:
            part.cancel()
        dictation.parts.clear()
        if dictation.collect is not None and not dictation.collect.done():
            dictation.collect.cancel()
        logger.info("voice.dictation_aborted dictation_id=%s", dictation_id)

    async def abort_all(self) -> None:
        for dictation_id in list(self._dictations):
            await self.abort(dictation_id)
        if self._reaper is not None:
            self._reaper.cancel()
            self._reaper = None
        for task in list(self._background):
            task.cancel()

    def _get(self, dictation_id: str) -> _Dictation:
        dictation = self._dictations.get(dictation_id)
        if dictation is None:
            raise dictation_not_found()
        return dictation

    def _start_part(self, dictation: _Dictation, segment: Segment) -> asyncio.Task[str]:
        previous = dictation.parts[-1] if dictation.parts else None
        task = asyncio.ensure_future(self._transcribe(dictation, segment, previous))
        task.add_done_callback(consume_exception)
        return task

    async def _transcribe(
        self, dictation: _Dictation, segment: Segment, previous: asyncio.Task[str] | None
    ) -> str:
        if previous is not None:
            # Segments run one after another anyway (the helper serializes
            # them); waiting here lets a failure of the previous one latch
            # before this one could respawn the worker and reload the model.
            # Silent segments wait too, or they would break the chain.
            await asyncio.wait({previous})
        if not segment.voiced:
            return ""
        for attempt in (1, 2):
            if dictation.failure is not None:
                raise _fresh(dictation.failure)
            try:
                result = await self._helper.transcribe(dictation.load, segment.pcm, SAMPLE_RATE)
                return result.text
            except HelperError as exc:
                if attempt == 2 or exc.code not in _RETRYABLE or dictation.failure is not None:
                    dictation.failure = dictation.failure or VoiceError(
                        503, "transcription_failed",
                        exc.message or "Speech recognition failed on this computer.",
                    )
                    raise _fresh(dictation.failure) from exc
                logger.warning("voice.segment_retry code=%s", exc.code)
        raise AssertionError("unreachable")

    async def _collect(self, dictation: _Dictation) -> str:
        started = time.monotonic()
        texts: list[str] = []
        try:
            for index, part in enumerate(dictation.parts):
                try:
                    texts.append(await part)
                except BaseException:
                    for rest in dictation.parts[index + 1 :]:
                        rest.cancel()
                    raise
        finally:
            dictation.finished_at = time.monotonic()
            segments = len(dictation.parts)
            dictation.parts = []
        logger.info(
            "voice.dictation_finished dictation_id=%s duration_ms=%d segments=%d tail_ms=%d",
            dictation.id, dictation.duration_ms, segments,
            int((dictation.finished_at - started) * 1000),
        )
        return " ".join(text for text in texts if text)

    async def _preload(self, dictation: _Dictation) -> None:
        try:
            await self._helper.preload(dictation.load)
        except HelperError as exc:
            logger.warning("voice.preload_failed code=%s", exc.code)
            if exc.code == "load_failed" and dictation.failure is None:
                # The model cannot load on this computer; retrying it for
                # every segment would only reload it again and again.
                dictation.failure = VoiceError(
                    503, "transcription_failed",
                    exc.message or "The speech model could not load on this computer.",
                )

    def _ensure_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.ensure_future(self._reap_loop())
            self._reaper.add_done_callback(consume_exception)

    async def _reap_loop(self) -> None:
        while self._dictations:
            await asyncio.sleep(_REAP_INTERVAL_SECONDS)
            now = time.monotonic()
            for dictation in list(self._dictations.values()):
                if dictation.collect is None:
                    if (
                        now - dictation.last_activity > IDLE_EXPIRE_SECONDS
                        or now - dictation.created_at > MAX_LIFETIME_SECONDS
                    ):
                        logger.info("voice.dictation_expired dictation_id=%s", dictation.id)
                        await self.abort(dictation.id)
                elif (
                    dictation.finished_at is not None
                    and now - dictation.finished_at > RESULT_TTL_SECONDS
                ):
                    self._dictations.pop(dictation.id, None)

    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        task.add_done_callback(consume_exception)


def _fresh(error: VoiceError) -> VoiceError:
    """A copy of a stored failure to raise again: raising the same exception
    object appends every raise's frames (with the request's audio) to its
    traceback, which would keep them alive until the dictation is reaped."""
    return VoiceError(error.status, error.code, error.message, **error.extra)


def _decode_chunk(pcm16_b64: str) -> bytes:
    if len(pcm16_b64) > _MAX_B64_CHARS:
        raise VoiceError(
            413, "chunk_too_large", f"An audio chunk can hold at most {MAX_CHUNK_BYTES} bytes.",
            max_chunk_bytes=MAX_CHUNK_BYTES,
        )
    try:
        pcm = base64.b64decode(pcm16_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise VoiceError(422, "bad_audio", "Audio must be base64-encoded PCM16.") from exc
    if len(pcm) > MAX_CHUNK_BYTES:
        raise VoiceError(
            413, "chunk_too_large", f"An audio chunk can hold at most {MAX_CHUNK_BYTES} bytes.",
            max_chunk_bytes=MAX_CHUNK_BYTES,
        )
    if len(pcm) % 2:
        raise VoiceError(422, "bad_audio", "PCM16 audio must have an even number of bytes.")
    return pcm

