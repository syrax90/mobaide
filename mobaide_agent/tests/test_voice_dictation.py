"""``DictationManager`` against a hand-written helper fake: chunk sequencing
(duplicates, gaps), the size/length limits, ``finish`` (seq check,
``transcribing`` then ``done``, idempotence, segment order), the single
retry after a helper crash, the failure latch (a model that cannot load is
loaded once and fails the next chunk), abort, and the reaper on a pinned
clock, plus the counts and peak level of the ``voice.dictation_finished``
log line."""
from __future__ import annotations

import asyncio
import base64
import logging
import math
from array import array
from pathlib import Path
from types import SimpleNamespace

import pytest

from mobaide.voice import dictation as dictation_module
from mobaide.voice.dictation import (
    IDLE_EXPIRE_SECONDS,
    MAX_ACTIVE_DICTATIONS,
    MAX_CHUNK_BYTES,
    MAX_LIFETIME_SECONDS,
    MAX_SECONDS,
    RESULT_TTL_SECONDS,
    DictationManager,
)
from mobaide.voice.errors import VoiceError, not_ready
from mobaide.voice.helper import HelperError, LoadSpec, TranscribeResult

RATE = 16000
SPEC = LoadSpec(key="fake:1", engine="fake", model_dir=Path("."), runtime_dir=None)


def _tone(seconds: float, amplitude: int = 3000) -> bytes:
    return array(
        "h", (int(amplitude * math.sin(2 * math.pi * 200 * i / RATE)) for i in range(int(seconds * RATE)))
    ).tobytes()


def _voiced_silent_voiced() -> bytes:
    """Speech, a pause, five seconds of hiss below the silence level
    closed by a quieter pause (a silent segment), then speech again."""
    return _tone(6.0) + _silence(0.6) + _tone(5.0, amplitude=12) + _tone(0.6, amplitude=3) + _tone(1.0)


def _silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(seconds * RATE)


def _b64(pcm: bytes) -> str:
    return base64.b64encode(pcm).decode()


async def _send(manager: DictationManager, did: str, pcm: bytes) -> int:
    """Upload ``pcm`` in maximum-size chunks; return the last ``seq``."""
    seq = -1
    for seq, offset in enumerate(range(0, len(pcm), MAX_CHUNK_BYTES)):
        await manager.append(did, seq, _b64(pcm[offset : offset + MAX_CHUNK_BYTES]))
    return seq


class _FakeHelper:
    """Transcribes a segment to its length (``"<ms>ms"``); can hold calls on
    a gate, fail the next calls with scripted helper error codes and fail the
    preload with ``preload_failure``."""

    def __init__(self) -> None:
        self.busy = False
        self.calls = 0
        self.preloads = 0
        self.failures: list[str] = []
        self.preload_failure: str | None = None
        self.gate: asyncio.Event | None = None
        self.hold_first: asyncio.Event | None = None

    async def preload(self, spec: LoadSpec) -> int:
        self.preloads += 1
        if self.preload_failure is not None:
            raise HelperError(self.preload_failure, "model will not load")
        return 1

    async def transcribe(self, spec: LoadSpec, pcm16: bytes, sample_rate: int) -> TranscribeResult:
        self.calls += 1
        if self.calls == 1 and self.hold_first is not None:
            await self.hold_first.wait()
        if self.gate is not None:
            await self.gate.wait()
        if self.failures:
            raise HelperError(self.failures.pop(0), "worker trouble")
        ms = len(pcm16) // 2 * 1000 // sample_rate
        return TranscribeResult(text=f"{ms}ms", decode_ms=1, load_ms=None, peak_rss_bytes=None)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def fake_helper() -> _FakeHelper:
    return _FakeHelper()


@pytest.fixture
async def manager(fake_helper):
    mgr = DictationManager(fake_helper, lambda: SPEC)
    yield mgr
    await mgr.abort_all()


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.005)


async def _raises(code: str, awaitable) -> VoiceError:
    with pytest.raises(VoiceError) as excinfo:
        await awaitable
    assert excinfo.value.code == code
    return excinfo.value


def _traceback_depth(error: BaseException) -> int:
    depth, tb = 0, error.__traceback__
    while tb is not None:
        depth, tb = depth + 1, tb.tb_next
    return depth


