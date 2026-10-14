"""The pip-less sherpa-onnx runtime install: wheel members are unpacked into
``runtime/<key>/`` except ``*.data/`` and anything that would escape the
directory, ``sherpa_onnx.libs/`` is kept, and a ready runtime is never
fetched again."""
from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from mobaide.voice.catalog import CatalogFile, RuntimeTarget
from mobaide.voice.download import DownloadError
from mobaide.voice.runtime import _safe_member, install_runtime
from mobaide.voice.store import VoicePaths, runtime_ready

KEY = "sherpa-onnx-1.13.8-cp312-test"


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buf.getvalue()


CORE = _zip(
    {
        "sherpa_onnx/lib/libonnxruntime.so": b"core-lib",
        "sherpa_onnx_core-1.13.8.dist-info/METADATA": b"Name: sherpa-onnx-core",
        "sherpa_onnx_core-1.13.8.data/scripts/sherpa-onnx": b"script",
    }
)
BINDING = _zip(
    {
        "sherpa_onnx/__init__.py": b"VERSION = '1.13.8'",
        "sherpa_onnx.libs/libasound.so.2": b"alsa",
        "../escaped.txt": b"evil",
        "/absolute.txt": b"evil",
    }
)


def _wheel(name: str, blob: bytes) -> CatalogFile:
    return CatalogFile(
        name=name, size=len(blob), sha256=hashlib.sha256(blob).hexdigest(), urls=("fake://",)
    )


class _MemoryFetcher:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.blobs = blobs
        self.fetched: list[str] = []

    async def fetch(self, file: CatalogFile, directory: Path, on_progress) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        self.fetched.append(file.name)
        path = directory / file.name
        path.write_bytes(self.blobs[file.name])
        on_progress(file.name, file.size)
        return path


def _target(core: bytes = CORE, binding: bytes = BINDING) -> tuple[RuntimeTarget, _MemoryFetcher]:
    wheels = (_wheel("core.whl", core), _wheel("binding.whl", binding))
    return RuntimeTarget(key=KEY, wheels=wheels), _MemoryFetcher({"core.whl": core, "binding.whl": binding})


@pytest.fixture
def paths(tmp_path) -> VoicePaths:
    return VoicePaths(tmp_path / "voice")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("sherpa_onnx/__init__.py", "sherpa_onnx/__init__.py"),
        ("sherpa_onnx.libs/libasound.so.2", "sherpa_onnx.libs/libasound.so.2"),
        ("sherpa_onnx-1.13.8.dist-info/RECORD", "sherpa_onnx-1.13.8.dist-info/RECORD"),
        ("sherpa_onnx-1.13.8.data/scripts/x", None),
        ("sherpa_onnx/", None),
        ("../outside", None),
        ("sherpa_onnx/../../outside", None),
        ("/etc/passwd", None),
        ("C:/Windows/evil.dll", None),
        ("sherpa_onnx/C:evil.dll", None),
        ("sherpa_onnx/lib/evil.dll:stream", None),
        ("sherpa_onnx\\evil.dll", None),
    ],
)
def test_safe_member(name, expected):
    result = _safe_member(name)

    assert (None if result is None else result.as_posix()) == expected


async def test_install_unpacks_both_wheels_and_marks_the_runtime_ready(paths, tmp_path):
    target, fetcher = _target()

    final = await install_runtime(paths, target, fetcher, lambda *_: None)

    assert final == paths.runtime_dir(KEY)
    assert (final / "sherpa_onnx" / "__init__.py").read_bytes() == b"VERSION = '1.13.8'"
    assert (final / "sherpa_onnx" / "lib" / "libonnxruntime.so").read_bytes() == b"core-lib"
    assert (final / "sherpa_onnx.libs" / "libasound.so.2").read_bytes() == b"alsa"
    assert not (final / "sherpa_onnx_core-1.13.8.data").exists()
    assert runtime_ready(paths, target)


async def test_install_never_writes_outside_the_runtime_dir(paths, tmp_path):
    target, fetcher = _target()

    final = await install_runtime(paths, target, fetcher, lambda *_: None)

    assert not (paths.runtime_root / "escaped.txt").exists()
    assert not (tmp_path / "escaped.txt").exists()
    assert not (final / "absolute.txt").exists()


async def test_install_cleans_downloads_and_staging(paths):
    target, fetcher = _target()

    await install_runtime(paths, target, fetcher, lambda *_: None)

    assert not paths.wheel_downloads.exists()
    assert sorted(p.name for p in paths.runtime_root.iterdir()) == [KEY]


async def test_ready_runtime_is_not_fetched_again(paths):
    target, fetcher = _target()
    await install_runtime(paths, target, fetcher, lambda *_: None)
    fetcher.fetched.clear()
    reported: list[tuple[str, int]] = []

    await install_runtime(paths, target, fetcher, lambda name, done: reported.append((name, done)))

    assert fetcher.fetched == []
    assert reported == [(w.name, w.size) for w in target.wheels]


async def test_corrupt_wheel_fails_with_io_error_and_leaves_nothing_behind(paths):
    target, fetcher = _target(binding=b"not a zip file")

    with pytest.raises(DownloadError) as excinfo:
        await install_runtime(paths, target, fetcher, lambda *_: None)

    assert excinfo.value.code == "io_error"
    assert not paths.runtime_dir(KEY).exists()
    assert not list(paths.runtime_root.glob(".staging-*"))
