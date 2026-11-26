"""Cover the staged-binary auto-updater state machine.

The install path is stubbed via the injected ``venv_builder`` (pre-swap) and
``finalizer`` (post-swap pip + wheel) callables — what we exercise is signature
verification, idle deferral, the atomic swap of ``current_version``, and the
``.installed`` completion sentinel. The manifest body itself is passed in
directly (no HTTP fetch in the relay-driven update path).

Two groups run the real install path instead: the forward-only checks (an
older signed manifest is refused) and the dependency-lock checks, which drive
a real ``pip`` against local wheel files only (no index, no network).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mobaide.agent.install_paths import RELEASE_SENTINEL, is_release_complete
from mobaide.agent.trust_store import ManifestTrust, load_manifest_trust
from mobaide.agent.updater import (
    _STALE_SCRATCH_SECONDS,
    DEPENDENCY_LOCK_NAME,
    InstallLayout,
    StagedBinaryUpdater,
    _assert_safe_version,
    _build_venv_without_pip,
    _finalize_wheel_install,
    _is_version_newer,
    _release_venv_python,
    _version_tuple,
    _wheel_basename_from_url,
)
from mobaide.manifest_signing import canonical_json

# Stands in for the hash-pinned lock at the tarball root. The stubbed
# finalizer never gives it to pip, so the pinned name and hash are not real.
_LOCK_TEXT = "example-dependency==1.0 --hash=sha256:" + "0" * 64 + "\n"


def _public_key_b64(private_key: Ed25519PrivateKey) -> str:
    return base64.b64encode(private_key.public_key().public_bytes_raw()).decode()


def _sign(private_key: Ed25519PrivateKey, body: dict) -> str:
    return base64.b64encode(private_key.sign(canonical_json(body))).decode()


def _write_tarball(path: Path, members: dict[str, bytes]) -> None:
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, BytesIO(data))
    path.write_bytes(buf.getvalue())


def _signed_manifest(
    temp_files_dir: Path,
    *,
    version: str = "0.2.0",
    private_key: Ed25519PrivateKey | None = None,
    lock_text: str | None = _LOCK_TEXT,
    wheel_bytes: bytes = b"PK\x03\x04 fake wheel",
    extra: dict | None = None,
):
    """A signed manifest plus the tarball and wheel it names.

    ``lock_text`` is the dependency lock placed at the tarball root (``None``
    leaves it out of the tarball). ``extra`` is merged into the body before
    signing, so a test can override or add a signed field.
    """
    private_key = private_key or Ed25519PrivateKey.generate()

    tarball_path = temp_files_dir / "agent.tar.gz"
    wheel_path = temp_files_dir / "mobaide_agent-0.2.0-py3-none-any.whl"
    tarball_path.parent.mkdir(parents=True, exist_ok=True)

    members = {"manifest.txt": b"hello"}
    if lock_text is not None:
        members[DEPENDENCY_LOCK_NAME] = lock_text.encode("utf-8")
    _write_tarball(tarball_path, members)
    wheel_path.write_bytes(wheel_bytes)

    body = {
        "version": version,
        "tarball_url": f"file://{tarball_path}",
        "tarball_sha256": hashlib.sha256(tarball_path.read_bytes()).hexdigest(),
        "wheel_url": f"file://{wheel_path}",
        "wheel_sha256": hashlib.sha256(wheel_path.read_bytes()).hexdigest(),
        "dependency_lock_sha256": hashlib.sha256(
            (lock_text or "").encode("utf-8")
        ).hexdigest(),
        "released_at": "2026-04-26T00:00:00+00:00",
    }
    body.update(extra or {})
    return _public_key_b64(private_key), body, _sign(private_key, body)


@pytest.fixture
def layout(tmp_path: Path) -> InstallLayout:
    layout = InstallLayout(prefix=tmp_path / "mobaide")
    layout.releases_dir.mkdir(parents=True, exist_ok=True)
    layout.trust_dir.mkdir(parents=True, exist_ok=True)
    return layout


def _fake_venv_builder(venv_dir: Path) -> None:
    """Stub the pre-swap venv build: create only the interpreter the updater's
    pre-swap verification (``_release_venv_python``) probes for — the move-safe
    symlink a real ``--without-pip`` venv would have. Mirrors the layout
    ``conftest.make_release`` builds (``Scripts`` on Windows, ``bin``
    elsewhere)."""
    if sys.platform == "win32":
        bin_dir = venv_dir / "Scripts"
        python = bin_dir / "python.exe"
    else:
        bin_dir = venv_dir / "bin"
        python = bin_dir / "python"
    bin_dir.mkdir(parents=True, exist_ok=True)
    python.write_text("#!/bin/sh\nexec /bin/true\n", encoding="utf-8")


def _fake_finalizer(python: Path, requirements_path: Path) -> None:
    """Stub the post-swap pip + wheel install: create the agent console script
    that a real ``pip install`` writes (so the release reads as complete via the
    legacy fallback in ``is_release_complete``). ``python`` is the FINAL-path
    interpreter, asserting the finalize runs after the swap."""
    bin_dir = python.parent
    script = bin_dir / (
        "mobaide-agent.exe" if sys.platform == "win32" else "mobaide-agent"
    )
    script.write_text("#!/bin/sh\nexec /bin/true\n", encoding="utf-8")


async def test_no_change_when_versions_match(layout) -> None:
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")
    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.2.0",
        venv_builder=lambda venv: None,
        finalizer=lambda python, wheel: None,
    )
    outcome = await updater.consider_manifest(
        body, signature_b64, body["version"],
    )
    assert outcome == "no_change"


async def test_signature_failed_blocks_swap(layout) -> None:
    _, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")
    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=base64.b64encode(b"\x00" * 32).decode(),
        session_manager=None,
        current_version="0.1.0",
        venv_builder=lambda venv: None,
        finalizer=lambda python, wheel: None,
    )
    outcome = await updater.consider_manifest(
        body, signature_b64, body["version"],
    )
    assert outcome == "signature_failed"
    assert not (layout.releases_dir / "0.2.0").exists()
    assert not layout.current_version_file.exists()


async def test_deferred_when_session_active(layout, monkeypatch) -> None:
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")

    busy_session = MagicMock()
    busy_session.state = "running"
    sm = MagicMock()
    sm.list_sessions.return_value = [busy_session]

    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=sm,
        current_version="0.1.0",
        venv_builder=lambda venv: None,
        finalizer=lambda python, wheel: None,
    )
    outcome = await updater.consider_manifest(
        body, signature_b64, body["version"],
    )
    assert outcome == "deferred"


async def test_deferred_when_busy_probe_reports_busy(layout) -> None:
    """Non-session work (an open voice dictation) defers the restart even
    with no live session."""
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")
    installer_calls: list = []

    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        venv_builder=lambda venv: installer_calls.append(venv),
        finalizer=lambda python, wheel: None,
        busy_probe=lambda: True,
    )
    outcome = await updater.consider_manifest(
        body, signature_b64, body["version"],
    )
    assert outcome == "deferred"
    assert installer_calls == []
    assert not (layout.releases_dir / "0.2.0").exists()


async def test_failing_busy_probe_does_not_block_the_update(layout) -> None:
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")

    def broken_probe() -> bool:
        raise RuntimeError("voice service gone")

    exits: list[int] = []
    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        venv_builder=_fake_venv_builder,
        finalizer=_fake_finalizer,
        exit_func=exits.append,
        busy_probe=broken_probe,
    )
    outcome = await updater.consider_manifest(
        body, signature_b64, body["version"],
    )
    assert outcome == "staged"
    assert exits == [75]


async def test_force_update_bypasses_busy_probe(layout) -> None:
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")
    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        venv_builder=_fake_venv_builder,
        finalizer=_fake_finalizer,
        exit_func=lambda code: None,
        busy_probe=lambda: True,
    )
    outcome = await updater.force_update_now(
        body, signature_b64, body["version"], exit_after_stage=False,
    )
    assert outcome == "staged"


async def test_staged_swap_succeeds(layout, monkeypatch) -> None:
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")

    exits: list[int] = []

    def fake_exit(code: int) -> None:
        exits.append(code)

    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        venv_builder=_fake_venv_builder,
        finalizer=_fake_finalizer,
        exit_func=fake_exit,
    )
    outcome = await updater.consider_manifest(
        body, signature_b64, body["version"],
    )
    assert outcome == "staged"
    assert (layout.releases_dir / "0.2.0").is_dir()
    # The completion sentinel is written before current_version flips, so a
    # staged release is always recognised as complete by the launcher.
    assert (layout.releases_dir / "0.2.0" / RELEASE_SENTINEL).exists()
    assert is_release_complete(layout.releases_dir / "0.2.0")
    assert layout.current_version_file.read_text().strip() == "0.2.0"
    assert exits == [75]


async def test_already_staged_version_is_not_staged_again(layout) -> None:
    """The running agent keeps going for its graceful shutdown after it asks
    for the restart. A manifest for the same release in that window (a relay
    push, a forced "Install now") must not rebuild it or ask again."""
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")
    builds: list[Path] = []
    exits: list[int] = []

    def counting_builder(venv_dir: Path) -> None:
        builds.append(venv_dir)
        _fake_venv_builder(venv_dir)

    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        venv_builder=counting_builder,
        finalizer=_fake_finalizer,
        exit_func=exits.append,
    )
    first = await updater.consider_manifest(body, signature_b64, body["version"])
    again = await updater.consider_manifest(body, signature_b64, body["version"])
    forced = await updater.force_update_now(body, signature_b64, body["version"])

    assert (first, again, forced) == ("staged", "staged", "staged")
    assert len(builds) == 1
    assert exits == [75]


async def test_queued_caller_does_not_restage_the_same_release(layout) -> None:
    """A relay push and an "Install now" for the same release can overlap:
    both pass the pre-lock check, and the second waits on the lock. Once the
    first stages, the queued one must return "staged" without a rebuild."""
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")
    builds: list[Path] = []
    exits: list[int] = []

    def counting_builder(venv_dir: Path) -> None:
        builds.append(venv_dir)
        _fake_venv_builder(venv_dir)

    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        venv_builder=counting_builder,
        finalizer=_fake_finalizer,
        exit_func=exits.append,
    )
    await updater._lock.acquire()
    first = asyncio.create_task(
        updater.consider_manifest(body, signature_b64, body["version"])
    )
    second = asyncio.create_task(
        updater.force_update_now(body, signature_b64, body["version"])
    )
    # Let both callers pass the pre-lock check and queue on the lock.
    await asyncio.sleep(0)
    updater._lock.release()

    assert await asyncio.gather(first, second) == ["staged", "staged"]
    assert len(builds) == 1
    assert exits == [75]


@pytest.mark.parametrize(
    "current_version, force",
    [
        # A relay push or the hourly poll.
        ("0.3.0", False),
        # "Install now" and the recovery check after a 4002 close.
        ("0.3.0", True),
        # The running version itself: "Install now" does not stage it again.
        ("0.2.0", True),
    ],
)
async def test_signed_manifest_that_is_not_newer_is_refused(
    layout, current_version: str, force: bool
) -> None:
    """The updater is forward-only on every path the relay can start: a
    correctly signed manifest whose version is not higher than the running
    one is a no_change. Nothing is built, the version pointer stays, and no
    restart is requested."""
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")
    installer_calls: list = []
    exits: list[int] = []
    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version=current_version,
        venv_builder=lambda venv: installer_calls.append(venv),
        finalizer=lambda python, requirements: installer_calls.append(python),
        exit_func=exits.append,
    )
    if force:
        outcome = await updater.force_update_now(
            body, signature_b64, body["version"]
        )
    else:
        outcome = await updater.consider_manifest(
            body, signature_b64, body["version"]
        )
    assert outcome == "no_change"
    assert installer_calls == []
    assert exits == []
    assert not (layout.releases_dir / "0.2.0").exists()
    assert not layout.current_version_file.exists()


async def test_allow_downgrade_stages_an_older_signed_manifest(layout) -> None:
    """Only the command at the computer (``update --allow-downgrade``) may
    move the agent to a version that is not higher than the running one."""
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")
    exits: list[int] = []
    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.3.0",
        venv_builder=_fake_venv_builder,
        finalizer=_fake_finalizer,
        exit_func=exits.append,
    )
    outcome = await updater.consider_manifest(
        body, signature_b64, body["version"], force=True, allow_downgrade=True,
    )
    assert outcome == "staged"
    assert layout.current_version_file.read_text().strip() == "0.2.0"
    assert exits == [75]


@pytest.mark.parametrize(
    "current_version, claimed_latest, expected_outcome, expected_releases",
    [
        # The signed body is older; a higher unsigned claim changes nothing.
        ("0.3.0", "9.9.9", "no_change", []),
        # The signed body is newer; a lower unsigned claim does not hide it.
        ("0.1.0", "0.1.0", "staged", ["0.2.0"]),
    ],
)
async def test_version_decision_uses_the_signed_body_only(
    layout,
    current_version: str,
    claimed_latest: str,
    expected_outcome: str,
    expected_releases: list[str],
) -> None:
    """``latest_version`` arrives next to the manifest without a signature,
    so it selects nothing: the version inside the signed body decides."""
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")
    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version=current_version,
        venv_builder=_fake_venv_builder,
        finalizer=_fake_finalizer,
        exit_func=lambda code: None,
    )
    outcome = await updater.consider_manifest(
        body, signature_b64, claimed_latest, force=True,
    )
    assert outcome == expected_outcome
    assert sorted(p.name for p in layout.releases_dir.iterdir()) == expected_releases


@pytest.mark.parametrize(
    "current_version, busy",
    [
        # Same version as the running one: the signature is still checked.
        ("0.2.0", False),
        # A busy agent reports the bad signature, it does not defer it.
        ("0.1.0", True),
    ],
)
async def test_bad_signature_is_reported_before_any_other_decision(
    layout, current_version: str, busy: bool
) -> None:
    _, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")
    other_key_b64 = _public_key_b64(Ed25519PrivateKey.generate())
    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=other_key_b64,
        session_manager=None,
        current_version=current_version,
        venv_builder=_fake_venv_builder,
        finalizer=_fake_finalizer,
        exit_func=lambda code: None,
        busy_probe=lambda: busy,
    )
    outcome = await updater.consider_manifest(
        body, signature_b64, body["version"],
    )
    assert outcome == "signature_failed"
    assert not (layout.releases_dir / "0.2.0").exists()


async def test_failed_force_restage_preserves_running_release(layout) -> None:
    """The original brick: a re-stage of the CURRENT version whose build FAILS
    must NOT destroy the running release (no rmtree-before-build) and must
    leave no scratch dirs behind. Only ``allow_downgrade`` stages the running
    version again."""
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")
    live = layout.releases_dir / "0.2.0"
    (live / "venv" / "bin").mkdir(parents=True)
    (live / "venv" / "bin" / "python").write_text("running", encoding="utf-8")

    def _boom(venv_dir):
        raise RuntimeError("venv build failed mid-stage")

    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.2.0",
        venv_builder=_boom,
        finalizer=_fake_finalizer,
        exit_func=lambda code: None,
    )
    outcome = await updater.consider_manifest(
        body, signature_b64, body["version"],
        force=True, exit_after_stage=False, allow_downgrade=True,
    )
    assert outcome == "signature_failed"
    # Running release intact — never rmtree'd before the new build succeeded.
    assert (live / "venv" / "bin" / "python").read_text() == "running"
    assert not list(layout.releases_dir.glob(".staging-*"))
    assert not list(layout.releases_dir.glob(".old-*"))


async def test_force_restage_existing_version_swaps_in_new_build(layout) -> None:
    """A re-stage of the running version (``allow_downgrade``) that SUCCEEDS
    atomically swaps in the freshly-built release (old contents replaced) and
    cleans the backup."""
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")
    live = layout.releases_dir / "0.2.0"
    (live / "venv" / "bin").mkdir(parents=True)
    (live / "venv" / "bin" / "python").write_text("old", encoding="utf-8")
    (live / "stale_marker").write_text("x", encoding="utf-8")

    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.2.0",
        venv_builder=_fake_venv_builder,
        finalizer=_fake_finalizer,
        exit_func=lambda code: None,
    )
    outcome = await updater.consider_manifest(
        body, signature_b64, body["version"],
        force=True, exit_after_stage=False, allow_downgrade=True,
    )
    assert outcome == "staged"
    # New build swapped in: the old-only marker is gone, the venv exists, and
    # no backup scratch dir lingers.
    assert not (live / "stale_marker").exists()
    assert (live / "venv" / "bin" / "python").exists()
    assert (live / RELEASE_SENTINEL).exists()
    assert not list(layout.releases_dir.glob(".old-*"))


async def test_finalizer_runs_at_final_path_not_staging(layout) -> None:
    """Regression guard for the 'bad interpreter' bug: pip + the wheel must be
    installed by the FINAL-path interpreter (``releases/<v>/venv``), never the
    staging interpreter — that is what makes the console-script shebangs point
    at the final path instead of the swept ``.staging-*`` dir."""
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")
    seen: dict[str, Path] = {}

    def recording_finalizer(python: Path, wheel_path: Path) -> None:
        seen["python"] = python
        _fake_finalizer(python, wheel_path)

    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        venv_builder=_fake_venv_builder,
        finalizer=recording_finalizer,
        exit_func=lambda code: None,
    )
    outcome = await updater.consider_manifest(body, signature_b64, body["version"])

    assert outcome == "staged"
    final_python = seen["python"]
    assert ".staging-" not in str(final_python), (
        f"finalizer ran the staging interpreter ({final_python}); shebangs would "
        f"break once the staging dir is swept — the exact bug being fixed"
    )
    assert (layout.releases_dir / "0.2.0") in final_python.parents


async def test_sentinel_written_only_after_finalize(layout) -> None:
    """The completion sentinel must be absent while the finalizer runs (the
    release is still incomplete) and present once staging returns — so a crash
    mid-finalize leaves an incomplete, never-selected release."""
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")
    release = layout.releases_dir / "0.2.0"
    sentinel_during_finalize: list[bool] = []

    def checking_finalizer(python: Path, wheel_path: Path) -> None:
        sentinel_during_finalize.append((release / RELEASE_SENTINEL).exists())
        _fake_finalizer(python, wheel_path)

    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        venv_builder=_fake_venv_builder,
        finalizer=checking_finalizer,
        exit_func=lambda code: None,
    )
    outcome = await updater.consider_manifest(body, signature_b64, body["version"])

    assert outcome == "staged"
    assert sentinel_during_finalize == [False], "sentinel must follow finalize"
    assert (release / RELEASE_SENTINEL).exists()


async def test_finalize_failure_restores_prior_release(layout) -> None:
    """A post-swap finalize failure on a re-stage of an existing version
    restores the prior good copy (non-destructive), writes no sentinel on the
    running release's content, and never flips ``current_version``."""
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")
    live = layout.releases_dir / "0.2.0"
    (live / "venv" / "bin").mkdir(parents=True)
    (live / "venv" / "bin" / "python").write_text("old-good", encoding="utf-8")
    (live / RELEASE_SENTINEL).write_text("0.2.0\n", encoding="utf-8")
    (live / "old_marker").write_text("x", encoding="utf-8")

    def boom_finalizer(python: Path, wheel_path: Path) -> None:
        raise RuntimeError("pip install failed post-swap")

    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        venv_builder=_fake_venv_builder,
        finalizer=boom_finalizer,
        exit_func=lambda code: None,
    )
    outcome = await updater.force_update_now(
        body, signature_b64, body["version"], exit_after_stage=False,
    )

    assert outcome == "signature_failed"
    # Prior release restored byte-for-byte; no leftover scratch.
    assert (live / "venv" / "bin" / "python").read_text() == "old-good"
    assert (live / "old_marker").exists()
    assert (live / RELEASE_SENTINEL).exists()
    assert not list(layout.releases_dir.glob(".staging-*"))
    assert not list(layout.releases_dir.glob(".old-*"))
    # current_version never advanced past the running version.
    assert not layout.current_version_file.exists()


