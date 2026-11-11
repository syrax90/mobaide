"""The voice root on disk: the persisted choice never raises on a bad file,
readiness comes from the ``.installed`` markers plus file sizes, garbage
collection keeps what it must, and the cross-process lock excludes a second
holder."""
from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import replace

import pytest

from mobaide.voice.catalog import FAKE_CONTENT, FAKE_MODEL, CatalogFile, RuntimeTarget
from mobaide.voice.locking import FileLock
from mobaide.voice.store import (
    MARKER,
    VoicePaths,
    VoiceSettings,
    bytes_on_disk,
    gc_models,
    gc_runtimes,
    load_settings,
    model_ready,
    remove_model,
    run_to_completion,
    runtime_ready,
    save_settings,
    write_model_marker,
    write_runtime_marker,
)


@pytest.fixture
def paths(tmp_path) -> VoicePaths:
    root = tmp_path / "voice"
    root.mkdir()
    return VoicePaths(root)


def _install_fake_model(paths: VoicePaths) -> None:
    directory = paths.model_dir(FAKE_MODEL.id)
    directory.mkdir(parents=True)
    for name, blob in FAKE_CONTENT.items():
        (directory / name).write_bytes(blob)
    write_model_marker(paths, FAKE_MODEL)


class TestSettings:

    @pytest.mark.parametrize("content", ["{not json", "[1, 2]", '"enabled"', ""])
    def test_corrupt_file_means_disabled(self, paths, content):
        paths.settings_file.write_text(content, encoding="utf-8")

        assert load_settings(paths) == VoiceSettings()

    def test_fields_of_the_wrong_type_are_ignored(self, paths):
        paths.settings_file.write_text(
            json.dumps(
                {
                    "enabled": "yes",
                    "model_id": 7,
                    "phase": "ready",
                    "speed_factor": "fast",
                    "load_ms": True,
                }
            ),
            encoding="utf-8",
        )

        assert load_settings(paths) == VoiceSettings(phase="ready")

    def test_round_trip(self, paths):
        settings = VoiceSettings(
            enabled=True,
            model_id=FAKE_MODEL.id,
            phase="failed",
            speed_factor=12,
            load_ms=830,
            error_code="hash_mismatch",
            error_message="bad bytes",
        )

        save_settings(paths, settings)

        assert load_settings(paths) == settings


class TestModelReadiness:
    def test_installed_model_is_ready(self, paths):
        _install_fake_model(paths)

        assert model_ready(paths, FAKE_MODEL)

    def test_missing_marker_is_not_ready(self, paths):
        _install_fake_model(paths)
        (paths.model_dir(FAKE_MODEL.id) / MARKER).unlink()

        assert not model_ready(paths, FAKE_MODEL)

    def test_truncated_file_is_not_ready(self, paths):
        _install_fake_model(paths)
        victim = paths.model_dir(FAKE_MODEL.id) / FAKE_MODEL.files[0].name
        victim.write_bytes(victim.read_bytes()[:-1])

        assert not model_ready(paths, FAKE_MODEL)

    def test_marker_from_a_previous_pin_is_not_ready(self, paths):
        _install_fake_model(paths)
        first, *rest = FAKE_MODEL.files
        repinned = replace(FAKE_MODEL, files=(replace(first, sha256="0" * 64), *rest))

        assert not model_ready(paths, repinned)


class TestRuntimeReadiness:
    def _target(self, sha: str = "a" * 64) -> RuntimeTarget:
        return RuntimeTarget(
            key="sherpa-onnx-1.13.8-cp312-x",
            wheels=(CatalogFile("core.whl", 1, sha, ("u",)), CatalogFile("b.whl", 1, "b" * 64, ("u",))),
        )

    def test_marker_with_the_pinned_wheels_is_ready(self, paths):
        target = self._target()
        directory = paths.runtime_dir(target.key)
        directory.mkdir(parents=True)
        write_runtime_marker(directory, target)

        assert runtime_ready(paths, target)

    def test_marker_from_other_wheels_is_not_ready(self, paths):
        target = self._target()
        directory = paths.runtime_dir(target.key)
        directory.mkdir(parents=True)
        write_runtime_marker(directory, self._target(sha="c" * 64))

        assert not runtime_ready(paths, target)

    def test_missing_runtime_is_not_ready(self, paths):
        assert not runtime_ready(paths, self._target())


