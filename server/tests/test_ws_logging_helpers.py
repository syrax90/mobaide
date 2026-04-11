"""Tests for the WebSocket lifecycle logging helpers.

Verifies that ``log_ws_connect`` / ``log_ws_authenticated`` /
``log_ws_disconnect`` / ``log_ws_error`` each emit the correct structlog
event *and* bind the expected contextvars. Per-message PTY traffic is
intentionally NOT logged and is not covered here.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import structlog
from structlog.testing import capture_logs

from claude_mobile.server.ws.logging import (
    _client_addr,
    log_ws_authenticated,
    log_ws_connect,
    log_ws_disconnect,
    log_ws_error,
)


@pytest.fixture(autouse=True)
def _reset_structlog_and_contextvars():
    """Ensure each test starts in default structlog mode.

    ``conftest.py``'s ``app`` fixture indirectly calls ``configure_logging``
    (via ``create_app``), which switches structlog to a stdlib-backed
    ``LoggerFactory`` with ``cache_logger_on_first_use=True``. Any test
    file that runs AFTER an app-fixture-using test inherits that frozen
    state, which breaks ``structlog.testing.capture_logs`` — emissions
    route through stdlib handlers instead of the in-process capture chain.

    Reset the defaults here AND rebind the module-level cached logger in
    ``claude_mobile.server.ws.logging`` so this test file sees a clean
    default-mode structlog regardless of execution order.
    """
    structlog.reset_defaults()
    from claude_mobile.server.ws import logging as _ws_logging_mod
    _ws_logging_mod._logger = structlog.get_logger("claude_mobile.ws")

    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


_ = _reset_structlog_and_contextvars


def _fake_ws(host: str | None = "1.2.3.4", port: int = 5678):
    """Build a minimal stand-in for ``starlette.websockets.WebSocket``.

    Only the ``.client`` attribute is inspected by ``_client_addr``.
    """
    if host is None:
        return SimpleNamespace(client=None)
    return SimpleNamespace(client=SimpleNamespace(host=host, port=port))


class TestClientAddr:
    def test_returns_none_when_client_missing(self):
        ws = _fake_ws(host=None)

        assert _client_addr(ws) is None

    def test_returns_none_when_attr_absent(self):
        ws = SimpleNamespace()  # no .client attribute at all

        assert _client_addr(ws) is None

    def test_formats_host_port(self):
        ws = _fake_ws(host="10.0.0.1", port=9090)

        assert _client_addr(ws) == "10.0.0.1:9090"


class TestLogWsConnect:
    def test_emits_connect_event(self):
        ws = _fake_ws(host="1.2.3.4", port=5678)

        with capture_logs() as logs:
            log_ws_connect("session-io", ws, session_id="sess-1")

        assert len(logs) == 1
        entry = logs[0]
        assert entry["event"] == "ws.connect"
        assert entry["channel"] == "session-io"
        assert entry["client_ip"] == "1.2.3.4:5678"
        assert entry["session_id"] == "sess-1"
        assert entry["log_level"] == "info"

    def test_binds_ws_channel_and_client_ip_to_contextvars(self):
        ws = _fake_ws(host="10.0.0.2", port=443)

        log_ws_connect("session-list", ws)

        ctx = structlog.contextvars.get_contextvars()
        assert ctx.get("ws_channel") == "session-list"
        assert ctx.get("client_ip") == "10.0.0.2:443"

    def test_client_ip_is_none_when_ws_has_no_client(self):
        ws = _fake_ws(host=None)

        with capture_logs() as logs:
            log_ws_connect("events", ws)

        assert logs[0]["client_ip"] is None
        ctx = structlog.contextvars.get_contextvars()
        assert ctx.get("client_ip") is None


class TestLogWsAuthenticated:
    def test_emits_authenticated_event(self):
        with capture_logs() as logs:
            log_ws_authenticated("session-io", "alice", session_id="s-1")

        assert len(logs) == 1
        entry = logs[0]
        assert entry["event"] == "ws.authenticated"
        assert entry["channel"] == "session-io"
        assert entry["username"] == "alice"
        assert entry["session_id"] == "s-1"

    def test_binds_username_to_contextvars(self):
        log_ws_authenticated("session-io", "bob")

        ctx = structlog.contextvars.get_contextvars()
        assert ctx.get("username") == "bob"


class TestLogWsDisconnect:
    def test_emits_disconnect_with_counters(self):
        with capture_logs() as logs:
            log_ws_disconnect(
                "session-io",
                bytes_in=1024,
                bytes_out=2048,
                msg_count=7,
                reason="client_closed",
            )

        assert len(logs) == 1
        entry = logs[0]
        assert entry["event"] == "ws.disconnect"
        assert entry["channel"] == "session-io"
        assert entry["bytes_in"] == 1024
        assert entry["bytes_out"] == 2048
        assert entry["msg_count"] == 7
        assert entry["reason"] == "client_closed"

    def test_defaults_are_zero_and_reason_none(self):
        with capture_logs() as logs:
            log_ws_disconnect("events")

        entry = logs[0]
        assert entry["bytes_in"] == 0
        assert entry["bytes_out"] == 0
        assert entry["msg_count"] == 0
        assert entry["reason"] is None

    def test_forwards_extra_kwargs(self):
        with capture_logs() as logs:
            log_ws_disconnect(
                "session-io",
                bytes_in=1,
                bytes_out=2,
                msg_count=3,
                reason="ok",
                session_id="sess-xyz",
            )

        assert logs[0]["session_id"] == "sess-xyz"


class TestLogWsError:
    def test_emits_error_with_type_and_message(self):
        err = ValueError("boom")

        with capture_logs() as logs:
            log_ws_error("session-io", err)

        assert len(logs) == 1
        entry = logs[0]
        assert entry["event"] == "ws.error"
        assert entry["channel"] == "session-io"
        assert entry["error_type"] == "ValueError"
        assert entry["error_message"] == "boom"
        assert entry["log_level"] == "error"

    def test_handles_custom_exception_type(self):
        class MyCustomError(RuntimeError):
            pass

        with capture_logs() as logs:
            log_ws_error("events", MyCustomError("x"))

        assert logs[0]["error_type"] == "MyCustomError"
        assert logs[0]["error_message"] == "x"

    def test_handles_base_exception_subclass(self):
        with capture_logs() as logs:
            log_ws_error("events", KeyboardInterrupt("stop"))

        assert logs[0]["error_type"] == "KeyboardInterrupt"
        assert logs[0]["error_message"] == "stop"

    def test_forwards_extra_kwargs(self):
        with capture_logs() as logs:
            log_ws_error(
                "session-io",
                ValueError("nope"),
                session_id="s-9",
                retry_count=2,
            )

        entry = logs[0]
        assert entry["session_id"] == "s-9"
        assert entry["retry_count"] == 2
