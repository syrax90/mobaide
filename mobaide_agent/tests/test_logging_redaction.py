"""Tests for the structlog redaction processor."""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

import mobaide.__main__ as entrypoint
import mobaide.agent.app as agent_app
from mobaide.agent.logging.redaction import REDACTED, redact_processor
from mobaide.agent.logging_setup import configure_logging, shutdown_logging

FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyLTEifQ.c2lnbmF0dXJlLWJ5dGVz"


def _run(event_dict):
    return redact_processor(None, "info", event_dict)


def test_redacts_password_key():
    out = _run({"event": "login", "password": "hunter2"})
    assert out["password"] == REDACTED


def test_redacts_authorization_header():
    out = _run({"event": "req", "headers": {"Authorization": "Bearer abc"}})
    assert out["headers"]["Authorization"] == REDACTED


def test_redacts_jwt_in_string():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.signaturepart12345"
    out = _run({"event": "msg", "detail": f"token={jwt}"})
    assert jwt not in out["detail"]
    assert REDACTED in out["detail"]


def test_redacts_linear_api_key():
    out = _run({"event": "config", "linear_api_key": "lin_api_xxx"})
    assert out["linear_api_key"] == REDACTED


def test_redacts_pty_stdin():
    out = _run({"event": "input", "pty_stdin": "secret typed text"})
    assert out["pty_stdin"] == REDACTED


def test_redacts_bearer_in_string():
    out = _run({"event": "req", "h": "Authorization: Bearer abc.def.ghi"})
    assert "Bearer abc.def.ghi" not in out["h"]


def test_passes_through_safe_data():
    out = _run({"event": "ok", "user": "alice", "count": 5})
    assert out["user"] == "alice"
    assert out["count"] == 5


def test_redacts_nested_dict():
    out = _run({"event": "req", "body": {"password": "p", "name": "alice"}})
    assert out["body"]["password"] == REDACTED
    assert out["body"]["name"] == "alice"


@pytest.mark.parametrize(
    ("url", "secret"),
    [
        ("/internal/hook?hook=tool&token=hook-secret-1", "hook-secret-1"),
        ("/ws/events?ticket=t-9f8e", "t-9f8e"),
    ],
)
def test_redacts_query_param_secrets_in_url(url, secret):
    out = _run({"event": "req", "path": url})
    assert secret not in out["path"]
    assert REDACTED in out["path"]


def test_uvicorn_websocket_handshake_line_is_redacted(monkeypatch, capsys):
    """The handshake line uvicorn logs must reach the redacting pipeline.

    Drives the real ``_run_agent`` so uvicorn's own logging setup runs with
    the exact kwargs the agent passes; uvicorn's default dictConfig is what
    used to bypass redaction.
    """
    monkeypatch.delenv("MOBAIDE_AGENT_LOG_FILE", raising=False)

    def fake_create_app():
        configure_logging(
            SimpleNamespace(log_format="json", log_level="INFO", audit_log_path=None)
        )
        return SimpleNamespace(
            state=SimpleNamespace(config=SimpleNamespace(host="127.0.0.1", port=0))
        )

    def fake_serve(server):
        server.started = True
        logging.getLogger("uvicorn.error").info(
            '%s - "WebSocket %s" %s',
            "192.168.1.20:0",
            f"/ws/events?token={FAKE_JWT}",
            "[accepted]",
        )

    monkeypatch.setattr(agent_app, "create_app", fake_create_app)
    monkeypatch.setattr(entrypoint, "_serve", fake_serve)
    try:
        entrypoint._run_agent()
    finally:
        shutdown_logging()

    out = capsys.readouterr()
    assert FAKE_JWT not in out.out + out.err
    lines = [ln for ln in out.out.splitlines() if "WebSocket" in ln]
    assert len(lines) == 1
    assert REDACTED in json.loads(lines[0])["event"]
