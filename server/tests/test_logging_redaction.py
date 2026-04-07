"""Tests for the structlog redaction processor."""
from __future__ import annotations

from claude_mobile.server.logging.redaction import REDACTED, redact_processor


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
