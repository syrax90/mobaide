"""Tests for the audit logger channel."""
from __future__ import annotations

import logging

import pytest

from claude_mobile.server.logging import audit_event


def test_audit_event_emits_to_audit_logger(capsys, caplog):
    with caplog.at_level(logging.DEBUG):
        audit_event(
            "audit.test.event",
            actor="alice",
            target="thing-1",
            outcome="ok",
            extra_field=42,
        )
    captured = capsys.readouterr().out
    log_text = "\n".join(r.getMessage() for r in caplog.records)
    combined = captured + log_text
    assert "audit.test.event" in combined
    assert "alice" in combined


@pytest.mark.asyncio
async def test_login_failure_emits_audit_event(client, caplog):
    with caplog.at_level(logging.INFO, logger="audit"):
        resp = await client.post(
            "/api/auth/token",
            json={"username": "ghost", "password": "wrong"},
        )
    assert resp.status_code == 401
    audit_msgs = [r.getMessage() for r in caplog.records if r.name == "audit"]
    assert any("audit.auth.login_failure" in m for m in audit_msgs)
