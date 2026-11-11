"""``VoiceService``: the install state machine (queued → downloading →
verifying → ready | failed), cancel and remove, the restart rules of
``start()`` (resume an interrupted install, repair a ready one, never retry a
failed one), the cross-process lock, the throttled ``voice.changed``
publisher, a runtime repair (cancelling it keeps the model), the watcher that
publishes an install running in another process, and dictations and
transcript cleanups through the facade.

Fake mode throughout (the generated model, no runtime, the fake engine, a
fake Claude Code run); nothing touches the network or the real voice root."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from mobaide.core.git_exec import CmdResult, ProcessHandle
from mobaide.voice import host as host_module
from mobaide.voice import service as service_module
from mobaide.voice.catalog import (
    DEFAULT_MODEL_ID,
    FAKE_CONTENT,
    FAKE_MODEL,
    RUNTIME_DISK_BYTES,
    CatalogFile,
    RuntimeTarget,
)
from mobaide.voice.cleanup import ClaudeCleanup, CodexCleanup, TranscriptCleaner
from mobaide.voice.download import DownloadError, FakeFetcher
from mobaide.voice.errors import VoiceError
from mobaide.voice.helper import TranscribeResult
from mobaide.voice.host import DISK_MARGIN_BYTES
from mobaide.voice.locking import FileLock
from mobaide.voice.models import (
    VoiceCleanupResponse,
    VoiceDictationAudioRequest,
    VoiceDictationFinishRequest,
    VoiceStatusResponse,
)
from mobaide.voice.service import VoiceService
from mobaide.voice.store import (
    MARKER,
    VoicePaths,
    VoiceSettings,
    load_settings,
    model_ready,
    save_settings,
    write_model_marker,
    write_runtime_marker,
)

INSTALL_TIMEOUT = 20.0


class _Fetcher:
    """Serves the fake model at once; with a ``gate`` it first leaves a
    partial file, reports ``burst`` progress steps and waits."""

    def __init__(self, *, gate: asyncio.Event | None = None, error: Exception | None = None, burst: int = 0):
        self.inner = FakeFetcher(FAKE_CONTENT, delay=0)
        self.gate = gate
        self.error = error
        self.burst = burst
        self.calls: list[str] = []

    async def fetch(self, file, directory: Path, on_progress):
        self.calls.append(file.name)
        directory.mkdir(parents=True, exist_ok=True)
        if self.gate is not None:
            (directory / (file.name + ".part")).write_bytes(b"partial")
            for done in range(1, self.burst + 1):
                on_progress(file.name, done)
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return await self.inner.fetch(file, directory, on_progress)


class _Helper:
    """Stands in for ``SttHelper`` where no real worker is needed; with a
    ``stop_gate`` a ``stop()`` waits on it."""

    def __init__(self, text: str = "fake words") -> None:
        self.text = text
        self.busy = False
        self.stops = 0
        self.stop_gate: asyncio.Event | None = None

    async def preload(self, spec) -> int:
        return 1

    async def transcribe(self, spec, pcm16: bytes, sample_rate: int) -> TranscribeResult:
        return TranscribeResult(text=self.text, decode_ms=100, load_ms=7, peak_rss_bytes=None)

    async def stop(self) -> None:
        self.stops += 1
        if self.stop_gate is not None:
            await self.stop_gate.wait()


class _Events:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def __call__(self, event_type: str, payload: dict) -> None:
        assert event_type == "voice.changed"
        VoiceStatusResponse.model_validate(payload)
        self.payloads.append(payload)

    def states(self) -> list[str]:
        out: list[str] = []
        for payload in self.payloads:
            if not out or out[-1] != payload["state"]:
                out.append(payload["state"])
        return out

    def with_state(self, state: str) -> list[dict]:
        return [p for p in self.payloads if p["state"] == state]


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)


async def _installed(service: VoiceService) -> VoiceStatusResponse:
    return await asyncio.wait_for(service.wait_for_install(), INSTALL_TIMEOUT)


@pytest.fixture
def voice_dir(tmp_path) -> Path:
    return tmp_path / "voice"


@pytest.fixture
async def make_service(voice_dir):
    services: list[VoiceService] = []

    def make(*, fetcher=None, helper=None, publish=None, fake=True, cleaner=None) -> VoiceService:
        service = VoiceService(
            voice_dir=voice_dir,
            fake=fake,
            publish=publish,
            fetcher=fetcher if fetcher is not None else _Fetcher(),
            helper=helper if helper is not None else _Helper(),
            cleaner=cleaner,
        )
        services.append(service)
        return service

    yield make
    for service in services:
        await service.shutdown()


def _write_settings(voice_dir: Path, **fields) -> None:
    voice_dir.mkdir(parents=True, exist_ok=True)
    save_settings(VoicePaths(voice_dir), VoiceSettings(**fields))


class TestStatus:
    def test_fresh_status_is_not_installed_and_creates_nothing(self, voice_dir):
        status = VoiceService(voice_dir=voice_dir, fake=True).status()

        assert status.state == "not_installed"
        assert status.enabled is False
        assert status.model.id == FAKE_MODEL.id
        assert status.model.download_bytes == FAKE_MODEL.download_bytes
        assert (status.progress, status.speed_factor, status.error) == (None, None, None)
        assert not voice_dir.exists()

    def test_corrupt_settings_read_as_not_installed(self, voice_dir):
        voice_dir.mkdir()
        (voice_dir / "settings.json").write_text("{garbage", encoding="utf-8")

        assert VoiceService(voice_dir=voice_dir, fake=True).status().state == "not_installed"

    @pytest.mark.parametrize("phase", ["installing", "ready"])
    def test_install_abandoned_by_a_dead_process_reads_as_interrupted(self, voice_dir, phase):
        _write_settings(voice_dir, enabled=True, model_id=FAKE_MODEL.id, phase=phase)

        status = VoiceService(voice_dir=voice_dir, fake=True).status()

        assert status.state == "failed"
        assert status.error is not None and status.error.code == "interrupted"
        assert status.progress is None

    def test_install_running_in_another_process_reads_as_queued(self, voice_dir):
        _write_settings(voice_dir, enabled=True, model_id=FAKE_MODEL.id, phase="installing")
        other = FileLock(VoicePaths(voice_dir).lock_file)
        assert other.try_acquire()
        try:
            status = VoiceService(voice_dir=voice_dir, fake=True).status()
        finally:
            other.release()

        assert status.state == "queued"
        assert status.error is None
        assert status.progress is not None


class TestInstall:
    async def test_install_with_the_real_worker_reaches_ready_and_dictates(self, voice_dir):
        service = VoiceService(voice_dir=voice_dir, fake=True, fetcher=_Fetcher())
        try:
            started = await service.install()
            assert started.enabled is True
            assert started.state in ("queued", "downloading", "verifying")

            ready = await _installed(service)

            assert ready.state == "ready"
            assert ready.speed_factor is not None and ready.speed_factor > 0
            assert ready.progress is None
            assert load_settings(service.paths).phase == "ready"
            notice = service.paths.model_dir(FAKE_MODEL.id) / "NOTICE"
            assert notice.read_text(encoding="utf-8") == FAKE_MODEL.notice

            created = await service.create_dictation()
            pcm = b"\x10\x27" * 16000  # 1 s of a constant, clearly voiced level
            await service.append_audio(
                created.dictation_id,
                VoiceDictationAudioRequest(seq=0, pcm16_b64=base64.b64encode(pcm).decode()),
            )
            result = await service.finish_dictation(
                created.dictation_id, VoiceDictationFinishRequest(last_seq=0)
            )

            assert (result.state, result.text, result.duration_ms) == (
                "done", "fake transcript 1000 ms", 1000,
            )
        finally:
            await service.shutdown()

    async def test_every_state_change_is_published(self, make_service):
        events = _Events()
        service = make_service(publish=events)

        await service.install()
        await _installed(service)
        await _wait_for(lambda: events.payloads and events.payloads[-1]["state"] == "ready")

        assert events.states() == ["queued", "downloading", "verifying", "ready"]

    async def test_unknown_model_is_refused_without_writing(self, make_service, voice_dir):
        service = make_service()

        with pytest.raises(VoiceError) as excinfo:
            await service.install("no-such-model")

        assert (excinfo.value.status, excinfo.value.code) == (422, "unknown_model")
        assert not voice_dir.exists()

    async def test_second_install_while_installing_joins_the_running_one(self, make_service):
        gate = asyncio.Event()
        fetcher = _Fetcher(gate=gate)
        service = make_service(fetcher=fetcher)
        await service.install()
        await _wait_for(lambda: fetcher.calls)

        again = await service.install()
        gate.set()
        await _installed(service)

        assert again.state == "downloading"
        assert sorted(fetcher.calls) == sorted(f.name for f in FAKE_MODEL.files)

    async def test_download_error_fails_with_its_code(self, make_service):
        service = make_service(fetcher=_Fetcher(error=DownloadError("hash_mismatch", "bad bytes")))

        await service.install()
        status = await _installed(service)

        assert status.state == "failed"
        assert status.enabled is True
        assert status.error is not None
        assert (status.error.code, status.error.message) == ("hash_mismatch", "bad bytes")
        assert load_settings(service.paths).phase == "failed"

    async def test_retry_after_a_failure_clears_the_error(self, make_service):
        fetcher = _Fetcher(error=DownloadError("network", "offline"))
        service = make_service(fetcher=fetcher)
        await service.install()
        await _installed(service)

        fetcher.error = None
        await service.install()
        status = await _installed(service)

        assert status.state == "ready"
        assert status.error is None

    async def test_selftest_that_recognises_nothing_fails(self, make_service):
        service = make_service(helper=_Helper(text="  "))

        await service.install()
        status = await _installed(service)

        assert status.state == "failed"
        assert status.error is not None and status.error.code == "selftest_failed"

    async def test_not_enough_disk_fails_before_downloading(self, make_service, monkeypatch):
        monkeypatch.setattr(service_module, "free_disk_bytes", lambda _path: 1024)
        fetcher = _Fetcher()
        service = make_service(fetcher=fetcher)

        await service.install()
        status = await _installed(service)

        assert status.state == "failed"
        assert status.error is not None and status.error.code == "disk_full"
        assert fetcher.calls == []

    async def test_queued_while_another_process_holds_the_lock(self, make_service, monkeypatch):
        monkeypatch.setattr(service_module, "LOCK_POLL_SECONDS", 0.02)
        fetcher = _Fetcher()
        service = make_service(fetcher=fetcher)
        other = FileLock(service.paths.lock_file)
        assert other.try_acquire()
        try:
            await service.install()
            await asyncio.sleep(0.1)

            assert service.status().state == "queued"
            assert fetcher.calls == []
        finally:
            other.release()

        assert (await _installed(service)).state == "ready"

    @pytest.mark.parametrize(
        ("meanwhile", "state"),
        [
            ({"enabled": False}, "not_installed"),
            (
                {
                    "enabled": True,
                    "model_id": FAKE_MODEL.id,
                    "phase": "failed",
                    "error_code": "interrupted",
                    "error_message": "The install was interrupted; retry to resume it.",
                },
                "failed",
            ),
        ],
        ids=["removed", "marked_interrupted"],
    )
    async def test_install_superseded_while_waiting_for_the_lock_downloads_nothing(
        self, make_service, voice_dir, monkeypatch, meanwhile, state
    ):
        monkeypatch.setattr(service_module, "LOCK_POLL_SECONDS", 0.02)
        fetcher = _Fetcher()
        service = make_service(fetcher=fetcher)
        other = FileLock(service.paths.lock_file)
        assert other.try_acquire()
        try:
            await service.install()
            _write_settings(voice_dir, **meanwhile)
        finally:
            other.release()

        status = await _installed(service)

        assert status.state == state
        assert fetcher.calls == []
        assert not service.paths.models_dir.exists()

    async def test_install_superseded_by_another_model_downloads_nothing(
        self, make_service, voice_dir, monkeypatch
    ):
        other_model = replace(FAKE_MODEL, id="fake-stt-other")
        monkeypatch.setattr(
            service_module, "MODELS", {FAKE_MODEL.id: FAKE_MODEL, other_model.id: other_model}
        )
        monkeypatch.setattr(service_module, "DEFAULT_MODEL_ID", FAKE_MODEL.id)
        monkeypatch.setattr(service_module, "LOCK_POLL_SECONDS", 0.02)
        fetcher = _Fetcher()
        service = make_service(fetcher=fetcher, fake=False)
        other = FileLock(service.paths.lock_file)
        assert other.try_acquire()
        try:
            await service.install(FAKE_MODEL.id)
            _write_settings(voice_dir, enabled=True, model_id=other_model.id, phase="installing")
        finally:
            other.release()

        await _installed(service)

        assert fetcher.calls == []
        assert not service.paths.model_dir(FAKE_MODEL.id).exists()

    async def test_install_of_the_ready_model_is_a_no_op(self, make_service):
        events = _Events()
        helper = _Helper()
        fetcher = _Fetcher()
        service = make_service(fetcher=fetcher, helper=helper, publish=events)
        await service.install()
        ready = await _installed(service)
        fetcher.calls.clear()
        stops, published = helper.stops, len(events.payloads)

        again = await service.install()

        assert again.state == "ready"
        assert again.speed_factor == ready.speed_factor
        assert (await _installed(service)).state == "ready"
        assert fetcher.calls == []
        assert helper.stops == stops
        assert events.payloads[published:] == []

    async def test_fake_install_never_collects_the_real_model_sharing_its_root(
        self, make_service, voice_dir
    ):
        paths = VoicePaths(voice_dir)
        real = paths.model_dir(DEFAULT_MODEL_ID)
        real.mkdir(parents=True)
        (real / "encoder.int8.onnx").write_bytes(b"weights")
        (real / MARKER).write_text("{}", encoding="utf-8")
        retired = paths.model_dir("retired-model")
        retired.mkdir()
        (retired / MARKER).write_text("{}", encoding="utf-8")
        service = make_service()

        await service.install()

        assert (await _installed(service)).state == "ready"
        assert (real / "encoder.int8.onnx").read_bytes() == b"weights"
        assert not retired.exists()

    async def test_shutdown_during_finalize_still_records_the_ready_install(
        self, make_service, monkeypatch
    ):
        marker_written = threading.Event()
        release = threading.Event()
        write_marker = service_module.write_model_marker

        def slow_marker(paths, spec):
            write_marker(paths, spec)
            marker_written.set()
            release.wait(INSTALL_TIMEOUT)

        monkeypatch.setattr(service_module, "write_model_marker", slow_marker)
        service = make_service()
        await service.install()
        await _wait_for(marker_written.is_set)
        try:
            stopping = asyncio.ensure_future(service.shutdown())
            await asyncio.sleep(0.05)
        finally:
            release.set()
        await stopping

        settings = load_settings(service.paths)
        assert settings.phase == "ready"
        assert settings.speed_factor is not None
        restarted = make_service().status()
        assert restarted.state == "ready"
        assert restarted.speed_factor == settings.speed_factor


class TestDiskCheck:
    """``status()`` sizes the rest of the install (downloads still missing
    plus the unpacked runtime) exactly as the install's own pre-check does."""

    WHEEL = CatalogFile(
        name="sherpa_onnx_test.whl", size=4096, sha256="0" * 64, urls=("https://example.invalid/w",)
    )
    TARGET = RuntimeTarget(key="sherpa-onnx-test", wheels=(WHEEL,))
    MODEL = replace(FAKE_MODEL, id="fake-stt-runtime", needs_runtime=True)

    @pytest.fixture(autouse=True)
    def _model_with_a_runtime(self, monkeypatch):
        monkeypatch.setattr(service_module, "MODELS", {self.MODEL.id: self.MODEL})
        monkeypatch.setattr(service_module, "DEFAULT_MODEL_ID", self.MODEL.id)
        monkeypatch.setattr(service_module, "resolve_runtime", lambda: (self.TARGET, None))

    @staticmethod
    def _free_disk(monkeypatch, free: int) -> None:
        monkeypatch.setattr(host_module, "free_disk_bytes", lambda _path: free)
        monkeypatch.setattr(service_module, "free_disk_bytes", lambda _path: free)

    async def test_disk_too_small_for_the_install_is_reported_by_status_in_the_same_words(
        self, make_service, monkeypatch
    ):
        needed = self.MODEL.download_bytes + self.TARGET.download_bytes + RUNTIME_DISK_BYTES
        self._free_disk(monkeypatch, needed + DISK_MARGIN_BYTES - 1)
        fetcher = _Fetcher()
        service = make_service(fake=False, fetcher=fetcher)

        before = service.status()
        await service.install()
        failed = await _installed(service)

        reasons = {r.code: r.message for r in before.host.reasons}
        assert before.host.recommendation == "unsupported"
        assert failed.state == "failed"
        assert failed.error is not None and failed.error.code == "disk_full"
        assert reasons["disk_insufficient"] == failed.error.message
        assert fetcher.calls == []

    @pytest.mark.parametrize(("unpacked", "blocked"), [(False, True), (True, False)])
    async def test_the_runtime_unpack_counts_until_the_runtime_is_unpacked(
        self, make_service, monkeypatch, voice_dir, unpacked, blocked
    ):
        if unpacked:
            runtime_dir = VoicePaths(voice_dir).runtime_dir(self.TARGET.key)
            runtime_dir.mkdir(parents=True)
            write_runtime_marker(runtime_dir, self.TARGET)
        self._free_disk(
            monkeypatch, self.MODEL.download_bytes + self.TARGET.download_bytes + DISK_MARGIN_BYTES
        )

        status = make_service(fake=False).status()

        assert ("disk_insufficient" in [r.code for r in status.host.reasons]) is blocked


