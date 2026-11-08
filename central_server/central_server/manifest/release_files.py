"""Content-addressed store for agent release files (source tarball + wheel).

Each file lives at ``<root>/<sha256>/<filename>``, and the public URL has the
same shape. The path names the bytes, so a URL never changes meaning: the
Cloudflare edge may cache it forever, and a re-run release that rebuilds
different bytes gets a new URL instead of a stale cache hit.

The relay hosts these files itself because anonymous GitHub release downloads
are not reliable enough for the update path: the GitHub edge has served
anonymous 504s for every repository for long stretches.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import threading
from collections.abc import Iterable
from pathlib import Path


MAX_RELEASE_FILE_BYTES = 32 * 1024 * 1024
# Rollback re-publishes an older manifest, which needs its files still on disk.
# Two files per release, so this keeps the last ~10 releases (a few MB).
KEEP_RELEASE_FILES = 20

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,199}$")


class ReleaseFileError(ValueError):
    """The upload is malformed: bad name, bad digest, or too large."""


def valid_key(sha256: str, filename: str) -> bool:
    return bool(_SHA256_RE.match(sha256)) and bool(_FILENAME_RE.match(filename))


class ReleaseFileStore:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._lock = threading.Lock()

    def path_for(self, sha256: str, filename: str) -> Path | None:
        if not valid_key(sha256, filename):
            return None
        path = self._root / sha256 / filename
        return path if path.is_file() else None

    def save(
        self,
        sha256: str,
        filename: str,
        data: bytes,
        *,
        protected: Iterable[str] = (),
    ) -> bool:
        """Store ``data`` under its digest; return True when the file is new.

        Blocking — call it through ``asyncio.to_thread``. ``protected`` names
        digests that pruning must keep (the live manifest's files).
        """
        if not valid_key(sha256, filename):
            raise ReleaseFileError("sha256 must be 64 lowercase hex chars and the filename a plain name")
        if len(data) > MAX_RELEASE_FILE_BYTES:
            raise ReleaseFileError(f"file is larger than {MAX_RELEASE_FILE_BYTES} bytes")
        actual = hashlib.sha256(data).hexdigest()
        if actual != sha256:
            raise ReleaseFileError(f"sha256 mismatch: the URL says {sha256}, the body is {actual}")

        with self._lock:
            target_dir = self._root / sha256
            target = target_dir / filename
            if target.is_file():
                os.utime(target_dir)
                return False
            target_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=target_dir, prefix=".upload-")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.chmod(tmp, 0o644)
                os.replace(tmp, target)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
            self._prune_locked(keep={sha256, *protected})
            return True

    def _prune_locked(self, keep: set[str]) -> None:
        entries = [
            p for p in self._root.iterdir()
            if p.is_dir() and _SHA256_RE.match(p.name)
        ]
        entries.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in entries[KEEP_RELEASE_FILES:]:
            if stale.name not in keep:
                shutil.rmtree(stale, ignore_errors=True)
