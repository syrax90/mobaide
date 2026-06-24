"""Codex CLI authentication helpers — detection + non-interactive API-key login.

Codex's first run needs interactive auth, which would surface a login TUI inside
the PTY relay. To keep the spawn path from hanging on that prompt, the launcher
checks :func:`codex_auth_present` before building a session and raises
``CodexAuthRequiredError`` when no credential is found. The app then drives
:func:`codex_login_with_api_key`, which runs ``codex login --with-api-key`` as a
one-shot subprocess with the key piped via **stdin** (never argv — OpenAI warns
an argv key leaks to process listings / shell history) so it cannot block the
relay and never persists the key (Codex writes its own ``auth.json``).

Detection and the login subprocess MUST resolve ``CODEX_HOME`` from the same
launch env, or a login would write ``auth.json`` where the guard never looks.
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from collections.abc import Mapping

_AUTH_FILE = "auth.json"
# Kept below the mobile OkHttp read timeout (30s) and the relay
# MobileProxy.forward timeout (30s): the agent must turn a hung login into the
# sanitized "timed out" detail before either client-facing deadline fires, or
# the user sees a generic socket timeout / relay 504 instead of the inline error.
_DEFAULT_TIMEOUT = 20.0
# `codex login status` is a fast local credential probe; bound it well under the
# session-create deadline so a wedged probe can never stall the spawn path.
_STATUS_TIMEOUT = 10.0
_MAX_DETAIL = 500
# CREATE_NO_WINDOW on Windows so the login subprocess never flashes a console.
_WINDOWS_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
# OpenAI-style secret shapes, scrubbed from any captured subprocess output.
_SK_RE = re.compile(r"sk-[A-Za-z0-9_-]{8,}")
# clap's signature when ``codex login status`` is not a known subcommand (an
# older CLI) — lets the probe distinguish "this codex can't tell me" from a
# genuine signed-out exit, so a stale-auth.json fallback only kicks in for the
# former.
_STATUS_UNSUPPORTED_RE = re.compile(
    r"unrecognized subcommand|unknown subcommand|invalid subcommand|unexpected argument",
    re.IGNORECASE,
)


def codex_home(env: Mapping[str, str]) -> str:
    """Codex's config/credentials dir — ``$CODEX_HOME`` or ``~/.codex``."""
    override = env.get("CODEX_HOME")
    return override if override else os.path.join(os.path.expanduser("~"), ".codex")


async def codex_auth_present(
    codex_bin: str,
    env: Mapping[str, str],
    *,
    status_timeout: float = _STATUS_TIMEOUT,
) -> bool:
    """True when Codex has a usable credential reachable from ``env``.

    ``codex login status`` (exit 0 == signed in) is authoritative and is the
    primary check: it is the only signal that reflects Codex's *actual*
    credential state across every store — the env key, the ``file`` store
    (``auth.json``), and the OS ``keyring`` / ``auto`` stores — so it both sees a
    keyring credential the file check would miss AND rejects a present-but-stale
    ``auth.json`` that would otherwise launch Codex straight into its interactive
    login/failure path.

    Order:

    1. An ``OPENAI_API_KEY`` in the env is a definitive credential — returned
       immediately, no probe.
    2. ``codex login status``: exit 0 -> signed in; a recognized non-zero ->
       signed out (returned even when ``auth.json`` exists — a stale file is not
       trusted).
    3. Only when the probe is *indeterminate* — it can't run (missing binary,
       timeout) or this codex predates the ``status`` subcommand — fall back to
       ``auth.json`` presence, so a file-authed host on an older CLI still
       builds. (A stale file on such an old CLI is the one residual the probe
       can't catch — tracked in CLA-81.)
    """
    if env.get("OPENAI_API_KEY"):
        return True
    status = await _codex_login_status(codex_bin, env, timeout=status_timeout)
    if status is not None:
        return status
    return os.path.isfile(os.path.join(codex_home(env), _AUTH_FILE))