class TestCancelAndRemove:
    async def test_cancel_during_download_leaves_zero_bytes(self, make_service):
        events = _Events()
        gate = asyncio.Event()
        fetcher = _Fetcher(gate=gate)
        service = make_service(fetcher=fetcher, publish=events)
        await service.install()
        await _wait_for(lambda: fetcher.calls)

        status = await service.cancel_install()

        assert status.state == "not_installed"
        assert status.enabled is False
        assert not service.paths.models_dir.exists()
        assert not list(service.paths.root.rglob("*.part"))
        assert events.payloads[-1]["state"] == "not_installed"

    async def test_remove_deletes_files_and_turns_voice_off(self, make_service):
        events = _Events()
        service = make_service(publish=events)
        await service.install()
        await _installed(service)

        status = await service.remove()

        assert status.state == "not_installed"
        assert not service.paths.models_dir.exists()
        assert not service.paths.runtime_root.exists()
        settings = load_settings(service.paths)
        assert (settings.enabled, settings.phase, settings.speed_factor) == (False, None, None)
        assert events.payloads[-1]["state"] == "not_installed"

    async def test_remove_aborts_open_dictations(self, make_service):
        service = make_service()
        await service.install()
        await _installed(service)
        created = await service.create_dictation()

        await service.remove()

        with pytest.raises(VoiceError) as excinfo:
            await service.finish_dictation(created.dictation_id, VoiceDictationFinishRequest(last_seq=-1))
        assert excinfo.value.code == "dictation_not_found"

    async def test_remove_is_refused_while_another_process_holds_the_lock(self, make_service):
        service = make_service()
        await service.install()
        await _installed(service)
        other = FileLock(service.paths.lock_file)
        assert other.try_acquire()
        try:
            with pytest.raises(VoiceError) as excinfo:
                await service.remove()
        finally:
            other.release()

        assert (excinfo.value.status, excinfo.value.code) == (409, "install_in_progress")
        assert service.status().state == "ready"

    @pytest.mark.parametrize("operation", ["remove", "cancel_install"])
    async def test_unopenable_lock_file_is_a_storage_error(self, make_service, voice_dir, operation):
        (voice_dir / ".lock").mkdir(parents=True)
        service = make_service()

        with pytest.raises(VoiceError) as excinfo:
            await getattr(service, operation)()

        assert (excinfo.value.status, excinfo.value.code) == (500, "storage_error")

    @pytest.mark.parametrize("operation", ["remove", "cancel_install"])
    async def test_stopping_a_download_publishes_only_the_outcome(self, make_service, operation):
        events = _Events()
        gate = asyncio.Event()
        fetcher = _Fetcher(gate=gate)
        service = make_service(fetcher=fetcher, publish=events)
        await service.install()
        await _wait_for(lambda: fetcher.calls)
        published = len(events.payloads)

        await getattr(service, operation)()

        assert [p["state"] for p in events.payloads[published:]] == ["not_installed"]

    @pytest.mark.parametrize("operation", ["remove", "cancel_install"])
    async def test_files_still_in_use_still_publishes_that_voice_is_off(
        self, make_service, monkeypatch, operation
    ):
        events = _Events()
        gate = asyncio.Event()
        fetcher = _Fetcher(gate=gate)
        service = make_service(fetcher=fetcher, publish=events)
        await service.install()
        await _wait_for(lambda: fetcher.calls)
        published = len(events.payloads)
        monkeypatch.setattr(service_module, "remove_tree", lambda _path: False)

        with pytest.raises(VoiceError) as excinfo:
            await getattr(service, operation)()

        assert (excinfo.value.status, excinfo.value.code) == (409, "files_in_use")
        assert [p["enabled"] for p in events.payloads[published:]] == [False]
        assert service.status().enabled is False

    async def test_cancel_of_a_ready_install_publishes_that_it_is_still_ready(self, make_service):
        events = _Events()
        service = make_service(publish=events)
        await service.install()
        await _installed(service)
        await _wait_for(lambda: events.payloads and events.payloads[-1]["state"] == "ready")
        published = len(events.payloads)

        await service.cancel_install()

        assert [p["state"] for p in events.payloads[published:]] == ["ready"]