class TestCreate:
    async def test_not_ready_refuses_and_opens_nothing(self, fake_helper):
        def ready():
            raise not_ready()

        mgr = DictationManager(fake_helper, ready)

        error = await _raises("voice_not_ready", mgr.create())

        assert error.status == 409
        assert not mgr.busy()

    async def test_create_preloads_the_model(self, manager, fake_helper):
        await manager.create()

        await _wait_for(lambda: fake_helper.preloads == 1)

    async def test_at_most_two_open_dictations(self, manager):
        first = await manager.create()
        for _ in range(MAX_ACTIVE_DICTATIONS - 1):
            await manager.create()

        error = await _raises("voice_busy", manager.create())

        assert error.status == 409
        await manager.finish(first, -1)
        assert await manager.create()

    async def test_finished_dictation_still_transcribing_keeps_its_slot(self, manager, fake_helper):
        fake_helper.gate = asyncio.Event()
        transcribing = await manager.create()
        await manager.append(transcribing, 0, _b64(_tone(1.0)))
        finishing = asyncio.ensure_future(manager.finish(transcribing, 0))
        await _wait_for(lambda: fake_helper.calls == 1)
        for _ in range(MAX_ACTIVE_DICTATIONS - 1):
            await manager.create()

        await _raises("voice_busy", manager.create())

        fake_helper.gate.set()
        assert (await finishing).state == "done"
        assert await manager.create()


class TestAppend:
    async def test_duration_accumulates_per_chunk(self, manager):
        did = await manager.create()

        assert await manager.append(did, 0, _b64(_tone(1.0))) == 1000
        assert await manager.append(did, 1, _b64(_tone(0.5))) == 1500

    async def test_retried_chunk_is_acknowledged_without_appending(self, manager):
        did = await manager.create()
        await manager.append(did, 0, _b64(_tone(1.0)))

        assert await manager.append(did, 0, _b64(_tone(1.0))) == 1000
        assert await manager.append(did, 1, _b64(_tone(1.0))) == 2000

    async def test_gap_is_refused_with_the_expected_seq(self, manager):
        did = await manager.create()
        await manager.append(did, 0, _b64(_tone(0.1)))

        error = await _raises("seq_gap", manager.append(did, 2, _b64(_tone(0.1))))

        assert error.status == 409
        assert error.detail()["expected_seq"] == 1

    async def test_first_chunk_must_be_seq_zero(self, manager):
        did = await manager.create()

        error = await _raises("seq_gap", manager.append(did, 1, _b64(_tone(0.1))))

        assert error.detail()["expected_seq"] == 0

    async def test_chunk_of_the_maximum_size_is_accepted(self, manager):
        did = await manager.create()

        assert await manager.append(did, 0, _b64(b"\x00" * MAX_CHUNK_BYTES)) == 2048

    async def test_oversized_chunk_is_413(self, manager):
        did = await manager.create()

        error = await _raises("chunk_too_large", manager.append(did, 0, _b64(b"\x00" * (MAX_CHUNK_BYTES + 2))))

        assert error.status == 413
        assert await manager.append(did, 0, _b64(_tone(0.1))) == 100

    @pytest.mark.parametrize("payload", ["!!!not base64!!!", _b64(b"\x00\x00\x00")], ids=["not_b64", "odd_bytes"])
    async def test_bad_audio_is_422(self, manager, payload):
        did = await manager.create()

        error = await _raises("bad_audio", manager.append(did, 0, payload))

        assert error.status == 422

    async def test_dictation_longer_than_the_limit_is_refused(self, manager):
        did = await manager.create()
        seq = await _send(manager, did, _silence(MAX_SECONDS))

        error = await _raises("too_long", manager.append(did, seq + 1, _b64(b"\x00\x00")))

        assert error.status == 409
        assert error.detail()["max_seconds"] == MAX_SECONDS

    async def test_unknown_dictation_is_404(self, manager):
        error = await _raises("dictation_not_found", manager.append("nope", 0, _b64(b"\x00\x00")))

        assert error.status == 404


