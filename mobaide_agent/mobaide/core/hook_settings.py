"""Per-session Claude Code hook settings for attention notifications.

The agent injects a dedicated ``--settings`` file when it spawns the Claude CLI
for an interactive session. The file registers three ``http`` hooks that POST
the hook's stdin JSON to the agent's local ``/internal/hook`` endpoint:

* ``Notification`` (matcher ``permission_prompt``) -> Claude needs approval
* ``Stop``                                          -> turn finished / your move
* ``SessionEnd``                                    -> session ended (+ reason)

The ``session_id`` and a per-agent ``token`` are baked **literally** into each
hook URL, so the endpoint can route the event to the right ``TerminalSession``
even when several concurrent sessions share a working directory — without
relying on Claude Code's hook env-var interpolation. ``http`` hooks need no
shell or external binary, so they behave identically on Windows and POSIX
(unlike a ``command``+``curl`` hook, which PowerShell would mangle by aliasing
``curl`` to ``Invoke-WebRequest``).

Files live under a per-port temp directory so two agents on one host don't sweep
each other's files. They are removed on ``SessionEnd`` and swept on startup;
crash orphans are cleared at the next boot.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger(__name__)

# Short timeout so an unreachable/slow endpoint never stalls Claude. ``http``
# hook timeouts surface as a non-blocking error; the endpoint itself returns
# 200 immediately after an in-memory enqueue, so this is only a failsafe.
_HOOK_TIMEOUT_SECONDS = 5


def _hooks_dir(port: int) -> Path:
    return Path(tempfile.gettempdir()) / f"mobaide_hooks_{port}"


def _settings_path(port: int, session_id: str) -> Path:
    return _hooks_dir(port) / f"{session_id}.json"


def _build_settings(session_id: str, port: int, token: str) -> dict:
    url = (
        f"http://127.0.0.1:{port}/internal/hook"
        f"?session_id={quote(session_id, safe='')}&token={quote(token, safe='')}"
    )
    http_hook = {"type": "http", "url": url, "timeout": _HOOK_TIMEOUT_SECONDS}
    return {
        "hooks": {
            "Notification": [
                {"matcher": "permission_prompt", "hooks": [http_hook]},
            ],
            "Stop": [{"matcher": "*", "hooks": [http_hook]}],
            "SessionEnd": [{"matcher": "*", "hooks": [http_hook]}],
        },
    }


def write_session_hooks(session_id: str, port: int, token: str) -> Path:
    """Write the per-session hook settings file and return its path.

    The file embeds the per-agent token in its hook URLs, so it is created
    atomically with owner-only permissions (0600) instead of written then
    chmod-ed: on a multi-user POSIX host ``tempfile.gettempdir()`` is the
    world-readable ``/tmp``, where a write-then-chmod leaves a brief window in
    which another local user could read the token. On Windows the per-user
    temp dir is already ACL-restricted, so the POSIX mode bits are a harmless
    no-op there.

    Raises ``OSError`` if the file cannot be written — the caller decides
    whether that should abort the launch (it does not: a session without hooks
    still runs, it just won't produce attention notifications).
    """
    directory = _hooks_dir(port)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass  # best-effort (no-op on Windows)
    target = _settings_path(port, session_id)
    payload = json.dumps(_build_settings(session_id, port, token), indent=2)
    # O_CREAT|O_TRUNC with an explicit 0600 mode makes the file owner-only from
    # creation (modulo umask), closing the write-then-chmod race on POSIX /tmp.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    return target


def remove_session_hooks(session_id: str, port: int) -> None:
    """Best-effort delete of a session's hook settings file."""
    try:
        _settings_path(port, session_id).unlink(missing_ok=True)
    except OSError:
        logger.debug("hook_settings.remove_failed session=%s", session_id, exc_info=True)


def sweep_hooks_dir(port: int) -> None:
    """Remove every hook settings file for this port (stale prior-run orphans)."""
    directory = _hooks_dir(port)
    if not directory.is_dir():
        return
    for path in directory.glob("*.json"):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.debug("hook_settings.sweep_failed path=%s", path, exc_info=True)
