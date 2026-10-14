"""Pinned speech-to-text catalog: the model files and the sherpa-onnx runtime.

The catalog is trusted because it ships inside the agent wheel, whose SHA-256
is pinned in the Ed25519-signed release manifest; every download is checked
against the size and SHA-256 recorded here.
"""
from __future__ import annotations

import functools
import hashlib
import io
import platform
import sys
import sysconfig
import wave
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CatalogFile:
    """One downloadable file; ``urls`` are tried in order."""

    name: str
    size: int
    sha256: str
    urls: tuple[str, ...]


@dataclass(frozen=True)
class ModelSpec:
    id: str
    name: str
    engine: str
    languages: tuple[str, ...]
    files: tuple[CatalogFile, ...]
    selftest_file: str
    ram_bytes: int
    notice: str
    # Engine constructor arguments: role -> file name inside the model dir.
    engine_files: dict[str, str] = field(default_factory=dict, hash=False)
    needs_runtime: bool = True

    @property
    def download_bytes(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def fingerprint(self) -> str:
        """Identity of the exact pinned file set; a re-pin changes it."""
        digest = hashlib.sha256()
        for f in sorted(self.files, key=lambda f: f.name):
            digest.update(f"{f.name}:{f.size}:{f.sha256}\n".encode())
        return digest.hexdigest()


@dataclass(frozen=True)
class RuntimeTarget:
    """The sherpa-onnx wheel pair for this interpreter and platform."""

    key: str
    wheels: tuple[CatalogFile, ...]

    @property
    def download_bytes(self) -> int:
        return sum(w.size for w in self.wheels)


RUNTIME_VERSION = "1.13.8"
SUPPORTED_PYTHON = ((3, 12), (3, 13), (3, 14))
# Upper bound of an unpacked runtime (the macOS arm64 pair unpacks to ~37 MB;
# the Windows core wheel is the largest).
RUNTIME_DISK_BYTES = 60 * 1024 * 1024
MIN_GLIBC = (2, 17)


def _wheel(filename: str, size: int, sha256: str, url: str) -> CatalogFile:
    return CatalogFile(name=filename, size=size, sha256=sha256, urls=(url,))


_RUNTIME_WHEELS: tuple[CatalogFile, ...] = (
    # macosx-arm64
    _wheel("sherpa_onnx_core-1.13.8-py3-none-macosx_11_0_arm64.whl", 9552498, "9312bbd46c93e31cecd3abda9cc8e71881d86cc3da3c35c7445ab04025b9fc3e", "https://files.pythonhosted.org/packages/8c/2a/2a47b423fe009bbbcb6d7eed7bcba755d6f9ae923b5664c7208128c32d06/sherpa_onnx_core-1.13.8-py3-none-macosx_11_0_arm64.whl"),
    _wheel("sherpa_onnx-1.13.8-cp312-cp312-macosx_11_0_arm64.whl", 2146858, "04b9268c348e9f4bd61315754ad06b02a3c185d3d7770acd8e2dcee9c2e039f9", "https://files.pythonhosted.org/packages/78/6e/8d9b92e95896ec500084e706529f5637eff797246e74c498922f6aa13a53/sherpa_onnx-1.13.8-cp312-cp312-macosx_11_0_arm64.whl"),
    _wheel("sherpa_onnx-1.13.8-cp313-cp313-macosx_11_0_arm64.whl", 2147122, "2c5a1799c326715b1ee69dae3be48883ddc6fb922fb813067606f7e9a29ea234", "https://files.pythonhosted.org/packages/ce/7b/9829c8e222e6103fd3571523252974c1095c49ae7d1511babf46c4f0d6ea/sherpa_onnx-1.13.8-cp313-cp313-macosx_11_0_arm64.whl"),
    _wheel("sherpa_onnx-1.13.8-cp314-cp314-macosx_11_0_arm64.whl", 2149334, "7fa6ec595a8b4bba2b91e6f8e58647777050ac4eb9845f5a5026c20636c0e2be", "https://files.pythonhosted.org/packages/33/f5/ff7f45799a1a77f165410fce5080be2cb59cfc98375bf4a56d7d1fe80540/sherpa_onnx-1.13.8-cp314-cp314-macosx_11_0_arm64.whl"),
    # macosx-x86_64
    _wheel("sherpa_onnx_core-1.13.8-py3-none-macosx_10_15_x86_64.whl", 10878335, "917422880287baa0f0ae1bd10d9528fa78af8cc7c233d2a953a185a4fe8d83f2", "https://files.pythonhosted.org/packages/17/2d/98e309811cff9fec48ae48a051b4caab44046755f6bc58daab0c475f40cb/sherpa_onnx_core-1.13.8-py3-none-macosx_10_15_x86_64.whl"),
    _wheel("sherpa_onnx-1.13.8-cp312-cp312-macosx_10_15_x86_64.whl", 2350796, "ef218d6545a9dcc2aff7043f39026c7d78d6cf46a3876d450d508feebffc00a9", "https://files.pythonhosted.org/packages/36/e6/a19257d7c60bf04c07f8d0772b943b6596b6b10cfb0694b7794d36630d40/sherpa_onnx-1.13.8-cp312-cp312-macosx_10_15_x86_64.whl"),
    _wheel("sherpa_onnx-1.13.8-cp313-cp313-macosx_10_15_x86_64.whl", 2351015, "5f8df54514af5025b9d428403dd9a27f6fe252b5e162311bf5c47228a50d0cc5", "https://files.pythonhosted.org/packages/7d/4d/b442afd30eff64b83112b54fa866c5d2e3a5aeec13ee8bd665c7b146fba1/sherpa_onnx-1.13.8-cp313-cp313-macosx_10_15_x86_64.whl"),
    _wheel("sherpa_onnx-1.13.8-cp314-cp314-macosx_10_15_x86_64.whl", 2351239, "22f699e1fb2e8dafe02b1521ce74e0928b38cb68ad19f481380acf51e6a6965b", "https://files.pythonhosted.org/packages/f5/fb/e8a289419f38ed9eb1882d22c12a2680254e5eb945e8e2517c5b2281dd23/sherpa_onnx-1.13.8-cp314-cp314-macosx_10_15_x86_64.whl"),
    # linux-x86_64
    _wheel("sherpa_onnx_core-1.13.8-py3-none-manylinux2014_x86_64.whl", 10642497, "4da90acf435373d7b2ba9cc0be806e7e274d28f63f5780880b5dea6721626e36", "https://files.pythonhosted.org/packages/b6/ca/e27c1fb5c54b181d4a5450f3d5c789d9da02eb42dc9b20cfdab5dda4c864/sherpa_onnx_core-1.13.8-py3-none-manylinux2014_x86_64.whl"),
    _wheel("sherpa_onnx-1.13.8-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl", 4400575, "6949773017647febc0c3696dffb2c67dd3febd4737b87e3dd135a42773704a06", "https://files.pythonhosted.org/packages/13/78/2f712b7408ddbb64614c20388912414705b728516224e737b63f2adec94a/sherpa_onnx-1.13.8-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl"),
    _wheel("sherpa_onnx-1.13.8-cp313-cp313-manylinux2014_x86_64.manylinux_2_17_x86_64.whl", 4401242, "0e5d870fb0648befb94e260c11de4cf4b293b2a90a63e3a6a2869be698927bc4", "https://files.pythonhosted.org/packages/8f/eb/b77acde02d9eee359436ade9239d9ade4351b09fbe85387a293f341c19ed/sherpa_onnx-1.13.8-cp313-cp313-manylinux2014_x86_64.manylinux_2_17_x86_64.whl"),
    _wheel("sherpa_onnx-1.13.8-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.whl", 4403669, "97309e4b5d9850490085da61e35b0fca002a39ec9da93cd9e21d2d0dcc1cc62a", "https://files.pythonhosted.org/packages/2d/e2/b1af63c1f6a9e0025cbc9aa3b1814ba97ede63db0ad9f297bb9f268fdfc2/sherpa_onnx-1.13.8-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.whl"),
    # linux-aarch64
    _wheel("sherpa_onnx_core-1.13.8-py3-none-manylinux2014_aarch64.whl", 13353426, "a51a03d55c376c32e15dd63ea852236042ba91a5cd73e253bbf7cf21f6582ba9", "https://files.pythonhosted.org/packages/9c/72/02227b14cd3fb3d504a8814ad144ed321a53331948c893821e02f4abb1d7/sherpa_onnx_core-1.13.8-py3-none-manylinux2014_aarch64.whl"),
    _wheel("sherpa_onnx-1.13.8-cp312-cp312-manylinux2014_aarch64.manylinux_2_17_aarch64.whl", 4176945, "2520b1e7b779a29a493a8cec893612218dfad7304786bf3f64df2ff7ac6c3302", "https://files.pythonhosted.org/packages/1f/5f/22e1571146b2c0b581657562d5919b69da13ed93dc70049f42d45fe9e84c/sherpa_onnx-1.13.8-cp312-cp312-manylinux2014_aarch64.manylinux_2_17_aarch64.whl"),
    _wheel("sherpa_onnx-1.13.8-cp313-cp313-manylinux2014_aarch64.manylinux_2_17_aarch64.whl", 4177700, "916ec38242e779ee3a99f7b161bd1bf3b02a7fad34cb073d091021c0469e0b58", "https://files.pythonhosted.org/packages/13/e3/115476caa9f80cd5f55e4e7b777a6c2d01a133d10f8fae574ba869fc48c3/sherpa_onnx-1.13.8-cp313-cp313-manylinux2014_aarch64.manylinux_2_17_aarch64.whl"),
    _wheel("sherpa_onnx-1.13.8-cp314-cp314-manylinux2014_aarch64.manylinux_2_17_aarch64.whl", 4182835, "45f16c9fabc3d7ce31ba9722ff8196627ddcaa5ffd02413ad0664ad1952ba419", "https://files.pythonhosted.org/packages/4f/9a/51821829b5735b3d7ce607992a62fe93d5f7215cda324c11191c77d49b9b/sherpa_onnx-1.13.8-cp314-cp314-manylinux2014_aarch64.manylinux_2_17_aarch64.whl"),
    # win-amd64
    _wheel("sherpa_onnx_core-1.13.8-py3-none-win_amd64.whl", 16903581, "5579e80196d516e6dae23c8f629292ce3142ab8869925d32b94612fd86f93733", "https://files.pythonhosted.org/packages/94/38/64356ad97f68fffcf01fe7545407ad18a2ea86c1ffae5b0950f7fac73638/sherpa_onnx_core-1.13.8-py3-none-win_amd64.whl"),
    _wheel("sherpa_onnx-1.13.8-cp312-cp312-win_amd64.whl", 2287856, "566e49ad3fb2aa8ceb723a52f9809aa6c2ee0a5eb3b0946c3fd2374af807a5c6", "https://files.pythonhosted.org/packages/24/af/33e9dcaa527bbc0c2526959713626607a46da2d7b19871b7773d8607d356/sherpa_onnx-1.13.8-cp312-cp312-win_amd64.whl"),
    _wheel("sherpa_onnx-1.13.8-cp313-cp313-win_amd64.whl", 2288187, "195455d8d6cc49f616e9d459b7d08f10a32daf2ddcbe90d4d479a8ba9aab28bf", "https://files.pythonhosted.org/packages/52/1f/a96759b33dab99647e57f94040d678c332e0d7eb595127d1e0b4e0b9af27/sherpa_onnx-1.13.8-cp313-cp313-win_amd64.whl"),
    _wheel("sherpa_onnx-1.13.8-cp314-cp314-win_amd64.whl", 2353481, "1ced230bdd02f4d50106ce5c41e5f9142b45248e67ca532c84439d0475932bb8", "https://files.pythonhosted.org/packages/24/a5/100a7c200e696b2756838b0bdb83e9cf96f62e524d44d43daba0a8e9d6fe/sherpa_onnx-1.13.8-cp314-cp314-win_amd64.whl"),
    # win-arm64
    _wheel("sherpa_onnx_core-1.13.8-py3-none-win_arm64.whl", 16067491, "85e9b12e9e985d73ad9165a1122c396e2c9384256a4023cd7cf9c91bc8d32073", "https://files.pythonhosted.org/packages/e4/e0/236bde7b6b9909d2f7f4282b9e18f5fa8d64b841e63265b508a86c3687a8/sherpa_onnx_core-1.13.8-py3-none-win_arm64.whl"),
    _wheel("sherpa_onnx-1.13.8-cp312-cp312-win_arm64.whl", 2249666, "1613f14db38b6936098c660f610baee95541378edbdec39a973a591d8df58f5b", "https://files.pythonhosted.org/packages/ea/0e/237f9baaa3da5c029a06803a7587d4717ec191284d6f3972c679683ad6d8/sherpa_onnx-1.13.8-cp312-cp312-win_arm64.whl"),
    _wheel("sherpa_onnx-1.13.8-cp313-cp313-win_arm64.whl", 2245867, "15e2d2ac2524e42efb40057f6e1d7cc53529417e791f5cf1bdd721a0ec2b1f7e", "https://files.pythonhosted.org/packages/71/96/86a9571b95336e9968519acedcb33f17fb4c3512e961344f6daed47636cc/sherpa_onnx-1.13.8-cp313-cp313-win_arm64.whl"),
    _wheel("sherpa_onnx-1.13.8-cp314-cp314-win_arm64.whl", 2314981, "ecce26fe96e4b733356568fac1e99df7038e9871b94b70d9f731e2ea8656db67", "https://files.pythonhosted.org/packages/1f/38/ce056f2cdcfa9abcf40ea572cc8ece2fac4fe19a5fe2f1ec589cf71e412c/sherpa_onnx-1.13.8-cp314-cp314-win_arm64.whl"),
)
_WHEELS_BY_NAME = {w.name: w for w in _RUNTIME_WHEELS}

# (sys.platform, machine) -> (sherpa_onnx platform tag, sherpa_onnx_core platform tag)
_PLATFORM_TAGS: dict[tuple[str, str], tuple[str, str]] = {
    ("darwin", "arm64"): ("macosx_11_0_arm64", "macosx_11_0_arm64"),
    ("darwin", "x86_64"): ("macosx_10_15_x86_64", "macosx_10_15_x86_64"),
    ("linux", "x86_64"): (
        "manylinux2014_x86_64.manylinux_2_17_x86_64", "manylinux2014_x86_64",
    ),
    ("linux", "aarch64"): (
        "manylinux2014_aarch64.manylinux_2_17_aarch64", "manylinux2014_aarch64",
    ),
    ("win32", "amd64"): ("win_amd64", "win_amd64"),
    ("win32", "arm64"): ("win_arm64", "win_arm64"),
}


def _host_machine() -> str:
    if sys.platform == "win32":
        # sysconfig reports the interpreter's architecture: an x64 Python
        # emulated on ARM64 Windows must get the amd64 wheel, which
        # ``platform.machine()`` (the OS architecture) would not pick.
        plat = sysconfig.get_platform()
        return plat.split("-", 1)[1].lower() if "-" in plat else "x86"
    # Every pinned wheel is 64-bit, while ``platform.machine()`` names the
    # kernel's architecture: a 32-bit Python on a 64-bit kernel (32-bit
    # Raspberry Pi OS on a Pi 4/5, an armhf or i386 container) must not match.
    if sys.maxsize <= 2**32:
        return "32bit"
    machine = platform.machine().lower()
    if sys.platform.startswith("linux") and machine == "arm64":
        return "aarch64"
    return machine


def _glibc_version() -> tuple[int, int] | None:
    libc, version = platform.libc_ver()
    if libc != "glibc":
        return None
    try:
        major, minor = version.split(".")[:2]
        return int(major), int(minor)
    except ValueError:
        return None


def resolve_runtime() -> tuple[RuntimeTarget | None, str | None]:
    """``(target, None)`` when a pinned wheel pair fits this host, else
    ``(None, reason_code)`` with ``python_unsupported`` |
    ``platform_unsupported`` | ``libc_unsupported``."""
    if sys.implementation.name != "cpython":
        return None, "python_unsupported"
    if sys.version_info[:2] not in SUPPORTED_PYTHON:
        return None, "python_unsupported"
    if sysconfig.get_config_var("Py_GIL_DISABLED"):
        return None, "python_unsupported"
    family = "linux" if sys.platform.startswith("linux") else sys.platform
    tags = _PLATFORM_TAGS.get((family, _host_machine()))
    if tags is None:
        return None, "platform_unsupported"
    if family == "linux":
        glibc = _glibc_version()
        if glibc is None or glibc < MIN_GLIBC:
            return None, "libc_unsupported"
    py_tag = f"cp{sys.version_info[0]}{sys.version_info[1]}"
    onnx_tag, core_tag = tags
    binding = _WHEELS_BY_NAME[f"sherpa_onnx-{RUNTIME_VERSION}-{py_tag}-{py_tag}-{onnx_tag}.whl"]
    core = _WHEELS_BY_NAME[f"sherpa_onnx_core-{RUNTIME_VERSION}-py3-none-{core_tag}.whl"]
    key = f"sherpa-onnx-{RUNTIME_VERSION}-{py_tag}-{core_tag}"
    return RuntimeTarget(key=key, wheels=(core, binding)), None


_PARAKEET_MIRROR = (
    "https://github.com/syrax90/mobaide-agent-releases/releases/download/"
    "voice-model-parakeet-tdt-0.6b-v3-int8/"
)
_PARAKEET_HF = (
    "https://huggingface.co/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8/"
    "resolve/2bda32ec70b097a55adaa07d9a7173915b43cc78/"
)


def _parakeet_file(name: str, size: int, sha256: str, hf_path: str | None = None) -> CatalogFile:
    return CatalogFile(
        name=name,
        size=size,
        sha256=sha256,
        urls=(_PARAKEET_MIRROR + name, _PARAKEET_HF + (hf_path or name)),
    )


PARAKEET_V3 = ModelSpec(
    id="parakeet-tdt-0.6b-v3-int8",
    name="NVIDIA Parakeet TDT 0.6B v3 (int8)",
    engine="sherpa_transducer",
    languages=(
        "bg", "cs", "da", "de", "el", "en", "es", "et", "fi", "fr", "hr", "hu",
        "it", "lt", "lv", "mt", "nl", "pl", "pt", "ro", "ru", "sk", "sl", "sv",
        "uk",
    ),
    files=(
        _parakeet_file(
            "encoder.int8.onnx", 652184281,
            "acfc2b4456377e15d04f0243af540b7fe7c992f8d898d751cf134c3a55fd2247",
        ),
        _parakeet_file(
            "decoder.int8.onnx", 11845275,
            "179e50c43d1a9de79c8a24149a2f9bac6eb5981823f2a2ed88d655b24248db4e",
        ),
        _parakeet_file(
            "joiner.int8.onnx", 6355277,
            "3164c13fc2821009440d20fcb5fdc78bff28b4db2f8d0f0b329101719c0948b3",
        ),
        _parakeet_file(
            "tokens.txt", 93939,
            "d58544679ea4bc6ac563d1f545eb7d474bd6cfa467f0a6e2c1dc1c7d37e3c35d",
        ),
        _parakeet_file(
            "selftest-en.wav", 184608,
            "148b936b43ce7c546a866e64da059f0458aee2d65e617f16e9d94f06e8d99ed6",
            hf_path="test_wavs/en.wav",
        ),
    ),
    selftest_file="selftest-en.wav",
    # Peak RSS of the helper measured over a 115 s dictation (segments are
    # capped at 25 s, which bounds it); idle costs nothing.
    ram_bytes=2_000_000_000,
    notice=(
        "Parakeet TDT 0.6B v3 (int8 ONNX), the speech-recognition model behind\n"
        "Mobaide voice input.\n"
        "\n"
        "Model: nvidia/parakeet-tdt-0.6b-v3, created by NVIDIA.\n"
        "  https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3\n"
        "License: Creative Commons Attribution 4.0 International (CC BY 4.0).\n"
        "  https://creativecommons.org/licenses/by/4.0/\n"
        "\n"
        "Changes: exported to ONNX and quantized to int8 by the k2-fsa/sherpa-onnx\n"
        "project; the self-test clip (selftest-en.wav) comes from the same\n"
        "repository.\n"
        "  https://huggingface.co/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8\n"
    ),
    engine_files={
        "encoder": "encoder.int8.onnx",
        "decoder": "decoder.int8.onnx",
        "joiner": "joiner.int8.onnx",
        "tokens": "tokens.txt",
    },
)

MODELS: dict[str, ModelSpec] = {PARAKEET_V3.id: PARAKEET_V3}
DEFAULT_MODEL_ID = PARAKEET_V3.id


FAKE_MODEL_ID = "fake-stt"


def _fake_blob(size: int) -> bytes:
    seed = b"mobaide-fake-voice-model:"
    return (seed * (size // len(seed) + 1))[:size]


def _fake_selftest_wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)
    return buf.getvalue()


@functools.cache
def _fake() -> tuple[dict[str, bytes], ModelSpec]:
    content = {
        "fake-model.bin": _fake_blob(2 * 1024 * 1024),
        "selftest.wav": _fake_selftest_wav(),
    }
    model = ModelSpec(
        id=FAKE_MODEL_ID,
        name="Fake speech-to-text (testing)",
        engine="fake",
        languages=("en",),
        files=tuple(
            CatalogFile(
                name=name,
                size=len(blob),
                sha256=hashlib.sha256(blob).hexdigest(),
                urls=(f"fake://{name}",),
            )
            for name, blob in content.items()
        ),
        selftest_file="selftest.wav",
        ram_bytes=50_000_000,
        notice="Fake speech-to-text model used for testing; it transcribes nothing.\n",
        needs_runtime=False,
    )
    return content, model


def __getattr__(name: str) -> object:
    # FAKE_CONTENT (the bytes ``download.FakeFetcher`` serves) and FAKE_MODEL
    # are built on first use: a production agent never pays for them.
    if name == "FAKE_CONTENT":
        return _fake()[0]
    if name == "FAKE_MODEL":
        return _fake()[1]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