def _codex_argv(codex_bin: str, *args: str) -> list[str]:
    """``codex <args>`` argv, wrapping a Windows ``.cmd``/``.bat`` shim in
    ``cmd /c`` (a shim is not directly CreateProcess-able). Shared by the login
    and ``login status`` invocations; secrets are never passed as argv elements.
    """
    base = [codex_bin, *args]
    if sys.platform == "win32" and codex_bin.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", *base]
    return base


async def _codex_login_status(
    codex_bin: str, env: Mapping[str, str], *, timeout: float,
) -> bool | None:
    """Run ``codex login status`` as a tri-state credential probe.

    Returns ``True`` (exit 0 -> signed in), ``False`` (ran and reported signed
    out), or ``None`` when the result is *indeterminate*: the probe couldn't run
    (missing/broken binary, timeout) or this codex doesn't recognise the
    ``status`` subcommand (older CLI). ``None`` tells the caller to fall back to a
    file-presence check rather than wrongly rejecting a valid credential. Runs
    via ``asyncio.to_thread`` for the same Windows-event-loop reason as
    :func:`codex_login_with_api_key`.
    """
    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            _codex_argv(codex_bin, "login", "status"),
            capture_output=True,
            timeout=timeout,
            env=dict(env),
            creationflags=_WINDOWS_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode == 0:
        return True
    if _status_probe_unsupported(completed):
        return None
    return False


def _status_probe_unsupported(completed: subprocess.CompletedProcess) -> bool:
    """True when a non-zero ``codex login status`` means the subcommand is
    unavailable (older CLI) rather than a genuine signed-out state: clap exits 2
    for an unknown subcommand and prints an "unrecognized subcommand" usage error.
    """
    stderr = completed.stderr.decode(errors="replace") if completed.stderr else ""
    return completed.returncode == 2 or bool(_STATUS_UNSUPPORTED_RE.search(stderr))


def _sanitize(text: str, api_key: str) -> str:
    """Strip the submitted key + ``sk-`` shapes from captured output; truncate."""
    cleaned = text.strip()
    if api_key:
        cleaned = cleaned.replace(api_key, "***")
    cleaned = _SK_RE.sub("***", cleaned)
    if len(cleaned) > _MAX_DETAIL:
        cleaned = cleaned[:_MAX_DETAIL] + "…"
    return cleaned


async def codex_login_with_api_key(
    api_key: str,
    *,
    codex_bin: str,
    env: Mapping[str, str],
    timeout: float = _DEFAULT_TIMEOUT,
) -> tuple[bool, str]:
    """Run ``codex login --with-api-key`` once, feeding the key on stdin.

    Uses ``asyncio.to_thread(subprocess.run, ...)`` rather than
    ``asyncio.create_subprocess_exec``: the latter raises ``NotImplementedError``
    under the ``WindowsSelectorEventLoop`` the agent runs uvicorn on (see
    ``mobaide.__main__`` and ``mobaide.linear.worktree._run_git`` for the
    rationale). ``subprocess.run`` kills + reaps the child on timeout, so a hung
    login can never block the relay or leave a zombie holding the key. Returns
    ``(ok, detail)`` where ``detail`` is sanitized output (the submitted key and
    ``sk-`` shapes scrubbed). The key is fed on stdin — never argv — and never
    persisted by us (Codex writes its own ``auth.json``).
    """
    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            _codex_argv(codex_bin, "login", "--with-api-key"),
            input=api_key.encode() + b"\n",
            capture_output=True,
            timeout=timeout,
            env=dict(env),
            creationflags=_WINDOWS_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return False, f"Codex login timed out after {timeout:.0f}s."

    if completed.returncode == 0:
        return True, ""
    stderr = completed.stderr.decode(errors="replace") if completed.stderr else ""
    stdout = completed.stdout.decode(errors="replace") if completed.stdout else ""
    detail = _sanitize(stderr, api_key) or _sanitize(stdout, api_key)
    return False, detail or f"codex login exited with code {completed.returncode}."
