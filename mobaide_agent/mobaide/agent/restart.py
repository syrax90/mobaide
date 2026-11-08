"""Ask the running agent process to exit with the restart exit code.

The supervisors bring the agent back only for specific exit codes: the
Windows supervisor (``deploy/windows_launcher.py``) respawns only on
``EXIT_RESTART_REQUESTED``, and Task Scheduler never restarts a task that
exited on its own, whatever the code. A bare ``sys.exit(75)`` does not reach
them from every context the updater runs in:

* during lifespan startup, uvicorn catches the ``SystemExit`` and exits with
  its own ``STARTUP_FAILURE`` code (3) instead;
* in a FastAPI ``BackgroundTask``, the call runs in a worker thread and
  uvicorn logs the ``SystemExit`` and keeps serving.

``request_restart`` records the code and tells the bound uvicorn server to
shut down gracefully; ``mobaide.__main__._run_agent`` exits with the recorded
code once the server returns. A daemon watchdog hard-exits with the same code
if the graceful shutdown hangs, so the supervisor always sees the restart.
Without a bound server (the CLI verbs, tests) it falls back to ``sys.exit``.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Any

from .updater import EXIT_RESTART_REQUESTED

logger = logging.getLogger(__name__)

_WATCHDOG_SECONDS = 60.0

_lock = threading.Lock()
_server: Any | None = None
_requested_code: int | None = None


def bind_server(server: Any | None) -> None:
    """Register the uvicorn ``Server`` that ``request_restart`` stops."""
    global _server
    with _lock:
        _server = server


def requested_exit_code() -> int | None:
    """The exit code a ``request_restart`` call asked for, else ``None``."""
    return _requested_code


def restart_requested() -> bool:
    return _requested_code is not None


def request_restart(code: int = EXIT_RESTART_REQUESTED) -> None:
    """Stop the agent so that the process exits with ``code``.

    Safe to call from the event loop or from a worker thread, and more than
    once: only the first call arms the watchdog.
    """
    global _requested_code
    with _lock:
        server = _server
        first = _requested_code is None
        if first:
            _requested_code = code
    if server is None:
        sys.exit(code)
    if not first:
        return
    logger.info("agent.restart_requested exit_code=%d", code)
    server.should_exit = True
    watchdog = threading.Timer(_WATCHDOG_SECONDS, _force_exit, args=(code,))
    watchdog.daemon = True
    watchdog.start()


def _force_exit(code: int) -> None:
    try:
        sys.stderr.write(
            f"mobaide-agent: graceful shutdown did not finish in "
            f"{_WATCHDOG_SECONDS:.0f}s; exiting with {code} for the restart.\n"
        )
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(code)

