"""Tests for structlog + stdlib logging setup (``logging_setup``).

Exercises ``configure_logging``, ``shutdown_logging``, and the
``_RawQueueHandler.prepare`` helper. The module owns a process-wide
``_listener`` global, so every test here runs inside an autouse fixture
that saves and restores it to avoid leaking state into the rest of the
suite.
"""
from __future__ import annotations

import logging
import logging.handlers
import queue

import pytest
import structlog

from mobaide.agent import logging_setup
from mobaide.agent.config import AgentConfig
from mobaide.agent.logging_setup import (
    _RawQueueHandler,
    configure_logging,
    shutdown_logging,
)


@pytest.fixture(autouse=True)
def _preserve_listener_state():
    """Save/restore the module-level ``_listener`` global AND structlog config.

    ``configure_logging`` mutates *three* pieces of process-global state:
    (1) the ``_listener`` QueueListener, (2) structlog's configured
    ``LoggerFactory`` + ``wrapper_class`` (via ``structlog.configure``),
    and (3) any module-level cached structlog logger captured at import
    time — once ``cache_logger_on_first_use=True`` is set, those cached
    loggers are frozen to the stdlib factory even after
    ``structlog.reset_defaults()``. Downstream test files that rely on
    ``structlog.testing.capture_logs`` (e.g. ``test_ws_logging_helpers``)
    silently fail in that frozen state because emissions route through
    stdlib handlers instead of the in-process capture chain. Reset all
    three.
    """
    saved = logging_setup._listener
    yield
    if (
        logging_setup._listener is not None
        and logging_setup._listener is not saved
    ):
        try:
            logging_setup._listener.stop()
        except Exception:
            pass
    logging_setup._listener = saved
    # Reset structlog to its library defaults so ``capture_logs`` works in
    # downstream test files that rely on it.
    structlog.reset_defaults()
    # Rebind any module-level cached structlog loggers that were frozen to
    # the stdlib factory during the test. These are the ones known to be
    # captured at import time; extend this list if new modules add similar
    # module-level ``get_logger()`` bindings.
    from mobaide.agent.ws import logging as _ws_logging_mod
    _ws_logging_mod._logger = structlog.get_logger("mobaide.ws")


_ = _preserve_listener_state


def _make_config(
    *,
    log_level: str = "INFO",
    log_format: str = "json",
    audit_log_path: str | None = None,
) -> AgentConfig:
    return AgentConfig(
        jwt_secret_key="test-secret",
        log_level=log_level,
        log_format=log_format,
        audit_log_path=audit_log_path,
    )


class TestRawQueueHandlerPrepare:
    def test_prepare_returns_record_unchanged(self):
        q: queue.Queue = queue.Queue()
        handler = _RawQueueHandler(q)
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg={"event": "hi", "k": 1},
            args=None,
            exc_info=None,
        )

        out = handler.prepare(record)

        assert out is record

    def test_prepare_preserves_dict_msg(self):
        """Dict ``msg`` must remain a dict — structlog relies on this."""
        q: queue.Queue = queue.Queue()
        handler = _RawQueueHandler(q)
        payload = {"event": "hello", "count": 3}
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg=payload,
            args=None,
            exc_info=None,
        )

        handler.prepare(record)

        assert record.msg is payload
        assert isinstance(record.msg, dict)


class TestConfigureLogging:
    def test_installs_queue_listener_and_handler(self):
        cfg = _make_config(log_level="DEBUG")

        configure_logging(cfg)

        assert logging_setup._listener is not None
        assert isinstance(
            logging_setup._listener, logging.handlers.QueueListener
        )
        assert isinstance(logging_setup._listener.queue, queue.Queue)

    def test_sets_root_logger_level_from_config(self):
        configure_logging(_make_config(log_level="WARNING"))

        assert logging.getLogger().level == logging.WARNING

    def test_invalid_log_level_falls_back_to_info(self):
        configure_logging(_make_config(log_level="NOT-A-LEVEL"))

        assert logging.getLogger().level == logging.INFO

    def test_is_idempotent_does_not_duplicate_root_handlers(self):
        cfg = _make_config(log_level="INFO")
        configure_logging(cfg)
        first_count = len(logging.getLogger().handlers)

        configure_logging(cfg)
        second_count = len(logging.getLogger().handlers)

        # Exactly one _RawQueueHandler installed — no duplication.
        assert first_count == second_count == 1
        assert isinstance(
            logging.getLogger().handlers[0], _RawQueueHandler
        )

    def test_reconfigure_stops_previous_listener(self):
        configure_logging(_make_config())
        first_listener = logging_setup._listener
        assert first_listener is not None

        configure_logging(_make_config())
        second_listener = logging_setup._listener

        assert second_listener is not None
        assert second_listener is not first_listener

    def test_console_format_does_not_crash(self):
        # Only sanity-check: the module swaps renderers internally and
        # the result is not easily inspectable, but we can at least run it.
        configure_logging(_make_config(log_format="console"))

        assert logging_setup._listener is not None

    def test_audit_handler_attached_when_path_set(self, tmp_path):
        audit_path = tmp_path / "audit.log"
        configure_logging(
            _make_config(audit_log_path=str(audit_path))
        )

        audit_logger = logging.getLogger("audit")
        try:
            handler_types = [type(h) for h in audit_logger.handlers]
            assert logging.FileHandler in handler_types
            assert audit_logger.propagate is False
            assert audit_path.exists()
        finally:
            for h in list(audit_logger.handlers):
                try:
                    h.close()
                except Exception:
                    pass
                audit_logger.removeHandler(h)

    def test_audit_propagates_when_no_path(self):
        audit_logger = logging.getLogger("audit")
        # Clean slate for a deterministic assertion.
        for h in list(audit_logger.handlers):
            audit_logger.removeHandler(h)

        configure_logging(_make_config(audit_log_path=None))

        assert audit_logger.propagate is True
        # No FileHandler was attached when path is None.
        assert not any(
            isinstance(h, logging.FileHandler) for h in audit_logger.handlers
        )


