"""Staged-binary auto-updater for ``mobaide_agent``.

State machine
-------------

    +---------------+   manifest    +-----------------+
    |  Idle (Run)   |  ----------> |  Verify         |
    +---------------+              +-----------------+
                                          |
                              valid sig + sha + new version
                                          v
                                  +-----------------+
                                  |  Defer if busy  |  -- retry every 30 min
                                  +-----------------+
                                          |
                                       idle
                                          v
                                  +-----------------+
                                  |  Stage release  |  releases/<v>/
                                  +-----------------+
                                          |
                                          v
                                  +-----------------+
                                  |  Swap pointer   |  current_version
                                  +-----------------+
                                          |
                                          v
                                  +-----------------+
                                  |  Exit code 75   |  supervisor reboot
                                  +-----------------+

* ``current_version`` swap is atomic via ``os.replace``.
* The previous release dir is retained for one cycle as a rollback target
  (gc'd by the launcher on the next successful boot of a new release).
* Activity-awareness: the updater queries ``SessionManager.list_sessions()``
  and defers when any session is non-stopped.
* Signature verification is fail-closed: a malformed or invalid signature
  rejects the manifest and the agent stays on the current version.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mobaide.manifest_signing import (
    ManifestSignatureError,
    verify_signature,
)


logger = logging.getLogger(__name__)


# Exit code requested by the updater when a swap has happened. Linux
# ``RestartForceExitStatus=75`` and Windows SC_ACTION_RESTART both
# interpret this as "please bring me back up", so the launcher boots into
# the freshly-staged release on the next start.
EXIT_RESTART_REQUESTED = 75

# Minimum interval between "deferred because busy" retry attempts.
DEFERRED_RETRY_SECONDS = 30 * 60

# Log a WARN if a single update has been deferred for longer than this.
LONG_DEFER_WARN_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class InstallLayout:
    """File-system layout the updater operates on."""

    prefix: Path

    @property
    def releases_dir(self) -> Path:
        return self.prefix / "releases"

    @property
    def current_version_file(self) -> Path:
        return self.prefix / "current_version"

    @property
    def trust_dir(self) -> Path:
        return self.prefix / "trust"

    @property
    def trust_public_key(self) -> Path:
        return self.trust_dir / "central_server.pub"


class StagedBinaryUpdater:
    """Apply signed manifest rollouts via the staged-binary swap pattern."""

    def __init__(
        self,
        layout: InstallLayout,
        public_key_b64: str,
        session_manager: Any | None,
        *,
        current_version: str,
        downloader: Any | None = None,
        installer: Any | None = None,
        exit_func: Any | None = None,
    ) -> None:
        self._layout = layout
        self._public_key_b64 = public_key_b64
        self._session_manager = session_manager
        self._current_version = current_version
        self._downloader = downloader or _http_download
        self._installer = installer or _install_wheel_into_venv
        self._exit_func = exit_func or sys.exit
        self._first_deferred_at: float | None = None
        self._lock = asyncio.Lock()

    @property
    def current_version(self) -> str:
        return self._current_version

    async def consider_manifest(
        self,
        manifest_url: str,
        signature_b64: str,
        latest_version: str,
        *,
        force: bool = False,
        exit_after_stage: bool = True,
    ) -> str:
        """Apply the manifest if appropriate.

        Returns one of:
            ``"no_change"`` — already on this version.
            ``"deferred"`` — busy session; will retry later.
            ``"staged"`` — release written + ``current_version`` swapped. When
            ``exit_after_stage`` is ``True`` (the default — used by the relay
            callback and the standalone CLI) ``self._exit_func`` is invoked
            with ``EXIT_RESTART_REQUESTED`` before the function returns. HTTP
            wrappers pass ``False`` so they can flush the response and then
            schedule the exit on the event loop.
            ``"signature_failed"`` — fail-closed; agent stays on current.
        """
        if not latest_version:
            return "no_change"
        if not force and latest_version == self._current_version:
            self._first_deferred_at = None
            return "no_change"

        async with self._lock:
            if not force and not self._is_idle():
                self._note_deferred()
                return "deferred"
            self._first_deferred_at = None

            try:
                manifest_body = await self._download_manifest(manifest_url)
                verify_signature(manifest_body, signature_b64, self._public_key_b64)
            except ManifestSignatureError as exc:
                logger.error("updater.signature_failed url=%s: %s", manifest_url, exc)
                return "signature_failed"
            except Exception as exc:
                logger.warning("updater.manifest_fetch_failed url=%s: %s", manifest_url, exc)
                return "signature_failed"

            target_version = str(manifest_body.get("version", ""))
            if not target_version:
                logger.warning("updater.manifest_missing_version url=%s", manifest_url)
                return "signature_failed"

            try:
                release_dir = await asyncio.to_thread(
                    self._stage_release, manifest_body, target_version
                )
            except Exception as exc:
                logger.error("updater.stage_failed version=%s: %s", target_version, exc)
                return "signature_failed"

            try:
                await asyncio.to_thread(self._swap_pointer, target_version)
            except Exception as exc:
                logger.error(
                    "updater.swap_failed version=%s release_dir=%s: %s",
                    target_version, release_dir, exc,
                )
                shutil.rmtree(release_dir, ignore_errors=True)
                return "signature_failed"

            logger.info(
                "updater.staged from=%s to=%s release_dir=%s",
                self._current_version, target_version, release_dir,
            )
            if exit_after_stage:
                self._exit_func(EXIT_RESTART_REQUESTED)
            return "staged"

    async def force_update_now(
        self,
        manifest_url: str,
        signature_b64: str,
        latest_version: str,
        *,
        exit_after_stage: bool = True,
    ) -> str:
        """User-triggered update — skips activity-awareness."""
        return await self.consider_manifest(
            manifest_url,
            signature_b64,
            latest_version,
            force=True,
            exit_after_stage=exit_after_stage,
        )

    def _is_idle(self) -> bool:
        if self._session_manager is None:
            return True
        try:
            sessions = list(self._session_manager.list_sessions())
        except Exception:
            return True
        return all(self._session_is_stopped(s) for s in sessions)

    @staticmethod
    def _session_is_stopped(session: Any) -> bool:
        state = getattr(session, "state", None)
        value = getattr(state, "value", state)
        return str(value).lower() in {"stopped", "crashed"}

    def _note_deferred(self) -> None:
        now = time.monotonic()
        if self._first_deferred_at is None:
            self._first_deferred_at = now
            return
        elapsed = now - self._first_deferred_at
        if elapsed > LONG_DEFER_WARN_SECONDS:
            logger.warning(
                "updater.deferred_too_long seconds=%d threshold=%d",
                int(elapsed), LONG_DEFER_WARN_SECONDS,
            )

    async def _download_manifest(self, manifest_url: str) -> dict[str, Any]:
        if manifest_url.startswith("file://") or manifest_url.startswith("/"):
            path = Path(manifest_url.removeprefix("file://"))
            text = path.read_text(encoding="utf-8")
        else:
            text = await asyncio.to_thread(self._downloader, manifest_url)
        import json as _json
        record = _json.loads(text)
        if isinstance(record, dict) and "body" in record and "signature_b64" in record:
            return record["body"]
        return record

    def _stage_release(
        self, manifest_body: dict[str, Any], target_version: str
    ) -> Path:
        release_dir = self._layout.releases_dir / target_version
        if release_dir.exists():
            shutil.rmtree(release_dir)
        release_dir.mkdir(parents=True, exist_ok=False)

        tarball_url = str(manifest_body.get("tarball_url", ""))
        tarball_sha = str(manifest_body.get("tarball_sha256", "")).lower()
        wheel_url = str(manifest_body.get("wheel_url", ""))
        wheel_sha = str(manifest_body.get("wheel_sha256", "")).lower()

        if not tarball_url or not tarball_sha:
            raise ValueError("manifest missing tarball_url/tarball_sha256")
        if not wheel_url or not wheel_sha:
            raise ValueError("manifest missing wheel_url/wheel_sha256")

        with tempfile.TemporaryDirectory(prefix="mobaide-agent-") as tmpdir:
            tarball_path = Path(tmpdir) / "agent.tar.gz"
            wheel_path = Path(tmpdir) / "agent.whl"
            self._download_to(tarball_url, tarball_path)
            self._verify_sha256(tarball_path, tarball_sha)
            self._download_to(wheel_url, wheel_path)
            self._verify_sha256(wheel_path, wheel_sha)

            with tarfile.open(tarball_path, mode="r:gz") as tar:
                # `filter="data"` mitigates CVE-2007-4559 (tar path traversal)
                # and matches the Python 3.14+ default. Required because the
                # tarball arrives over the network — a malicious manifest
                # could otherwise write outside ``release_dir``.
                tar.extractall(release_dir, filter="data")

            venv_dir = release_dir / "venv"
            self._installer(venv_dir, wheel_path)
        return release_dir

    @staticmethod
    def _download_to(url: str, dest: Path) -> None:
        if url.startswith("file://") or url.startswith("/"):
            src = Path(url.removeprefix("file://"))
            shutil.copyfile(src, dest)
            return
        with urllib.request.urlopen(url, timeout=120) as resp, dest.open("wb") as fh:
            shutil.copyfileobj(resp, fh)

    @staticmethod
    def _verify_sha256(path: Path, expected: str) -> None:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        actual = digest.hexdigest()
        if actual.lower() != expected.lower():
            raise ValueError(
                f"sha256 mismatch for {path.name}: expected {expected}, got {actual}"
            )

    def _swap_pointer(self, new_version: str) -> None:
        target = self._layout.current_version_file
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(new_version + "\n", encoding="utf-8")
        os.replace(tmp, target)


def _http_download(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read().decode("utf-8")


def _install_wheel_into_venv(venv_dir: Path, wheel_path: Path) -> None:
    """Create a venv at ``venv_dir`` and install the wheel into it."""
    venv_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        check=True,
        capture_output=True,
    )
    if sys.platform == "win32":
        pip = venv_dir / "Scripts" / "pip.exe"
    else:
        pip = venv_dir / "bin" / "pip"
    subprocess.run(
        [str(pip), "install", "--no-cache-dir", "--upgrade", "pip"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [str(pip), "install", "--no-cache-dir", str(wheel_path)],
        check=True,
        capture_output=True,
    )
