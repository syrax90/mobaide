"""Sensitive-data redaction processor for structlog.

Strips secrets from log records before they are rendered. Applied globally
in ``logging_setup.configure_logging`` so every log line — app, audit, uvicorn —
passes through it.
"""
from __future__ import annotations

import re
from typing import Any

REDACTED = "***REDACTED***"

# Keys (case-insensitive) whose values are always replaced.
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "cookie",
        "api_key",
        "apikey",
        "linear_api_key",
        "secret",
        "jwt_secret_key",
        "x-api-key",
        # PTY input may contain typed secrets — never log raw stdin.
        "pty_stdin",
        "stdin",
    }
)

# JWT-shaped tokens: three base64url segments separated by dots, starting with "eyJ".
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
# "Bearer <token>" anywhere in a string.
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+")


def _scrub_string(value: str) -> str:
    value = _JWT_RE.sub(REDACTED, value)
    value = _BEARER_RE.sub("Bearer " + REDACTED, value)
    return value


def _walk(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: (REDACTED if k.lower() in SENSITIVE_KEYS else _walk(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_walk(v) for v in value)
    if isinstance(value, str):
        return _scrub_string(value)
    return value


def redact_processor(logger, method_name, event_dict):
    """structlog processor: walk the event_dict and redact sensitive data."""
    return _walk(event_dict)
