"""On-disk layout of the voice root and the persisted user choice.

``<root>/settings.json`` holds whether the user enabled voice and how the last
install ended; ``models/<id>/`` and ``runtime/<key>/`` hold the files, each
completed by an ``.installed`` marker written last. Readiness is always
decided from disk, so an install made by the CLI is visible to a running
agent and a truncated file is noticed without re-hashing 650 MB.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TypeVar

_T = TypeVar("_T")

from mobaide.agent._env_writer import atomic_write

from .catalog import ModelSpec, RuntimeTarget

logger = logging.getLogger(__name__)

MARKER = ".installed"
PART_SUFFIX = ".part"

# ``phase`` records how the last install attempt ended so a restart can tell
# an interrupted download (resume) from a failed one (wait for the user).
PHASE_INSTALLING = "installing"
PHASE_READY = "ready"
PHASE_FAILED = "failed"


@dataclass(frozen=True)
class VoicePaths:
    root: Path

    @property
    def settings_file(self) -> Path:
        return self.root / "settings.json"

    @property
    def lock_file(self) -> Path:
        return self.root / ".lock"

    @property
    def models_dir(self) -> Path:
        return self.root / "models"

    @property
    def runtime_root(self) -> Path:
        return self.root / "runtime"

    def model_dir(self, model_id: str) -> Path:
        return self.models_dir / model_id

    def runtime_dir(self, key: str) -> Path:
        return self.runtime_root / key

    @property
    def wheel_downloads(self) -> Path:
        return self.runtime_root / ".downloads"


@dataclass
class VoiceSettings:
    enabled: bool = False
    model_id: str | None = None
    phase: str | None = None
    speed_factor: float | None = None
    load_ms: int | None = None
    error_code: str | None = None
    error_message: str | None = None


def load_settings(paths: VoicePaths) -> VoiceSettings:
    """Read the persisted choice; a missing or corrupt file means disabled."""
    try:
        raw = json.loads(paths.settings_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return VoiceSettings()
    except (OSError, ValueError) as exc:
        logger.warning("voice.settings_unreadable path=%s: %s", paths.settings_file, exc)
        return VoiceSettings()
    if not isinstance(raw, dict):
        return VoiceSettings()
    settings = VoiceSettings()
    for key, expected in (
        ("enabled", bool),
        ("model_id", str),
        ("phase", str),
        ("speed_factor", (int, float)),
        ("load_ms", int),
        ("error_code", str),
        ("error_message", str),
    ):
        value = raw.get(key)
        if isinstance(value, expected) and not (expected is int and isinstance(value, bool)):
            setattr(settings, key, value)
    return settings


def save_settings(paths: VoicePaths, settings: VoiceSettings) -> None:
    atomic_write(paths.settings_file, json.dumps(asdict(settings), indent=2) + "\n")


def _read_marker(directory: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads((directory / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _sizes_match(directory: Path, expected: dict[str, int]) -> bool:
    for name, size in expected.items():
        try:
            if (directory / name).stat().st_size != size:
                return False
        except OSError:
            return False
    return True


def model_ready(paths: VoicePaths, spec: ModelSpec) -> bool:
    directory = paths.model_dir(spec.id)
    marker = _read_marker(directory)
    if marker is None or marker.get("fingerprint") != spec.fingerprint:
        return False
    return _sizes_match(directory, {f.name: f.size for f in spec.files})


def write_model_marker(paths: VoicePaths, spec: ModelSpec) -> None:
    atomic_write(
        paths.model_dir(spec.id) / MARKER,
        json.dumps(
            {
                "model_id": spec.id,
                "fingerprint": spec.fingerprint,
                "installed_at": int(time.time()),
            },
            indent=2,
        )
        + "\n",
    )


def runtime_ready(paths: VoicePaths, target: RuntimeTarget) -> bool:
    marker = _read_marker(paths.runtime_dir(target.key))
    return marker is not None and marker.get("wheels") == [w.sha256 for w in target.wheels]


def write_runtime_marker(directory: Path, target: RuntimeTarget) -> None:
    atomic_write(
        directory / MARKER,
        json.dumps({"key": target.key, "wheels": [w.sha256 for w in target.wheels]}, indent=2)
        + "\n",
    )


def bytes_on_disk(directory: Path, names: list[str]) -> int:
    """Bytes already fetched for ``names`` in ``directory``: complete files
    plus partial ``.part`` downloads."""
    total = 0
    for name in names:
        for candidate in (directory / name, directory / (name + PART_SUFFIX)):
            try:
                total += candidate.stat().st_size
                break
            except OSError:
                continue
    return total


async def run_to_completion(fn: Callable[[], _T]) -> _T:
    """Run blocking disk work in a thread and, if the caller is cancelled,
    still wait for it to finish before propagating: a cancelled install must
    not release the voice lock while its thread is still writing files."""
    future = asyncio.ensure_future(asyncio.to_thread(fn))
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        await asyncio.wait({future})
        raise


def remove_tree(path: Path) -> bool:
    """``rmtree`` that tolerates a missing path and retries on Windows, where
    a just-killed helper can hold DLL and model handles for a moment.
    Returns whether the path is gone."""
    attempts = 5 if sys.platform == "win32" else 1
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            return True
        except OSError as exc:
            if attempt == attempts - 1:
                logger.warning("voice.remove_failed path=%s: %s", path, exc)
                return False
            time.sleep(0.5)
    return not path.exists()


def remove_model(paths: VoicePaths, model_id: str) -> bool:
    """Delete one model dir, marker first so a half-deleted dir never reads
    as installed."""
    directory = paths.model_dir(model_id)
    try:
        (directory / MARKER).unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("voice.marker_unlink_failed path=%s: %s", directory, exc)
    return remove_tree(directory)


def gc_models(paths: VoicePaths, keep: set[str]) -> None:
    """Delete installed models that are no longer in the catalog; a dir
    without our marker is left alone."""
    try:
        entries = list(paths.models_dir.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.is_dir() and entry.name not in keep and (entry / MARKER).is_file():
            remove_model(paths, entry.name)


def gc_runtimes(paths: VoicePaths, version_prefix: str) -> None:
    """Delete runtimes of other sherpa-onnx versions and stale staging dirs.
    Other Python ABIs of the current version stay: a dev run and the
    installed agent may share this root with different interpreters."""
    try:
        entries = list(paths.runtime_root.iterdir())
    except OSError:
        return
    for entry in entries:
        if not entry.is_dir() or entry.name == paths.wheel_downloads.name:
            continue
        if entry.name.startswith(".staging-") or not entry.name.startswith(version_prefix):
            remove_tree(entry)
