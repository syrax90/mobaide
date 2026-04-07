"""WebSocket lifecycle logging helpers.

Per-message PTY input/output is intentionally NOT logged. Only connect /
authenticated / disconnect / error events are emitted, plus aggregated counters
(``bytes_in``, ``bytes_out``, ``msg_count``) on disconnect.
"""
from __future__ import annotations

from typing import Any

from ..logging import bind_request_context, get_logger

_logger = get_logger("claude_mobile.ws")


def _client_addr(websocket: Any) -> str | None:
    client = getattr(websocket, "client", None)
    if client is None:
        return None
    return f"{client.host}:{client.port}"


def log_ws_connect(channel: str, websocket: Any, **extra) -> None:
    bind_request_context(ws_channel=channel, client_ip=_client_addr(websocket))
    _logger.info("ws.connect", channel=channel, client_ip=_client_addr(websocket), **extra)


def log_ws_authenticated(channel: str, username: str, **extra) -> None:
    bind_request_context(username=username)
    _logger.info("ws.authenticated", channel=channel, username=username, **extra)


def log_ws_disconnect(
    channel: str,
    *,
    bytes_in: int = 0,
    bytes_out: int = 0,
    msg_count: int = 0,
    reason: str | None = None,
    **extra,
) -> None:
    _logger.info(
        "ws.disconnect",
        channel=channel,
        bytes_in=bytes_in,
        bytes_out=bytes_out,
        msg_count=msg_count,
        reason=reason,
        **extra,
    )


def log_ws_error(channel: str, error: BaseException, **extra) -> None:
    _logger.error(
        "ws.error",
        channel=channel,
        error_type=type(error).__name__,
        error_message=str(error),
        **extra,
    )