class TestStartAfterRestart:

    async def test_interrupted_install_resumes_after_restart(self, make_service):
        gate = asyncio.Event()
        first = make_service(fetcher=_Fetcher(gate=gate))
        await first.install()
        await _wait_for(lambda: first.status().state == "downloading")
        await first.shutdown()
        partial = list(first.paths.root.rglob("*.part"))
        assert partial, "shutdown must keep the partial download"
        assert load_settings(first.paths).phase == "installing"

        fetcher = _Fetcher()
        restarted = make_service(fetcher=fetcher)
        await restarted.start()

        assert restarted.status().state in ("queued", "downloading", "verifying")
        assert (await _installed(restarted)).state == "ready"
        assert fetcher.calls

    async def test_failed_install_is_never_retried_automatically(self, make_service):
        first = make_service(fetcher=_Fetcher(error=DownloadError("network", "offline")))
        await first.install()
        await _installed(first)

        fetcher = _Fetcher()
        restarted = make_service(fetcher=fetcher)
        await restarted.start()
        await asyncio.sleep(0.05)

        assert restarted.status().state == "failed"
        assert fetcher.calls == []

    async def test_intact_ready_install_is_left_alone(self, make_service):
        first = make_service()
        await first.install()
        await _installed(first)

        fetcher = _Fetcher()
        restarted = make_service(fetcher=fetcher)
        await restarted.start()

        assert restarted.status().state == "ready"
        assert fetcher.calls == []

    async def test_ready_install_with_a_truncated_file_is_repaired(self, make_service):
        first = make_service()
        await first.install()
        await _installed(first)
        victim = first.paths.model_dir(FAKE_MODEL.id) / FAKE_MODEL.files[0].name
        victim.write_bytes(victim.read_bytes()[:100])

        fetcher = _Fetcher()
        restarted = make_service(fetcher=fetcher)
        await restarted.start()

        assert restarted.status().state != "ready"
        assert (await _installed(restarted)).state == "ready"
        assert FAKE_MODEL.files[0].name in fetcher.calls

    @pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="POSIX non-root permissions")
    async def test_unwritable_voice_dir_never_raises(self, make_service, voice_dir):
        _write_settings(voice_dir, enabled=True, model_id=FAKE_MODEL.id, phase="ready")
        voice_dir.chmod(0o500)
        try:
            service = make_service()

            await service.start()

            assert service.status().state in ("queued", "failed", "downloading", "verifying")
        finally:
            await service.shutdown()
            voice_dir.chmod(0o700)


