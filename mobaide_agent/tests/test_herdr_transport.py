"""Tests for the byte transports of the Herdr backends
(``mobaide.herdr.transport``).

The bridge to a pane is a child process with piped stdio. A bridge that stops
reading its stdin must not keep the pane view from closing: that close runs
when a client leaves, when control changes and when the agent shuts down.

The child here is the Python interpreter. No test starts ``herdr``.
"""
from __future__ import annotations

import asyncio
import sys

import pytest

from mobaide.herdr.transport import spawn_piped

# More than any pipe buffer holds, so the write waits for a reader.
PIPE_OVERFLOW_BYTES = 4 * 1024 * 1024
# The child ends on its own if a test fails before it ends it.
IDLE_CHILD = [sys.executable, "-c", "import time; time.sleep(30)"]


async def test_terminate_returns_while_a_stdin_write_is_blocked() -> None:
    process = await spawn_piped(IDLE_CHILD)
    write = asyncio.ensure_future(process.write_stdin(b"x" * PIPE_OVERFLOW_BYTES))
    _, blocked = await asyncio.wait({write}, timeout=0.2)
    assert blocked == {write}

    await asyncio.wait_for(process.terminate(), 10)

    with pytest.raises((OSError, ValueError)):
        await asyncio.wait_for(write, 10)
    # The end of stdout: the child is gone.
    assert await asyncio.wait_for(process.stdout.read(), 10) == b""
