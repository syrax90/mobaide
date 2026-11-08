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
* The previous release dir is retained as a rollback target; the launcher
  GCs releases older than the retained set (current + rollback target) on
  its next boot (``mobaide.launcher._prune_old_releases``).
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
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mobaide._http as _http
from mobaide.agent.install_paths import RELEASE_SENTINEL, is_release_complete
from mobaide.manifest_signing import (
    ManifestSignatureError,
    verify_signature,
)


logger = logging.getLogger(__name__)


# Exit code requested by the updater when a swap has happened. Linux
# ``RestartForceExitStatus=75`` and the Windows supervisor
# (``deploy/windows_launcher.py``) both interpret this as "please bring me
# back up", so the launcher boots into the freshly-staged release on the next
# start. The running agent requests it through ``mobaide.agent.restart``.
EXIT_RESTART_REQUESTED = 75

# Minimum interval between "deferred because busy" retry attempts.
DEFERRED_RETRY_SECONDS = 30 * 60

# Log a WARN if a single update has been deferred for longer than this.
LONG_DEFER_WARN_SECONDS = 24 * 60 * 60

# A ``.staging-*`` / ``.old-*`` scratch dir older than this is treated as a
# crashed-stage leftover safe to GC even if its pid is not this process's — a
# concurrent in-flight stage uses a fresh mtime, so it is never swept.
_STALE_SCRATCH_SECONDS = 24 * 60 * 60

# Suppress the transient console window every ``python -m venv`` and
# ``pip install`` would otherwise pop into the user's session. The agent
# runs under ``pythonw.exe`` via the Scheduled Task (no parent console),
# so a default ``subprocess.run`` of a console-mode child creates a fresh
# console for the child. ``CREATE_NO_WINDOW`` suppresses that while
# preserving ``capture_output=True``'s stdio inheritance.
_WINDOWS_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _version_tuple(version: str) -> tuple[int, ...]:
    """Leading numeric ``X.Y.Z`` components of a version string.

    Strips PEP 440 build/pre-release metadata (``+local``, ``-rc1``) and stops
    at the first non-numeric component, returning ``()`` when nothing parses.
    Deliberately separate from the import-isolated ``launcher._parse_version``
    (which the launcher keeps dependency-free and which only ranks clean
    on-disk ``X.Y.Z`` release-dir names) — do not unify them.
    """
    core = version.split("+", 1)[0].split("-", 1)[0]
    parts: list[int] = []
    for component in core.split("."):
        if component.isdigit():
            parts.append(int(component))
        else:
            break
    return tuple(parts)


def _is_version_newer(candidate: str, current: str) -> bool:
    """Return True only when ``candidate`` is strictly newer than ``current``.

    Conservative: returns False when either version is unparseable, so a
    malformed manifest version never drives a destructive downgrade-style
    stage on the non-forced auto-update path.
    """
    cand = _version_tuple(candidate)
    cur = _version_tuple(current)
    if not cand or not cur:
        return False
    return cand > cur


def _release_venv_python(release_dir: Path) -> Path:
    """Path to a release dir's venv interpreter (``Scripts`` on Windows).

    Mirrors ``mobaide.launcher._resolve_python`` — the interpreter the
    supervisor execs — so the updater can verify a staged release is runnable
    before swapping it into ``releases/<v>``.
    """
    if sys.platform == "win32":
        return release_dir / "venv" / "Scripts" / "python.exe"
    return release_dir / "venv" / "bin" / "python"


# Allowlist for manifest versions used as a ``releases/<v>`` path component.
# Mirrors the ``install.sh`` ``stage_release`` charset guard so the two
# manifest->filesystem boundaries agree on what is safe.
_SAFE_VERSION_RE = re.compile(r"[0-9A-Za-z.+_-]+")


