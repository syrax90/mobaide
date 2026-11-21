"""Per-session Claude Code hook settings for attention notifications.

The agent injects a dedicated ``--settings`` file when it spawns the Claude CLI
for an interactive session. The file registers ``http`` hooks that POST the
hook's stdin JSON to the agent's local ``/internal/hook`` endpoint:

* ``Notification`` (matcher ``permission_prompt``) -> Claude needs approval
* ``Notification`` (matcher ``idle_prompt``)       -> genuinely idle, your move
* ``Stop``                                          -> turn ended (debounced)
* ``UserPromptSubmit``                              -> new turn, reset the gate
* ``PreToolUse``                                    -> heartbeat: work in progress
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

A chat session gets the same kind of hooks as a settings object, with no file:
see :func:`chat_hook_settings`. A Junie terminal session gets a Junie config
file in the same place instead: see :func:`write_junie_session_hooks`.

Files live under a per-port temp directory so two agents on one host don't sweep
each other's files. They are removed on ``SessionEnd`` and swept on startup;
crash orphans are cleared at the next boot.
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import stat
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

from .attention_gate import HookKind

logger = logging.getLogger(__name__)

# Short timeout so an unreachable/slow endpoint never stalls Claude. ``http``
# hook timeouts surface as a non-blocking error; the endpoint itself returns
# 200 immediately after an in-memory enqueue, so this is only a failsafe.
_HOOK_TIMEOUT_SECONDS = 5

# ``PreToolUse`` holds the tool call until the hook answers, and it fires for
# every call of the main agent and of each sub-agent. A missed heartbeat costs
# nothing, so it gets less patience than the hooks that carry an alert.
_HEARTBEAT_TIMEOUT_SECONDS = 2

# Where a chat session's hooks carry the per-agent token: the CLI fills the
# header from the environment variable (see ``chat_hook_settings``).
HOOK_TOKEN_ENV = "MOBAIDE_HOOK_TOKEN"
HOOK_TOKEN_HEADER = "X-Mobaide-Hook-Token"

# The hook *events* a terminal session's ``--settings`` file may ever register.
# (A chat session's settings object has its own rule: see ``chat_hook_settings``.)
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
# never fires, and a missing field reads as absent. ``PreToolUse`` qualifies: it
# shipped with the first hooks release, before ``UserPromptSubmit`` and
# ``SessionEnd`` existed.
REGISTERED_HOOK_EVENTS = (
    "Notification", "Stop", "UserPromptSubmit", "PreToolUse", "SessionEnd",
)


def _hooks_dir(port: int) -> Path:
    return Path(tempfile.gettempdir()) / f"mobaide_hooks_{port}"


def _settings_path(port: int, session_id: str) -> Path:
    return _hooks_dir(port) / f"{session_id}.json"


def _hook_url(session_id: str, port: int, kind: HookKind) -> str:
    return (
        f"http://127.0.0.1:{port}/internal/hook"
        f"?session_id={quote(session_id, safe='')}"
        f"&hook={kind.value}"
    )


def _build_settings(session_id: str, port: int, token: str, *, fail_open: bool = False) -> dict:
    def hook(kind: HookKind, timeout: int = _HOOK_TIMEOUT_SECONDS) -> dict:
        url = f"{_hook_url(session_id, port, kind)}&token={quote(token, safe='')}"
        entry: dict = {"type": "http", "url": url, "timeout": timeout}
        if fail_open:
            entry["onFailure"] = "continue"
        return entry

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
            "PreToolUse": [
                {
                    "matcher": "*",
                    "hooks": [hook(HookKind.TOOL, _HEARTBEAT_TIMEOUT_SECONDS)],
                },
            ],
            "SessionEnd": [{"matcher": "*", "hooks": [hook(HookKind.END)]}],
        },
    }


def chat_hook_settings(session_id: str, port: int) -> dict:
    """The hook settings of a chat session, as a settings object.

    The adapter hands the object to the CLI as a ``--settings`` argument, which
    every local user can read, so the token is not in the hook URL: each hook
    sends it in a header the CLI fills from ``HOOK_TOKEN_ENV``. A CLI that may
    not read that variable sends no valid token, and the session works as one
    with no hooks. ``StopFailure`` is newer than ``REGISTERED_HOOK_EVENTS`` but
    safe here: with no terminal, an unknown event is dropped and no dialog blocks.
    """
    def hook(kind: HookKind, timeout: int = _HOOK_TIMEOUT_SECONDS) -> dict:
        return {
            "type": "http",
            "url": _hook_url(session_id, port, kind),
            "timeout": timeout,
            "headers": {HOOK_TOKEN_HEADER: f"${HOOK_TOKEN_ENV}"},
            "allowedEnvVars": [HOOK_TOKEN_ENV],
        }

    return {
        "hooks": {
            "Stop": [{"matcher": "*", "hooks": [hook(HookKind.STOP)]}],
            "StopFailure": [{"matcher": "*", "hooks": [hook(HookKind.STOP_FAILURE)]}],
            "UserPromptSubmit": [
                {"matcher": "*", "hooks": [hook(HookKind.PROMPT_SUBMIT)]},
            ],
            "PreToolUse": [
                {
                    "matcher": "*",
                    "hooks": [hook(HookKind.TOOL, _HEARTBEAT_TIMEOUT_SECONDS)],
                },
            ],
        },
    }


def write_session_hooks(
    session_id: str,
    port: int,
    token: str,
    extra: dict | None = None,
    *,
    fail_open: bool = False,
) -> Path:
    """Write the per-session hook settings file and return its path.

    ``extra`` keys (e.g. ``{"fastMode": True}`` / ``{"ultracode": True}``) are
    merged into the document at the top level alongside ``hooks`` so a Claude
    launch carries a single ``--settings`` flag rather than two.

    ``fail_open`` is for a Code Mode session: there a hook that fails or times
    out refuses the tool call, so a stalled or restarting agent would block
    Claude's work. ``onFailure`` needs Claude Code 2.1.295, older than Code
    Mode itself; an older CLI would stop at an "invalid settings" dialog.

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
    settings = _build_settings(session_id, port, token, fail_open=fail_open)
    if extra:
        settings.update(extra)
    return _write_owner_only(port, session_id, settings)


