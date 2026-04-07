"""Structured logging package for the Claude Mobile server.

Provides:
- ``get_logger``: thin wrapper around ``structlog.get_logger``.
- ``log_call``: decorator that traces internal calls with duration + outcome.
- ``audit_event``: dedicated channel for sensitive/auditable actions.
- ``bind_request_context`` / ``clear_request_context``: contextvars helpers.
"""
from __future__ import annotations

import structlog

from .audit import audit_event, get_audit_logger
from .decorators import log_call
from .redaction import redact_processor


def get_logger(name: str | None = None):
    """Return a structlog logger bound to the given name (module convention)."""
    return structlog.get_logger(name) if name else structlog.get_logger()


def bind_request_context(**kwargs) -> None:
    """Bind correlation fields (request_id, username, session_id, ...) into contextvars."""
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_request_context() -> None:
    """Reset all contextvars set by ``bind_request_context``."""
    structlog.contextvars.clear_contextvars()


__all__ = [
    "audit_event",
    "bind_request_context",
    "clear_request_context",
    "get_audit_logger",
    "get_logger",
    "log_call",
    "redact_processor",
]