class TestFinish:
    async def test_transcript_joins_voiced_segments_in_order(self, manager, fake_helper):
        """A segment reaches the helper only once the one before it is done;
        the transcript keeps speech order."""
        fake_helper.hold_first = asyncio.Event()
        did = await manager.create()
        last_seq = await _send(manager, did, _tone(6.0) + _silence(0.6) + _tone(1.0))
        finishing = asyncio.ensure_future(manager.finish(did, last_seq))
        await _wait_for(lambda: fake_helper.calls == 1)
        await asyncio.sleep(0.05)
        assert fake_helper.calls == 1
        fake_helper.hold_first.set()

        result = await finishing

        assert (result.state, result.text, result.duration_ms) == ("done", "6260ms 1340ms", 7600)
        assert fake_helper.calls == 2

    async def test_a_silent_segment_keeps_the_chain(self, manager, fake_helper):
        """The speech after a silent segment still waits for the speech
        before it."""
        fake_helper.hold_first = asyncio.Event()
        did = await manager.create()
        last_seq = await _send(manager, did, _voiced_silent_voiced())
        finishing = asyncio.ensure_future(manager.finish(did, last_seq))
        await _wait_for(lambda: fake_helper.calls >= 1)
        await asyncio.sleep(0.05)
        assert fake_helper.calls == 1
        fake_helper.hold_first.set()

        result = await finishing

        assert (result.state, result.text) == ("done", "6260ms 1340ms")
        assert fake_helper.calls == 2

    async def test_silence_is_never_sent_to_the_helper(self, manager, fake_helper):
        did = await manager.create()
        await manager.append(did, 0, _b64(_silence(2.0)))

        result = await manager.finish(did, 0)

        assert (result.state, result.text) == ("done", "")
        assert fake_helper.calls == 0

    async def test_quiet_speech_below_the_voice_level_reaches_the_helper(
        self, manager, fake_helper
    ):
        """Words at about -52 dBFS between digital silence, as an ungained
        phone microphone records them."""
        did = await manager.create()
        speech = b"".join(_silence(0.3) + _tone(0.3, amplitude=113) for _ in range(4))
        last_seq = await _send(manager, did, speech)

        result = await manager.finish(did, last_seq)

        assert (result.state, result.text) == ("done", "2400ms")
        assert fake_helper.calls == 1

    async def test_finish_without_audio(self, manager):
        did = await manager.create()

        result = await manager.finish(did, -1)

        assert (result.state, result.text, result.duration_ms) == ("done", "", 0)

    async def test_finish_before_the_last_chunk_arrived_is_a_seq_gap(self, manager):
        did = await manager.create()
        await manager.append(did, 0, _b64(_tone(0.5)))

        error = await _raises("seq_gap", manager.finish(did, 1))

        assert error.detail()["expected_seq"] == 1
        assert await manager.append(did, 1, _b64(_tone(0.5))) == 1000

    async def test_chunk_refused_as_too_long_does_not_count_as_sent(self, manager):
        did = await manager.create()
        last_accepted = await _send(manager, did, _silence(MAX_SECONDS))
        await _raises("too_long", manager.append(did, last_accepted + 1, _b64(b"\x00\x00")))

        gap = await _raises("seq_gap", manager.finish(did, last_accepted + 1))
        result = await manager.finish(did, last_accepted)

        assert gap.detail()["expected_seq"] == last_accepted + 1
        assert (result.state, result.duration_ms) == ("done", MAX_SECONDS * 1000)

    async def test_finish_is_idempotent(self, manager, fake_helper):
        did = await manager.create()
        await manager.append(did, 0, _b64(_tone(1.0)))

        first = await manager.finish(did, 0)
        again = await manager.finish(did, 0)

        assert first == again
        assert first.text == "1000ms"
        assert fake_helper.calls == 1

    async def test_audio_after_finish_is_refused(self, manager):
        did = await manager.create()
        await manager.finish(did, -1)

        await _raises("dictation_finished", manager.append(did, 0, _b64(_tone(0.1))))

    async def test_slow_transcript_answers_transcribing_then_done(
        self, manager, fake_helper, monkeypatch
    ):
        monkeypatch.setattr(dictation_module, "FINISH_WAIT_SECONDS", 0.05)
        fake_helper.gate = asyncio.Event()
        did = await manager.create()
        await manager.append(did, 0, _b64(_tone(1.0)))

        pending = await manager.finish(did, 0)
        assert (pending.state, pending.text, pending.duration_ms) == ("transcribing", None, 1000)
        assert manager.busy()

        fake_helper.gate.set()
        done = await manager.finish(did, 0)

        assert (done.state, done.text) == ("done", "1000ms")
        assert not manager.busy()

    async def test_helper_crash_is_retried_once(self, manager, fake_helper):
        fake_helper.failures = ["helper_crashed"]
        did = await manager.create()
        await manager.append(did, 0, _b64(_tone(1.0)))

        result = await manager.finish(did, 0)

        assert result.text == "1000ms"
        assert fake_helper.calls == 2

    async def test_second_failure_is_transcription_failed(self, manager, fake_helper):
        fake_helper.failures = ["helper_timeout", "helper_crashed"]
        did = await manager.create()
        await manager.append(did, 0, _b64(_tone(1.0)))

        error = await _raises("transcription_failed", manager.finish(did, 0))

        assert error.status == 503
        assert fake_helper.calls == 2

    async def test_non_retryable_failure_is_not_retried(self, manager, fake_helper):
        fake_helper.failures = ["load_failed"]
        did = await manager.create()
        await manager.append(did, 0, _b64(_tone(1.0)))

        await _raises("transcription_failed", manager.finish(did, 0))

        assert fake_helper.calls == 1

    @pytest.mark.parametrize(
        ("code", "calls"), [("load_failed", 1), ("helper_crashed", 2)], ids=["not_retryable", "retry_failed"]
    )
    async def test_later_segments_of_a_failed_dictation_never_reach_the_helper(
        self, manager, fake_helper, code, calls
    ):
        fake_helper.failures = [code] * 10
        did = await manager.create()
        last_seq = await _send(
            manager, did, _tone(6.0) + _silence(0.6) + _tone(6.0) + _silence(0.6) + _tone(1.0)
        )

        error = await _raises("transcription_failed", manager.finish(did, last_seq))

        assert error.status == 503
        assert error.message == "worker trouble"
        assert fake_helper.calls == calls

    async def test_a_failed_dictation_does_not_fail_the_next_one(self, manager, fake_helper):
        fake_helper.failures = ["load_failed"]
        failed = await manager.create()
        await manager.append(failed, 0, _b64(_tone(1.0)))
        await _raises("transcription_failed", manager.finish(failed, 0))

        did = await manager.create()
        await manager.append(did, 0, _b64(_tone(1.0)))

        assert (await manager.finish(did, 0)).text == "1000ms"


