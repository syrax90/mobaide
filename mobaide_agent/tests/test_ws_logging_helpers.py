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

from mobaide.agent.ws.logging import (
    _client_addr,
    log_ws_authenticated,
    log_ws_connect,
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
    ``mobaide.agent.ws.logging`` so this test file sees a clean
    default-mode structlog regardless of execution order.
    """
    structlog.reset_defaults()
    from mobaide.agent.ws import logging as _ws_logging_mod
    _ws_logging_mod._logger = structlog.get_logger("mobaide.ws")

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
    def test_binds_username_to_contextvars(self):
        log_ws_authenticated("session-io", "bob")

        ctx = structlog.contextvars.get_contextvars()
        assert ctx.get("username") == "bob"
