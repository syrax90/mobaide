"""Dedicated audit logging channel.

Audit records always carry: timestamp (added by structlog), actor, action,
target, outcome, request_id, client_ip. ``request_id`` and ``client_ip`` are
pulled automatically from contextvars (set by the HTTP middleware).
"""
from __future__ import annotations

import structlog

_audit_logger = structlog.get_logger("audit")


def get_audit_logger():
    return _audit_logger


def audit_event(
    action: str,
    *,
    actor: str | None = None,
    target: str | None = None,
    outcome: str = "ok",
    **extra,
) -> None:
    """Emit a structured audit record.

    ``request_id`` and ``client_ip`` are merged automatically by structlog from
    the active contextvars (bound by the HTTP middleware). Callers only need to
    provide what's specific to the action.
    """
    _audit_logger.info(
        action,
        audit=True,
        actor=actor,
        target=target,
        outcome=outcome,
        **extra,
    )