def test_swap_into_place_returns_backup_for_existing_release(layout) -> None:
    """``_swap_into_place`` now returns the ``.old-*`` backup (the caller drops
    it only after finalize + sentinel), or ``None`` when there was no prior
    release — the contract the finalize-restore path in ``_stage_release``
    depends on."""
    releases = layout.releases_dir
    staging = releases / ".staging-0.2.0-1"
    (staging / "venv" / "bin").mkdir(parents=True)
    (staging / "venv" / "bin" / "python").write_text("new", encoding="utf-8")

    # No prior release -> returns None.
    new_only = releases / "0.9.0"
    assert StagedBinaryUpdater._swap_into_place(staging, new_only) is None
    assert (new_only / "venv" / "bin" / "python").read_text() == "new"

    # Prior release present -> returns the surviving backup, NOT deleted.
    staging2 = releases / ".staging-0.3.0-1"
    (staging2 / "venv" / "bin").mkdir(parents=True)
    (staging2 / "venv" / "bin" / "python").write_text("newer", encoding="utf-8")
    existing = releases / "0.3.0"
    (existing / "venv" / "bin").mkdir(parents=True)
    (existing / "venv" / "bin" / "python").write_text("prior", encoding="utf-8")

    backup = StagedBinaryUpdater._swap_into_place(staging2, existing)
    assert backup is not None and backup.exists(), "backup must survive for restore"
    assert (backup / "venv" / "bin" / "python").read_text() == "prior"
    assert (existing / "venv" / "bin" / "python").read_text() == "newer"


