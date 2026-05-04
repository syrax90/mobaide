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
    _wheel_basename_from_url,
)
from mobaide.manifest_signing import canonical_json


def _signed_manifest(temp_files_dir: Path):
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes_raw()
    public_key_b64 = base64.b64encode(public_bytes).decode()

    tarball_path = temp_files_dir / "agent.tar.gz"
    wheel_path = temp_files_dir / "mobaide_agent-0.2.0-py3-none-any.whl"
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


async def test_staging_rejects_non_pep427_wheel_url(layout) -> None:
    # pip 25.2+ enforces PEP 427 wheel-filename validation. A manifest whose
    # `wheel_url` doesn't end in a compliant `name-version-...-platform.whl`
    # must be rejected fail-closed BEFORE pip is invoked, otherwise the bare
    # `agent.whl` placeholder used by older versions would pass through and
    # blow up at install time. The catch-all in ``consider_manifest`` maps
    # ``ValueError`` from ``_stage_release`` to ``"signature_failed"``.
    private_key = Ed25519PrivateKey.generate()
    public_key_b64 = base64.b64encode(private_key.public_key().public_bytes_raw()).decode()

    temp_files_dir = layout.prefix / "tmp"
    temp_files_dir.mkdir(parents=True, exist_ok=True)
    tarball_path = temp_files_dir / "agent.tar.gz"
    wheel_path = temp_files_dir / "agent.whl"
    import hashlib

    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="manifest.txt")
        data = b"hello"
        info.size = len(data)
        tar.addfile(info, BytesIO(data))
    tarball_path.write_bytes(buf.getvalue())
    wheel_path.write_bytes(b"PK\x03\x04 fake wheel")

    body = {
        "version": "0.2.0",
        "tarball_url": f"file://{tarball_path}",
        "tarball_sha256": hashlib.sha256(tarball_path.read_bytes()).hexdigest(),
        "wheel_url": f"file://{wheel_path}",
        "wheel_sha256": hashlib.sha256(wheel_path.read_bytes()).hexdigest(),
        "released_at": "2026-04-26T00:00:00+00:00",
    }
    sig_b64 = base64.b64encode(private_key.sign(canonical_json(body))).decode()
    manifest_url = layout.prefix / "manifest.json"
    manifest_url.write_text(json.dumps({"body": body, "signature_b64": sig_b64}))

    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        installer=lambda venv, wheel: None,
    )
    outcome = await updater.consider_manifest(
        f"file://{manifest_url}", sig_b64, body["version"],
    )
    assert outcome == "signature_failed"
    assert not (layout.releases_dir / "0.2.0").exists()
    assert not layout.current_version_file.exists()


def test_wheel_basename_from_url_extracts_canonical_name() -> None:
    # Production CDN URL — the canonical case the release workflow emits.
    # Query strings (e.g. signed download tokens) must not leak into the
    # filename pip sees.
    url = "https://cdn.example.com/releases/mobaide_agent-0.2.0-py3-none-any.whl"
    assert _wheel_basename_from_url(url) == "mobaide_agent-0.2.0-py3-none-any.whl"

    signed = (
        "https://cdn.example.com/releases/"
        "mobaide_agent-1.4.2-cp312-cp312-manylinux2014_x86_64.whl"
        "?Signature=abc123&Expires=99999"
    )
    assert (
        _wheel_basename_from_url(signed)
        == "mobaide_agent-1.4.2-cp312-cp312-manylinux2014_x86_64.whl"
    )


@pytest.mark.parametrize(
    "url",
    [
        # Wrong extension entirely — exercises the `endswith(".whl")` branch
        # which the integration-level rejection test doesn't reach (it uses
        # `agent.whl`, which passes the extension check but fails on dashes).
        "https://cdn.example.com/releases/mobaide_agent-0.2.0.tar.gz",
        "https://cdn.example.com/releases/mobaide_agent-0.2.0",
        # Compliant extension, insufficient dashes (PEP 427 minimum is 4).
        "https://cdn.example.com/releases/agent.whl",
        "https://cdn.example.com/releases/foo-bar.whl",
        "https://cdn.example.com/releases/foo-bar-baz.whl",
        "https://cdn.example.com/releases/foo-1.0-py3.whl",
    ],
)
def test_wheel_basename_from_url_rejects_non_pep427(url: str) -> None:
    with pytest.raises(ValueError, match="non-PEP427 filename"):
        _wheel_basename_from_url(url)


def test_wheel_basename_from_url_error_includes_diagnostic_url() -> None:
    # The docstring guarantees the original URL is surfaced in the exception
    # so install-time failures can be traced back to the manifest field that
    # produced them. Pin both the offending basename and the source URL.
    bad = "https://cdn.example.com/releases/agent.whl"
    with pytest.raises(ValueError) as exc_info:
        _wheel_basename_from_url(bad)
    msg = str(exc_info.value)
    assert "agent.whl" in msg
    assert bad in msg


@pytest.mark.parametrize(
    "url",
    [
        # Backslash path-traversal vector: posixpath.basename leaves `\`
        # literal, so without the separator-char guard `Path(tmpdir) / name`
        # would resolve outside ``tmpdir`` on Windows and let `_download_to`
        # write the wheel body to an attacker-chosen location.
        "https://attacker.example/legit/..\\..\\..\\Windows\\System32\\evil-1-2-3-4.whl",
        # NUL byte injection — would be silently truncated by C-string APIs
        # downstream of pip.
        "https://attacker.example/legit/foo\x00bar-1.0-py3-none-any.whl",
    ],
)
def test_wheel_basename_from_url_rejects_path_separators(url: str) -> None:
    with pytest.raises(ValueError, match="path-separator chars"):
        _wheel_basename_from_url(url)
