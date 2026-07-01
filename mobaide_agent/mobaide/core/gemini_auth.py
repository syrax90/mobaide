"""Gemini CLI authentication detection.

Gemini's first run needs interactive auth, which would surface a "Sign in with
Google" TUI inside the PTY relay. To keep the spawn path from hanging on that
prompt, the launcher checks :func:`gemini_auth_present` before building a session
and raises ``GeminiAuthRequiredError`` when no credential is found.

Unlike Codex, Gemini exposes **no** ``login`` / ``--with-api-key`` / ``login
status`` subcommand and its OAuth requires a browser, so there is no
headless-completable login and no status probe to run. Detection is therefore a
pure environment + filesystem check — no subprocess. In-app API-key entry (which
would inject ``GEMINI_API_KEY`` and pre-seed ``settings.json``) is deferred;
v1 only detects and reports auth state.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping

_OAUTH_CREDS_FILE = "oauth_creds.json"
_SETTINGS_FILE = "settings.json"
# Env vars that carry a usable credential directly. ``GEMINI_API_KEY`` /
# ``GOOGLE_API_KEY`` are Gemini API keys; ``GOOGLE_APPLICATION_CREDENTIALS`` is a
# Vertex-AI service-account JSON path (checked for existence below).
_API_KEY_ENV_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")


def gemini_home(env: Mapping[str, str]) -> str:
    """Gemini's config/credentials dir — ``$GEMINI_HOME`` or ``~/.gemini``.

    Honouring ``GEMINI_HOME`` keeps the probe redirectable under test and matches
    the shape of :func:`mobaide.core.codex_auth.codex_home`.
    """
    override = env.get("GEMINI_HOME")
    return override if override else os.path.join(os.path.expanduser("~"), ".gemini")


def gemini_auth_present(env: Mapping[str, str]) -> bool:
    """True when Gemini has a usable credential reachable from ``env``.

    Detection is a pure env + filesystem probe (Gemini has no credential-status
    subcommand). Any one of the following signals is treated as authenticated:

    1. A Gemini API key in the env (``GEMINI_API_KEY`` / ``GOOGLE_API_KEY``).
    2. ``GOOGLE_APPLICATION_CREDENTIALS`` pointing at an existing service-account
       file (Vertex AI).
    3. Cached "Sign in with Google" OAuth creds at ``~/.gemini/oauth_creds.json``.
    4. A configured auth type in ``~/.gemini/settings.json`` (``selectedAuthType``
       or the newer nested ``security.auth.selectedType``).
    """
    if any(env.get(name) for name in _API_KEY_ENV_VARS):
        return True
    adc = env.get("GOOGLE_APPLICATION_CREDENTIALS")
    if adc and os.path.isfile(adc):
        return True
    home = gemini_home(env)
    if os.path.isfile(os.path.join(home, _OAUTH_CREDS_FILE)):
        return True
    return _settings_selected_auth(os.path.join(home, _SETTINGS_FILE))


def _settings_selected_auth(settings_path: str) -> bool:
    """True when ``settings.json`` records a non-empty selected auth type.

    Reads defensively: a missing/unreadable/malformed file is simply "no auth
    configured" (``False``), never an error. Supports both the flat
    ``selectedAuthType`` key and the nested ``security.auth.selectedType`` shape.
    """
    try:
        with open(settings_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    flat = data.get("selectedAuthType")
    if isinstance(flat, str) and flat.strip():
        return True
    security = data.get("security")
    if isinstance(security, dict):
        auth = security.get("auth")
        if isinstance(auth, dict):
            nested = auth.get("selectedType")
            if isinstance(nested, str) and nested.strip():
                return True
    return False