def test_reclaim_incomplete_releases(layout) -> None:
    """The pre-stage sweep reclaims stale, unfinalized (sentinel-less) release
    dirs, while sparing fresh ones (a possible concurrent finalize), complete
    ones, and the current version."""
    releases = layout.releases_dir
    old = time.time() - (_STALE_SCRATCH_SECONDS + 100)

    def _mk(name: str, *, complete: bool, stale: bool) -> Path:
        d = releases / name
        (d / "venv" / "bin").mkdir(parents=True)
        (d / "venv" / "bin" / "python").write_text("x", encoding="utf-8")
        if complete:
            (d / RELEASE_SENTINEL).write_text(name, encoding="utf-8")
        if stale:
            os.utime(d, (old, old))
        return d

    stale_incomplete = _mk("0.1.5", complete=False, stale=True)
    fresh_incomplete = _mk("0.1.6", complete=False, stale=False)
    stale_complete = _mk("0.1.7", complete=True, stale=True)
    current = _mk("0.1.0", complete=False, stale=True)  # == current_version

    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64="x",
        session_manager=None,
        current_version="0.1.0",
        venv_builder=lambda venv: None,
        finalizer=lambda python, wheel: None,
    )
    updater._reclaim_incomplete_releases()

    assert not stale_incomplete.exists(), "stale + unfinalized must be reclaimed"
    assert fresh_incomplete.exists(), "fresh unfinalized (maybe finalizing) is spared"
    assert stale_complete.exists(), "a complete release is never reclaimed"
    assert current.exists(), "the current version is never reclaimed"


