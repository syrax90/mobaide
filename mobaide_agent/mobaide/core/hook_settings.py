"""Per-session Claude Code hook settings for attention notifications.

The agent injects a dedicated ``--settings`` file when it spawns the Claude CLI
for an interactive session. The file registers ``http`` hooks that POST the
hook's stdin JSON to the agent's local ``/internal/hook`` endpoint:

* ``Notification`` (matcher ``permission_prompt``) -> Claude needs approval
* ``Notification`` (matcher ``idle_prompt``)       -> genuinely idle, your move
* ``Stop``                                          -> turn ended (debounced)
* ``UserPromptSubmit``                              -> new turn, reset the gate
* ``SessionEnd``                                    -> session ended (+ reason)

``Stop`` is not an alert on its own — see :mod:`mobaide.core.attention_gate` for
why and for what each registration means downstream.

The ``session_id``, a per-agent ``token`` and the gate-facing ``hook`` kind are
baked **literally** into each hook URL, so the endpoint can route the event to
the right ``TerminalSession`` and classify it without parsing the body — even
when several concurrent sessions share a working directory, and without relying
on Claude Code's hook env-var interpolation. ``http`` hooks need no shell or
external binary, so they behave identically on Windows and POSIX (unlike a
``command``+``curl`` hook, which PowerShell would mangle by aliasing ``curl`` to
``Invoke-WebRequest``).

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

from .attention_gate import HookKind

logger = logging.getLogger(__name__)

# Short timeout so an unreachable/slow endpoint never stalls Claude. ``http``
# hook timeouts surface as a non-blocking error; the endpoint itself returns
# 200 immediately after an in-memory enqueue, so this is only a failsafe.
_HOOK_TIMEOUT_SECONDS = 5

# The hook *events* this file may ever register.
#
# Claude Code strips an unrecognised ``hooks.<Event>`` key with a validation
# *warning*, and its startup path raises a blocking "invalid settings" dialog for
# any validation record regardless of severity whenever the process is
# interactive — which a PTY session always is. Registering an event newer than
# the oldest CLI we support would therefore hang every session at a modal, and
# the startup-stall watchdog would not catch it because the dialog renders far
# more than ``STARTUP_STALL_MIN_BYTES``.
#
# Only add a key at least as old as the ones already here. Anything else belongs
# in a matcher value or an optional payload field: an unknown matcher simply
# never fires, and a missing field reads as absent.
REGISTERED_HOOK_EVENTS = ("Notification", "Stop", "UserPromptSubmit", "SessionEnd")


def _hooks_dir(port: int) -> Path:
    return Path(tempfile.gettempdir()) / f"mobaide_hooks_{port}"


def _settings_path(port: int, session_id: str) -> Path:
    return _hooks_dir(port) / f"{session_id}.json"


def _build_settings(session_id: str, port: int, token: str) -> dict:
    def hook(kind: HookKind) -> dict:
        url = (
            f"http://127.0.0.1:{port}/internal/hook"
            f"?session_id={quote(session_id, safe='')}"
            f"&token={quote(token, safe='')}"
            f"&hook={kind.value}"
        )
        return {"type": "http", "url": url, "timeout": _HOOK_TIMEOUT_SECONDS}

    return {
        "hooks": {
            "Notification": [
                {"matcher": "permission_prompt", "hooks": [hook(HookKind.PERMISSION)]},
                {"matcher": "idle_prompt", "hooks": [hook(HookKind.IDLE)]},
            ],
            "Stop": [{"matcher": "*", "hooks": [hook(HookKind.STOP)]}],
            "UserPromptSubmit": [
                {"matcher": "*", "hooks": [hook(HookKind.PROMPT_SUBMIT)]},
            ],
            "SessionEnd": [{"matcher": "*", "hooks": [hook(HookKind.END)]}],
        },
    }


def write_session_hooks(
    session_id: str, port: int, token: str, extra: dict | None = None,
) -> Path:
    """Write the per-session hook settings file and return its path.

    ``extra`` keys (e.g. ``{"fastMode": True}`` / ``{"ultracode": True}``) are
    merged into the document at the top level alongside ``hooks`` so a Claude
    launch carries a single ``--settings`` flag rather than two.

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
    settings = _build_settings(session_id, port, token)
    if extra:
        settings.update(extra)
    payload = json.dumps(settings, indent=2)
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
