"""Non-blocking cross-process lock on the voice root.

An installed agent, a source/dev run and the ``mobaide-agent voice`` CLI can
share one voice root; only the holder of this lock downloads, deletes or
garbage-collects. The OS releases the lock when its holder dies.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class FileLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def try_acquire(self) -> bool:
        if self._fd is not None:
            return True
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if sys.platform == "win32":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            if sys.platform == "win32":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            os.close(fd)

    def is_held_elsewhere(self) -> bool:
        """Probe without keeping the lock (status/CLI checks)."""
        if self._fd is not None:
            return False
        if not self._path.exists():
            return False
        try:
            acquired = self.try_acquire()
        except OSError:
            return False
        if acquired:
            self.release()
            return False
        return True