class TestBytesOnDisk:
    def test_counts_complete_files_and_partial_downloads(self, paths):
        paths.root.joinpath("a.bin").write_bytes(b"x" * 10)
        paths.root.joinpath("b.bin.part").write_bytes(b"x" * 4)

        assert bytes_on_disk(paths.root, ["a.bin", "b.bin", "c.bin"]) == 14


class TestRemoveAndCollect:
    def test_remove_model_deletes_the_directory(self, paths):
        _install_fake_model(paths)

        assert remove_model(paths, FAKE_MODEL.id)
        assert not paths.model_dir(FAKE_MODEL.id).exists()

    def test_gc_models_removes_installed_models_dropped_from_the_catalog(self, paths):
        _install_fake_model(paths)
        old = paths.model_dir("old-model")
        old.mkdir(parents=True)
        (old / "weights.onnx").write_bytes(b"x")
        (old / MARKER).write_text("{}", encoding="utf-8")

        gc_models(paths, keep={FAKE_MODEL.id})

        assert not old.exists()
        assert model_ready(paths, FAKE_MODEL)

    def test_gc_models_leaves_directories_it_did_not_install(self, paths):
        """GC only removes marker-carrying model dirs absent from the catalog:
        anything without our marker is not ours to delete."""
        foreign = paths.model_dir("not-ours")
        foreign.mkdir(parents=True)
        (foreign / "file.bin").write_bytes(b"x")

        gc_models(paths, keep={FAKE_MODEL.id})

        assert (foreign / "file.bin").exists()

    def test_gc_runtimes_keeps_current_version_abis_and_downloads(self, paths):
        root = paths.runtime_root
        for name in (
            "sherpa-onnx-1.13.8-cp312-macosx_11_0_arm64",
            "sherpa-onnx-1.13.8-cp314-macosx_11_0_arm64",
            "sherpa-onnx-1.12.0-cp312-macosx_11_0_arm64",
            ".staging-sherpa-onnx-1.13.8-cp312-macosx_11_0_arm64-123",
            ".downloads",
        ):
            (root / name).mkdir(parents=True)

        gc_runtimes(paths, "sherpa-onnx-1.13.8-")

        assert sorted(p.name for p in root.iterdir()) == [
            ".downloads",
            "sherpa-onnx-1.13.8-cp312-macosx_11_0_arm64",
            "sherpa-onnx-1.13.8-cp314-macosx_11_0_arm64",
        ]


class TestRunToCompletion:

    async def test_cancelled_caller_waits_for_the_thread_to_finish(self):
        started, release = threading.Event(), threading.Event()
        finished: list[bool] = []

        def work() -> None:
            started.set()
            release.wait(5)
            finished.append(True)

        task = asyncio.ensure_future(run_to_completion(work))
        await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done()

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished == [True]


class TestFileLock:
    def test_second_holder_is_refused_until_the_first_releases(self, paths):
        first, second = FileLock(paths.lock_file), FileLock(paths.lock_file)
        try:
            assert first.try_acquire()
            assert not second.try_acquire()
            assert not second.held
            assert second.is_held_elsewhere()

            first.release()

            assert not second.is_held_elsewhere()
            assert second.try_acquire()
        finally:
            first.release()
            second.release()

    def test_reacquire_by_the_holder_is_idempotent(self, paths):
        lock = FileLock(paths.lock_file)
        try:
            assert lock.try_acquire()
            assert lock.try_acquire()
            assert lock.held
            assert not lock.is_held_elsewhere()
        finally:
            lock.release()

        assert not lock.held

    def test_probe_without_a_lock_file_does_not_create_one(self, tmp_path):
        paths = VoicePaths(tmp_path / "absent")

        assert not FileLock(paths.lock_file).is_held_elsewhere()
        assert not paths.root.exists()
