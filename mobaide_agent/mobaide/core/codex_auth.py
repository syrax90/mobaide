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

    Probed cheapest-first so the common cases pay no subprocess cost:

    1. an ``OPENAI_API_KEY`` in the env, then
    2. an ``auth.json`` under :func:`codex_home` (the ``file`` credential store),
       then
    3. ``codex login status`` — which exits 0 when signed in and is the only
       check that also sees the OS keyring behind Codex's ``keyring`` / ``auto``
       credential stores (the file check in (2) cannot). Consulted only when (1)
       and (2) miss, so an env-/file-authed host never spawns it.

    Still presence-, not validity-based: ``status`` exiting non-zero (or being
    unavailable on an older codex) falls through to "unauthenticated", and a
    stale ``auth.json`` from (2) is trusted. Probing an existing credential's
    freshness is deferred (CLA-81).
    """
    if env.get("OPENAI_API_KEY"):
        return True
    if os.path.isfile(os.path.join(codex_home(env), _AUTH_FILE)):
        return True
    return await _codex_login_status(codex_bin, env, timeout=status_timeout)


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
) -> bool:
    """True iff ``codex login status`` exits 0 (a credential exists in the active
    store, including the OS keyring the ``auth.json`` check can't see).

    A probe that cannot run — missing/old binary, timeout — returns False: the
    caller has already ruled out the env and file credentials, so "can't confirm"
    is safely treated as unauthenticated. Runs via ``asyncio.to_thread`` for the
    same Windows-event-loop reason as :func:`codex_login_with_api_key`.
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
        return False
    return completed.returncode == 0


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