def _place_verified_model(paths: VoicePaths, spec) -> None:
    directory = paths.model_dir(spec.id)
    directory.mkdir(parents=True, exist_ok=True)
    for file in spec.files:
        (directory / file.name).write_bytes(FAKE_CONTENT[file.name])
    write_model_marker(paths, spec)


class TestRuntimeRepair:
    """An enabled voice whose verified model is in place but whose runtime is
    gone (a Python upgrade) is a repair: it shows as ``repairing`` and
    cancelling it must never delete the model."""

    WHEEL = CatalogFile(
        name="sherpa_onnx_test.whl", size=4096, sha256="0" * 64, urls=("https://example.invalid/w",)
    )
    TARGET = RuntimeTarget(key="sherpa-onnx-test", wheels=(WHEEL,))
    MODEL = replace(FAKE_MODEL, id="fake-stt-runtime", needs_runtime=True)

    @pytest.fixture(autouse=True)
    def _model_with_a_runtime(self, monkeypatch):
        monkeypatch.setattr(service_module, "MODELS", {self.MODEL.id: self.MODEL})
        monkeypatch.setattr(service_module, "DEFAULT_MODEL_ID", self.MODEL.id)
        monkeypatch.setattr(service_module, "resolve_runtime", lambda: (self.TARGET, None))
        monkeypatch.setattr(host_module, "free_disk_bytes", lambda _path: 10**12)
        monkeypatch.setattr(service_module, "free_disk_bytes", lambda _path: 10**12)

    @pytest.fixture
    def runtime_install(self, monkeypatch):
        """Stands in for ``install_runtime``: leaves a partial wheel and a
        staging dir, then waits on ``gate``."""
        state = SimpleNamespace(started=asyncio.Event(), gate=asyncio.Event())

        async def install_runtime(paths, target, fetcher, on_progress):
            paths.wheel_downloads.mkdir(parents=True, exist_ok=True)
            (paths.wheel_downloads / (self.WHEEL.name + ".part")).write_bytes(b"partial")
            (paths.runtime_root / f".staging-{target.key}-1").mkdir()
            state.started.set()
            await state.gate.wait()
            return paths.runtime_dir(target.key)

        monkeypatch.setattr(service_module, "install_runtime", install_runtime)
        return state

    @pytest.mark.parametrize(
        ("enabled", "model", "runtime", "repairing"),
        [
            (True, True, False, True),
            (True, False, False, False),
            (True, True, True, False),
            (False, True, False, False),
        ],
        ids=["model_without_runtime", "nothing_yet", "installed", "voice_off"],
    )
    def test_repairing_only_when_an_enabled_model_lacks_its_runtime(
        self, voice_dir, enabled, model, runtime, repairing
    ):
        paths = VoicePaths(voice_dir)
        _write_settings(voice_dir, enabled=enabled, model_id=self.MODEL.id, phase="ready")
        if model:
            _place_verified_model(paths, self.MODEL)
        if runtime:
            runtime_dir = paths.runtime_dir(self.TARGET.key)
            runtime_dir.mkdir(parents=True)
            write_runtime_marker(runtime_dir, self.TARGET)

        status = VoiceService(voice_dir=voice_dir).status()

        assert status.repairing is repairing

    async def test_cancelling_a_repair_keeps_the_model_and_offers_a_retry(
        self, make_service, voice_dir, runtime_install
    ):
        paths = VoicePaths(voice_dir)
        _write_settings(voice_dir, enabled=True, model_id=self.MODEL.id, phase="ready")
        _place_verified_model(paths, self.MODEL)
        events = _Events()
        service = make_service(fake=False, publish=events)
        await service.start()
        await asyncio.wait_for(runtime_install.started.wait(), INSTALL_TIMEOUT)
        during = service.status()

        status = await service.cancel_install()

        assert (during.state, during.repairing) == ("downloading", True)
        assert (status.state, status.enabled, status.repairing) == ("failed", True, True)
        assert status.error is not None and status.error.code == "interrupted"
        assert status.error.message == "Voice needs a small update for this agent; retry to finish."
        assert model_ready(paths, self.MODEL)
        assert not paths.wheel_downloads.exists()
        assert not list(paths.runtime_root.glob(".staging-*"))
        assert load_settings(paths).enabled is True
        assert (events.payloads[-1]["state"], events.payloads[-1]["repairing"]) == ("failed", True)

    @pytest.fixture
    def runtime_unpacked(self, monkeypatch):
        """Stands in for ``install_runtime``: unpacks at once and writes the
        runtime marker, as the real one does before the self-test runs."""
        calls: list[str] = []

        async def install_runtime(paths, target, fetcher, on_progress):
            calls.append(target.key)
            runtime_dir = paths.runtime_dir(target.key)
            runtime_dir.mkdir(parents=True, exist_ok=True)
            write_runtime_marker(runtime_dir, target)
            return runtime_dir

        monkeypatch.setattr(service_module, "install_runtime", install_runtime)
        # TARGET's key carries no sherpa-onnx version, so the real collector
        # would delete the runtime the moment the install finishes.
        monkeypatch.setattr(service_module, "gc_runtimes", lambda paths, prefix: None)
        return calls

    def _place_runtime(self, paths: VoicePaths) -> None:
        runtime_dir = paths.runtime_dir(self.TARGET.key)
        runtime_dir.mkdir(parents=True)
        write_runtime_marker(runtime_dir, self.TARGET)

    async def test_failed_self_test_after_a_repair_is_reported_and_retried(
        self, make_service, voice_dir, runtime_unpacked
    ):
        paths = VoicePaths(voice_dir)
        _write_settings(voice_dir, enabled=True, model_id=self.MODEL.id, phase="ready")
        _place_verified_model(paths, self.MODEL)
        helper = _Helper(text="  ")
        fetcher = _Fetcher()
        service = make_service(fake=False, helper=helper, fetcher=fetcher)
        await service.start()

        status = await _installed(service)

        # Both markers are on disk, but the repaired runtime never passed.
        assert (status.state, status.enabled, status.repairing) == ("failed", True, True)
        assert status.error is not None and status.error.code == "selftest_failed"
        with pytest.raises(VoiceError) as excinfo:
            await service.create_dictation()
        assert excinfo.value.code == "voice_not_ready"

        helper.text = "fake words"
        fetched = list(fetcher.calls)
        await service.install()
        status = await _installed(service)

        assert (status.state, status.repairing, status.error) == ("ready", False, None)
        # The retry only verified again: nothing was fetched or unpacked twice.
        assert runtime_unpacked == [self.TARGET.key]
        assert fetcher.calls == fetched

    async def test_restart_during_a_repair_self_test_verifies_again(
        self, make_service, voice_dir, runtime_unpacked
    ):
        paths = VoicePaths(voice_dir)
        _write_settings(voice_dir, enabled=True, model_id=self.MODEL.id, phase="installing")
        _place_verified_model(paths, self.MODEL)
        self._place_runtime(paths)
        helper = _Helper()
        service = make_service(fake=False, helper=helper)
        before = service.status()

        await service.start()
        status = await _installed(service)

        assert before.state != "ready"
        assert status.state == "ready"
        assert helper.stops >= 1
        assert runtime_unpacked == []

    async def test_cancelling_after_a_failed_repair_keeps_the_model(
        self, make_service, voice_dir
    ):
        paths = VoicePaths(voice_dir)
        _write_settings(
            voice_dir, enabled=True, model_id=self.MODEL.id, phase="failed",
            error_code="selftest_failed", error_message="no speech",
        )
        _place_verified_model(paths, self.MODEL)
        self._place_runtime(paths)
        service = make_service(fake=False)

        status = await service.cancel_install()

        assert (status.state, status.enabled, status.repairing) == ("failed", True, True)
        assert model_ready(paths, self.MODEL)

    async def test_cancelling_a_first_install_with_a_runtime_still_deletes_everything(
        self, make_service, runtime_install
    ):
        service = make_service(fake=False)
        await service.install()
        await asyncio.wait_for(runtime_install.started.wait(), INSTALL_TIMEOUT)

        status = await service.cancel_install()

        assert (status.state, status.enabled, status.repairing) == ("not_installed", False, False)
        assert not service.paths.runtime_root.exists()
        assert not service.paths.models_dir.exists()


