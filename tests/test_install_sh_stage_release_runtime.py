"""Behavioural tests for ``download_asset`` and ``stage_release`` in
``deploy/install.sh``.

``stage_release`` runs for real against ``file://`` assets: curl is never
reached, and ``resolve_base_python`` is swapped for a stub interpreter whose
``-m venv`` writes a fake ``pip`` and a fake ``python`` that log their
arguments. That keeps the test offline while still covering the download,
sha256 check, extraction, the dependency-lock check, ``.installed`` sentinel,
``current_version`` pin and the move-aside / restore of a release that is
being reinstalled.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
from io import BytesIO
from pathlib import Path

import pytest

from test_install_script_trap import _BASH, _sh_function


pytestmark = [
    pytest.mark.skipif(_BASH is None, reason="bash unavailable on this platform"),
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX stub interpreter"),
]

VERSION = "9.9.9"
WHEEL_NAME = f"mobaide_agent-{VERSION}-py3-none-any.whl"
# Stands in for the hash-pinned lock at the tarball root; the fake
# interpreter never gives it to a real pip.
LOCK_TEXT = "example-dependency==1.0 --hash=sha256:" + "0" * 64 + "\n"


def _run_download_asset(url: str, dest: Path) -> subprocess.CompletedProcess[str]:
    assert _BASH is not None
    harness = (
        "set -euo pipefail\n"
        "fail() { printf '[ERROR] %s\\n' \"$*\" >&2; exit 1; }\n"
        + _sh_function("download_asset")
        + f"download_asset {shlex.quote(url)} {shlex.quote(str(dest))} ua/1\n"
    )
    return subprocess.run([_BASH, "-c", harness], capture_output=True, text=True)


@pytest.mark.parametrize("scheme", ["file://", ""], ids=["file-url", "absolute-path"])
def test_download_asset_copies_local_sources_without_curl(tmp_path, scheme) -> None:
    src = tmp_path / "wheel.whl"
    src.write_bytes(b"canonical wheel payload")
    dest = tmp_path / "staged.download"

    proc = _run_download_asset(f"{scheme}{src}", dest)

    assert proc.returncode == 0, proc.stderr
    assert dest.read_bytes() == b"canonical wheel payload"


def test_download_asset_fails_on_a_missing_local_source(tmp_path) -> None:
    proc = _run_download_asset(f"file://{tmp_path / 'missing.whl'}", tmp_path / "out")

    assert proc.returncode != 0


def _assets(
    tmp_path: Path,
    *,
    corrupt_wheel_sha: bool = False,
    lock_in_tarball: str | None = LOCK_TEXT,
    signed_lock: str | None = LOCK_TEXT,
) -> Path:
    """``signed_lock`` is the lock text whose SHA-256 the manifest names
    (``None``: a release made before the lock existed). ``lock_in_tarball``
    is the lock the tarball really carries (``None``: no lock in it)."""
    assets = tmp_path / "assets"
    assets.mkdir()
    members = {"mobaide/__init__.py": f'__version__ = "{VERSION}"\n'.encode()}
    if lock_in_tarball is not None:
        members["requirements.lock"] = lock_in_tarball.encode()
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, payload in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(payload)
            tar.addfile(info, BytesIO(payload))
    tarball = assets / "agent.tar.gz"
    tarball.write_bytes(buf.getvalue())
    wheel = assets / WHEEL_NAME
    wheel.write_bytes(b"PK\x03\x04 placeholder wheel bytes")
    body = {
        "version": VERSION,
        "tarball_url": f"file://{tarball}",
        "tarball_sha256": hashlib.sha256(tarball.read_bytes()).hexdigest(),
        "wheel_url": f"file://{wheel}",
        "wheel_sha256": (
            "0" * 64
            if corrupt_wheel_sha
            else hashlib.sha256(wheel.read_bytes()).hexdigest()
        ),
    }
    if signed_lock is not None:
        body["dependency_lock_sha256"] = hashlib.sha256(
            signed_lock.encode()
        ).hexdigest()
    manifest = assets / "manifest.json"
    manifest.write_text(
        json.dumps({"body": body, "signature_b64": base64.b64encode(b"x").decode()})
    )
    return manifest


def _fake_base_python(tmp_path: Path, pip_log: Path, wheel_rc: int) -> Path:
    """``-m venv DIR`` creates ``DIR/bin/pip`` and ``DIR/bin/python``.

    The pip logs its argv and exits ``wheel_rc`` for the wheel install. The
    python (the hash-pinned path runs ``python -m pip``) logs its argv with a
    ``python`` prefix, keeps a copy of the requirements file (its last
    argument, in a temp dir that ``stage_release`` removes) and exits
    ``wheel_rc`` too."""
    py = tmp_path / "fake-python"
    py.write_text(
        "#!/bin/sh\n"
        '[ "$1" = "-m" ] && [ "$2" = "venv" ] || exit 2\n'
        'mkdir -p "$3/bin"\n'
        "cat > \"$3/bin/pip\" <<'PIP'\n"
        "#!/bin/sh\n"
        f"echo \"$*\" >> '{pip_log}'\n"
        f'case "$*" in *.whl*) exit {wheel_rc} ;; esac\n'
        "exit 0\n"
        "PIP\n"
        "cat > \"$3/bin/python\" <<'PYTHON'\n"
        "#!/bin/sh\n"
        f"echo \"python $*\" >> '{pip_log}'\n"
        'for last in "$@"; do :; done\n'
        f"cp \"$last\" '{tmp_path / 'requirements.copy'}'\n"
        f"exit {wheel_rc}\n"
        "PYTHON\n"
        'chmod +x "$3/bin/pip" "$3/bin/python"\n',
        encoding="utf-8",
    )
    py.chmod(0o755)
    return py


def _stage_release(
    tmp_path: Path, prefix: Path, manifest: Path, *, wheel_rc: int = 0
) -> tuple[subprocess.CompletedProcess[str], Path]:
    assert _BASH is not None
    pip_log = tmp_path / "pip.log"
    fake_py = _fake_base_python(tmp_path, pip_log, wheel_rc)
    harness = (
        "set -euo pipefail\n"
        f"PATH={shlex.quote(os.path.dirname(sys.executable))}:\"$PATH\"\n"
        f"MOBAIDE_AGENT_PREFIX={shlex.quote(str(prefix))}\n"
        "info() { :; }\n"
        "warn() { printf '[WARN] %s\\n' \"$*\" >&2; }\n"
        "fail() { printf '[ERROR] %s\\n' \"$*\" >&2; exit 1; }\n"
        + _sh_function("download_asset")
        + _sh_function("cleanup_on_exit")
        + _sh_function("stage_release")
        + f"resolve_base_python() {{ printf '%s' {shlex.quote(str(fake_py))}; }}\n"
        + "trap cleanup_on_exit EXIT\n"
        + f"stage_release {shlex.quote(str(manifest))}\n"
    )
    proc = subprocess.run([_BASH, "-c", harness], capture_output=True, text=True)
    return proc, pip_log


def _existing_release(prefix: Path) -> Path:
    release = prefix / "releases" / VERSION
    release.mkdir(parents=True)
    (release / ".installed").write_text("old")
    (release / "previous-marker").write_text("keep me")
    (prefix / "current_version").write_text(f"{VERSION}\n")
    return release


def _scratch_dirs(prefix: Path) -> list[str]:
    return [p.name for p in (prefix / "releases").iterdir() if p.name.startswith(".")]


def test_fresh_install_stages_marks_complete_and_pins_the_version(tmp_path) -> None:
    prefix = tmp_path / "prefix"

    proc, _ = _stage_release(tmp_path, prefix, _assets(tmp_path))

    assert proc.returncode == 0, proc.stderr
    release = prefix / "releases" / VERSION
    assert (release / "mobaide" / "__init__.py").is_file()
    assert (release / ".installed").read_text().strip() == VERSION
    assert (prefix / "current_version").read_text().strip() == VERSION
    assert WHEEL_NAME in (tmp_path / "requirements.copy").read_text()


def test_successful_reinstall_drops_the_moved_aside_release(tmp_path) -> None:
    prefix = tmp_path / "prefix"
    release = _existing_release(prefix)

    proc, _ = _stage_release(tmp_path, prefix, _assets(tmp_path))

    assert proc.returncode == 0, proc.stderr
    assert not (release / "previous-marker").exists()
    assert (release / ".installed").read_text().strip() == VERSION
    assert _scratch_dirs(prefix) == []


def test_release_with_a_dependency_lock_installs_only_from_the_pinned_file(
    tmp_path,
) -> None:
    """A manifest that names a dependency lock gets one install step: pip
    with ``--require-hashes`` on a file that holds the signed lock plus the
    wheel pinned by its signed SHA-256. No other pip step runs."""
    prefix = tmp_path / "prefix"
    manifest = _assets(tmp_path)

    proc, pip_log = _stage_release(tmp_path, prefix, manifest)

    assert proc.returncode == 0, proc.stderr
    installs = [shlex.split(line) for line in pip_log.read_text().splitlines()]
    assert len(installs) == 1
    (install,) = installs
    assert install[:4] == ["python", "-m", "pip", "install"]
    assert "--require-hashes" in install
    assert install[install.index("--only-binary") + 1] == ":all:"
    assert install[-2] == "-r"

    requirements = (tmp_path / "requirements.copy").read_text()
    wheel_sha = json.loads(manifest.read_text())["body"]["wheel_sha256"]
    assert requirements.startswith(LOCK_TEXT)
    assert re.fullmatch(
        rf"mobaide-agent @ file://\S*/{re.escape(WHEEL_NAME)} --hash=sha256:{wheel_sha}",
        requirements[len(LOCK_TEXT):].strip(),
    )
    assert (prefix / "releases" / VERSION / ".installed").read_text().strip() == VERSION
    assert (prefix / "current_version").read_text().strip() == VERSION


@pytest.mark.parametrize(
    "fault",
    [
        "wheel-sha-mismatch",
        "lock-not-in-signed-release",
        "lock-differs-from-signed-hash",
        "lock-missing-from-tarball",
        "hash-pinned-install-fails",
    ],
)
def test_failed_reinstall_restores_the_previous_release(tmp_path, fault) -> None:
    prefix = tmp_path / "prefix"
    release = _existing_release(prefix)
    if fault == "lock-differs-from-signed-hash":
        manifest = _assets(
            tmp_path,
            lock_in_tarball="example-dependency==2.0 --hash=sha256:" + "1" * 64 + "\n",
        )
    elif fault == "lock-missing-from-tarball":
        manifest = _assets(tmp_path, lock_in_tarball=None)
    elif fault == "lock-not-in-signed-release":
        # A release from before the lock: nothing in it pins the
        # dependencies, so it is refused rather than installed unchecked.
        manifest = _assets(tmp_path, signed_lock=None)
    else:
        manifest = _assets(tmp_path, corrupt_wheel_sha=fault == "wheel-sha-mismatch")

    proc, pip_log = _stage_release(
        tmp_path,
        prefix,
        manifest,
        wheel_rc=1 if fault == "hash-pinned-install-fails" else 0,
    )

    assert proc.returncode != 0
    if fault == "wheel-sha-mismatch":
        assert "sha256 mismatch" in proc.stderr
    if fault.startswith("lock-"):
        # The lock is checked before any install step runs.
        assert not pip_log.exists()
    assert (release / "previous-marker").read_text() == "keep me"
    assert (release / ".installed").read_text() == "old"
    assert _scratch_dirs(prefix) == []
    assert (prefix / "current_version").read_text().strip() == VERSION
