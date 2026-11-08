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
import time
from collections.abc import Iterable
from pathlib import Path


# A release is ~0.4 MB of tarball and ~0.4 MB of wheel; the caps leave wide
# headroom while bounding the worst case to MAX_RELEASE_TOTAL_BYTES.
MAX_RELEASE_FILE_BYTES = 16 * 1024 * 1024
MAX_RELEASE_TOTAL_BYTES = 128 * 1024 * 1024
# Rollback re-publishes an older manifest, which needs its files still on disk.
# Two files per release, so this keeps the last ~10 releases.
KEEP_RELEASE_FILES = 20
# An upload never takes the volume below this; SQLite needs the room more.
MIN_FREE_BYTES = 1024 * 1024 * 1024
# A temp file older than this is left over from a killed upload.
STALE_UPLOAD_SECONDS = 3600

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,199}$")
_TMP_PREFIX = ".upload-"


class ReleaseFileError(ValueError):
    """The upload is malformed: bad name, bad digest, or too large."""


class ReleaseStorageFullError(OSError):
    """Storing the upload would leave the volume with less than MIN_FREE_BYTES."""


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
                os.utime(target)
                return False
            self._root.mkdir(parents=True, exist_ok=True)
            # Prune first so a store at its limits frees room before the
            # free-space check, then again so the new file counts.
            keep = {sha256, *protected}
            self._prune_locked(keep)
            if shutil.disk_usage(self._root).free - len(data) < MIN_FREE_BYTES:
                raise ReleaseStorageFullError("not enough free disk space for the release file")
            target_dir.mkdir(exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=target_dir, prefix=_TMP_PREFIX)
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
            self._prune_locked(keep)
            return True

    def _prune_locked(self, keep: set[str]) -> None:
        """Bound the store to KEEP_RELEASE_FILES files and MAX_RELEASE_TOTAL_BYTES.

        Newest files win; files under a ``keep`` digest always stay. Also
        drops temp files a killed upload left behind, and empty directories.
        """
        now = time.time()
        files: list[tuple[float, int, Path]] = []
        kept_count = 0
        kept_bytes = 0
        dirs = [p for p in self._root.iterdir() if p.is_dir() and _SHA256_RE.match(p.name)]
        for d in dirs:
            for f in d.iterdir():
                try:
                    st = f.stat()
                except OSError:
                    continue
                if f.name.startswith(_TMP_PREFIX):
                    if now - st.st_mtime > STALE_UPLOAD_SECONDS:
                        f.unlink(missing_ok=True)
                elif d.name in keep:
                    kept_count += 1
                    kept_bytes += st.st_size
                else:
                    files.append((st.st_mtime, st.st_size, f))

        files.sort(key=lambda item: item[0], reverse=True)
        for _, size, f in files:
            if kept_count < KEEP_RELEASE_FILES and kept_bytes + size <= MAX_RELEASE_TOTAL_BYTES:
                kept_count += 1
                kept_bytes += size
            else:
                f.unlink(missing_ok=True)

        for d in dirs:
            try:
                d.rmdir()
            except OSError:
                pass