def _assert_safe_version(version: str) -> None:
    """Reject a manifest version that could escape ``releases/`` as a path.

    Allowlist: a version may contain only ``[0-9A-Za-z.+_-]`` and may not be
    ``.`` / ``..``. This blocks path separators (``/``, ``\\``), the Windows
    drive marker (``:`` — a ``PureWindowsPath`` join with ``"z:evil"`` is
    *drive-relative* and drops the ``releases/`` anchor), NUL, and any other
    metacharacter by construction. ``version`` is Ed25519-verified, so this is
    defense-in-depth against a compromised / buggy signer; raised as
    ``ValueError`` so ``consider_manifest`` maps it to ``"signature_failed"``.
    """
    if (
        not version
        or version in (".", "..")
        or not _SAFE_VERSION_RE.fullmatch(version)
    ):
        raise ValueError(
            f"manifest version is not a safe path component: {version!r}"
        )


def _scratch_pid(name: str) -> int | None:
    """Trailing ``-<pid>`` of a ``.staging-<v>-<pid>`` / ``.old-<v>-<pid>`` name."""
    tail = name.rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _is_stale_scratch(path: Path, now: float) -> bool:
    """True when ``path`` is older than the stale-scratch horizon."""
    try:
        return (now - path.stat().st_mtime) > _STALE_SCRATCH_SECONDS
    except OSError:
        return False


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


def _wheel_basename_from_url(url: str) -> str:
    """Return the canonical wheel filename from a manifest ``wheel_url``.

    pip 25.2+ enforces PEP 427 wheel-filename validation
    (``name-version-pyver-abi-platform.whl`` ⇒ ≥4 dashes in the stem). The
    release workflow always emits a compliant URL, so deriving the basename
    from the URL gives pip the filename it needs. A non-compliant URL is
    rejected fail-closed before any download begins.

    ``file://`` URLs are handled with ``pathlib.Path`` because ``urlparse``
    routes Windows paths like ``file://C:\\…\\foo.whl`` entirely into
    ``netloc`` (no forward slashes ⇒ empty ``path``); the Path-based split
    matches what the sibling ``_download_to`` already does.
    """
    if url.startswith("file://"):
        name = Path(url.removeprefix("file://")).name
    else:
        name = posixpath.basename(urllib.parse.urlparse(url).path)
    # Reject path-separator characters surviving in the basename: a
    # `posixpath`-derived basename leaves Windows backslashes literal, so a
    # crafted `wheel_url` like `https://.../legit/..\..\evil-1-2-3-4.whl`
    # would otherwise pass the PEP 427 shape check below and `Path(tmpdir) /
    # name` would resolve outside ``tmpdir`` on Windows.
    if any(ch in name for ch in ("/", "\\", "\x00")):
        raise ValueError(
            f"manifest wheel_url basename contains path-separator chars: {name!r} (from {url!r})"
        )
    if not name.endswith(".whl") or name.count("-") < 4:
        raise ValueError(
            f"manifest wheel_url has non-PEP427 filename: {name!r} (from {url!r})"
        )
    return name