class TestForeignInstall:
    """An install running in another process (the CLI, a second agent on the
    same prefix) is published from disk while it holds the lock, and its
    outcome once the lock frees."""

    @pytest.fixture(autouse=True)
    def _fast_polls(self, monkeypatch):
        monkeypatch.setattr(service_module, "FOREIGN_INSTALL_POLL_SECONDS", 0.02)
        monkeypatch.setattr(service_module, "MIN_PUBLISH_INTERVAL", 0.01)

    @staticmethod
    def _watchers() -> int:
        return sum(
            1
            for task in asyncio.all_tasks()
            if not task.done() and task.get_coro().__name__ == "_watch_lock"
        )

    async def test_progress_is_published_while_held_and_the_outcome_once_freed(
        self, make_service, voice_dir
    ):
        _write_settings(voice_dir, enabled=True, model_id=FAKE_MODEL.id, phase="installing")
        events = _Events()
        service = make_service(publish=events)
        other = FileLock(service.paths.lock_file)
        assert other.try_acquire()
        try:
            assert service.status().state == "queued"
            await _wait_for(lambda: len(events.payloads) >= 2)
            assert events.states() == ["queued"]
            _place_verified_model(service.paths, FAKE_MODEL)
            _write_settings(voice_dir, enabled=True, model_id=FAKE_MODEL.id, phase="ready")
        finally:
            other.release()

        await _wait_for(lambda: events.payloads[-1]["state"] == "ready")
        published = len(events.payloads)
        await asyncio.sleep(0.1)

        assert len(events.payloads) == published
        assert self._watchers() == 0

    async def test_at_most_one_watcher_runs(self, make_service, voice_dir):
        _write_settings(voice_dir, enabled=True, model_id=FAKE_MODEL.id, phase="installing")
        service = make_service(publish=_Events())
        other = FileLock(service.paths.lock_file)
        assert other.try_acquire()
        try:
            for _ in range(5):
                assert service.status().state == "queued"
            await asyncio.sleep(0.05)

            assert self._watchers() == 1
        finally:
            other.release()

    async def test_shutdown_stops_the_watcher(self, make_service, voice_dir):
        _write_settings(voice_dir, enabled=True, model_id=FAKE_MODEL.id, phase="installing")
        events = _Events()
        service = make_service(publish=events)
        other = FileLock(service.paths.lock_file)
        assert other.try_acquire()
        try:
            service.status()
            await _wait_for(lambda: events.payloads)

            await service.shutdown()
            published = len(events.payloads)
            await asyncio.sleep(0.1)

            assert len(events.payloads) == published
            assert self._watchers() == 0
        finally:
            other.release()