@pytest.mark.parametrize(
    "bad",
    [
        "../evil", "..", ".", "a/b", "a\\b", "x\x00y", "",
        # Windows drive-relative escape: ``releases / "z:evil"`` drops the
        # anchor. The allowlist rejects ``:`` (and any other metacharacter).
        "z:evil", "a:b:c", "a;b", "a b", "$(x)",
    ],
)
def test_assert_safe_version_rejects_path_escapes(bad: str) -> None:
    with pytest.raises(ValueError, match="safe path component"):
        _assert_safe_version(bad)


@pytest.mark.parametrize("ok", ["0.2.13", "0.2.0rc1", "1.0.0+build", "0.2.0.dev1"])
def test_assert_safe_version_allows_real_versions(ok: str) -> None:
    _assert_safe_version(ok)  # must not raise


async def test_force_stage_rejects_path_escaping_version(layout) -> None:
    """A signed-but-malicious version must be rejected before it becomes a
    releases/<v> path component — defense-in-depth at the manifest->fs boundary.
    Such a version never parses as higher than the running one, so only
    ``allow_downgrade`` reaches _stage_release, where the guard fires."""
    private_key = Ed25519PrivateKey.generate()
    public_key_b64 = base64.b64encode(
        private_key.public_key().public_bytes_raw()
    ).decode()
    tmp = layout.prefix / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    tb = tmp / "agent.tar.gz"
    wh = tmp / "mobaide_agent-9.9.9-py3-none-any.whl"
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="x")
        info.size = 1
        tar.addfile(info, BytesIO(b"x"))
    tb.write_bytes(buf.getvalue())
    wh.write_bytes(b"PK\x03\x04")
    body = {
        "version": "../../../tmp/mobaide-escape",
        "tarball_url": f"file://{tb}",
        "tarball_sha256": hashlib.sha256(tb.read_bytes()).hexdigest(),
        "wheel_url": f"file://{wh}",
        "wheel_sha256": hashlib.sha256(wh.read_bytes()).hexdigest(),
        "released_at": "2026-04-26T00:00:00+00:00",
    }
    sig = base64.b64encode(private_key.sign(canonical_json(body))).decode()
    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        venv_builder=_fake_venv_builder,
        finalizer=_fake_finalizer,
        exit_func=lambda code: None,
    )
    outcome = await updater.consider_manifest(
        body, sig, body["version"],
        force=True, exit_after_stage=False, allow_downgrade=True,
    )
    assert outcome == "signature_failed"
    # No directory created outside releases/.
    assert not (layout.prefix.parent / "mobaide-escape").exists()
    assert not (layout.prefix / "mobaide-escape").exists()


def test_sweep_preserves_concurrent_other_pid_scratch(layout) -> None:
    """The scratch sweep removes this process's own leftovers but must NOT
    delete a concurrent process's recent in-flight staging dir."""
    releases = layout.releases_dir
    own = releases / f".staging-0.2.0-{os.getpid()}"
    own.mkdir()
    other_recent = releases / f".staging-0.2.0-{os.getpid() + 1}"
    other_recent.mkdir()
    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64="x",
        session_manager=None,
        current_version="0.1.0",
        venv_builder=lambda venv: None,
        finalizer=lambda python, wheel: None,
    )
    updater._sweep_scratch_dirs()
    assert not own.exists(), "own-pid scratch should be swept"
    assert other_recent.exists(), (
        "a concurrent other-pid recent scratch must be preserved (no cross-"
        "process corruption)"
    )


def test_sweep_removes_stale_other_pid_scratch(layout) -> None:
    """A scratch dir old enough that its creator is certainly gone is GC'd even
    if its pid is not this process's."""
    import time as _time

    releases = layout.releases_dir
    stale = releases / f".old-0.2.0-{os.getpid() + 999}"
    stale.mkdir()
    old = _time.time() - (_STALE_SCRATCH_SECONDS + 100)
    os.utime(stale, (old, old))
    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64="x",
        session_manager=None,
        current_version="0.1.0",
        venv_builder=lambda venv: None,
        finalizer=lambda python, wheel: None,
    )
    updater._sweep_scratch_dirs()
    assert not stale.exists(), "stale other-pid scratch should be GC'd"


async def test_non_dict_body_fails_closed(layout) -> None:
    """A garbage payload arriving on the WebSocket (e.g. a serialised
    list or a stray string) must short-circuit before reaching
    ``verify_signature`` rather than blowing up with a TypeError."""
    public_key_b64 = base64.b64encode(b"\x00" * 32).decode()
    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        venv_builder=lambda venv: None,
        finalizer=lambda python, wheel: None,
    )
    outcome = await updater.consider_manifest(
        ["not", "a", "dict"],  # type: ignore[arg-type]
        "sig",
        "0.2.0",
    )
    assert outcome == "signature_failed"
    assert not layout.current_version_file.exists()


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

    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        venv_builder=lambda venv: None,
        finalizer=lambda python, wheel: None,
    )
    outcome = await updater.consider_manifest(
        body, sig_b64, body["version"],
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
        # Compliant extension, insufficient dashes (PEP 427 minimum is 4).
        "https://cdn.example.com/releases/agent.whl",
        "https://cdn.example.com/releases/foo-1.0-py3.whl",
    ],
)
def test_wheel_basename_from_url_rejects_non_pep427(url: str) -> None:
    with pytest.raises(ValueError, match="non-PEP427 filename"):
        _wheel_basename_from_url(url)


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


class _FakeBytesResp:
    def __init__(self, body: bytes = b"manifest-body"):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n: int = -1) -> bytes:
        if n == -1 or n >= len(self._body):
            out, self._body = self._body, b""
            return out
        out, self._body = self._body[:n], self._body[n:]
        return out


async def test_consider_manifest_apply_path_never_calls_urlopen(
    layout, monkeypatch,
) -> None:
    """Regression guard for the deleted ``_download_manifest`` HTTP fetch.

    The original bug was a relative ``manifest_url`` ("/api/v1/agent/manifest.json")
    being read as a local filesystem path inside ``_download_manifest``, which
    silently failed-closed to ``"signature_failed"``. The fix deletes that
    function entirely and delivers the body inline over the relay WebSocket.

    This test pins the *architectural* invariant the fix relies on: the
    manifest-apply path (``consider_manifest`` -> stage release -> swap) must
    not, under any circumstance, perform an HTTP fetch for the manifest. We
    enforce that by patching ``_http.urlopen`` to raise — and feeding the
    updater ``file://`` tarball/wheel URLs so the legitimate asset-download
    branch short-circuits inside ``_download_to`` without ever calling
    ``urlopen``. If a future change re-introduces any HTTP fetch on the
    manifest path (a re-fetch for "freshness", a CDN warm-up, etc.) this
    test fails loudly instead of producing a fail-closed signature_failed at
    runtime against real users.
    """
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")

    urlopen_calls: list[str] = []

    def _urlopen_must_not_be_called(*args, **kwargs):
        urlopen_calls.append(repr((args, kwargs)))
        raise AssertionError(
            "manifest-apply path must not call _http.urlopen — body is "
            "delivered inline over the relay WebSocket (see plan: 'eliminates "
            "the entire _download_manifest HTTP path')"
        )

    monkeypatch.setattr(
        "mobaide.agent.updater._http.urlopen", _urlopen_must_not_be_called
    )

    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        venv_builder=_fake_venv_builder,
        finalizer=_fake_finalizer,
        exit_func=lambda code: None,
    )
    outcome = await updater.consider_manifest(
        body, signature_b64, body["version"],
    )

    assert outcome == "staged", (
        f"expected 'staged' on file:// asset paths; got {outcome!r}. The most "
        f"likely cause is a regression that introduced an HTTP fetch on the "
        f"manifest path. urlopen_calls={urlopen_calls!r}"
    )
    assert urlopen_calls == [], (
        f"manifest-apply path called _http.urlopen — regression to pre-fix "
        f"HTTP-fetch model. Calls captured: {urlopen_calls!r}"
    )


