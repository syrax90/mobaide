"""Voice input facade: install state machine, ``voice.changed`` and dictations.

Nothing is downloaded or started until the user enables voice. The install
runs as one background task:

``not_installed`` → ``queued`` (waiting for the cross-process lock) →
``downloading`` → ``verifying`` (self-test in the helper) → ``ready``, or
``failed`` with a coded error until the user retries. ``unsupported`` means
no runtime exists for this interpreter/platform.

Construction does no I/O and starts nothing, so the service works without the
app lifespan (tests, OpenAPI dumps); every public entry point keeps voice
failures from reaching the rest of the agent.
"""
from __future__ import annotations

import asyncio
import logging
import time
import wave
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from mobaide.agent._env_writer import atomic_write
from mobaide.agent.install_paths import state_prefix

from . import catalog
from .catalog import (
    DEFAULT_MODEL_ID,
    FAKE_MODEL_ID,
    MODELS,
    RUNTIME_DISK_BYTES,
    RUNTIME_VERSION,
    ModelSpec,
    RuntimeTarget,
    resolve_runtime,
)
from .dictation import (
    MAX_CHUNK_BYTES,
    MAX_SECONDS,
    SAMPLE_RATE,
    DictationManager,
)
from .download import DownloadError, FakeFetcher, HttpFetcher
from .errors import VoiceError, not_ready
from .helper import HelperError, LoadSpec, SttHelper, consume_exception
from .host import assess, disk_shortfall, free_disk_bytes, runtime_reason
from .locking import FileLock
from .models import (
    VoiceDictationAudioRequest,
    VoiceDictationAudioResponse,
    VoiceDictationFinishRequest,
    VoiceDictationResponse,
    VoiceDictationResultResponse,
    VoiceErrorInfo,
    VoiceHostInfo,
    VoiceHostReason,
    VoiceModelInfo,
    VoiceProgress,
    VoiceStatusResponse,
)
from .runtime import install_runtime
from .store import (
    PHASE_FAILED,
    PHASE_INSTALLING,
    PHASE_READY,
    VoicePaths,
    bytes_on_disk,
    gc_models,
    gc_runtimes,
    load_settings,
    model_ready,
    remove_tree,
    run_to_completion,
    runtime_ready,
    save_settings,
    write_model_marker,
)

logger = logging.getLogger(__name__)

STATE_UNSUPPORTED = "unsupported"
STATE_NOT_INSTALLED = "not_installed"
STATE_QUEUED = "queued"
STATE_DOWNLOADING = "downloading"
STATE_VERIFYING = "verifying"
STATE_READY = "ready"
STATE_FAILED = "failed"

EVENT_TYPE = "voice.changed"
MIN_PUBLISH_INTERVAL = 0.5
LOCK_POLL_SECONDS = 5.0
FOREIGN_INSTALL_POLL_SECONDS = 2.0
_SHUTDOWN_WAIT_SECONDS = 5.0

PublishFn = Callable[[str, dict[str, Any]], None]