class TestMarkInterrupted:
    async def test_interrupted_foreground_install_is_recorded_as_failed(self, make_service, voice_dir):
        _write_settings(voice_dir, enabled=True, model_id=FAKE_MODEL.id, phase="installing")
        service = make_service()

        service.mark_interrupted()

        status = service.status()
        assert status.state == "failed"
        assert status.error is not None and status.error.code == "interrupted"


class TestDictationGate:
    async def test_dictation_before_install_is_voice_not_ready(self, make_service):
        with pytest.raises(VoiceError) as excinfo:
            await make_service().create_dictation()

        assert (excinfo.value.status, excinfo.value.code) == (409, "voice_not_ready")

    async def test_busy_while_a_dictation_is_open(self, make_service):
        service = make_service()
        await service.install()
        await _installed(service)
        assert not service.busy()

        created = await service.create_dictation()
        assert service.busy()

        await service.delete_dictation(created.dictation_id)
        assert not service.busy()

    async def test_dictation_while_voice_is_being_removed_is_voice_not_ready(self, make_service):
        helper = _Helper()
        service = make_service(helper=helper)
        await service.install()
        await _installed(service)
        helper.stop_gate = asyncio.Event()
        stops = helper.stops
        removing = asyncio.ensure_future(service.remove())
        await _wait_for(lambda: helper.stops > stops)

        with pytest.raises(VoiceError) as excinfo:
            await service.create_dictation()

        assert (excinfo.value.status, excinfo.value.code) == (409, "voice_not_ready")
        helper.stop_gate.set()
        assert (await removing).state == "not_installed"


