"""Cover the staged-binary auto-updater state machine.

The download / install paths are stubbed via injected ``downloader`` /
``installer`` callables — what we exercise is signature verification, idle
deferral, and the atomic swap of ``current_version``.
"""
from __future__ import annotations

import base64
import json
import sys
import tarfile
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mobaide.agent.updater import (
    InstallLayout,
    StagedBinaryUpdater,
)
from mobaide.manifest_signing import canonical_json


def _signed_manifest(temp_files_dir: Path):
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes_raw()
    public_key_b64 = base64.b64encode(public_bytes).decode()

    tarball_path = temp_files_dir / "agent.tar.gz"
    wheel_path = temp_files_dir / "agent.whl"
    tarball_path.parent.mkdir(parents=True, exist_ok=True)

    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="manifest.txt")
        data = b"hello"
        info.size = len(data)
        tar.addfile(info, BytesIO(data))
    tarball_path.write_bytes(buf.getvalue())
    wheel_path.write_bytes(b"PK\x03\x04 fake wheel")

    import hashlib

    body = {
        "version": "0.2.0",
        "tarball_url": f"file://{tarball_path}",
        "tarball_sha256": hashlib.sha256(tarball_path.read_bytes()).hexdigest(),
        "wheel_url": f"file://{wheel_path}",
        "wheel_sha256": hashlib.sha256(wheel_path.read_bytes()).hexdigest(),
        "released_at": "2026-04-26T00:00:00+00:00",
    }
    signature = private_key.sign(canonical_json(body))
    signature_b64 = base64.b64encode(signature).decode()
    record = {"body": body, "signature_b64": signature_b64}
    return public_key_b64, record


@pytest.fixture
def layout(tmp_path: Path) -> InstallLayout:
    layout = InstallLayout(prefix=tmp_path / "mobaide")
    layout.releases_dir.mkdir(parents=True, exist_ok=True)
    layout.trust_dir.mkdir(parents=True, exist_ok=True)
    return layout


async def test_no_change_when_versions_match(layout) -> None:
    public_key_b64, record = _signed_manifest(layout.prefix / "tmp")
    manifest_url = layout.prefix / "manifest.json"
    manifest_url.write_text(json.dumps(record))
    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.2.0",
        installer=lambda venv, wheel: None,
    )
    outcome = await updater.consider_manifest(
        f"file://{manifest_url}", record["signature_b64"], record["body"]["version"],
    )
    assert outcome == "no_change"


async def test_signature_failed_blocks_swap(layout) -> None:
    _, record = _signed_manifest(layout.prefix / "tmp")
    manifest_url = layout.prefix / "manifest.json"
    manifest_url.write_text(json.dumps(record))
    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=base64.b64encode(b"\x00" * 32).decode(),
        session_manager=None,
        current_version="0.1.0",
        installer=lambda venv, wheel: None,
    )
    outcome = await updater.consider_manifest(
        f"file://{manifest_url}", record["signature_b64"], record["body"]["version"],
    )
    assert outcome == "signature_failed"
    assert not (layout.releases_dir / "0.2.0").exists()
    assert not layout.current_version_file.exists()


async def test_deferred_when_session_active(layout, monkeypatch) -> None:
    public_key_b64, record = _signed_manifest(layout.prefix / "tmp")
    manifest_url = layout.prefix / "manifest.json"
    manifest_url.write_text(json.dumps(record))

    busy_session = MagicMock()
    busy_session.state = "running"
    sm = MagicMock()
    sm.list_sessions.return_value = [busy_session]

    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=sm,
        current_version="0.1.0",
        installer=lambda venv, wheel: None,
    )
    outcome = await updater.consider_manifest(
        f"file://{manifest_url}", record["signature_b64"], record["body"]["version"],
    )
    assert outcome == "deferred"


async def test_staged_swap_succeeds(layout, monkeypatch) -> None:
    public_key_b64, record = _signed_manifest(layout.prefix / "tmp")
    manifest_url = layout.prefix / "manifest.json"
    manifest_url.write_text(json.dumps(record))

    exits: list[int] = []

    def fake_exit(code: int) -> None:
        exits.append(code)

    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        installer=lambda venv, wheel: venv.mkdir(parents=True, exist_ok=True),
        exit_func=fake_exit,
    )
    outcome = await updater.consider_manifest(
        f"file://{manifest_url}", record["signature_b64"], record["body"]["version"],
    )
    assert outcome == "staged"
    assert (layout.releases_dir / "0.2.0").is_dir()
    assert layout.current_version_file.read_text().strip() == "0.2.0"
    assert exits == [75]
