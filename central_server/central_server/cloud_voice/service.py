"""Cloud voice input: dictations, limits and the speech service's health.

The audio is held in memory until the speech
service answers and is then dropped; neither audio nor text is logged or
stored by this server. One uvicorn worker serves the app, so the dictations
and counters below need no shared store.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from ..auth.rate_limit import ResendRateLimiter
from ..relay.routing import broadcast_user_event
from .audio import (
    MAX_BYTES,
    MAX_CHUNK_BYTES,
    MAX_SECONDS,
    MIN_SPEECH_BYTES,
    SAMPLE_RATE,
    SILENCE_PEAK,
    decode_chunk,
    duration_ms,
    peak,
    tidy_transcript,
)
from .errors import (
    CloudVoiceError,
    busy,
    dictation_not_found,
    disabled,
    limit_reached,
    unavailable,
)
from .speech_client import SpeechClient, SpeechServiceError

if TYPE_CHECKING:
    from ..auth.models import UserTokenPayload
    from ..config import CentralServerConfig
    from ..db.database import Database
    from ..relay.routing import RoutingTable

logger = logging.getLogger(__name__)

EVENT_CHANGED = "cloud_voice.changed"

STATE_OFF = "off"
STATE_READY = "ready"
STATE_UNAVAILABLE = "unavailable"
STATE_LIMIT_REACHED = "limit_reached"

STATE_DONE = "done"
STATE_TRANSCRIBING = "transcribing"

LIMITS = {
    "sample_rate": SAMPLE_RATE,
    "max_seconds": MAX_SECONDS,
    "max_chunk_bytes": MAX_CHUNK_BYTES,
}

# Dictations of all users that still hold audio: at 3.84 MB each (and about
# twice that while one is sent), a bound on memory. A finished one holds
# only its text and does not count.
MAX_OPEN_DICTATIONS = 64
# Below the clients' 35 s wait for one ``finish`` answer.
FINISH_WAIT_SECONDS = 20.0
IDLE_EXPIRE_SECONDS = 60.0
MAX_LIFETIME_SECONDS = 2 * MAX_SECONDS + 60
RESULT_TTL_SECONDS = 120.0
_REAP_INTERVAL_SECONDS = 10.0
_TRANSCRIBE_TIMEOUT_SECONDS = 45.0
# The whole request, whatever the speech service does between its bytes.
_TRANSCRIBE_DEADLINE_SECONDS = 75.0
_GATE_WAIT_SECONDS = 5.0
_DICTATIONS_PER_MINUTE = 30
USAGE_RETENTION_DAYS = 35
# How long an answer of the speech service proves it is up: checked before a
# recording starts, and less often for the status the mic is drawn from.
_FRESH_FOR_DICTATION_SECONDS = 60.0
_FRESH_FOR_STATUS_SECONDS = 300.0
# Short, so a user who taps "Try again" learns within seconds that the
# service is back; a probe is one tiny request.
_PROBE_INTERVAL_SECONDS = 5.0
_PROBE_TIMEOUT_SECONDS = 6.0
# The whole probe, as for a transcription: connect, the answer, and a margin.
_PROBE_DEADLINE_SECONDS = 20.0
# After a failed attempt, the same dictation is not sent again before
# 2, 4, 8, then 16 s have passed: each attempt uploads the whole recording.
_MAX_RETRY_DELAY_SECONDS = 16.0
# How long a status request waits for a probe before it answers from the last
# known state; the probe runs on and the next request sees its result.
_STATUS_PROBE_WAIT_SECONDS = 2.0
_PROBE_USER = "health-check"
_PROBE_AUDIO = bytes(MIN_SPEECH_BYTES)
# A user the speech service refuses is not asked about again for this long.
_BLOCK_MEMORY_SECONDS = 600.0


@dataclass
class _Dictation:
    id: str
    user_id: str
    created_at: float
    last_activity: float
    next_seq: int = 0
    received_bytes: int = 0
    peak: int = 0
    chunks: list[bytes] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    task: asyncio.Task[str] | None = None
    text: str | None = None
    finished_at: float | None = None
    failures: int = 0
    retry_at: float = 0.0
    last_error: CloudVoiceError | None = None


@dataclass(frozen=True)
class DictationResult:
    dictation_id: str
    state: str
    text: str | None
    duration_ms: int


def _consume_exception(task: asyncio.Task[Any]) -> None:
    if not task.cancelled():
        task.exception()


def _copy(error: CloudVoiceError) -> CloudVoiceError:
    """A stored refusal to raise again: raising the same object twice would
    grow its traceback with every request."""
    return CloudVoiceError(error.status, error.code, error.message, **error.extra)


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _resets_at() -> str:
    tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
    return f"{tomorrow.isoformat()}T00:00:00Z"


def usage_cutoff_day() -> str:
    """The oldest UTC day whose usage rows are kept."""
    oldest = datetime.now(timezone.utc).date() - timedelta(days=USAGE_RETENTION_DAYS)
    return oldest.isoformat()


class CloudVoiceService:
    def __init__(
        self,
        db: "Database",
        routing: "RoutingTable",
        config: "CentralServerConfig",
        client: SpeechClient | None,
    ) -> None:
        self._db = db
        self._routing = routing
        self._client = client
        self._user_daily_seconds = config.cloud_voice_user_daily_seconds
        self._global_daily_seconds = config.cloud_voice_global_daily_seconds
        self._allowed_emails = frozenset(config.cloud_voice_allowed_emails)
        self._gate = asyncio.Semaphore(config.cloud_voice_max_concurrency)
        self._rate = ResendRateLimiter(
            cooldown_seconds=0, hourly_cap=_DICTATIONS_PER_MINUTE, window_seconds=60
        )
        self._dictations: dict[str, _Dictation] = {}
        self._by_user: dict[str, str] = {}
        self._reaper: asyncio.Task[None] | None = None
        self._up = True
        self._last_ok = -math.inf
        self._last_probe = -math.inf
        self._probe: asyncio.Task[bool] | None = None
        self._blocked: dict[str, float] = {}

    @classmethod
    def from_config(
        cls, db: "Database", routing: "RoutingTable", config: "CentralServerConfig"
    ) -> "CloudVoiceService":
        client: SpeechClient | None = None
        missing = config.cloud_voice_missing
        if not missing and config.cloud_voice_enabled:
            assert config.cloud_voice_url and config.cloud_voice_api_key
            assert config.cloud_voice_model
            client = SpeechClient(
                base_url=config.cloud_voice_url,
                api_key=config.cloud_voice_api_key,
                model=config.cloud_voice_model,
                prompt=config.cloud_voice_prompt,
            )
            logger.info("cloud_voice.enabled")
        elif missing and len(missing) < 3:
            # Some of the three settings are there: a typo, not an opt-out.
            logger.warning("cloud_voice.misconfigured missing=%s", ",".join(missing))
        return cls(db, routing, config, client)

    def _offered(self, user: "UserTokenPayload") -> bool:
        if self._client is None:
            return False
        if self._allowed_emails and user.email.strip().lower() not in self._allowed_emails:
            return False
        blocked_at = self._blocked.get(user.sub)
        if blocked_at is None:
            return True
        if time.monotonic() - blocked_at > _BLOCK_MEMORY_SECONDS:
            del self._blocked[user.sub]
            return True
        return False

    async def status(self, user: "UserTokenPayload", *, probe: bool = True) -> dict[str, Any]:
        """``probe=False`` answers from the last known health, for a caller
        that must not wait for the speech service."""
        day = _today()
        used = 0
        if not self._offered(user):
            state = STATE_OFF
        elif (used := await self._db.get_cloud_voice_seconds(user.sub, day)) >= (
            self._user_daily_seconds
        ):
            state = STATE_LIMIT_REACHED
        elif await self._db.get_cloud_voice_seconds_total(day) >= self._global_daily_seconds:
            state = STATE_UNAVAILABLE
        elif not (
            await self._healthy(_FRESH_FOR_STATUS_SECONDS, wait=_STATUS_PROBE_WAIT_SECONDS)
            if probe
            else self._up
        ):
            state = STATE_UNAVAILABLE
        else:
            state = STATE_READY
        return {
            "state": state,
            "limits": LIMITS,
            "usage": {
                "used_seconds": used,
                "limit_seconds": self._user_daily_seconds,
                "resets_at": _resets_at(),
            },
            "engines": await self._db.get_voice_engines(user.sub),
        }

    async def set_engine(
        self, user: "UserTokenPayload", agent_id: str, engine: str
    ) -> dict[str, Any]:
        """Store the caller's choice for one of their agents and tell their
        other devices."""
        agent = await self._db.get_agent(agent_id)
        if agent is None or agent["owner_user_id"] != user.sub:
            raise CloudVoiceError(404, "agent_not_found", "This agent does not exist.")
        if engine == "cloud" and not self._offered(user):
            raise disabled()
        if await self._db.set_voice_engine(user.sub, agent_id, engine):
            broadcast_user_event(
                self._routing,
                user.sub,
                EVENT_CHANGED,
                {"engines": await self._db.get_voice_engines(user.sub)},
            )
        return await self.status(user, probe=False)

    async def create(self, user: "UserTokenPayload") -> str:
        if not self._offered(user):
            raise disabled()
        if await self._rate.try_acquire(user.sub) is not None:
            raise busy("Too many dictations in a minute. Try again shortly.")
        day = _today()
        if await self._db.get_cloud_voice_seconds(user.sub, day) >= self._user_daily_seconds:
            raise limit_reached(_resets_at())
        if await self._db.get_cloud_voice_seconds_total(day) >= self._global_daily_seconds:
            logger.warning("cloud_voice.global_limit_reached day=%s", day)
            raise unavailable()
        # Asked before the user has said much, so an outage costs them a few
        # words instead of a whole recording.
        if not await self._healthy(_FRESH_FOR_DICTATION_SECONDS):
            raise unavailable()
        # One open dictation per user: a new one replaces the one a crashed
        # or abandoned client left behind.
        await self._drop(self._by_user.get(user.sub))
        holding_audio = sum(1 for d in self._dictations.values() if d.finished_at is None)
        if holding_audio >= MAX_OPEN_DICTATIONS:
            raise busy()
        now = time.monotonic()
        dictation = _Dictation(
            id=uuid.uuid4().hex, user_id=user.sub, created_at=now, last_activity=now
        )
        self._dictations[dictation.id] = dictation
        self._by_user[user.sub] = dictation.id
        self._ensure_reaper()
        return dictation.id

    async def append(self, user_id: str, dictation_id: str, seq: int, pcm16_b64: str) -> int:
        """Append chunk ``seq``; return the audio duration received so far."""
        dictation = self._get(user_id, dictation_id)
        async with dictation.lock:
            if dictation.task is not None or dictation.text is not None:
                raise CloudVoiceError(
                    409, "dictation_finished", "This dictation is already finished."
                )
            if seq < dictation.next_seq:
                return duration_ms(dictation.received_bytes)
            if seq > dictation.next_seq:
                raise CloudVoiceError(
                    409,
                    "seq_gap",
                    "An earlier audio chunk is missing.",
                    expected_seq=dictation.next_seq,
                )
            pcm = decode_chunk(pcm16_b64)
            if dictation.received_bytes + len(pcm) > MAX_BYTES:
                raise CloudVoiceError(
                    409,
                    "too_long",
                    f"A dictation can last at most {MAX_SECONDS} seconds.",
                    max_seconds=MAX_SECONDS,
                )
            dictation.chunks.append(pcm)
            dictation.received_bytes += len(pcm)
            dictation.peak = max(dictation.peak, peak(pcm))
            dictation.next_seq += 1
            dictation.last_activity = time.monotonic()
            return duration_ms(dictation.received_bytes)

    async def finish(self, user_id: str, dictation_id: str, last_seq: int) -> DictationResult:
        """Transcribe what was uploaded. A failed attempt keeps the audio, so
        calling again tries the speech service again."""
        dictation = self._get(user_id, dictation_id)
        length_ms = duration_ms(dictation.received_bytes)
        if dictation.task is None and dictation.text is None and not self._up:
            # While the service is down, a repeated finish must not be the
            # way around the probe interval.
            dictation.last_activity = time.monotonic()
            if not await self._healthy(_FRESH_FOR_DICTATION_SECONDS):
                dictation.last_activity = time.monotonic()
                raise unavailable()
            # The probe can take seconds: an abort or a new dictation of the
            # same account may have dropped this one meanwhile.
            if self._dictations.get(dictation_id) is not dictation:
                raise dictation_not_found()
        async with dictation.lock:
            dictation.last_activity = time.monotonic()
            if dictation.text is not None:
                return DictationResult(dictation.id, STATE_DONE, dictation.text, length_ms)
            if dictation.task is None:
                if dictation.last_error is not None and time.monotonic() < dictation.retry_at:
                    raise _copy(dictation.last_error)
                if last_seq >= dictation.next_seq:
                    raise CloudVoiceError(
                        409,
                        "seq_gap",
                        "An audio chunk has not arrived yet.",
                        expected_seq=dictation.next_seq,
                    )
                dictation.task = asyncio.ensure_future(self._transcribe(dictation))
                dictation.task.add_done_callback(_consume_exception)
            task = dictation.task
        done, _ = await asyncio.wait({task}, timeout=FINISH_WAIT_SECONDS)
        if not done:
            return DictationResult(dictation.id, STATE_TRANSCRIBING, None, length_ms)
        if task.cancelled():
            raise dictation_not_found()
        error = task.exception()
        if error is None:
            return DictationResult(dictation.id, STATE_DONE, task.result(), length_ms)
        if not isinstance(error, CloudVoiceError):
            logger.error("cloud_voice.transcribe_error type=%s", type(error).__name__)
            error = unavailable()
        if dictation.task is task:
            dictation.task = None
            dictation.failures += 1
            dictation.retry_at = time.monotonic() + min(
                2.0**dictation.failures, _MAX_RETRY_DELAY_SECONDS
            )
            dictation.last_error = error
        dictation.last_activity = time.monotonic()
        raise _copy(error)

    async def abort(self, user_id: str, dictation_id: str) -> None:
        dictation = self._dictations.get(dictation_id)
        if dictation is not None and dictation.user_id == user_id:
            await self._drop(dictation_id)

    async def aclose(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
            self._reaper = None
        for dictation_id in list(self._dictations):
            await self._drop(dictation_id)
        if self._probe is not None:
            self._probe.cancel()
        if self._client is not None:
            await self._client.aclose()

    def _get(self, user_id: str, dictation_id: str) -> _Dictation:
        dictation = self._dictations.get(dictation_id)
        if dictation is None or dictation.user_id != user_id:
            raise dictation_not_found()
        return dictation

    async def _drop(self, dictation_id: str | None) -> None:
        if dictation_id is None:
            return
        dictation = self._dictations.pop(dictation_id, None)
        if dictation is None:
            return
        if self._by_user.get(dictation.user_id) == dictation_id:
            del self._by_user[dictation.user_id]
        dictation.chunks = []
        if dictation.task is not None and not dictation.task.done():
            dictation.task.cancel()

    async def _transcribe(self, dictation: _Dictation) -> str:
        client = self._client
        if client is None:
            raise disabled()
        if dictation.received_bytes < MIN_SPEECH_BYTES or dictation.peak < SILENCE_PEAK:
            return self._finished(dictation, "")
        try:
            await asyncio.wait_for(self._gate.acquire(), _GATE_WAIT_SECONDS)
        except TimeoutError:
            raise busy() from None
        started = time.monotonic()
        day = _today()
        seconds = 0
        charged = False
        try:
            # Joined only now, and kept as the one copy: a recording that
            # waits for the gate, or is tried again, is not held twice.
            pcm = b"".join(dictation.chunks)
            dictation.chunks = [pcm]
            seconds = math.ceil(len(pcm) / (SAMPLE_RATE * 2))
            # Charged before the request and given back only when the speech
            # service refuses it or cannot be reached. A request the client
            # abandons half-way, or one the service does not answer in time,
            # was still worked on and must not be free.
            await self._db.add_cloud_voice_usage(dictation.user_id, day, seconds)
            charged = True
            try:
                async with asyncio.timeout(_TRANSCRIBE_DEADLINE_SECONDS):
                    # Speech runs at about five tokens a second; the cap leaves
                    # room for fast talkers and stops an answer that is not a
                    # transcript.
                    transcript = await client.transcribe(
                        pcm,
                        user=dictation.user_id,
                        max_tokens=64 + 16 * seconds,
                        timeout=_TRANSCRIBE_TIMEOUT_SECONDS,
                    )
            except TimeoutError:
                raise SpeechServiceError("unavailable", "deadline", timed_out=True) from None
        except SpeechServiceError as exc:
            if charged and not exc.timed_out:
                await self._db.refund_cloud_voice_usage(dictation.user_id, day, seconds)
            if exc.kind == "busy":
                raise busy() from None
            if exc.kind == "blocked":
                self._blocked[dictation.user_id] = time.monotonic()
                logger.warning("cloud_voice.user_blocked user=%s", dictation.user_id)
                raise disabled() from None
            if exc.kind == "rejected":
                # The service answered, but not for this recording: no outage
                # for everyone else. The next health check asks it afresh.
                logger.warning(
                    "cloud_voice.request_rejected user=%s reason=%s",
                    dictation.user_id,
                    exc.reason,
                )
                self._last_ok = -math.inf
                raise unavailable() from None
            self._mark_down(exc.reason)
            raise unavailable() from None
        except asyncio.CancelledError:
            if charged:
                logger.info(
                    "cloud_voice.cancelled user=%s audio_ms=%d",
                    dictation.user_id,
                    duration_ms(dictation.received_bytes),
                )
            raise
        finally:
            self._gate.release()
        self._mark_up()
        # About 1 ms per 1,000 characters; the token cap bounds the answer.
        text = tidy_transcript(transcript.text)
        logger.info(
            "cloud_voice.transcribed user=%s audio_ms=%d service_ms=%d chars=%d truncated=%s",
            dictation.user_id,
            duration_ms(dictation.received_bytes),
            int((time.monotonic() - started) * 1000),
            len(text),
            transcript.truncated,
        )
        return self._finished(dictation, text)

    @staticmethod
    def _finished(dictation: _Dictation, text: str) -> str:
        dictation.text = text
        dictation.chunks = []
        dictation.finished_at = time.monotonic()
        return text

    async def _healthy(self, fresh_for: float, *, wait: float | None = None) -> bool:
        """Whether the speech service answers, probing it when the last
        answer is older than ``fresh_for``. One probe runs at a time, and a
        service that is down is asked at most every 5 s. With ``wait``, a
        probe slower than that is not waited for: the last known state is the
        answer, and the probe finishes on its own."""
        now = time.monotonic()
        if self._up and now - self._last_ok <= fresh_for:
            return True
        if not self._up and now - self._last_probe < _PROBE_INTERVAL_SECONDS:
            return False
        if self._probe is None or self._probe.done():
            self._probe = asyncio.ensure_future(self._run_probe())
            self._probe.add_done_callback(_consume_exception)
        try:
            return await asyncio.wait_for(asyncio.shield(self._probe), wait)
        except TimeoutError:
            return self._up

    async def _run_probe(self) -> bool:
        client = self._client
        if client is None:
            return False
        self._last_probe = time.monotonic()
        try:
            async with asyncio.timeout(_PROBE_DEADLINE_SECONDS):
                await client.transcribe(
                    _PROBE_AUDIO, user=_PROBE_USER, max_tokens=1, timeout=_PROBE_TIMEOUT_SECONDS
                )
        except TimeoutError:
            self._mark_down("probe_deadline")
            return False
        except SpeechServiceError as exc:
            # Rate limited is still an answer: the service is there.
            if exc.kind != "busy":
                self._mark_down(exc.reason)
                return False
        self._mark_up()
        return True

    def _mark_up(self) -> None:
        if not self._up:
            logger.info("cloud_voice.up")
        self._up = True
        self._last_ok = time.monotonic()

    def _mark_down(self, reason: str) -> None:
        if self._up:
            logger.warning("cloud_voice.down reason=%s", reason)
            # Only on the way down: repeated failures of one dictation must
            # not keep pushing the next probe away.
            self._last_probe = time.monotonic()
        self._up = False

    def _ensure_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.ensure_future(self._reap_loop())
            self._reaper.add_done_callback(_consume_exception)

    async def _reap_loop(self) -> None:
        while self._dictations:
            await asyncio.sleep(_REAP_INTERVAL_SECONDS)
            now = time.monotonic()
            for dictation in list(self._dictations.values()):
                if dictation.finished_at is not None:
                    expired = now - dictation.finished_at > RESULT_TTL_SECONDS
                elif dictation.task is not None and not dictation.task.done():
                    expired = False
                else:
                    expired = (
                        now - dictation.last_activity > IDLE_EXPIRE_SECONDS
                        or now - dictation.created_at > MAX_LIFETIME_SECONDS
                    )
                if expired:
                    await self._drop(dictation.id)