class _Claude:
    """Stands in for ``run_capped`` running ``claude -p``: answers ``text``
    and keeps each run's handle; with a ``gate`` it first waits on it."""

    def __init__(self, text: str = "Cleaned up.") -> None:
        self.text = text
        self.gate: threading.Event | None = None
        self.handles: list[ProcessHandle] = []

    def __call__(self, argv, *, handle: ProcessHandle, **_kwargs) -> CmdResult:
        self.handles.append(handle)
        if self.gate is not None:
            self.gate.wait(INSTALL_TIMEOUT)
        payload = {"type": "result", "subtype": "success", "is_error": False, "result": self.text}
        return CmdResult(rc=0, stdout=json.dumps(payload).encode(), stderr=b"")


class TestCleanup:
    @pytest.fixture
    def make_cleaner(self, tmp_path):
        def make(claude: _Claude) -> TranscriptCleaner:
            return TranscriptCleaner(
                clis={"claude": ClaudeCleanup(lambda: str(tmp_path / "bin" / "claude"))},
                work_dir=tmp_path / "voice-cleanup",
                run=claude,
            )

        return make

    async def test_without_a_cleaner_is_cleanup_unavailable(self, make_service):
        with pytest.raises(VoiceError) as excinfo:
            await make_service().cleanup_transcript("hello")

        assert (excinfo.value.status, excinfo.value.code) == (503, "cleanup_unavailable")

    async def test_cleans_up_without_the_speech_model_installed(self, make_service, make_cleaner):
        service = make_service(cleaner=make_cleaner(_Claude("Hello there.")))

        response = await service.cleanup_transcript("um hello there")

        assert response == VoiceCleanupResponse(text="Hello there.")
        assert service.status().state == "not_installed"

    async def test_the_session_kind_reaches_the_cleaner(self, make_service, tmp_path):
        argvs: list[list[str]] = []

        def codex(argv, **_kwargs) -> CmdResult:
            argvs.append(list(argv))
            item = {"type": "item.completed", "item": {"type": "agent_message", "text": "Hi."}}
            done = {"type": "turn.completed"}
            return CmdResult(rc=0, stdout=f"{json.dumps(item)}\n{json.dumps(done)}".encode(), stderr=b"")

        async def signed_in(_binary, _env) -> bool:
            return True

        cleaner = TranscriptCleaner(
            clis={"codex": CodexCleanup(lambda: "codex", lambda: None, auth_present=signed_in)},
            work_dir=tmp_path / "voice-cleanup",
            run=codex,
        )

        response = await make_service(cleaner=cleaner).cleanup_transcript("um hi", "codex")

        assert response == VoiceCleanupResponse(text="Hi.")
        assert argvs[0][:2] == ["codex", "exec"]

    async def test_busy_while_a_cleanup_runs(self, make_service, make_cleaner):
        claude = _Claude()
        claude.gate = threading.Event()
        service = make_service(cleaner=make_cleaner(claude))
        assert not service.busy()

        running = asyncio.ensure_future(service.cleanup_transcript("hello"))
        try:
            await _wait_for(lambda: claude.handles)
            assert service.busy()
        finally:
            claude.gate.set()
            await running

        assert not service.busy()

    async def test_shutdown_aborts_a_running_cleanup(self, make_service, make_cleaner):
        claude = _Claude()
        claude.gate = threading.Event()
        service = make_service(cleaner=make_cleaner(claude))
        running = asyncio.ensure_future(service.cleanup_transcript("hello"))
        try:
            await _wait_for(lambda: claude.handles)

            await service.shutdown()

            assert claude.handles[0].cancelled is True
        finally:
            claude.gate.set()
            await running


