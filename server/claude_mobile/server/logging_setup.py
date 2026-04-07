"""structlog + stdlib logging configuration.

Called once at app startup from ``create_app``. Configures:

- structlog with contextvars merging, ISO timestamps, exception formatting,
  sensitive-data redaction, and a JSON or console renderer (per ``log_format``).
- The stdlib root logger to share the same renderer via ``ProcessorFormatter``,
  so uvicorn access logs and third-party libraries emit the same format.
- A separate ``audit`` logger that writes to ``audit_log_path`` (or stdout
  tagged ``audit=true`` when no path is configured) and does not propagate to
  the app stream.
- A ``QueueHandler`` / ``QueueListener`` pair so logging I/O is off the
  asyncio event loop.

Forward-compatible with OpenTelemetry: correlation IDs are 16-byte hex strings
(W3C ``trace-id`` shape), so a future OTel migration can promote them to real
``trace_id``s without changing fields.
"""
from __future__ import annotations

import logging
import logging.handlers
import queue
import sys
from typing import TYPE_CHECKING

import structlog

from .logging.redaction import redact_processor

if TYPE_CHECKING:
    from .config import ServerConfig


_listener: logging.handlers.QueueListener | None = None


class _RawQueueHandler(logging.handlers.QueueHandler):
    """QueueHandler that does NOT pre-format records.

    The default ``QueueHandler.prepare`` calls ``self.format(record)`` which
    stringifies the record's ``msg`` field. structlog's ``ProcessorFormatter``
    requires ``record.msg`` to remain a dict, so we forward records unmodified
    and let the listener-side handler do the formatting.
    """

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        return record


def configure_logging(config: "ServerConfig") -> None:
    """Configure structlog + stdlib logging. Idempotent."""
    global _listener

    log_format = (getattr(config, "log_format", "json") or "json").lower()
    audit_log_path = getattr(config, "audit_log_path", None)
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
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer(colors=False)
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

    # Stop any previous listener (idempotent reconfigure for tests).
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

    # Reset root logger handlers, install our queue handler.
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(queue_handler)
    root.setLevel(level)

    # Align uvicorn loggers with our format.
    for uv_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(uv_name)
        for h in list(uv_logger.handlers):
            uv_logger.removeHandler(h)
        uv_logger.propagate = True
        uv_logger.setLevel(level)

    # -- Audit logger --
    audit_logger = logging.getLogger("audit")
    for h in list(audit_logger.handlers):
        audit_logger.removeHandler(h)
    audit_logger.setLevel(level)

    if audit_log_path:
        audit_handler: logging.Handler = logging.FileHandler(
            audit_log_path, encoding="utf-8"
        )
        audit_handler.setFormatter(formatter)
        audit_logger.addHandler(audit_handler)
        audit_logger.propagate = False
    else:
        # Route audit through the shared queue so it lands on stdout (tagged audit=true).
        audit_logger.propagate = True


def shutdown_logging() -> None:
    """Stop the QueueListener thread (called during app shutdown)."""
    global _listener
    if _listener is not None:
        try:
            _listener.stop()
        except Exception:
            pass
        _listener = None