async def test_force_update_now_apply_path_never_calls_urlopen(
    layout, monkeypatch,
) -> None:
    """Same invariant as the previous test, but exercising the
    ``force_update_now`` entry point (the one wired to ``POST
    /api/agent/update/force_now`` — the user-facing "Install now" CTA that
    triggered the original production bug).

    Force-update bypasses idle-deferral but goes through the exact same
    ``consider_manifest`` body — a regression that adds an HTTP fetch only
    inside the force path (rather than the watch-loop path) would still
    reproduce the original UX failure even if the previous test stayed
    green.
    """
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")

    urlopen_calls: list[str] = []

    def _urlopen_must_not_be_called(*args, **kwargs):
        urlopen_calls.append(repr((args, kwargs)))
        raise AssertionError("force_update_now must not HTTP-fetch the manifest")

    monkeypatch.setattr(
        "mobaide.agent.updater._http.urlopen", _urlopen_must_not_be_called
    )

    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        venv_builder=_fake_venv_builder,
        finalizer=_fake_finalizer,
        exit_func=lambda code: None,
    )
    outcome = await updater.force_update_now(
        body, signature_b64, body["version"], exit_after_stage=False,
    )

    assert outcome == "staged"
    assert urlopen_calls == []


def test_download_to_routes_through_user_agent_wrapper(tmp_path) -> None:
    # Regression guard for the Cloudflare 1010 UA block on the wheel/tarball download path. The
    # ``file://`` short-circuit in ``_download_to`` means existing tests
    # never touch ``urllib`` — pin the http(s) branch explicitly so a
    # future revert can't slip past unnoticed. The manifest itself is no
    # longer fetched over HTTP from the relay-driven update path (body
    # arrives inline over the WebSocket), so the matching
    # ``_http_download`` UA guard moved with the function deletion.
    captured: dict = {}
    payload = b"PK\x03\x04 fake wheel bytes"

    def _fake_urlopen(req, timeout=None, context=None):
        captured["request"] = req
        return _FakeBytesResp(payload)

    dest = tmp_path / "agent.whl"
    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        StagedBinaryUpdater._download_to(
            "https://cdn.example.com/releases/mobaide_agent-0.2.0-py3-none-any.whl",
            dest,
        )

    assert dest.read_bytes() == payload
    ua = captured["request"].get_header("User-agent")
    assert ua is not None and ua.startswith("mobaide-agent/")


class TestVenvBuildAndFinalizeHideWindow:
    """Pin the Windows ``CREATE_NO_WINDOW`` flag on every ``subprocess.run``
    invocation in ``_build_venv_without_pip`` and ``_finalize_wheel_install``.

    The updater runs inside the long-lived agent process. The agent
    runs under ``pythonw.exe`` via the per-user Scheduled Task (no
    parent console). A default ``subprocess.run`` of a console-mode
    child like ``python -m venv`` or ``pip install`` would therefore
    pop a fresh console window into the user's session on every
    auto-update. ``CREATE_NO_WINDOW`` (0x08000000) suppresses that
    while preserving ``capture_output=True``'s stdio inheritance
    (unlike ``DETACHED_PROCESS``).

    The flag is module-level ``_WINDOWS_NO_WINDOW``: equals
    ``subprocess.CREATE_NO_WINDOW`` on win32, ``0`` elsewhere. Tests
    monkeypatch ``subprocess.run`` so they run on every host
    regardless of whether the actual venv/pip invocations would
    succeed.
    """

    def _capture_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> list[dict]:
        """Wire a deterministic ``subprocess.run`` recorder onto the
        updater module's bound ``subprocess`` reference (mirrors the
        pattern in ``test_env_writer.TestRestrictPermsWindowsAclArgv``).
        """
        calls: list[dict] = []

        def fake_run(cmd, **kwargs):
            calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            return MagicMock(returncode=0, stdout=b"", stderr=b"")

        import mobaide.agent.updater as _updater_mod

        monkeypatch.setattr(_updater_mod.subprocess, "run", fake_run)
        return calls

    def test_all_subprocess_run_calls_pass_creationflags(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        # _build_venv_without_pip + _finalize_wheel_install make three
        # subprocess.run calls total:
        #   1. python -m venv --without-pip <venv_dir>
        #   2. <python> -m ensurepip --default-pip
        #   3. <python> -m pip install --require-hashes ... -r <requirements>
        # All three MUST pass ``creationflags=_WINDOWS_NO_WINDOW``. Without any
        # one, that subprocess pops a console window on Windows on every
        # auto-update cycle.
        calls = self._capture_run(monkeypatch)
        import mobaide.agent.updater as _updater_mod

        venv_dir = tmp_path / "venv"
        final_python = venv_dir / "bin" / "python"
        requirements_path = tmp_path / "requirements.txt"
        requirements_path.write_text(_LOCK_TEXT, encoding="utf-8")

        _build_venv_without_pip(venv_dir)
        _finalize_wheel_install(final_python, requirements_path)

        assert len(calls) == 3, (
            f"expected exactly 3 subprocess.run calls (venv --without-pip, "
            f"ensurepip, hash-pinned install); got {len(calls)}"
        )
        # The only install is the hash-pinned one: every file must match a
        # listed hash, nothing is built from source, and no step fetches a
        # package that the requirements file does not pin (the bundled pip is
        # used as it is).
        install = calls[2]["cmd"]
        assert install[1:4] == ["-m", "pip", "install"]
        assert "--require-hashes" in install
        assert install[install.index("--only-binary") + 1] == ":all:"
        assert install[install.index("-r") + 1] == str(requirements_path)
        assert "--upgrade" not in install
        assert [arg for arg in install[4:] if not arg.startswith("-")] == [
            ":all:", str(requirements_path),
        ]
        # The pip-less build is what avoids baking a staging shebang; finalize
        # invokes pip as ``python -m pip`` (never the bin/pip script) so it never
        # depends on a shebang. Pin both invariants.
        assert "--without-pip" in calls[0]["cmd"], (
            f"venv build must pass --without-pip; got {calls[0]['cmd']!r}"
        )
        for call in calls[1:]:
            assert call["cmd"][1:3] in (["-m", "ensurepip"], ["-m", "pip"]), (
                f"finalize must invoke ensurepip / python -m pip, not the "
                f"bin/pip script; got {call['cmd']!r}"
            )

        expected_flag = _updater_mod._WINDOWS_NO_WINDOW
        for idx, call in enumerate(calls):
            flag = call["kwargs"].get("creationflags")
            assert flag == expected_flag, (
                f"subprocess.run call #{idx + 1} "
                f"(cmd={call['cmd']!r}) must pass "
                f"creationflags=_WINDOWS_NO_WINDOW ({expected_flag!r}); "
                f"got creationflags={flag!r}. On Windows this means "
                f"the call would pop a console window for the "
                f"subprocess (pip / venv / python.exe child)"
            )

    def test_every_child_runs_with_python_safe_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        # The venv, ensurepip and pip children start in the directory the
        # agent or the CLI was started in. PYTHONSAFEPATH keeps that
        # directory off their module search path.
        monkeypatch.delenv("PYTHONSAFEPATH", raising=False)
        monkeypatch.setenv("MOBAIDE_TEST_INHERITED_VALUE", "kept")
        calls = self._capture_run(monkeypatch)
        venv_dir = tmp_path / "venv"
        requirements_path = tmp_path / "requirements.txt"
        requirements_path.write_text(_LOCK_TEXT, encoding="utf-8")

        _build_venv_without_pip(venv_dir)
        _finalize_wheel_install(venv_dir / "bin" / "python", requirements_path)

        assert len(calls) == 3
        for call in calls:
            env = call["kwargs"].get("env") or {}
            assert env.get("PYTHONSAFEPATH") == "1", call["cmd"]
            # The rest of the environment (PATH, proxy settings) still
            # reaches the child.
            assert env.get("MOBAIDE_TEST_INHERITED_VALUE") == "kept", call["cmd"]


@pytest.mark.parametrize(
    "version, expected",
    [
        ("0.2.0", (0, 2, 0)),
        # PEP 440 local/build metadata after `+` is stripped.
        ("0.2.13+local", (0, 2, 13)),
        # Pre-release metadata after the first `-` is stripped.
        ("1.4.2-rc1", (1, 4, 2)),
        # Parsing stops at the first non-numeric dotted component.
        ("1.2.dev3", (1, 2)),
        ("2.0.0a1", (2, 0)),
        # Nothing numeric -> empty tuple (the "unparseable" signal the
        # forward-only gate treats conservatively).
        ("garbage", ()),
        ("", ()),
        ("v1.2.3", ()),
    ],
)
def test_version_tuple_strips_metadata_and_stops_at_non_numeric(
    version: str, expected: tuple[int, ...]
) -> None:
    assert _version_tuple(version) == expected


@pytest.mark.parametrize(
    "candidate, current, expected",
    [
        ("0.2.1", "0.2.0", True),
        ("0.3.0", "0.2.9", True),
        # Numeric (not lexical) comparison of components.
        ("0.10.0", "0.9.0", True),
        ("0.2.0", "0.2.0", False),
        ("0.2.0", "0.3.0", False),
        # Conservative: an unparseable candidate is never "newer", so a
        # malformed manifest version cannot drive a stage on the auto path.
        ("garbage", "0.2.0", False),
        ("", "0.2.0", False),
        # Conservative: an unparseable current also blocks (we can't prove
        # the candidate is forward).
        ("0.3.0", "garbage", False),
    ],
)
def test_is_version_newer_is_strict_and_conservative(
    candidate: str, current: str, expected: bool
) -> None:
    assert _is_version_newer(candidate, current) is expected


@pytest.mark.parametrize("force", [False, True])
async def test_forward_only_skips_unparseable_signed_version(
    layout, force: bool
) -> None:
    """A signed version that does not parse is never "higher" (conservative
    ``_is_version_newer`` -> False), so it is a no_change on the automatic and
    the forced path: a malformed version string never reaches the stage."""
    public_key_b64, body, signature_b64 = _signed_manifest(
        layout.prefix / "tmp", version="garbage",
    )
    installer_calls: list = []
    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.2.0",
        venv_builder=lambda venv: installer_calls.append(venv),
        finalizer=lambda python, wheel: None,
    )

    outcome = await updater.consider_manifest(
        body, signature_b64, body["version"], force=force,
    )

    assert outcome == "no_change"
    assert installer_calls == []
    assert not (layout.releases_dir / "garbage").exists()