class TestFailureLatch:
    async def test_a_model_that_fails_to_load_is_loaded_only_once(self, manager, fake_helper):
        """Later segments wait for the first; once it fails for good they
        never reach the helper (which would load the model again)."""
        fake_helper.hold_first = asyncio.Event()
        fake_helper.failures = ["load_failed"] * 10
        did = await manager.create()
        last_seq = await _send(
            manager, did, _tone(6.0) + _silence(0.6) + _tone(6.0) + _silence(0.6) + _tone(1.0)
        )
        finishing = asyncio.ensure_future(manager.finish(did, last_seq))
        await _wait_for(lambda: fake_helper.calls == 1)
        await asyncio.sleep(0.05)
        fake_helper.hold_first.set()

        error = await _raises("transcription_failed", finishing)

        assert error.status == 503
        assert fake_helper.calls == 1

    async def test_a_silent_segment_between_does_not_load_the_model_again(
        self, manager, fake_helper
    ):
        fake_helper.hold_first = asyncio.Event()
        fake_helper.failures = ["load_failed"] * 10
        did = await manager.create()
        last_seq = await _send(manager, did, _voiced_silent_voiced())
        finishing = asyncio.ensure_future(manager.finish(did, last_seq))
        await _wait_for(lambda: fake_helper.calls >= 1)
        await asyncio.sleep(0.05)
        fake_helper.hold_first.set()

        error = await _raises("transcription_failed", finishing)

        assert error.status == 503
        assert fake_helper.calls == 1

    async def test_failed_segment_refuses_the_next_chunk_while_recording(self, manager, fake_helper):
        fake_helper.failures = ["load_failed"]
        did = await manager.create()
        seq = await _send(manager, did, _tone(6.0) + _silence(0.6))
        await _wait_for(lambda: fake_helper.calls == 1)
        await asyncio.sleep(0.01)

        error = await _raises("transcription_failed", manager.append(did, seq + 1, _b64(_tone(0.1))))

        assert error.status == 503
        assert error.message == "worker trouble"

    async def test_retried_chunk_after_finish_still_reads_dictation_finished(
        self, manager, fake_helper
    ):
        fake_helper.failures = ["load_failed"]
        did = await manager.create()
        await manager.append(did, 0, _b64(_tone(1.0)))
        await _raises("transcription_failed", manager.finish(did, 0))

        await _raises("dictation_finished", manager.append(did, 0, _b64(_tone(1.0))))

    async def test_preload_that_cannot_load_the_model_fails_the_next_chunk(
        self, manager, fake_helper
    ):
        fake_helper.preload_failure = "load_failed"
        did = await manager.create()
        await _wait_for(lambda: fake_helper.preloads == 1)
        await asyncio.sleep(0.01)

        error = await _raises("transcription_failed", manager.append(did, 0, _b64(_tone(0.5))))

        assert error.status == 503
        assert error.message == "model will not load"
        assert fake_helper.calls == 0

    async def test_preload_that_cannot_load_the_model_fails_finish_of_silence(
        self, manager, fake_helper
    ):
        """Silence never reaches the helper, so without the latch a broken
        model would read as an empty transcript."""
        did = await manager.create()
        await manager.append(did, 0, _b64(_silence(1.0)))
        await _wait_for(lambda: fake_helper.preloads == 1)
        fake_helper.preload_failure = "load_failed"
        failing = await manager.create()
        await _wait_for(lambda: fake_helper.preloads == 2)
        await asyncio.sleep(0.01)

        assert (await manager.finish(did, 0)).text == ""
        error = await _raises("transcription_failed", manager.finish(failing, -1))

        assert error.status == 503
        assert fake_helper.calls == 0

    @pytest.mark.parametrize("code", ["helper_crashed", "helper_timeout"])
    async def test_preload_crash_is_not_latched(self, manager, fake_helper, code):
        fake_helper.preload_failure = code
        did = await manager.create()
        await _wait_for(lambda: fake_helper.preloads == 1)
        await asyncio.sleep(0.01)

        await manager.append(did, 0, _b64(_tone(1.0)))

        assert (await manager.finish(did, 0)).text == "1000ms"

    async def test_refusals_raise_fresh_copies_of_the_latched_failure(self, manager, fake_helper):
        """Raising the stored failure again would chain every request's
        frames, and the audio they hold, onto its traceback."""
        fake_helper.preload_failure = "load_failed"
        did = await manager.create()
        await _wait_for(lambda: fake_helper.preloads == 1)
        await asyncio.sleep(0.01)
        latched = manager._dictations[did].failure

        raised = [
            await _raises("transcription_failed", manager.append(did, 0, _b64(_tone(0.1))))
            for _ in range(3)
        ]
        raised.append(await _raises("transcription_failed", manager.finish(did, -1)))

        assert latched.__traceback__ is None
        assert len({id(error) for error in [latched, *raised]}) == 5
        for error in raised:
            assert (error.status, error.detail()) == (503, latched.detail())
            assert error.message == "model will not load"

    async def test_asking_a_failed_dictation_again_does_not_grow_the_stored_traceback(
        self, manager, fake_helper
    ):
        fake_helper.failures = ["load_failed"]
        did = await manager.create()
        await manager.append(did, 0, _b64(_tone(1.0)))
        first = await _raises("transcription_failed", manager.finish(did, 0))
        latched = manager._dictations[did].failure
        depth = _traceback_depth(latched)

        again = [await _raises("transcription_failed", manager.finish(did, 0)) for _ in range(3)]

        assert _traceback_depth(latched) == depth
        assert len({id(error) for error in [latched, first, *again]}) == 5
        for error in [first, *again]:
            assert (error.status, error.detail()) == (503, latched.detail())


