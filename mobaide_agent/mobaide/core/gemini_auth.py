"""Gemini CLI authentication detection.

Gemini's first run needs interactive auth, which would surface a "Sign in with
Google" TUI inside the PTY relay. To keep the spawn path from hanging on that
prompt, the launcher checks :func:`gemini_auth_present` before building a session
and raises ``GeminiAuthRequiredError`` when no credential is found.

Unlike Codex, Gemini exposes **no** ``login`` / ``--with-api-key`` / ``login
status`` subcommand and its OAuth requires a browser, so there is no
headless-completable login and no status probe to run. Detection is therefore a
pure environment + filesystem check — no subprocess. It mirrors the credential
sources the real ``gemini`` reads: process env, cached OAuth / ``settings.json``
under the config home (``$GEMINI_CLI_HOME``, else ``~``), and the ``.env`` file
Gemini auto-loads. In-app API-key entry (which would inject ``GEMINI_API_KEY``
and pre-seed ``settings.json``) is deferred; v1 only detects and reports state.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping

try:
    from dotenv import dotenv_values
except ImportError:  # pragma: no cover - python-dotenv ships with pydantic-settings
    dotenv_values = None

_OAUTH_CREDS_FILE = "oauth_creds.json"
_SETTINGS_FILE = "settings.json"
_CONFIG_DIR = ".gemini"
_DOTENV_FILE = ".env"
# Env vars that carry a usable credential directly. ``GEMINI_API_KEY`` /
# ``GOOGLE_API_KEY`` are Gemini API keys; ``GOOGLE_APPLICATION_CREDENTIALS`` is a
# Vertex-AI service-account JSON path (checked for existence below).
_API_KEY_ENV_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
# Gemini's documented config-home override. The CLI creates its ``.gemini`` dir
# *inside* this root (default: the OS user home) — NOT the ``.gemini`` dir itself.
_CONFIG_HOME_ENV_VAR = "GEMINI_CLI_HOME"


def gemini_home_root(env: Mapping[str, str]) -> str:
    """Root dir Gemini treats as home — ``$GEMINI_CLI_HOME`` or the OS user home.

    ``GEMINI_CLI_HOME`` is Gemini's documented config-home override; the CLI puts
    its ``.gemini`` folder *inside* this root. Honouring it lets the probe see the
    same credentials the real ``gemini`` would, and lets tests redirect the whole
    home tree with one env var.
    """
    override = env.get(_CONFIG_HOME_ENV_VAR)
    return override if override else os.path.expanduser("~")


def gemini_home(env: Mapping[str, str]) -> str:
    """Gemini's config/credentials dir — ``<root>/.gemini`` (root per
    :func:`gemini_home_root`)."""
    return os.path.join(gemini_home_root(env), _CONFIG_DIR)


def gemini_auth_present(env: Mapping[str, str], cwd: str | None = None) -> bool:
    """True when Gemini has a usable credential reachable from ``env`` / ``cwd``.

    Detection is a pure env + filesystem probe (Gemini has no credential-status
    subcommand). Any one of the following signals is treated as authenticated:

    1. A Gemini API key in the env (``GEMINI_API_KEY`` / ``GOOGLE_API_KEY``).
    2. ``GOOGLE_APPLICATION_CREDENTIALS`` pointing at an existing service-account
       file (Vertex AI).
    3. Cached "Sign in with Google" OAuth creds at ``<home>/oauth_creds.json``.
    4. A configured auth type in ``<home>/settings.json`` (``selectedAuthType``
       or the nested ``security.auth.selectedType``).
    5. A credential in the ``.env`` file Gemini itself auto-loads — searched
       upward from ``cwd`` (stopping at a ``.git`` project root or the home root),
       then ``<home>/.env``, then ``<root>/.env``. Without this, an operator
       following Gemini's documented ``.env`` persistence would be wrongly
       reported unauthenticated and blocked at the pre-spawn guard even though the
       real ``gemini`` would authenticate.
    """
    if _mapping_has_credential(env):
        return True
    home = gemini_home(env)
    if os.path.isfile(os.path.join(home, _OAUTH_CREDS_FILE)):
        return True
    if _settings_selected_auth(os.path.join(home, _SETTINGS_FILE)):
        return True
    dotenv_path = _find_gemini_dotenv(cwd, env)
    return dotenv_path is not None and _dotenv_has_credential(dotenv_path)


def _mapping_has_credential(values: Mapping[str, str | None]) -> bool:
    """True when a Gemini/Google API key or an existing ADC file is set in
    ``values`` — shared by the process-env and ``.env``-file checks so both honour
    the same credential shapes."""
    if any(values.get(name) for name in _API_KEY_ENV_VARS):
        return True
    adc = values.get("GOOGLE_APPLICATION_CREDENTIALS")
    return bool(adc and os.path.isfile(adc))


def _find_gemini_dotenv(cwd: str | None, env: Mapping[str, str]) -> str | None:
    """Path of the first ``.env`` Gemini CLI would auto-load, or ``None``.

    Mirrors Gemini's resolver: climb from ``cwd`` preferring ``.gemini/.env`` then
    ``.env`` at each level, stopping after a directory that holds ``.git`` (project
    root) or is the home root; then fall back to ``<home>/.env`` and ``<root>/.env``.
    First file found wins (Gemini does not merge).
    """
    root = gemini_home_root(env)
    if cwd:
        current = os.path.abspath(cwd)
        while True:
            for candidate in (
                os.path.join(current, _CONFIG_DIR, _DOTENV_FILE),
                os.path.join(current, _DOTENV_FILE),
            ):
                if os.path.isfile(candidate):
                    return candidate
            parent = os.path.dirname(current)
            if (
                current == root
                or os.path.isdir(os.path.join(current, ".git"))
                or parent == current
            ):
                break
            current = parent
    for fallback in (
        os.path.join(root, _CONFIG_DIR, _DOTENV_FILE),
        os.path.join(root, _DOTENV_FILE),
    ):
        if os.path.isfile(fallback):
            return fallback
    return None


def _dotenv_has_credential(path: str) -> bool:
    """True when the ``.env`` at ``path`` defines a usable Gemini credential.

    Parsed with ``python-dotenv`` (the loader pydantic-settings uses) so quoting
    and ``export`` prefixes match; kept at ``utf-8`` to match how the real
    ``gemini`` reads it. A missing/unreadable *or non-UTF-8* file degrades to
    ``False`` (``ValueError`` covers ``UnicodeDecodeError`` — e.g. a UTF-16 ``.env``
    from PowerShell ``>`` redirection — so the guard falls through to the clean
    ``409 gemini_auth_required`` instead of raising, matching the sibling
    ``_settings_selected_auth``).
    """
    if dotenv_values is None:
        return False
    try:
        values = dotenv_values(path, encoding="utf-8")
    except (OSError, ValueError):
        return False
    return _mapping_has_credential(values)


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