def _read_wav(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as clip:
        if clip.getsampwidth() != 2 or clip.getnchannels() != 1:
            raise ValueError("self-test clip must be mono PCM16")
        return clip.readframes(clip.getnframes()), clip.getframerate()


class VoiceService:
    def __init__(
        self,
        *,
        voice_dir: str | Path | None = None,
        test_mode: bool = False,
        fake: bool = False,
        publish: PublishFn | None = None,
        fetcher: Any | None = None,
        helper: SttHelper | None = None,
    ) -> None:
        self._voice_dir = Path(voice_dir) if voice_dir else None
        self._test_mode = test_mode
        self._fake = fake
        self._models = {FAKE_MODEL_ID: catalog.FAKE_MODEL} if fake else dict(MODELS)
        self._default_model_id = FAKE_MODEL_ID if fake else DEFAULT_MODEL_ID
        self._fetcher = fetcher
        self._helper = helper if helper is not None else SttHelper()
        self._dictations = DictationManager(self._helper, self._ready_load_spec)
        self._publish_fn = publish
        self._paths: VoicePaths | None = None
        self._task: asyncio.Task[None] | None = None
        self._task_model: str | None = None
        self._state: str | None = None
        self._progress: dict[str, int] = {}
        self._op_lock: asyncio.Lock | None = None
        self._publish_timer: asyncio.TimerHandle | None = None
        self._publish_pending = False
        self._lock_watcher: asyncio.Task[None] | None = None

    @property
    def paths(self) -> VoicePaths:
        if self._paths is None:
            if self._voice_dir is not None:
                root = self._voice_dir
            else:
                # A fake install gets its own root so it can never collect or
                # remove a real model sharing the same prefix.
                name = "voice-fake" if self._fake else "voice"
                root = state_prefix(test_mode=self._test_mode) / name
            self._paths = VoicePaths(root)
        return self._paths

    def busy(self) -> bool:
        """True while a dictation or a transcription is in progress (the
        auto-updater defers its restart)."""
        return self._dictations.busy()

    async def start(self) -> None:
        """Resume an install the last run did not finish, or repair a ready
        one whose files went missing (a Python upgrade needs a new runtime).
        An install that ended ``failed`` waits for the user to retry."""
        try:
            paths = self.paths
            if not paths.settings_file.exists():
                return
            settings = load_settings(paths)
            if not settings.enabled or settings.phase not in (PHASE_INSTALLING, PHASE_READY):
                return
            spec = self._spec_for(settings.model_id)
            target, reason = self._runtime(spec)
            if reason is not None or self._verified(paths, settings, spec, target):
                return
            logger.info("voice.install_resume model=%s phase=%s", spec.id, settings.phase)
            if settings.phase != PHASE_INSTALLING:
                settings.phase = PHASE_INSTALLING
                save_settings(paths, settings)
            self._start_provisioning(spec)
        except Exception:
            logger.exception("voice.start_failed")

    async def shutdown(self) -> None:
        """Stop everything; a running download keeps its partial files and
        resumes on the next start."""
        try:
            if self._lock_watcher is not None:
                self._lock_watcher.cancel()
                self._lock_watcher = None
            task = self._task
            if task is not None and not task.done():
                task.cancel()
                await asyncio.wait({task}, timeout=_SHUTDOWN_WAIT_SECONDS)
            await self._dictations.abort_all()
            await self._helper.stop()
        except Exception:
            logger.exception("voice.shutdown_failed")
        finally:
            if self._publish_timer is not None:
                self._publish_timer.cancel()
                self._publish_timer = None

    def status(self) -> VoiceStatusResponse:
        paths = self.paths
        settings = load_settings(paths)
        spec = self._spec_for(settings.model_id)
        target, reason = self._runtime(spec)
        installed = reason is None and self._verified(paths, settings, spec, target)
        state = self._derive_state(settings, installed, reason)
        repairing = (
            not installed
            and settings.enabled
            and target is not None
            and self._repairable(paths, settings, spec, target)
        )

        total = spec.download_bytes + (target.download_bytes if target else 0)
        done = self._done_bytes(paths, spec, target) if not installed else total
        remaining = 0 if installed else self._bytes_still_needed(paths, spec, target, done)
        host = assess(paths.root, remaining_bytes=remaining, runtime_reason_code=reason)
        progress = None
        if state in (STATE_QUEUED, STATE_DOWNLOADING, STATE_VERIFYING):
            progress = VoiceProgress(done_bytes=min(done, total), total_bytes=total)
        error = None
        if state == STATE_FAILED:
            error = VoiceErrorInfo(
                code=settings.error_code or "interrupted",
                message=settings.error_message
                or "The install stopped before it finished; retry to resume it.",
            )
        return VoiceStatusResponse(
            state=state,
            enabled=settings.enabled,
            repairing=repairing,
            model=VoiceModelInfo(
                id=spec.id,
                name=spec.name,
                languages=list(spec.languages),
                download_bytes=total,
                disk_bytes=spec.download_bytes + (RUNTIME_DISK_BYTES if spec.needs_runtime else 0),
                ram_bytes=spec.ram_bytes,
                notice=spec.notice,
            ),
            progress=progress,
            host=VoiceHostInfo(
                ram_total_bytes=host.ram_total_bytes,
                disk_free_bytes=host.disk_free_bytes,
                cpu_count=host.cpu_count,
                recommendation=host.recommendation,
                reasons=[VoiceHostReason(code=r.code, message=r.message) for r in host.reasons],
            ),
            speed_factor=settings.speed_factor if state == STATE_READY else None,
            error=error,
        )

    async def install(self, model_id: str | None = None) -> VoiceStatusResponse:
        """Enable voice and start (or retry) the install; answers at once."""
        return await self._serialized(lambda: self._install(model_id))

    async def cancel_install(self) -> VoiceStatusResponse:
        """Stop an install and delete what it fetched; a ready install is
        left alone."""
        return await self._serialized(self._cancel_install)

    async def remove(self) -> VoiceStatusResponse:
        """Turn voice off and delete the model and runtime."""
        return await self._serialized(self._remove)

    async def wait_for_install(self) -> VoiceStatusResponse:
        task = self._task
        if task is not None:
            await asyncio.shield(task)
        return self.status()

    def mark_interrupted(self) -> None:
        """Record a foreground (CLI) install the user interrupted as failed,
        so no other process silently picks it up; a retry resumes it."""
        settings = load_settings(self.paths)
        if settings.enabled and settings.phase == PHASE_INSTALLING:
            self._save_failure(
                "interrupted", "The install was interrupted; retry to resume it."
            )

    def selftest_clip(self) -> tuple[bytes, int]:
        """PCM16 and sample rate of the installed model's self-test clip."""
        spec = self._spec_for(load_settings(self.paths).model_id)
        return _read_wav(self.paths.model_dir(spec.id) / spec.selftest_file)

    async def create_dictation(self) -> VoiceDictationResponse:
        dictation_id = await self._dictations.create()
        return VoiceDictationResponse(
            dictation_id=dictation_id,
            sample_rate=SAMPLE_RATE,
            max_seconds=MAX_SECONDS,
            max_chunk_bytes=MAX_CHUNK_BYTES,
        )

    async def append_audio(
        self, dictation_id: str, request: VoiceDictationAudioRequest
    ) -> VoiceDictationAudioResponse:
        duration_ms = await self._dictations.append(dictation_id, request.seq, request.pcm16_b64)
        return VoiceDictationAudioResponse(seq=request.seq, duration_ms=duration_ms)

    async def finish_dictation(
        self, dictation_id: str, request: VoiceDictationFinishRequest
    ) -> VoiceDictationResultResponse:
        result = await self._dictations.finish(dictation_id, request.last_seq)
        return VoiceDictationResultResponse(
            dictation_id=dictation_id,
            state=result.state,
            text=result.text,
            duration_ms=result.duration_ms,
        )

    async def delete_dictation(self, dictation_id: str) -> None:
        await self._dictations.abort(dictation_id)

    def _spec_for(self, model_id: str | None) -> ModelSpec:
        return self._models.get(model_id or "") or self._models[self._default_model_id]

    @staticmethod
    def _runtime(spec: ModelSpec) -> tuple[RuntimeTarget | None, str | None]:
        if not spec.needs_runtime:
            return None, None
        return resolve_runtime()

    @staticmethod
    def _installed(paths: VoicePaths, spec: ModelSpec, target: RuntimeTarget | None) -> bool:
        if not model_ready(paths, spec):
            return False
        return target is None or runtime_ready(paths, target)

    @classmethod
    def _verified(
        cls, paths: VoicePaths, settings: Any, spec: ModelSpec, target: RuntimeTarget | None
    ) -> bool:
        """Installed, and the self-test passed on these files: a runtime
        repair writes the runtime marker before its self-test runs."""
        return settings.phase == PHASE_READY and cls._installed(paths, spec, target)

    @staticmethod
    def _repairable(
        paths: VoicePaths, settings: Any, spec: ModelSpec, target: RuntimeTarget
    ) -> bool:
        """The verified model is in place and only the runtime is missing or
        not yet verified with it."""
        if not model_ready(paths, spec):
            return False
        return not runtime_ready(paths, target) or settings.phase != PHASE_READY

    def _derive_state(self, settings: Any, installed: bool, reason: str | None) -> str:
        if reason is not None:
            return STATE_UNSUPPORTED
        if self._task is not None and not self._task.done():
            return self._state or STATE_QUEUED
        if settings.enabled and installed:
            return STATE_READY
        if not settings.enabled:
            return STATE_NOT_INSTALLED
        if settings.phase == PHASE_FAILED:
            return STATE_FAILED
        # Enabled but not installed with no local task: queued behind the
        # process that holds the install lock, or abandoned by one that died
        # mid-install (offered as a retry).
        if FileLock(self.paths.lock_file).is_held_elsewhere():
            self._watch_foreign_install()
            return STATE_QUEUED
        return STATE_FAILED

    def _watch_foreign_install(self) -> None:
        """Another process (``mobaide-agent voice install``, a second agent on
        the same prefix) holds the install lock: publish its progress from
        disk while it runs and the outcome once the lock frees, so clients
        see it without polling. At most one watcher runs."""
        if self._publish_fn is None:
            return
        if self._lock_watcher is not None and not self._lock_watcher.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._lock_watcher = loop.create_task(self._watch_lock())
        self._lock_watcher.add_done_callback(consume_exception)

    async def _watch_lock(self) -> None:
        lock = FileLock(self.paths.lock_file)
        while True:
            await asyncio.sleep(FOREIGN_INSTALL_POLL_SECONDS)
            if self._task is not None and not self._task.done():
                # This agent's own install now publishes for itself.
                return
            if not lock.is_held_elsewhere():
                break
            self._publish()
        self._publish(force=True)

    def _done_bytes(self, paths: VoicePaths, spec: ModelSpec, target: RuntimeTarget | None) -> int:
        if self._task is not None and not self._task.done() and self._progress:
            return sum(self._progress.values())
        return sum(self._bytes_present(paths, spec, target).values())

    @staticmethod
    def _bytes_still_needed(
        paths: VoicePaths, spec: ModelSpec, target: RuntimeTarget | None, done: int
    ) -> int:
        """Disk the rest of the install writes: what is left to download plus,
        while the runtime is not unpacked yet, its unpacked copy."""
        total = spec.download_bytes + (target.download_bytes if target else 0)
        unpack = (
            RUNTIME_DISK_BYTES
            if target is not None and not runtime_ready(paths, target)
            else 0
        )
        return max(total - done, 0) + unpack

    @staticmethod
    def _bytes_present(
        paths: VoicePaths, spec: ModelSpec, target: RuntimeTarget | None
    ) -> dict[str, int]:
        """Bytes already on disk per catalog file (complete or ``.part``)."""
        present = {
            f.name: bytes_on_disk(paths.model_dir(spec.id), [f.name]) for f in spec.files
        }
        if target is not None:
            ready = runtime_ready(paths, target)
            for wheel in target.wheels:
                present[wheel.name] = (
                    wheel.size if ready else bytes_on_disk(paths.wheel_downloads, [wheel.name])
                )
        return present

    def _ready_load_spec(self) -> LoadSpec:
        paths = self.paths
        settings = load_settings(paths)
        spec = self._spec_for(settings.model_id)
        target, reason = self._runtime(spec)
        if reason is not None:
            raise VoiceError(409, "voice_unsupported", runtime_reason(reason).message)
        if self._task is not None and not self._task.done():
            raise not_ready("Voice input is still being installed.")
        if self._op_lock is not None and self._op_lock.locked():
            raise not_ready("Voice input is being changed; try again in a moment.")
        if not (settings.enabled and self._verified(paths, settings, spec, target)):
            raise not_ready()
        return self._load_spec(paths, spec, target)

    @staticmethod
    def _load_spec(paths: VoicePaths, spec: ModelSpec, target: RuntimeTarget | None) -> LoadSpec:
        return LoadSpec(
            key=f"{spec.id}:{spec.fingerprint[:12]}:{target.key if target else '-'}",
            engine=spec.engine,
            model_dir=paths.model_dir(spec.id),
            runtime_dir=paths.runtime_dir(target.key) if target else None,
            files=dict(spec.engine_files),
        )

    async def _serialized(
        self, operation: Callable[[], Awaitable[VoiceStatusResponse]]
    ) -> VoiceStatusResponse:
        # Mutations run as their own task: a caller that goes away (a relay
        # request cancelled on shutdown) never leaves half a remove behind.
        if self._op_lock is None:
            self._op_lock = asyncio.Lock()
        lock = self._op_lock

        async def run() -> VoiceStatusResponse:
            async with lock:
                return await operation()

        task = asyncio.ensure_future(run())
        task.add_done_callback(consume_exception)
        return await asyncio.shield(task)

    async def _install(self, model_id: str | None) -> VoiceStatusResponse:
        spec = self._models.get(model_id or self._default_model_id)
        if spec is None:
            raise VoiceError(422, "unknown_model", f"Unknown voice model {model_id!r}.")
        target, reason = self._runtime(spec)
        if reason is not None:
            raise VoiceError(409, "voice_unsupported", runtime_reason(reason).message)
        if self._task is not None and not self._task.done():
            if self._task_model != spec.id:
                raise VoiceError(409, "install_in_progress", "Another voice model is being installed.")
            return self.status()
        paths = self.paths
        settings = load_settings(paths)
        if (
            settings.enabled
            and self._spec_for(settings.model_id).id == spec.id
            and self._verified(paths, settings, spec, target)
        ):
            # Already ready: re-running the self-test would stop the helper
            # under another client's dictation.
            return self.status()
        settings.enabled = True
        settings.model_id = spec.id
        settings.phase = PHASE_INSTALLING
        settings.error_code = None
        settings.error_message = None
        self._save(settings)
        logger.info("voice.install_requested model=%s", spec.id)
        self._start_provisioning(spec)
        return self.status()

    async def _cancel_install(self) -> VoiceStatusResponse:
        await self._stop_task()
        paths = self.paths
        settings = load_settings(paths)
        spec = self._spec_for(settings.model_id)
        target, _ = self._runtime(spec)
        if settings.enabled and self._verified(paths, settings, spec, target):
            self._publish(force=True)
            return self.status()
        if (
            settings.enabled
            and target is not None
            and self._repairable(paths, settings, spec, target)
        ):
            # Stopping a runtime repair must never cost the user the verified
            # model: only the runtime's partial files go, and a retry fetches
            # just the runtime again.
            try:
                await self._discard_runtime_partials(paths)
                self._save_failure(
                    "interrupted", "Voice needs a small update for this agent; retry to finish."
                )
            finally:
                self._publish(force=True)
            logger.info("voice.repair_cancelled")
            return self.status()
        try:
            if paths.root.exists():
                await self._wipe(paths)
        finally:
            # The wipe turns voice off before deleting; even a failed delete
            # has changed what every client should show.
            self._publish(force=True)
        logger.info("voice.install_cancelled")
        return self.status()

    async def _remove(self) -> VoiceStatusResponse:
        await self._stop_task()
        await self._dictations.abort_all()
        await self._helper.stop()
        paths = self.paths
        try:
            if paths.root.exists():
                await self._wipe(paths)
        finally:
            self._publish(force=True)
        logger.info("voice.removed")
        return self.status()

    async def _stop_task(self) -> None:
        task = self._task
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.wait({task})

    @staticmethod
    def _acquire_lock(paths: VoicePaths) -> FileLock:
        lock = FileLock(paths.lock_file)
        try:
            acquired = lock.try_acquire()
        except OSError as exc:
            raise VoiceError(
                500, "storage_error", f"Cannot write voice files: {exc.strerror or exc}"
            ) from exc
        if not acquired:
            raise VoiceError(
                409, "install_in_progress",
                "Another Mobaide process is installing voice input on this computer.",
            )
        return lock

    async def _discard_runtime_partials(self, paths: VoicePaths) -> None:
        lock = self._acquire_lock(paths)

        def discard() -> None:
            try:
                entries = list(paths.runtime_root.iterdir())
            except OSError:
                entries = []
            for entry in entries:
                if entry.is_dir() and entry.name.startswith(".staging-"):
                    remove_tree(entry)
            remove_tree(paths.wheel_downloads)

        try:
            await asyncio.to_thread(discard)
        finally:
            lock.release()

    async def _wipe(self, paths: VoicePaths) -> None:
        lock = self._acquire_lock(paths)
        try:
            settings = load_settings(paths)
            settings.enabled = False
            settings.phase = None
            settings.speed_factor = None
            settings.load_ms = None
            settings.error_code = None
            settings.error_message = None
            self._save(settings)
            removed = await asyncio.to_thread(
                lambda: remove_tree(paths.models_dir) & remove_tree(paths.runtime_root)
            )
        finally:
            lock.release()
        if not removed:
            raise VoiceError(
                409, "files_in_use",
                "Some voice files are still in use; try again in a minute.",
            )

    def _save(self, settings: Any) -> None:
        try:
            save_settings(self.paths, settings)
        except OSError as exc:
            raise VoiceError(
                500, "storage_error", f"Cannot write voice settings: {exc.strerror or exc}"
            ) from exc

    def _save_failure(self, code: str, message: str) -> None:
        try:
            settings = load_settings(self.paths)
            settings.phase = PHASE_FAILED
            settings.error_code = code
            settings.error_message = message[:500]
            save_settings(self.paths, settings)
        except OSError as exc:
            logger.warning("voice.settings_write_failed: %s", exc)

    def _start_provisioning(self, spec: ModelSpec) -> None:
        self._state = STATE_QUEUED
        self._progress = {}
        self._task_model = spec.id
        self._task = asyncio.ensure_future(self._provision(spec))
        self._task.add_done_callback(self._on_provision_done)
        self._publish(force=True)

    def _on_provision_done(self, task: asyncio.Task[None]) -> None:
        consume_exception(task)
        if self._task is task:
            self._state = None
            # Whoever cancelled it (cancel, remove) publishes the outcome.
            if not task.cancelled():
                self._publish(force=True)

    async def _provision(self, spec: ModelSpec) -> None:
        paths = self.paths
        lock = FileLock(paths.lock_file)
        started = time.monotonic()
        try:
            while not lock.try_acquire():
                self._set_state(STATE_QUEUED)
                await asyncio.sleep(LOCK_POLL_SECONDS)
            try:
                # Another process may have removed voice, or recorded its own
                # install as interrupted, while this one waited for the lock.
                settings = load_settings(paths)
                if (
                    not settings.enabled
                    or settings.phase == PHASE_FAILED
                    or self._spec_for(settings.model_id).id != spec.id
                ):
                    logger.info("voice.install_superseded model=%s", spec.id)
                    return
                await self._provision_locked(spec, paths)
            finally:
                lock.release()
        except asyncio.CancelledError:
            logger.info("voice.install_stopped model=%s", spec.id)
            raise
        except (VoiceError, DownloadError) as exc:
            self._fail(exc.code, exc.message)
        except HelperError as exc:
            self._fail("selftest_failed", exc.message or exc.code)
        except OSError as exc:
            self._fail("storage_error", f"Cannot write voice files: {exc.strerror or exc}")
        except Exception as exc:
            logger.exception("voice.install_crashed")
            self._fail("internal_error", f"{type(exc).__name__}: {exc}")
        else:
            logger.info(
                "voice.install_ready model=%s seconds=%.0f", spec.id, time.monotonic() - started
            )

    async def _provision_locked(self, spec: ModelSpec, paths: VoicePaths) -> None:
        target, _ = self._runtime(spec)
        need_runtime = target is not None and not runtime_ready(paths, target)
        need_model = not model_ready(paths, spec)
        if need_runtime or need_model:
            present = sum(self._bytes_present(paths, spec, target).values())
            shortfall = disk_shortfall(
                self._bytes_still_needed(paths, spec, target, present),
                free_disk_bytes(paths.root),
            )
            if shortfall is not None:
                raise DownloadError("disk_full", shortfall.message)
            self._progress = self._bytes_present(paths, spec, target)
            self._set_state(STATE_DOWNLOADING)
            fetcher = self._get_fetcher()
            if target is not None:
                await install_runtime(paths, target, fetcher, self._on_progress)
            for file in spec.files:
                await fetcher.fetch(file, paths.model_dir(spec.id), self._on_progress)
        self._set_state(STATE_VERIFYING)
        speed_factor, load_ms = await self._selftest(spec, paths, target)
        await run_to_completion(lambda: self._finalize(spec, paths, speed_factor, load_ms))
        logger.info(
            "voice.selftest_passed model=%s speed_factor=%.1f load_ms=%s",
            spec.id, speed_factor, load_ms,
        )

    async def _selftest(
        self, spec: ModelSpec, paths: VoicePaths, target: RuntimeTarget | None
    ) -> tuple[float, int | None]:
        pcm, rate = await asyncio.to_thread(
            _read_wav, paths.model_dir(spec.id) / spec.selftest_file
        )
        try:
            result = await self._helper.transcribe(self._load_spec(paths, spec, target), pcm, rate)
        finally:
            await self._helper.stop()
        if not result.text.strip():
            raise HelperError("selftest_failed", "The model loaded but recognised no speech.")
        seconds = len(pcm) / 2 / rate
        return round(seconds / max(result.decode_ms / 1000, 0.001), 1), result.load_ms

    def _finalize(
        self, spec: ModelSpec, paths: VoicePaths, speed_factor: float, load_ms: int | None
    ) -> None:
        # One uninterrupted step: a cancel landing here can never leave the
        # marker written without the matching ``ready`` settings.
        atomic_write(paths.model_dir(spec.id) / "NOTICE", spec.notice)
        write_model_marker(paths, spec)
        settings = load_settings(paths)
        settings.model_id = spec.id
        settings.phase = PHASE_READY
        settings.speed_factor = speed_factor
        settings.load_ms = load_ms
        settings.error_code = None
        settings.error_message = None
        save_settings(paths, settings)
        # Every model this agent knows stays, so a fake run sharing a root
        # with a real install never collects the real model.
        gc_models(paths, keep=set(MODELS) | {FAKE_MODEL_ID})
        gc_runtimes(paths, f"sherpa-onnx-{RUNTIME_VERSION}-")

    def _get_fetcher(self) -> Any:
        if self._fetcher is None:
            self._fetcher = FakeFetcher(catalog.FAKE_CONTENT) if self._fake else HttpFetcher()
        return self._fetcher

    def _fail(self, code: str, message: str) -> None:
        logger.warning("voice.install_failed code=%s", code)
        self._save_failure(code, message)

    def _on_progress(self, name: str, done: int) -> None:
        self._progress[name] = done
        self._publish()

    def _set_state(self, state: str) -> None:
        if self._state != state:
            self._state = state
            self._publish(force=True)

    def _publish(self, *, force: bool = False) -> None:
        if self._publish_fn is None:
            return
        if force:
            if self._publish_timer is not None:
                self._publish_timer.cancel()
                self._publish_timer = None
            self._publish_pending = False
            self._emit()
            return
        if self._publish_timer is not None:
            self._publish_pending = True
            return
        self._emit()
        self._arm_publish_timer()

    def _arm_publish_timer(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._publish_timer = loop.call_later(MIN_PUBLISH_INTERVAL, self._publish_tick)

    def _publish_tick(self) -> None:
        self._publish_timer = None
        if self._publish_pending:
            self._publish_pending = False
            self._emit()
            self._arm_publish_timer()

    def _emit(self) -> None:
        if self._publish_fn is None:
            return
        try:
            self._publish_fn(EVENT_TYPE, self.status().model_dump(mode="json"))
        except Exception:
            logger.exception("voice.publish_failed")