class TestAbortAndBusy:
    async def test_abort_forgets_the_dictation_and_is_idempotent(self, manager):
        did = await manager.create()

        await manager.abort(did)
        await manager.abort(did)

        await _raises("dictation_not_found", manager.append(did, 0, _b64(_tone(0.1))))
        assert not manager.busy()

    async def test_abort_while_transcribing_frees_the_slot(self, manager, fake_helper, monkeypatch):
        monkeypatch.setattr(dictation_module, "FINISH_WAIT_SECONDS", 0.01)
        fake_helper.gate = asyncio.Event()
        did = await manager.create()
        await manager.append(did, 0, _b64(_tone(1.0)))
        await manager.finish(did, 0)

        await manager.abort(did)

        assert not manager.busy()
        await _raises("dictation_not_found", manager.finish(did, 0))

    async def test_busy_while_open_or_transcribing_only(self, manager, fake_helper):
        assert not manager.busy()
        did = await manager.create()
        assert manager.busy()

        await manager.finish(did, -1)

        assert not manager.busy()
        fake_helper.busy = True
        assert manager.busy()


class TestReaper:
    @pytest.fixture
    def clock(self, monkeypatch) -> _Clock:
        clock = _Clock()
        monkeypatch.setattr(dictation_module, "time", SimpleNamespace(monotonic=clock.monotonic))
        monkeypatch.setattr(dictation_module, "_REAP_INTERVAL_SECONDS", 0.001)
        return clock

    async def test_idle_dictation_expires(self, manager, clock):
        did = await manager.create()
        clock.now += IDLE_EXPIRE_SECONDS - 1
        await asyncio.sleep(0.02)
        await manager.append(did, 0, _b64(_tone(0.1)))

        clock.now += IDLE_EXPIRE_SECONDS - 1
        await asyncio.sleep(0.02)
        assert manager.busy()

        clock.now += 2
        await _wait_for(lambda: not manager.busy())
        await _raises("dictation_not_found", manager.append(did, 1, _b64(_tone(0.1))))

    async def test_open_dictation_is_aborted_after_its_lifetime_even_while_active(
        self, manager, clock
    ):
        did = await manager.create()
        age, seq = 0.0, 0
        while age < MAX_LIFETIME_SECONDS:
            step = min(IDLE_EXPIRE_SECONDS - 1, MAX_LIFETIME_SECONDS - age)
            clock.now += step
            age += step
            await manager.append(did, seq, _b64(_tone(0.1)))
            seq += 1
            await asyncio.sleep(0.02)
        assert manager.busy()

        clock.now += 1
        await _wait_for(lambda: not manager.busy())
        await _raises("dictation_not_found", manager.append(did, seq, _b64(_tone(0.1))))

    async def test_finished_result_is_kept_then_dropped(self, manager, clock):
        did = await manager.create()
        await manager.append(did, 0, _b64(_tone(1.0)))
        await manager.finish(did, 0)

        clock.now += RESULT_TTL_SECONDS - 1
        await asyncio.sleep(0.02)
        assert (await manager.finish(did, 0)).text == "1000ms"

        clock.now += 2
        await asyncio.sleep(0.02)
        await _raises("dictation_not_found", manager.finish(did, 0))