class TestCleanupSetting:

    async def test_stored_announced_and_shared_by_a_new_service(self, make_service):
        events = _Events()
        service = make_service(publish=events)

        status = service.set_cleanup_enabled(True)

        assert status.cleanup_enabled is True
        assert events.payloads[-1]["cleanup_enabled"] is True
        assert make_service().status().cleanup_enabled is True

        assert service.set_cleanup_enabled(False).cleanup_enabled is False
        assert events.payloads[-1]["cleanup_enabled"] is False

    async def test_survives_model_removal(self, make_service):
        service = make_service()
        await service.install()
        await _installed(service)
        service.set_cleanup_enabled(True)

        status = await service.remove()

        assert status.state == "not_installed"
        assert status.cleanup_enabled is True


class TestUnsupportedHost:
    @pytest.fixture(autouse=True)
    def _no_runtime(self, monkeypatch):
        monkeypatch.setattr(service_module, "resolve_runtime", lambda: (None, "platform_unsupported"))

    async def test_status_is_unsupported_with_the_reason(self, make_service):
        status = make_service(fake=False).status()

        assert status.state == "unsupported"
        assert status.host.recommendation == "unsupported"
        assert status.host.reasons[0].code == "platform_unsupported"

    async def test_install_and_dictation_are_refused(self, make_service, voice_dir):
        fetcher = _Fetcher()
        service = make_service(fake=False, fetcher=fetcher)

        for call in (service.install(), service.create_dictation()):
            with pytest.raises(VoiceError) as excinfo:
                await call
            assert (excinfo.value.status, excinfo.value.code) == (409, "voice_unsupported")
        assert fetcher.calls == []
        assert not voice_dir.exists()

    async def test_start_never_downloads(self, make_service, voice_dir):
        _write_settings(voice_dir, enabled=True, phase="installing")
        fetcher = _Fetcher()
        service = make_service(fake=False, fetcher=fetcher)

        await service.start()
        await asyncio.sleep(0.05)

        assert fetcher.calls == []
        assert service.status().state == "unsupported"


class TestPublishThrottle:
    async def test_progress_is_coalesced_and_the_latest_value_follows(self, make_service, monkeypatch):
        monkeypatch.setattr(service_module, "MIN_PUBLISH_INTERVAL", 0.05)
        events = _Events()
        gate = asyncio.Event()
        fetcher = _Fetcher(gate=gate, burst=100)
        service = make_service(fetcher=fetcher, publish=events)

        await service.install()
        await _wait_for(lambda: fetcher.calls)

        burst = [p["progress"]["done_bytes"] for p in events.with_state("downloading")]
        assert burst == [0, 1]

        await _wait_for(lambda: len(events.with_state("downloading")) == 3)
        assert events.with_state("downloading")[-1]["progress"]["done_bytes"] == 100
        await asyncio.sleep(0.15)
        assert len(events.with_state("downloading")) == 3

    async def test_state_change_is_published_at_once_even_inside_the_window(self, make_service):
        events = _Events()
        gate = asyncio.Event()
        fetcher = _Fetcher(gate=gate, burst=3)
        service = make_service(fetcher=fetcher, publish=events)
        await service.install()
        await _wait_for(lambda: fetcher.calls)

        await service.cancel_install()

        assert events.payloads[-1]["state"] == "not_installed"

    async def test_failing_publisher_never_breaks_the_install(self, make_service):
        def explode(_event_type, _payload):
            raise RuntimeError("bus closed")

        service = make_service(publish=explode)

        await service.install()

        assert (await _installed(service)).state == "ready"
