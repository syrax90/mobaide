"""Install the sherpa-onnx runtime into ``runtime/<key>/`` without pip.

The two hash-pinned wheels are fetched like model files and unpacked into a
staging dir that is renamed into place once complete. No index, no resolver
and no ``pip.conf`` is involved, and nothing touches the agent's own venv: a
platform without a wheel can never break ``pip install mobaide-agent``, and
an agent update (which rebuilds the venv) leaves the runtime alone.
"""
from __future__ import annotations

import asyncio
import errno
import os
import shutil
import zipfile
from pathlib import Path, PurePosixPath

from .catalog import RuntimeTarget
from .download import DownloadError, Fetcher, ProgressFn
from .store import (
    VoicePaths,
    remove_tree,
    run_to_completion,
    runtime_ready,
    write_runtime_marker,
)


def _safe_member(name: str) -> PurePosixPath | None:
    """Relative path of a wheel member to extract, or ``None`` to skip it.

    ``*.data/`` holds scripts and duplicate DLLs the import never needs.
    ``sherpa_onnx.libs/`` must stay: the Linux extension's RPATH points at it.
    """
    if name.endswith("/") or "\\" in name:
        return None
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return None
    if any(":" in part for part in path.parts) or path.parts[0].endswith(".data"):
        return None
    return path


def _extract(wheel: Path, dest: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        for info in archive.infolist():
            member = _safe_member(info.filename)
            if member is None:
                continue
            target = dest.joinpath(*member.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out, 1024 * 1024)


async def install_runtime(
    paths: VoicePaths,
    target: RuntimeTarget,
    fetcher: Fetcher,
    on_progress: ProgressFn,
) -> Path:
    """Return the ready runtime dir, installing it first if needed."""
    final = paths.runtime_dir(target.key)
    if runtime_ready(paths, target):
        for wheel in target.wheels:
            on_progress(wheel.name, wheel.size)
        return final
    downloads = paths.wheel_downloads
    wheels = [await fetcher.fetch(w, downloads, on_progress) for w in target.wheels]
    staging = paths.runtime_root / f".staging-{target.key}-{os.getpid()}"

    def unpack() -> None:
        remove_tree(staging)
        staging.mkdir(parents=True)
        for wheel in wheels:
            _extract(wheel, staging)
        write_runtime_marker(staging, target)
        remove_tree(final)
        os.replace(staging, final)

    try:
        await run_to_completion(unpack)
    except (OSError, zipfile.BadZipFile) as exc:
        await asyncio.to_thread(remove_tree, staging)
        if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
            raise DownloadError("disk_full", "The disk is full.") from exc
        raise DownloadError("io_error", f"Could not unpack the speech runtime: {exc}") from exc
    await asyncio.to_thread(remove_tree, downloads)
    return final