def test_swap_into_place_restores_backup_when_forward_replace_fails(
    layout, monkeypatch, caplog
) -> None:
    """The data-loss-prevention branch the integration tests don't reach: when
    ``releases/<v>`` already exists, ``_swap_into_place`` moves it aside to a
    ``.old-*`` backup and only then forward-replaces it with the staging dir.
    If that forward ``os.replace`` raises, the backup MUST be restored to the
    original release path (so the running release is never left missing) and the
    error re-raised for the caller's cleanup. The success path is already
    covered by ``test_force_restage_existing_version_swaps_in_new_build``; this
    pins the failure-restore half of the all-or-nothing contract."""
    release_dir = layout.releases_dir / "0.2.0"
    (release_dir / "venv" / "bin").mkdir(parents=True)
    (release_dir / "venv" / "bin" / "python").write_text("running", encoding="utf-8")

    staging_dir = layout.releases_dir / ".staging-0.2.0-99999"
    (staging_dir / "venv" / "bin").mkdir(parents=True)
    (staging_dir / "venv" / "bin" / "python").write_text("new", encoding="utf-8")

    real_replace = os.replace
    calls: list[tuple[str, str]] = []

    def flaky_replace(src, dst, *args, **kwargs):
        calls.append((str(src), str(dst)))
        # Let the move-aside (release -> backup) succeed, then blow up on the
        # forward move (staging -> release) that the restore logic guards.
        if Path(src) == staging_dir and Path(dst) == release_dir:
            raise OSError("simulated cross-device / locked-file failure")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", flaky_replace)

    with caplog.at_level(logging.ERROR, logger="mobaide.agent.updater"):
        with pytest.raises(OSError, match="simulated"):
            StagedBinaryUpdater._swap_into_place(staging_dir, release_dir)

    # Restore happened: the original release is back in place with its content,
    # and no `.old-*` backup is left orphaned at the release path.
    assert (release_dir / "venv" / "bin" / "python").read_text() == "running"
    assert not list(layout.releases_dir.glob(".old-*"))
    # Restore succeeded, so the "restore failed" loud-error path must NOT fire.
    restore_failures = [
        r.getMessage()
        for r in caplog.records
        if "updater.swap_restore_failed" in r.getMessage()
    ]
    assert restore_failures == []


async def test_stage_sweeps_stale_scratch_dirs_before_building(layout) -> None:
    """A stage killed mid-build can leave ``.staging-*`` / ``.old-*`` scratch
    dirs behind. ``_stage_release`` sweeps them at entry (``_sweep_scratch_dirs``)
    so they don't accumulate across attempts; a successful stage then writes the
    real ``releases/<v>``. The scratch names never parse as a version, so this
    is purely about keeping ``releases/`` tidy -- but a regression that dropped
    the sweep would silently leak directories on every recovered update."""
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")

    stale_staging = layout.releases_dir / f".staging-0.1.9-{os.getpid()}"
    stale_old = layout.releases_dir / f".old-0.1.9-{os.getpid()}"
    for scratch in (stale_staging, stale_old):
        scratch.mkdir(parents=True)
        (scratch / "leftover").write_text("x", encoding="utf-8")

    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        venv_builder=_fake_venv_builder,
        finalizer=_fake_finalizer,
        exit_func=lambda code: None,
    )
    outcome = await updater.consider_manifest(body, signature_b64, body["version"])

    assert outcome == "staged"
    assert (layout.releases_dir / "0.2.0").is_dir()
    # Both pre-existing scratch dirs were swept at stage start.
    assert not stale_staging.exists()
    assert not stale_old.exists()
    assert not list(layout.releases_dir.glob(".staging-*"))
    assert not list(layout.releases_dir.glob(".old-*"))


def _refusing_updater(
    layout, public_key_b64: str, installer_calls: list
) -> StagedBinaryUpdater:
    """An updater on 0.1.0 that records every install step it reaches."""
    return StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        venv_builder=lambda venv: installer_calls.append(venv),
        finalizer=lambda python, requirements: installer_calls.append(python),
        exit_func=lambda code: None,
    )


def _assert_nothing_installed(layout, installer_calls: list) -> None:
    assert installer_calls == []
    assert list(layout.releases_dir.iterdir()) == []
    assert not layout.current_version_file.exists()


@pytest.mark.parametrize("lock_sha", [None, "not-a-hash", "a" * 63])
async def test_manifest_without_a_dependency_lock_hash_is_refused(
    layout, lock_sha: str | None
) -> None:
    """A signed manifest must name the SHA-256 of the dependency lock. One
    that does not (a release made before the lock existed, or a malformed
    value) is refused before any download."""
    private_key = Ed25519PrivateKey.generate()
    public_key_b64, body, _ = _signed_manifest(
        layout.prefix / "tmp", private_key=private_key,
    )
    if lock_sha is None:
        del body["dependency_lock_sha256"]
    else:
        body["dependency_lock_sha256"] = lock_sha
    signature_b64 = _sign(private_key, body)
    installer_calls: list = []
    updater = _refusing_updater(layout, public_key_b64, installer_calls)

    outcome = await updater.consider_manifest(
        body, signature_b64, body["version"],
    )

    assert outcome == "signature_failed"
    _assert_nothing_installed(layout, installer_calls)


