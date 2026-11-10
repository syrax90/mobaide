"""Antigravity's "conversation already open" marker.

Every ``agy`` process that opens a conversation holds an exclusive
``flock`` on ``<app data>/presence/<conversation id>.lock``. A second
``agy`` that finds the lock taken warns that sending from both may conflict:
two processes on one conversation each keep their own copy of it and
overwrite each other's turns. ``agy acp`` takes no such lock, so a chat
session holds it itself while the conversation is open, and probes it to
tell the user that another app on the PC has the conversation open.

POSIX only: on Windows every check answers "not held".
"""
from __future__ import annotations

import os
import re
import sys
import threading
from pathlib import Path

_SESSION_ID = re.compile(r"^[A-Za-z0-9._-]{1,200}$")

# Conversations a chat session of this process holds. A probe from this
# process would see its own lock as taken (flock is per open file).
_held: set[tuple[str, str]] = set()
_held_guard = threading.Lock()


def default_app_data_dir() -> Path:
    return Path.home() / ".gemini" / "antigravity-cli"


class ConversationPresence:
    """The presence locks of one Antigravity app data folder."""

    def __init__(self, app_data_dir: Path | None = None) -> None:
        self._dir = (app_data_dir or default_app_data_dir()) / "presence"

    def _path(self, session_id: str) -> Path | None:
        if not _SESSION_ID.fullmatch(session_id) or session_id in (".", ".."):
            return None
        return self._dir / f"{session_id}.lock"

    def _key(self, session_id: str) -> tuple[str, str]:
        return (str(self._dir), session_id)

    def open_elsewhere(self, session_id: str) -> bool:
        """Whether another process has the conversation open (blocking I/O)."""
        if sys.platform == "win32":
            return False
        path = self._path(session_id)
        if path is None:
            return False
        with _held_guard:
            if self._key(session_id) in _held:
                return False
        import fcntl

        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        except OSError:
            return False
        finally:
            os.close(fd)
        return False

    def acquire(self, session_id: str) -> PresenceHold | None:
        """Hold the conversation's lock; None when another process has it
        (or the lock cannot be taken). Blocking I/O."""
        if sys.platform == "win32":
            return None
        path = self._path(session_id)
        if path is None:
            return None
        import fcntl

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        except OSError:
            return None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return None
        key = self._key(session_id)
        with _held_guard:
            _held.add(key)
        return PresenceHold(fd, key)


class PresenceHold:
    """A held presence lock; :meth:`release` is idempotent."""

    def __init__(self, fd: int, key: tuple[str, str]) -> None:
        self._fd: int | None = fd
        self._key = key

    @property
    def session_id(self) -> str:
        return self._key[1]

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        with _held_guard:
            _held.discard(self._key)
        # Closing the only descriptor drops the flock.
        try:
            os.close(fd)
        except OSError:
            pass
