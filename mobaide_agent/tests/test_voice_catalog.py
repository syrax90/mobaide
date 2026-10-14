"""The pinned voice catalog: model and runtime wheel entries are internally
consistent, and ``resolve_runtime`` picks the right sherpa-onnx wheel pair
(or a coded refusal) for every interpreter/platform combination."""
from __future__ import annotations

import hashlib
import io
import re
import subprocess
import sys
import wave
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import mobaide
from mobaide.voice import catalog
from mobaide.voice.catalog import (
    FAKE_CONTENT,
    FAKE_MODEL,
    MODELS,
    PARAKEET_V3,
    RUNTIME_VERSION,
    SUPPORTED_PYTHON,
    resolve_runtime,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _pin_host(
    monkeypatch,
    *,
    platform: str = "darwin",
    machine: str = "arm64",
    version: tuple[int, int] = (3, 12),
    implementation: str = "cpython",
    gil_disabled: int = 0,
    libc: tuple[str, str] = ("glibc", "2.31"),
    win_platform: str = "win-amd64",
) -> None:
    monkeypatch.setattr(
        catalog,
        "sys",
        SimpleNamespace(
            platform=platform,
            version_info=(*version, 0, "final", 0),
            implementation=SimpleNamespace(name=implementation),
        ),
    )
    monkeypatch.setattr(
        catalog,
        "sysconfig",
        SimpleNamespace(
            get_config_var=lambda name: gil_disabled if name == "Py_GIL_DISABLED" else None,
            get_platform=lambda: win_platform,
        ),
    )
    monkeypatch.setattr(
        catalog,
        "platform",
        SimpleNamespace(machine=lambda: machine, libc_ver=lambda: libc),
    )


class TestModelCatalog:
    def test_default_model_is_in_the_catalog(self):
        assert catalog.DEFAULT_MODEL_ID in MODELS

    @pytest.mark.parametrize("spec", [PARAKEET_V3, FAKE_MODEL], ids=lambda s: s.id)
    def test_engine_files_and_selftest_clip_are_catalog_files(self, spec):
        names = [f.name for f in spec.files]

        assert len(names) == len(set(names))
        assert spec.selftest_file in names
        assert set(spec.engine_files.values()) <= set(names)

    def test_parakeet_files_are_pinned_with_mirror_first_then_hugging_face(self):
        for file in PARAKEET_V3.files:
            assert _SHA256.match(file.sha256), file.name
            assert file.size > 0
            mirror, fallback = file.urls
            assert mirror.startswith(
                "https://github.com/syrax90/mobaide-agent-releases/releases/download/"
            )
            assert mirror.endswith("/" + file.name)
            assert fallback.startswith("https://huggingface.co/")
            # A pinned revision, never a moving branch.
            assert "/resolve/main/" not in fallback

    def test_download_bytes_sums_the_files(self):
        assert PARAKEET_V3.download_bytes == sum(f.size for f in PARAKEET_V3.files)

    def test_fingerprint_changes_when_a_file_is_re_pinned(self):
        first, *rest = PARAKEET_V3.files
        repinned = replace(PARAKEET_V3, files=(replace(first, sha256="0" * 64), *rest))

        assert repinned.fingerprint != PARAKEET_V3.fingerprint

    def test_fingerprint_ignores_file_order(self):
        reordered = replace(PARAKEET_V3, files=tuple(reversed(PARAKEET_V3.files)))

        assert reordered.fingerprint == PARAKEET_V3.fingerprint

    def test_fake_model_entries_match_the_served_content(self):
        assert FAKE_MODEL.needs_runtime is False
        assert FAKE_MODEL.engine == "fake"
        for file in FAKE_MODEL.files:
            blob = FAKE_CONTENT[file.name]
            assert file.size == len(blob)
            assert file.sha256 == hashlib.sha256(blob).hexdigest()

    def test_fake_selftest_clip_is_16k_mono_pcm16(self):
        with wave.open(io.BytesIO(FAKE_CONTENT[FAKE_MODEL.selftest_file]), "rb") as clip:
            assert clip.getnchannels() == 1
            assert clip.getsampwidth() == 2
            assert clip.getframerate() == 16000
            assert clip.getnframes() > 0


class TestLazyFakeModel:
    def test_importing_the_catalog_and_the_service_builds_no_fake_model(self):
        code = (
            "import mobaide.voice.service\n"
            "from mobaide.voice import catalog\n"
            "print(catalog._fake.cache_info().currsize)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(mobaide.__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "0"

    def test_fake_model_is_built_once_under_its_id(self):
        assert catalog.FAKE_MODEL.id == catalog.FAKE_MODEL_ID
        assert catalog.FAKE_MODEL is catalog.FAKE_MODEL
        assert catalog.FAKE_CONTENT is catalog.FAKE_CONTENT

    def test_unknown_module_attribute_still_raises(self):
        assert not hasattr(catalog, "NO_SUCH_NAME")


class TestRuntimeWheels:
    def test_every_wheel_is_pinned_to_the_runtime_version(self):
        for wheel in catalog._RUNTIME_WHEELS:
            assert wheel.name.endswith(".whl")
            assert f"-{RUNTIME_VERSION}-" in wheel.name
            assert _SHA256.match(wheel.sha256), wheel.name
            (url,) = wheel.urls
            assert url.startswith("https://files.pythonhosted.org/")
            assert url.endswith("/" + wheel.name)

    @pytest.mark.parametrize(
        ("platform", "machine", "core_tag"),
        [
            ("darwin", "arm64", "macosx_11_0_arm64"),
            ("darwin", "x86_64", "macosx_10_15_x86_64"),
            ("linux", "x86_64", "manylinux2014_x86_64"),
            ("linux", "aarch64", "manylinux2014_aarch64"),
            ("win32", "amd64", "win_amd64"),
            ("win32", "arm64", "win_arm64"),
        ],
    )
    @pytest.mark.parametrize("version", SUPPORTED_PYTHON, ids=lambda v: f"py{v[0]}{v[1]}")
    def test_every_supported_host_resolves_a_core_and_binding_pair(
        self, monkeypatch, platform, machine, core_tag, version
    ):
        _pin_host(
            monkeypatch,
            platform=platform,
            machine=machine,
            version=version,
            win_platform=f"win-{machine}",
        )

        target, reason = resolve_runtime()

        py_tag = f"cp{version[0]}{version[1]}"
        assert reason is None
        assert target is not None
        assert target.key == f"sherpa-onnx-{RUNTIME_VERSION}-{py_tag}-{core_tag}"
        core, binding = target.wheels
        assert core.name == f"sherpa_onnx_core-{RUNTIME_VERSION}-py3-none-{core_tag}.whl"
        assert binding.name.startswith(f"sherpa_onnx-{RUNTIME_VERSION}-{py_tag}-{py_tag}-")
        assert target.download_bytes == core.size + binding.size


class TestResolveRuntimeRefusals:
    def test_pypy_is_python_unsupported(self, monkeypatch):
        _pin_host(monkeypatch, implementation="pypy")

        assert resolve_runtime() == (None, "python_unsupported")

    @pytest.mark.parametrize("version", [(3, 11), (3, 15)])
    def test_python_outside_the_wheel_set_is_unsupported(self, monkeypatch, version):
        _pin_host(monkeypatch, version=version)

        assert resolve_runtime() == (None, "python_unsupported")

    def test_free_threaded_build_is_unsupported(self, monkeypatch):
        _pin_host(monkeypatch, gil_disabled=1)

        assert resolve_runtime() == (None, "python_unsupported")

    @pytest.mark.parametrize(
        ("platform", "machine"),
        [("linux", "riscv64"), ("freebsd14", "amd64"), ("win32", "x86")],
    )
    def test_unknown_platform_is_unsupported(self, monkeypatch, platform, machine):
        _pin_host(monkeypatch, platform=platform, machine=machine, win_platform="win32")

        assert resolve_runtime() == (None, "platform_unsupported")

    def test_musl_linux_is_libc_unsupported(self, monkeypatch):
        _pin_host(monkeypatch, platform="linux", machine="x86_64", libc=("", ""))

        assert resolve_runtime() == (None, "libc_unsupported")

    def test_glibc_older_than_2_17_is_libc_unsupported(self, monkeypatch):
        _pin_host(monkeypatch, platform="linux", machine="x86_64", libc=("glibc", "2.16"))

        assert resolve_runtime() == (None, "libc_unsupported")

    def test_glibc_2_17_is_enough(self, monkeypatch):
        _pin_host(monkeypatch, platform="linux", machine="x86_64", libc=("glibc", "2.17"))

        target, reason = resolve_runtime()

        assert reason is None and target is not None


class TestHostMachine:
    def test_linux_arm64_spelling_maps_to_aarch64(self, monkeypatch):
        _pin_host(monkeypatch, platform="linux", machine="ARM64")

        target, _ = resolve_runtime()

        assert target is not None
        assert target.key.endswith("manylinux2014_aarch64")

    def test_windows_uses_the_interpreter_architecture_not_the_os(self, monkeypatch):
        """An x64 Python emulated on ARM64 Windows must get the amd64 wheel."""
        _pin_host(monkeypatch, platform="win32", machine="ARM64", win_platform="win-amd64")

        target, _ = resolve_runtime()

        assert target is not None
        assert target.key.endswith("win_amd64")