@pytest.mark.parametrize(
    "lock_in_tarball, signed_lock_text",
    [
        # The lock in the tarball is not the one that was signed.
        ("example-dependency==2.0 --hash=sha256:" + "1" * 64 + "\n", _LOCK_TEXT),
        # The tarball carries no lock at all.
        (None, _LOCK_TEXT),
    ],
)
async def test_lock_file_that_differs_from_the_signed_hash_is_refused(
    layout, lock_in_tarball: str | None, signed_lock_text: str
) -> None:
    """The lock at the tarball root must hash to the signed
    ``dependency_lock_sha256``; otherwise no venv is built and pip never runs."""
    public_key_b64, body, signature_b64 = _signed_manifest(
        layout.prefix / "tmp",
        lock_text=lock_in_tarball,
        extra={
            "dependency_lock_sha256": hashlib.sha256(
                signed_lock_text.encode("utf-8")
            ).hexdigest(),
        },
    )
    installer_calls: list = []
    updater = _refusing_updater(layout, public_key_b64, installer_calls)

    outcome = await updater.consider_manifest(
        body, signature_b64, body["version"],
    )

    assert outcome == "signature_failed"
    _assert_nothing_installed(layout, installer_calls)


def _write_wheel(
    directory: Path,
    name: str,
    version: str,
    *,
    requires: tuple[str, ...] = (),
    module_source: bytes = b"",
) -> Path:
    """Write a minimal pure-Python wheel by hand (no build tool, no network)."""
    module = name.replace("-", "_")
    dist_info = f"{module}-{version}.dist-info"
    metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n" + "".join(
        f"Requires-Dist: {requirement}\n" for requirement in requires
    )
    files = {
        f"{module}.py": module_source,
        f"{dist_info}/METADATA": metadata.encode("utf-8"),
        f"{dist_info}/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: mobaide-tests\n"
            b"Root-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    record = "".join(
        "{},sha256={},{}\n".format(
            path,
            base64.urlsafe_b64encode(hashlib.sha256(data).digest())
            .rstrip(b"=")
            .decode(),
            len(data),
        )
        for path, data in files.items()
    ) + f"{dist_info}/RECORD,,\n"
    directory.mkdir(parents=True, exist_ok=True)
    wheel_path = directory / f"{module}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, "w") as archive:
        for path, data in files.items():
            archive.writestr(path, data)
        archive.writestr(f"{dist_info}/RECORD", record)
    return wheel_path