class StagedBinaryUpdater:
    """Apply signed manifest rollouts via the staged-binary swap pattern."""

    def __init__(
        self,
        layout: InstallLayout,
        public_key_b64: str,
        session_manager: Any | None,
        *,
        current_version: str,
        venv_builder: Any | None = None,
        finalizer: Any | None = None,
        exit_func: Any | None = None,
        busy_probe: Callable[[], bool] | None = None,
    ) -> None:
        self._layout = layout
        self._public_key_b64 = public_key_b64
        self._session_manager = session_manager
        self._current_version = current_version
        # Extra non-session work that must not be cut by a restart (an active
        # voice dictation); forced updates bypass it like they bypass sessions.
        self._busy_probe = busy_probe
        # Two-phase install: build the pip-less venv in staging (move-safe),
        # then finalize (pip + wheel) at the final path AFTER the swap so no
        # console-script shebang ever encodes the staging dir. Both phases are
        # injectable so tests can stub the subprocess work.
        self._venv_builder = venv_builder or _build_venv_without_pip
        self._finalizer = finalizer or _finalize_wheel_install
        self._exit_func = exit_func or sys.exit
        self._first_deferred_at: float | None = None
        # Set once a release is staged. The agent keeps running for its
        # graceful shutdown after asking for the restart, and a manifest that
        # arrives in that window must not stage the same release again.
        self._staged_version: str | None = None
        self._lock = asyncio.Lock()

    @property
    def current_version(self) -> str:
        return self._current_version

    async def consider_manifest(
        self,
        manifest_body: dict[str, Any],
        signature_b64: str,
        latest_version: str,
        *,
        force: bool = False,
        exit_after_stage: bool = True,
    ) -> str:
        """Apply the manifest if appropriate.

        ``manifest_body`` is the full manifest dict (the same shape that was
        Ed25519-signed by the release pipeline). Delivered inline over the
        relay WebSocket — no HTTP fetch on this path.

        Returns one of:
            ``"no_change"`` — already on this version.
            ``"deferred"`` — busy session; will retry later.
            ``"staged"`` — release written + ``current_version`` swapped. When
            ``exit_after_stage`` is ``True`` (the default — used by the relay
            callback and the standalone CLI) ``self._exit_func`` is invoked
            with ``EXIT_RESTART_REQUESTED`` before the function returns. HTTP
            wrappers pass ``False`` so they can flush the response and then
            schedule the exit on the event loop. A manifest for the version
            this updater already staged returns ``"staged"`` with no new work
            and no second exit call (the restart is still pending).
            ``"signature_failed"`` — fail-closed; agent stays on current.
        """
        if not latest_version:
            return "no_change"
        if latest_version == self._staged_version:
            return "staged"
        if not force and latest_version == self._current_version:
            self._first_deferred_at = None
            return "no_change"
        if not force and not _is_version_newer(latest_version, self._current_version):
            # Auto/poll updates are forward-only: never stage a non-newer (or
            # unparseable) version. This is the guard that stops a stale or
            # mismatched manifest — e.g. a newer dev build seeing an older
            # published version — from driving a destructive re-stage. Forced
            # paths (user "install now", 4002 protocol-incompatible recovery)
            # bypass this so a deliberate same/older reinstall still works.
            self._first_deferred_at = None
            logger.info(
                "updater.forward_only_skipped latest=%s current=%s",
                latest_version, self._current_version,
            )
            return "no_change"

        async with self._lock:
            # Re-check under the lock: a caller queued behind a stage of the
            # same release must not rebuild it while the restart is pending.
            if latest_version == self._staged_version:
                return "staged"
            if not force and not self._is_idle():
                self._note_deferred()
                return "deferred"
            self._first_deferred_at = None

            if not isinstance(manifest_body, dict):
                logger.error(
                    "updater.signature_failed: manifest body is not a dict (got %s)",
                    type(manifest_body).__name__,
                )
                return "signature_failed"

            try:
                verify_signature(manifest_body, signature_b64, self._public_key_b64)
            except ManifestSignatureError as exc:
                logger.error("updater.signature_failed: %s", exc)
                return "signature_failed"

            target_version = str(manifest_body.get("version", ""))
            if not target_version:
                logger.warning("updater.manifest_missing_version")
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
            self._staged_version = target_version
            if exit_after_stage:
                self._exit_func(EXIT_RESTART_REQUESTED)
            return "staged"

    async def force_update_now(
        self,
        manifest_body: dict[str, Any],
        signature_b64: str,
        latest_version: str,
        *,
        exit_after_stage: bool = True,
    ) -> str:
        """User-triggered update — skips activity-awareness."""
        return await self.consider_manifest(
            manifest_body,
            signature_b64,
            latest_version,
            force=True,
            exit_after_stage=exit_after_stage,
        )

    def _is_idle(self) -> bool:
        if self._busy_probe is not None:
            try:
                if self._busy_probe():
                    return False
            except Exception as exc:
                logger.warning("updater.busy_probe_failed: %s", exc)
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

    def _stage_release(
        self, manifest_body: dict[str, Any], target_version: str
    ) -> Path:
        # Validate the manifest shape BEFORE any on-disk side effects so a
        # rejected manifest leaves no empty ``releases/<v>/`` skeleton — the
        # fail-closed contract documented at the top of this module.
        _assert_safe_version(target_version)
        tarball_url = str(manifest_body.get("tarball_url", ""))
        tarball_sha = str(manifest_body.get("tarball_sha256", "")).lower()
        wheel_url = str(manifest_body.get("wheel_url", ""))
        wheel_sha = str(manifest_body.get("wheel_sha256", "")).lower()

        if not tarball_url or not tarball_sha:
            raise ValueError("manifest missing tarball_url/tarball_sha256")
        if not wheel_url or not wheel_sha:
            raise ValueError("manifest missing wheel_url/wheel_sha256")
        wheel_name = _wheel_basename_from_url(wheel_url)

        releases_dir = self._layout.releases_dir
        releases_dir.mkdir(parents=True, exist_ok=True)
        # Clear scratch dirs and stale half-finalized releases left by a
        # previously-crashed stage before building.
        self._sweep_scratch_dirs()
        self._reclaim_incomplete_releases()

        # Build the release in a scratch dir on the SAME filesystem as
        # ``releases/`` (so the swap is an atomic ``os.replace`` rename), swap
        # the pip-less venv into ``releases/<v>``, then finalize (pip + wheel)
        # at the FINAL path. A failed download / extract / venv build never
        # touches an existing release; a failed finalize tears down the
        # incomplete release and restores the prior copy. The scratch name
        # never parses as a version, and the swapped-but-not-yet-finalized
        # release has no sentinel, so launcher rollback/prune/recovery ignore
        # both until ``_write_release_sentinel`` marks the release complete.
        staging_dir = releases_dir / f".staging-{target_version}-{os.getpid()}"
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        staging_dir.mkdir(parents=True, exist_ok=False)
        release_dir = releases_dir / target_version
        backup: Path | None = None
        swapped = False
        try:
            with tempfile.TemporaryDirectory(prefix="mobaide-agent-") as tmpdir:
                tarball_path = Path(tmpdir) / "agent.tar.gz"
                wheel_path = Path(tmpdir) / wheel_name
                self._download_to(tarball_url, tarball_path)
                self._verify_sha256(tarball_path, tarball_sha)
                self._download_to(wheel_url, wheel_path)
                self._verify_sha256(wheel_path, wheel_sha)

                with tarfile.open(tarball_path, mode="r:gz") as tar:
                    # `filter="data"` mitigates CVE-2007-4559 (tar path
                    # traversal) and matches the Python 3.14+ default. Required
                    # because the tarball arrives over the network — a malicious
                    # manifest could otherwise write outside ``staging_dir``.
                    tar.extractall(staging_dir, filter="data")

                # Build the venv WITHOUT pip in staging: only the bin/python
                # symlinks exist (no console-script shebangs that would bake in
                # the staging path and break once the dir is moved). pip + the
                # wheel are installed below, AFTER the swap, at the final path.
                venv_dir = staging_dir / "venv"
                self._venv_builder(venv_dir)

                # Refuse to swap in a release whose interpreter is missing — that
                # is exactly the bricked state (a launcher that can't exec its
                # venv python) this rewrite exists to prevent.
                staged_python = _release_venv_python(staging_dir)
                if not staged_python.exists():
                    raise RuntimeError(
                        f"staged venv has no interpreter at {staged_python}"
                    )

                # Atomic swap into the final path, capturing any displaced prior
                # release so a post-swap finalize failure can restore it.
                backup = self._swap_into_place(staging_dir, release_dir)
                swapped = True

                # POST-SWAP: bootstrap pip + install the wheel with the FINAL
                # interpreter so every shebang points at releases/<v>/venv, not
                # the (now-gone) staging dir. Still inside the tmpdir context so
                # ``wheel_path`` is valid.
                self._finalizer(_release_venv_python(release_dir), wheel_path)

            # Fully built at the final path: mark complete. Only now is the
            # release eligible for selection, and only now does
            # ``consider_manifest`` flip ``current_version``.
            _write_release_sentinel(release_dir)
            if backup is not None:
                shutil.rmtree(backup, ignore_errors=True)
        except BaseException:
            if swapped:
                # A finalize/sentinel failure left an incomplete release at the
                # final path. Tear it down and restore the prior good copy (if
                # any) so a re-stage is non-destructive.
                shutil.rmtree(release_dir, ignore_errors=True)
                if backup is not None:
                    try:
                        os.replace(backup, release_dir)
                    except OSError:
                        logger.error(
                            "updater.finalize_restore_failed release_dir=%s "
                            "backup=%s (previous release left at the backup path)",
                            release_dir, backup,
                        )
            # If the swap never completed, ``_swap_into_place`` already restored
            # any displaced release itself; only the staging dir needs cleaning.
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise
        return release_dir

    def _sweep_scratch_dirs(self) -> None:
        """Best-effort removal of leftover ``.staging-*`` / ``.old-*`` dirs.

        A stage killed mid-build can leave scratch dirs behind. Only this
        process's own leftovers (matching pid) and dirs old enough that their
        creator is certainly gone (``_STALE_SCRATCH_SECONDS``) are removed —
        NEVER a concurrent process's in-flight scratch (a separate
        ``mobaide-agent update`` run uses a different pid and a fresh mtime), so
        a concurrent stage is not corrupted into a spurious failure. Either way
        these names never parse as a version, so launcher GC ignores them.
        """
        releases_dir = self._layout.releases_dir
        my_pid = os.getpid()
        now = time.time()
        for pattern in (".staging-*", ".old-*"):
            for scratch in releases_dir.glob(pattern):
                if (
                    _scratch_pid(scratch.name) == my_pid
                    or _is_stale_scratch(scratch, now)
                ):
                    shutil.rmtree(scratch, ignore_errors=True)

    def _reclaim_incomplete_releases(self) -> None:
        """Remove stale, never-finalized ``releases/<v>`` dirs.

        The two-phase install swaps a pip-less venv into ``releases/<v>`` and
        only then installs the wheel + writes the sentinel. A crash in that
        post-swap window leaves a version-named dir every selector already
        ignores (it is not :func:`is_release_complete`), but whose name is not a
        ``.staging-*`` / ``.old-*`` scratch pattern, so ``_sweep_scratch_dirs``
        won't reclaim it. Reclaim it here — but ONLY when it is stale (a release
        another process just swapped in and is mid-finalize on has a fresh mtime
        and is spared) and never the current version.
        """
        releases_dir = self._layout.releases_dir
        now = time.time()
        try:
            entries = [p for p in releases_dir.iterdir() if p.is_dir()]
        except OSError:
            return
        for entry in entries:
            if entry.name == self._current_version:
                continue
            if _version_tuple(entry.name) == ():
                continue  # scratch/foreign name — handled by _sweep_scratch_dirs
            if is_release_complete(entry):
                continue
            if not _is_stale_scratch(entry, now):
                continue  # fresh — may be a concurrent finalize in progress
            shutil.rmtree(entry, ignore_errors=True)
            logger.info(
                "updater.reclaimed_incomplete_release version=%s", entry.name
            )

    @staticmethod
    def _swap_into_place(staging_dir: Path, release_dir: Path) -> Path | None:
        """Atomically move a fully-built staging dir to ``releases/<v>``.

        Returns the ``.old-*`` backup path of a displaced prior release — which
        the caller deletes only after the post-swap finalize + sentinel succeed,
        so a failed finalize can restore it — or ``None`` when there was no
        prior release at ``release_dir`` (the normal new-version case).

        ``os.replace`` renames atomically on the same filesystem when the
        target does not exist. When ``releases/<v>`` already exists (a re-stage
        of an on-disk version), the existing dir is moved aside first and
        restored if the forward move fails, so the swap is all-or-nothing and
        never leaves the release missing. On Windows a re-stage of the
        *currently running* version raises (the live ``python.exe`` is locked);
        that propagates and the caller cleans the staging dir, leaving the
        running release intact — non-destructive by construction.
        """
        if not release_dir.exists():
            os.replace(staging_dir, release_dir)
            return None
        backup = release_dir.parent / f".old-{release_dir.name}-{os.getpid()}"
        if backup.exists():
            shutil.rmtree(backup)
        os.replace(release_dir, backup)
        try:
            os.replace(staging_dir, release_dir)
        except BaseException:
            try:
                os.replace(backup, release_dir)
            except OSError:
                logger.error(
                    "updater.swap_restore_failed release_dir=%s backup=%s "
                    "(previous release left at the backup path)",
                    release_dir, backup,
                )
            raise
        return backup

    @staticmethod
    def _download_to(url: str, dest: Path) -> None:
        if url.startswith("file://") or url.startswith("/"):
            src = Path(url.removeprefix("file://"))
            shutil.copyfile(src, dest)
            return
        with _http.urlopen(url, timeout=120) as resp, dest.open("wb") as fh:
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


