"""Detect a Claude Code turn that the user ended with Esc or Ctrl+C.

Such a turn fires no ``Stop`` and no ``idle_prompt``, but the CLI appends a
main-chain user entry starting ``[Request interrupted by user`` to the
transcript. An interrupt key while the session works opens a short watch on the
transcript path from the hooks' ``transcript_path``; only that entry ends the
turn, because Esc alone may just close a menu.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# PTY writes that can interrupt a turn: Esc, a double Esc delivered as one
# chunk, and Ctrl+C.
INTERRUPT_KEYS = frozenset({b"\x1b", b"\x1b\x1b", b"\x03"})

INTERRUPT_MARKER = "[Request interrupted by user"
_INTERRUPT_MARKER_BYTES = INTERRUPT_MARKER.encode()

# The CLI writes the entry within milliseconds of the key; the window only
# leaves room for a tool call that is slow to abort.
INTERRUPT_WATCH_SECONDS = 15.0
INTERRUPT_POLL_SECONDS = 0.25

# Bound on one read and on a partial line kept between reads, so a huge entry
# appended during the watch cannot grow memory without limit.
_READ_LIMIT_BYTES = 1 << 20


def is_interrupt_entry(line: bytes) -> bool:
    """Whether one transcript line is the CLI's record of an interrupted turn."""
    if _INTERRUPT_MARKER_BYTES not in line:
        return False
    try:
        entry: Any = json.loads(line)
    except ValueError:
        return False
    if not isinstance(entry, dict) or entry.get("type") != "user":
        return False
    if entry.get("isSidechain") is True:
        return False
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content.startswith(INTERRUPT_MARKER)
    if not isinstance(content, list):
        return False
    return any(
        isinstance(item, dict)
        and item.get("type") == "text"
        and isinstance(item.get("text"), str)
        and item["text"].startswith(INTERRUPT_MARKER)
        for item in content
    )


def _read_from(path: str, offset: int) -> bytes | None:
    try:
        with open(path, "rb") as handle:
            handle.seek(offset)
            return handle.read(_READ_LIMIT_BYTES)
    except OSError:
        return None


class InterruptWatch:
    """Watches a session transcript for an interrupt after an interrupt key.

    ``arm`` must run before the key reaches the PTY: it records the
    transcript's size at that moment, so an older interrupt entry is never
    read as this one.
    """

    def __init__(
        self,
        on_interrupt: Callable[[], None],
        *,
        window_seconds: float = INTERRUPT_WATCH_SECONDS,
        poll_seconds: float = INTERRUPT_POLL_SECONDS,
    ) -> None:
        self._on_interrupt = on_interrupt
        self._window_seconds = window_seconds
        self._poll_seconds = poll_seconds
        self._path: str | None = None
        self._deadline = 0.0
        self._task: asyncio.Task[None] | None = None

    @property
    def active(self) -> bool:
        return self._task is not None

    def note_transcript(self, path: Any) -> None:
        """Record the transcript path a hook payload names."""
        if not isinstance(path, str) or not path.endswith(".jsonl"):
            return
        if not os.path.isabs(path) or path == self._path:
            return
        self._path = path
        self.cancel()

    def arm(self) -> None:
        """Start, or extend, a watch from the transcript's current end."""
        self._deadline = time.monotonic() + self._window_seconds
        if self._task is not None or self._path is None:
            return
        try:
            offset = os.stat(self._path).st_size
        except OSError:
            return
        self._task = asyncio.get_running_loop().create_task(
            self._watch(self._path, offset),
        )

    def cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _watch(self, path: str, offset: int) -> None:
        pending = b""
        try:
            while time.monotonic() < self._deadline:
                await asyncio.sleep(self._poll_seconds)
                chunk = await asyncio.to_thread(_read_from, path, offset)
                if chunk is None:
                    return
                offset += len(chunk)
                lines = (pending + chunk).split(b"\n")
                pending = lines.pop()
                if len(pending) > _READ_LIMIT_BYTES:
                    pending = b""
                if any(is_interrupt_entry(line) for line in lines):
                    self._task = None
                    self._on_interrupt()
                    return
        except Exception:
            logger.debug("interrupt_watch.failed path=%s", path, exc_info=True)
        finally:
            if self._task is asyncio.current_task():
                self._task = None