class TestFinishedLog:
    @staticmethod
    def _finished_line(caplog) -> str:
        lines = [
            r.getMessage() for r in caplog.records
            if r.getMessage().startswith("voice.dictation_finished ")
        ]
        assert len(lines) == 1
        return lines[0]

    async def test_counts_skipped_segments_and_reports_the_peak_level(self, manager, caplog):
        did = await manager.create()
        last_seq = await _send(manager, did, _tone(6.0, amplitude=3000) + _silence(1.6))

        with caplog.at_level(logging.INFO, logger="mobaide.voice.dictation"):
            await manager.finish(did, last_seq)

        assert " segments=2 skipped=1 peak_dbfs=-24 tail_ms=" in self._finished_line(caplog)

    @pytest.mark.parametrize(
        ("pcm", "fields"),
        [
            (None, "segments=0 skipped=0 peak_dbfs=-99"),
            (_silence(1.0), "segments=1 skipped=1 peak_dbfs=-99"),
            (b"\x01\x00" + _silence(1.0), "segments=1 skipped=1 peak_dbfs=-99"),
        ],
        ids=["no-audio", "digital-silence", "below-minus-99-dbfs"],
    )
    async def test_silence_reads_minus_99_dbfs(self, manager, caplog, pcm, fields):
        did = await manager.create()
        last_seq = -1 if pcm is None else await _send(manager, did, pcm)

        with caplog.at_level(logging.INFO, logger="mobaide.voice.dictation"):
            await manager.finish(did, last_seq)

        assert f" {fields} tail_ms=" in self._finished_line(caplog)
