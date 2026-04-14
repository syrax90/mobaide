"""structlog + stdlib logging configuration for the Central Server.

Mirrors the agent's logging_setup pattern: structlog with contextvars,
ISO timestamps, sensitive-data redaction, QueueHandler/QueueListener
for async-safe I/O.
"""
from __future__ import annotations

import logging
import logging.handlers
import queue
import re
import sys
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from .config import CentralServerConfig

# ---------------------------------------------------------------------------
# Redaction (inlined to avoid cross-package dependency on claude_agent)
# ---------------------------------------------------------------------------

REDACTED = "***REDACTED***"

_SENSITIVE_KEYS = frozenset(
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
        "secret",
        "jwt_secret_key",
        "x-api-key",
        "agent_token",
        "pairing_token",
    }
)

_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+")


def _scrub_string(value: str) -> str:
    value = _JWT_RE.sub(REDACTED, value)
    value = _BEARER_RE.sub("Bearer " + REDACTED, value)
    return value


def _walk(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: (REDACTED if k.lower() in _SENSITIVE_KEYS else _walk(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_walk(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_walk(v) for v in value)
    if isinstance(value, str):
        return _scrub_string(value)
    return value


def redact_processor(logger, method_name, event_dict):
    """structlog processor: walk event_dict and redact sensitive data."""
    return _walk(event_dict)


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------


def get_logger(name: str | None = None):
    return structlog.get_logger(name) if name else structlog.get_logger()


def bind_request_context(**kwargs) -> None:
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_request_context() -> None:
    structlog.contextvars.clear_contextvars()


# ---------------------------------------------------------------------------
# QueueHandler that preserves structlog dict messages
# ---------------------------------------------------------------------------

_listener: logging.handlers.QueueListener | None = None


class _RawQueueHandler(logging.handlers.QueueHandler):
    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        return record


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def configure_logging(config: CentralServerConfig) -> None:
    """Configure structlog + stdlib logging. Idempotent."""
    global _listener

    log_format = (getattr(config, "log_format", "json") or "json").lower()
    level = logging.getLevelName(config.log_level.upper())
    if not isinstance(level, int):
        level = logging.INFO

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_processor,
    ]

    if log_format == "console":
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer(
            colors=False,
        )
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    if _listener is not None:
        try:
            _listener.stop()
        except Exception:
            pass
        _listener = None

    log_queue: queue.Queue = queue.Queue(-1)

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)

    _listener = logging.handlers.QueueListener(
        log_queue, stdout_handler, respect_handler_level=True
    )
    _listener.start()

    queue_handler = _RawQueueHandler(log_queue)

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(queue_handler)
    root.setLevel(level)

    for uv_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(uv_name)
        for h in list(uv_logger.handlers):
            uv_logger.removeHandler(h)
        uv_logger.propagate = True
        uv_logger.setLevel(level)


def shutdown_logging() -> None:
    """Stop the QueueListener thread (called during app shutdown)."""
    global _listener
    if _listener is not None:
        try:
            _listener.stop()
        except Exception:
            pass
        _listener = None