@pytest.fixture
def local_wheels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Limit the real pip of the finalizer to one local directory of wheels:
    no package index, no network, no user configuration."""
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setenv("PIP_FIND_LINKS", str(wheels))
    monkeypatch.setenv("PIP_CONFIG_FILE", os.devnull)
    return wheels


def _release_with_one_dependency(layout, lock_text: str):
    """A signed 0.2.0 release whose wheel requires ``mobaide-test-dep==1.0``."""
    agent_wheel = _write_wheel(
        layout.prefix / "build",
        "mobaide-agent",
        "0.2.0",
        requires=("mobaide-test-dep==1.0",),
    )
    return _signed_manifest(
        layout.prefix / "tmp",
        lock_text=lock_text,
        wheel_bytes=agent_wheel.read_bytes(),
    )


def _real_install_updater(layout, public_key_b64: str) -> StagedBinaryUpdater:
    """An updater with the real venv build and the real pip finalizer."""
    return StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        exit_func=lambda code: None,
    )


async def test_real_install_takes_the_dependency_that_the_lock_pins(
    layout, local_wheels: Path
) -> None:
    """End to end with the real pip: the wheel and its dependency install
    from the combined, hash-pinned requirements file."""
    dependency = _write_wheel(local_wheels, "mobaide-test-dep", "1.0")
    dependency_sha = hashlib.sha256(dependency.read_bytes()).hexdigest()
    public_key_b64, body, signature_b64 = _release_with_one_dependency(
        layout, f"mobaide-test-dep==1.0 --hash=sha256:{dependency_sha}\n",
    )
    updater = _real_install_updater(layout, public_key_b64)

    outcome = await updater.consider_manifest(
        body, signature_b64, body["version"],
    )

    assert outcome == "staged"
    assert layout.current_version_file.read_text().strip() == "0.2.0"
    release_python = _release_venv_python(layout.releases_dir / "0.2.0")
    subprocess.run(
        [str(release_python), "-c", "import mobaide_agent, mobaide_test_dep"],
        check=True,
        capture_output=True,
    )


@pytest.mark.parametrize("case", ["file_differs_from_lock", "dependency_not_in_lock"])
async def test_real_install_refuses_a_dependency_that_the_lock_does_not_match(
    layout, local_wheels: Path, case: str
) -> None:
    """End to end with the real pip: a dependency file whose SHA-256 is not the
    one in the signed lock, or a dependency the lock does not list, stops the
    install. The release is removed and the agent stays on its version."""
    dependency = _write_wheel(local_wheels, "mobaide-test-dep", "1.0")
    dependency_sha = hashlib.sha256(dependency.read_bytes()).hexdigest()
    if case == "file_differs_from_lock":
        lock_text = f"mobaide-test-dep==1.0 --hash=sha256:{dependency_sha}\n"
        # Same name and version, other content than the lock was made from.
        _write_wheel(
            local_wheels, "mobaide-test-dep", "1.0", module_source=b"CHANGED = True\n",
        )
        assert hashlib.sha256(dependency.read_bytes()).hexdigest() != dependency_sha
    else:
        lock_text = "# no dependencies\n"
    public_key_b64, body, signature_b64 = _release_with_one_dependency(
        layout, lock_text,
    )
    updater = _real_install_updater(layout, public_key_b64)

    outcome = await updater.consider_manifest(
        body, signature_b64, body["version"],
    )

    assert outcome == "signature_failed"
    assert updater.current_version == "0.1.0"
    assert not layout.current_version_file.exists()
    assert list(layout.releases_dir.iterdir()) == []


async def test_release_key_added_by_a_signed_manifest_can_sign_the_next_one(
    layout,
) -> None:
    """A manifest signed with the pinned key can add a second release key.
    Before that, a manifest signed with the second key is refused; after it,
    the same manifest is accepted."""
    pinned_key = Ed25519PrivateKey.generate()
    second_key = Ed25519PrivateKey.generate()
    _, next_body, next_signature = _signed_manifest(
        layout.prefix / "tmp-next", version="0.3.0", private_key=second_key,
    )
    _, adding_body, adding_signature = _signed_manifest(
        layout.prefix / "tmp",
        private_key=pinned_key,
        extra={"trust": {"release_public_keys": [_public_key_b64(second_key)]}},
    )
    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=_public_key_b64(pinned_key),
        session_manager=None,
        current_version="0.2.0",
        venv_builder=_fake_venv_builder,
        finalizer=_fake_finalizer,
        exit_func=lambda code: None,
    )

    before = await updater.consider_manifest(next_body, next_signature, "0.3.0")
    adding = await updater.consider_manifest(adding_body, adding_signature, "0.2.0")
    after = await updater.consider_manifest(next_body, next_signature, "0.3.0")

    assert (before, adding, after) == ("signature_failed", "no_change", "staged")
    assert layout.current_version_file.read_text().strip() == "0.3.0"


@pytest.mark.parametrize(
    "case, expected_outcome",
    [("older_version", "no_change"), ("untrusted_signer", "signature_failed")],
)
async def test_trust_data_of_a_refused_manifest_is_not_recorded(
    layout, case: str, expected_outcome: str
) -> None:
    """Trust data is taken only from a manifest that verifies and is not older
    than the running version, so a replayed older manifest and a manifest from
    an unknown signer change no trusted key."""
    pinned_key = Ed25519PrivateKey.generate()
    signer = pinned_key if case == "older_version" else Ed25519PrivateKey.generate()
    offered_key = _public_key_b64(Ed25519PrivateKey.generate())
    _, body, signature_b64 = _signed_manifest(
        layout.prefix / "tmp",
        private_key=signer,
        extra={
            "trust": {
                "release_public_keys": [offered_key],
                "relay_pairing_public_keys": [offered_key],
            },
        },
    )
    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=_public_key_b64(pinned_key),
        session_manager=None,
        current_version="0.3.0",
        venv_builder=_fake_venv_builder,
        finalizer=_fake_finalizer,
        exit_func=lambda code: None,
    )

    outcome = await updater.consider_manifest(body, signature_b64, body["version"])

    assert outcome == expected_outcome
    assert load_manifest_trust(layout) == ManifestTrust()


async def test_trust_data_is_recorded_once_per_manifest_on_the_event_loop(
    layout, monkeypatch
) -> None:
    """The relay sends the same signed manifest at every tunnel sign-in. Its
    trust data is recorded once, on the event loop thread. A manifest with
    other signed content is still recorded."""
    import mobaide.agent.updater as _updater_mod

    pinned_key = Ed25519PrivateKey.generate()
    first_pairing_key = _public_key_b64(Ed25519PrivateKey.generate())
    next_pairing_key = _public_key_b64(Ed25519PrivateKey.generate())
    _, body, signature_b64 = _signed_manifest(
        layout.prefix / "tmp",
        private_key=pinned_key,
        extra={"trust": {"relay_pairing_public_keys": [first_pairing_key]}},
    )
    _, next_body, next_signature_b64 = _signed_manifest(
        layout.prefix / "tmp-next",
        private_key=pinned_key,
        extra={
            "released_at": "2026-05-26T00:00:00+00:00",
            "trust": {"relay_pairing_public_keys": [next_pairing_key]},
        },
    )
    recorded_on: list[threading.Thread] = []
    real_record = _updater_mod.record_manifest_trust

    def _recording(record_layout, manifest_body):
        recorded_on.append(threading.current_thread())
        return real_record(record_layout, manifest_body)

    monkeypatch.setattr(_updater_mod, "record_manifest_trust", _recording)
    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=_public_key_b64(pinned_key),
        session_manager=None,
        current_version="0.2.0",
        venv_builder=_fake_venv_builder,
        finalizer=_fake_finalizer,
        exit_func=lambda code: None,
    )

    for _ in range(3):
        outcome = await updater.consider_manifest(body, signature_b64, "0.2.0")
        assert outcome == "no_change"

    assert recorded_on == [threading.current_thread()]
    assert load_manifest_trust(layout).relay_pairing_public_keys == (
        first_pairing_key,
    )

    await updater.consider_manifest(next_body, next_signature_b64, "0.2.0")

    assert recorded_on == [threading.current_thread()] * 2
    assert load_manifest_trust(layout).relay_pairing_public_keys == (
        next_pairing_key,
    )


@pytest.mark.parametrize(
    "flags, staged_version",
    [([], None), (["--allow-downgrade"], "0.2.0")],
)
def test_update_command_goes_to_an_older_version_only_with_allow_downgrade(
    layout, monkeypatch, flags: list[str], staged_version: str | None
) -> None:
    """``mobaide-agent update`` on 0.3.0 with a signed 0.2.0 manifest: the
    plain command changes nothing; ``--allow-downgrade`` stages it and asks
    for the restart."""
    import mobaide.__main__ as _main
    import mobaide.agent.updater as _updater_mod

    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")
    layout.trust_public_key.write_text(public_key_b64 + "\n", encoding="utf-8")
    manifest_file = layout.prefix / "tmp" / "manifest.json"
    manifest_file.write_text(
        json.dumps({"body": body, "signature_b64": signature_b64}),
        encoding="utf-8",
    )
    monkeypatch.delenv("MOBAIDE_AGENT_TRUST_KEY", raising=False)
    monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(layout.prefix))
    monkeypatch.setenv("MOBAIDE_AGENT_MANIFEST_URL", manifest_file.as_uri())
    monkeypatch.setattr(_main, "__version__", "0.3.0")
    monkeypatch.setattr(_updater_mod, "_build_venv_without_pip", _fake_venv_builder)
    monkeypatch.setattr(_updater_mod, "_finalize_wheel_install", _fake_finalizer)

    parser = _main._build_parser()
    args = parser.parse_args(["update", *flags])

    if staged_version is None:
        assert _main._dispatch(parser, "update", args) == 0
        assert not layout.current_version_file.exists()
        assert list(layout.releases_dir.iterdir()) == []
    else:
        assert _main._dispatch(parser, "update", args) == 75
        assert layout.current_version_file.read_text().strip() == staged_version


def _update_command(
    layout, monkeypatch, *, installed: str, flags: list[str], pinned_key_signed: bool = True
):
    """``mobaide-agent update`` on version ``installed`` with a published,
    signed 0.2.0 manifest. Returns a function that runs the command."""
    import mobaide.__main__ as _main
    import mobaide.agent.updater as _updater_mod

    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")
    if not pinned_key_signed:
        public_key_b64 = _public_key_b64(Ed25519PrivateKey.generate())
    layout.trust_public_key.write_text(public_key_b64 + "\n", encoding="utf-8")
    manifest_file = layout.prefix / "tmp" / "manifest.json"
    manifest_file.write_text(
        json.dumps({"body": body, "signature_b64": signature_b64}),
        encoding="utf-8",
    )
    monkeypatch.delenv("MOBAIDE_AGENT_TRUST_KEY", raising=False)
    monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(layout.prefix))
    monkeypatch.setenv("MOBAIDE_AGENT_MANIFEST_URL", manifest_file.as_uri())
    monkeypatch.setattr(_main, "__version__", installed)
    monkeypatch.setattr(_updater_mod, "_build_venv_without_pip", _fake_venv_builder)
    monkeypatch.setattr(_updater_mod, "_finalize_wheel_install", _fake_finalizer)

    parser = _main._build_parser()
    args = parser.parse_args(["update", *flags])
    return lambda: _main._dispatch(parser, "update", args)


def _operator_lines(stdout: str) -> list[str]:
    return [
        line for line in stdout.splitlines() if line.startswith("[mobaide-agent]")
    ]


@pytest.mark.parametrize(
    "installed, pinned_key_signed, exit_code, versions_named, offers_downgrade",
    [
        pytest.param("0.3.0", True, 0, ["0.2.0", "0.3.0"], True, id="older"),
        pytest.param("0.2.0", True, 0, ["0.2.0"], False, id="same"),
        pytest.param("0.3.0", False, 2, [], False, id="older-not-verified"),
    ],
)
def test_update_command_says_why_nothing_was_installed(
    layout,
    monkeypatch,
    capsys,
    installed: str,
    pinned_key_signed: bool,
    exit_code: int,
    versions_named: list[str],
    offers_downgrade: bool,
) -> None:
    """The exit code is 0 both for "already installed" and for a refused older
    version, so the one line on stdout is all the operator has. Only a
    verified, older version points to ``--allow-downgrade``: a manifest that
    did not pass the signature check must not."""
    run = _update_command(
        layout, monkeypatch,
        installed=installed, flags=[], pinned_key_signed=pinned_key_signed,
    )

    assert run() == exit_code

    (line,) = _operator_lines(capsys.readouterr().out)
    for version in versions_named:
        assert version in line
    assert ("--allow-downgrade" in line) is offers_downgrade
    assert not layout.current_version_file.exists()


def test_update_command_with_allow_downgrade_prints_no_refusal(
    layout, monkeypatch, capsys
) -> None:
    run = _update_command(
        layout, monkeypatch, installed="0.3.0", flags=["--allow-downgrade"],
    )

    assert run() == 75

    assert layout.current_version_file.read_text().strip() == "0.2.0"
    output = capsys.readouterr()
    assert "--allow-downgrade" not in output.out + output.err
    assert "Nothing was installed" not in output.out + output.err