class TestMobaideAgentLogFileEnvVar:
    """Pin the ``MOBAIDE_AGENT_LOG_FILE`` belt-and-suspenders sink.

    The Windows service wrapper redirects the agent subprocess's
    stdout to ``<prefix>/logs/agent.out.log``; that handles the
    bulk-of-deployments case. ``MOBAIDE_AGENT_LOG_FILE`` is an
    explicit operator-visible sink for the (rare) case where a
    custom supervisor pins stdout elsewhere — and so unit tests
    can opt into file logging without grappling with the
    subprocess wiring.
    """

    def test_log_file_handler_attached_when_env_var_set(
        self, tmp_path, monkeypatch
    ):
        log_path = tmp_path / "agent.out.log"
        monkeypatch.setenv("MOBAIDE_AGENT_LOG_FILE", str(log_path))

        configure_logging(_make_config())

        listener = logging_setup._listener
        assert listener is not None
        handler_types = [type(h) for h in listener.handlers]
        # The RotatingFileHandler MUST be wired in addition to the
        # stdout handler so a future subprocess-wiring change cannot
        # silently regress observability.
        assert logging.handlers.RotatingFileHandler in handler_types, (
            f"expected RotatingFileHandler when MOBAIDE_AGENT_LOG_FILE "
            f"is set; got: {handler_types!r}"
        )

    def test_no_log_file_handler_when_env_var_unset(self, monkeypatch):
        monkeypatch.delenv("MOBAIDE_AGENT_LOG_FILE", raising=False)

        configure_logging(_make_config())

        listener = logging_setup._listener
        assert listener is not None
        assert not any(
            isinstance(h, logging.handlers.RotatingFileHandler)
            for h in listener.handlers
        ), (
            "no RotatingFileHandler should be attached when "
            "MOBAIDE_AGENT_LOG_FILE is unset — the stdout handler is "
            "the only sink, and a regression that always attaches one "
            "would silently double-write every log line on every "
            "deployment"
        )

    def test_falls_back_when_log_file_unopenable(self, tmp_path, monkeypatch):
        # An unwritable path (directory missing in a way that mkdir
        # can't recover from — e.g. parent is a file) must not crash
        # the agent. The handler is best-effort; the stdout handler
        # remains as the primary sink.
        parent_is_file = tmp_path / "blocker"
        parent_is_file.write_text("not a directory")
        bad_path = parent_is_file / "agent.out.log"
        monkeypatch.setenv("MOBAIDE_AGENT_LOG_FILE", str(bad_path))

        # Must not raise.
        configure_logging(_make_config())

        listener = logging_setup._listener
        assert listener is not None
        # No file handler attached, but the listener is still running
        # on the stdout handler.
        assert any(
            isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
            for h in listener.handlers
        )


class TestShutdownLogging:
    def test_stops_listener_and_clears_global(self):
        configure_logging(_make_config())
        assert logging_setup._listener is not None

        shutdown_logging()

        assert logging_setup._listener is None

    def test_safe_when_listener_is_none(self):
        logging_setup._listener = None
        # Must not raise.
        shutdown_logging()
        assert logging_setup._listener is None

    def test_safe_to_call_twice(self):
        configure_logging(_make_config())
        shutdown_logging()
        # Must not raise.
        shutdown_logging()
        assert logging_setup._listener is None