def _build_venv_without_pip(venv_dir: Path) -> None:
    """Create a pip-less venv — only the ``bin/python*`` symlinks, no scripts.

    A ``--without-pip`` venv carries no console-script shebangs (``pip``,
    ``mobaide-agent``), only interpreter symlinks that point at the stable base
    interpreter and survive an ``os.replace`` move. That makes it safe to swap
    into ``releases/<v>`` before pip and the wheel are installed; the
    shebang-bearing scripts are written later by :func:`_finalize_wheel_install`
    running the FINAL-path interpreter, so they never encode the staging dir
    (the "bad interpreter" bug this avoids).
    """
    venv_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(venv_dir)],
        check=True,
        capture_output=True,
        creationflags=_WINDOWS_NO_WINDOW,
    )


def _finalize_wheel_install(python: Path, wheel_path: Path) -> None:
    """Bootstrap pip and install the wheel using ``python`` at its final path.

    Runs AFTER the venv is swapped into ``releases/<v>`` so every shebang pip
    writes (``pip``, ``mobaide-agent``) points at the final interpreter. pip is
    always invoked as ``python -m pip`` — never the ``bin/pip`` script — so the
    finalize step itself never depends on a (possibly not-yet-written) shebang.
    """
    # ``--default-pip`` also writes the unversioned ``pip`` console script so an
    # updater-built venv matches an install.sh-built one (which uses plain
    # ``python -m venv``). All scripts get the final-path shebang because this
    # runs the final-path interpreter post-swap.
    subprocess.run(
        [str(python), "-m", "ensurepip", "--default-pip"],
        check=True,
        capture_output=True,
        creationflags=_WINDOWS_NO_WINDOW,
    )
    subprocess.run(
        [str(python), "-m", "pip", "install", "--no-cache-dir", "--upgrade", "pip"],
        check=True,
        capture_output=True,
        creationflags=_WINDOWS_NO_WINDOW,
    )
    subprocess.run(
        [str(python), "-m", "pip", "install", "--no-cache-dir", str(wheel_path)],
        check=True,
        capture_output=True,
        creationflags=_WINDOWS_NO_WINDOW,
    )


def _write_release_sentinel(release_dir: Path) -> None:
    """Mark a release complete — the final step of a successful install.

    Written only after pip + the wheel are installed at the final path, so a
    release carrying ``RELEASE_SENTINEL`` is guaranteed runnable. The launcher
    self-heal, rollback, recovery, and prune logic gate on its presence (or the
    legacy console-script fallback in ``install_paths.is_release_complete``), so
    a half-built release swapped into place but not yet finalized is never
    selected. Content is irrelevant — presence is the signal — so a torn write
    is harmless (finalize has already succeeded by the time this runs).
    """
    (release_dir / RELEASE_SENTINEL).write_text(
        release_dir.name + "\n", encoding="utf-8"
    )