# Junie runs only ``command`` hooks, so each one starts this stdlib script,
# which posts the hook to ``/internal/hook`` (see :mod:`.junie_hook_post`).
JUNIE_HOOK_POST_SCRIPT = str(Path(__file__).with_name("junie_hook_post.py"))

# Junie event -> gate kind. Every entry must be ``async``: a sync
# ``PermissionRequest`` hook that exits 0 approves with no dialog, and the poster
# always exits 0. No ``SessionEnd``: Junie fires it on ``/restart`` too and the
# shim relaunches with the same file; the session manager deletes it instead.
_JUNIE_HOOK_EVENTS = (
    ("UserPromptSubmit", HookKind.PROMPT_SUBMIT),
    ("PreToolUse", HookKind.TOOL),
    ("PermissionRequest", HookKind.PERMISSION),
    ("Stop", HookKind.STOP),
    ("StopFailure", HookKind.STOP_FAILURE),
)


def junie_hook_config(session_id: str, port: int) -> dict:
    """The Junie config document that wires a terminal session's hooks.

    Holds no token: the poster reads it from ``HOOK_TOKEN_ENV``, which the
    launcher puts in the Junie process environment.
    """
    def entry(kind: HookKind) -> list[dict]:
        command = shlex.join([
            sys.executable, "-I", JUNIE_HOOK_POST_SCRIPT,
            str(port), session_id, kind.value,
        ])
        return [{
            "hooks": [{
                "type": "command",
                "command": command,
                "async": True,
                "timeout": _HOOK_TIMEOUT_SECONDS,
            }],
        }]

    return {"hooks": {event: entry(kind) for event, kind in _JUNIE_HOOK_EVENTS}}


def write_junie_session_hooks(session_id: str, port: int) -> Path:
    """Write a Junie terminal session's hook config and return its path.

    Same directory and name as :func:`write_session_hooks`, so
    :func:`sweep_hooks_dir` covers it too. Raises ``OSError`` when the file
    cannot be written.
    """
    return _write_owner_only(port, session_id, junie_hook_config(session_id, port))


def _write_owner_only(port: int, session_id: str, document: dict) -> Path:
    directory = _hooks_dir(port)
    directory.mkdir(parents=True, exist_ok=True)
    _check_own_directory(directory)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass  # best-effort (no-op on Windows)
    target = _settings_path(port, session_id)
    payload = json.dumps(document, indent=2)
    # O_CREAT|O_TRUNC with an explicit 0600 mode makes the file owner-only from
    # creation (modulo umask), closing the write-then-chmod race on POSIX /tmp.
    # O_NOFOLLOW: a planted symlink must not redirect the write.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(target, flags, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    return target


def _check_own_directory(directory: Path) -> None:
    """Refuse a hooks directory another local user could control.

    On a multi-user POSIX host the directory sits in the shared ``/tmp``, and
    the files in it name commands (Junie) or URLs with the token (Claude).
    Raises ``OSError``, which the launchers treat as "launch without hooks".
    """
    info = os.lstat(directory)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise OSError(f"hooks directory is not a plain directory: {directory}")
    getuid = getattr(os, "getuid", None)
    if getuid is not None and info.st_uid != getuid():
        raise OSError(f"hooks directory belongs to another user: {directory}")


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
