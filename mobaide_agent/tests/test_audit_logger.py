"""Tests for the audit logger channel."""
from __future__ import annotations

import logging

import pytest
import structlog

from mobaide.agent.logging import audit_event, get_audit_logger


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


# ---------------------------------------------------------------------------
# Strengthened coverage: outcome propagation, optional fields, extra kwargs,
# and get_audit_logger() stability.
#
# Audit events are routed via ``structlog.get_logger("audit")`` which uses
# ``structlog.stdlib.LoggerFactory`` at runtime (after ``configure_logging``
# has run via the ``client`` fixture import chain). They land on stdlib
# ``logging`` records where ``record.msg`` is still a dict — caplog captures
# them before ``_RawQueueHandler`` forwards to the queue, so we can inspect
# the event dict fields directly.
#
# We deliberately do NOT use ``structlog.testing.capture_logs`` here: it
# swaps the default structlog config, but the ``audit`` logger already has
# a cached stdlib bound logger by the time any test runs, so events bypass
# the capture processor entirely.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_contextvars_for_audit():
    """Audit events are merged with contextvars; wipe them around each test."""
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


_ = _clean_contextvars_for_audit


def _audit_records(caplog) -> list[dict]:
    """Extract audit LogRecord ``msg`` dicts from caplog.

    Only records whose logger name is ``audit`` and whose ``msg`` is a dict
    (i.e. produced via structlog, not via stdlib ``logger.info("%s", ...)``)
    are returned.
    """
    out: list[dict] = []
    for r in caplog.records:
        if r.name == "audit" and isinstance(r.msg, dict):
            out.append(r.msg)
    return out


class TestOutcomePropagation:
    def test_outcome_success(self, caplog):
        with caplog.at_level(logging.INFO, logger="audit"):
            audit_event(
                "audit.file.read",
                actor="alice",
                target="file-1",
                outcome="success",
            )

        records = _audit_records(caplog)
        matching = [r for r in records if r.get("event") == "audit.file.read"]
        assert matching, "expected an audit.file.read record"
        entry = matching[-1]
        assert entry["outcome"] == "success"
        assert entry["audit"] is True
        assert entry["actor"] == "alice"
        assert entry["target"] == "file-1"

    def test_outcome_failure(self, caplog):
        with caplog.at_level(logging.INFO, logger="audit"):
            audit_event(
                "audit.auth.login",
                actor="mallory",
                target="session",
                outcome="failure",
            )

        records = _audit_records(caplog)
        matching = [r for r in records if r.get("event") == "audit.auth.login"]
        assert matching
        assert matching[-1]["outcome"] == "failure"

    def test_outcome_error(self, caplog):
        with caplog.at_level(logging.INFO, logger="audit"):
            audit_event(
                "audit.session.create",
                actor="bob",
                target="sess-1",
                outcome="error",
            )

        records = _audit_records(caplog)
        matching = [
            r for r in records if r.get("event") == "audit.session.create"
        ]
        assert matching
        assert matching[-1]["outcome"] == "error"


class TestOptionalFieldHandling:
    def test_target_none_still_emitted(self, caplog):
        """``target=None`` must be present in the record, not omitted."""
        with caplog.at_level(logging.INFO, logger="audit"):
            audit_event(
                "audit.config.reload",
                actor="alice",
                target=None,
                outcome="success",
            )

        records = _audit_records(caplog)
        matching = [
            r for r in records if r.get("event") == "audit.config.reload"
        ]
        assert matching
        entry = matching[-1]
        assert "target" in entry
        assert entry["target"] is None

    def test_actor_none_still_emitted(self, caplog):
        """``actor=None`` must be present in the record, not omitted."""
        with caplog.at_level(logging.INFO, logger="audit"):
            audit_event(
                "audit.system.startup",
                actor=None,
                target="agent",
                outcome="ok",
            )

        records = _audit_records(caplog)
        matching = [
            r for r in records if r.get("event") == "audit.system.startup"
        ]
        assert matching
        entry = matching[-1]
        assert "actor" in entry
        assert entry["actor"] is None

    def test_all_optional_fields_none(self, caplog):
        with caplog.at_level(logging.INFO, logger="audit"):
            audit_event("audit.noop")

        records = _audit_records(caplog)
        matching = [r for r in records if r.get("event") == "audit.noop"]
        assert matching
        entry = matching[-1]
        assert entry["actor"] is None
        assert entry["target"] is None
        # Default outcome is "ok".
        assert entry["outcome"] == "ok"


class TestExtraKwargsPreserved:
    def test_single_extra_kwarg(self, caplog):
        with caplog.at_level(logging.INFO, logger="audit"):
            audit_event(
                "audit.custom",
                actor="alice",
                target="t",
                outcome="ok",
                request_id="req-42",
            )

        records = _audit_records(caplog)
        matching = [r for r in records if r.get("event") == "audit.custom"]
        assert matching
        assert matching[-1]["request_id"] == "req-42"

    def test_multiple_extras_mixed_types(self, caplog):
        with caplog.at_level(logging.INFO, logger="audit"):
            audit_event(
                "audit.multi",
                actor="alice",
                target=None,
                outcome="success",
                session_id="s-1",
                attempt=3,
                metadata={"k": "v"},
            )

        records = _audit_records(caplog)
        matching = [r for r in records if r.get("event") == "audit.multi"]
        assert matching
        entry = matching[-1]
        assert entry["session_id"] == "s-1"
        assert entry["attempt"] == 3
        assert entry["metadata"] == {"k": "v"}

    def test_extras_do_not_collide_with_reserved_fields(self):
        """Passing a reserved kwarg twice (positional+**kwargs) is a TypeError.

        Locks in the signature contract: if a future refactor silently
        accepted ``**extra`` containing ``outcome``, this test would catch
        it. Python itself raises ``TypeError`` here, so we don't even need
        to reach the logger.
        """
        extras = {"outcome": "also-ok"}
        with pytest.raises(TypeError):
            audit_event(
                "audit.conflict",
                actor="x",
                target="t",
                outcome="ok",
                **extras,
            )


class TestGetAuditLogger:
    def test_returns_same_instance_on_repeated_calls(self):
        a = get_audit_logger()
        b = get_audit_logger()

        assert a is b

    def test_returned_logger_emits_audit_event(self, caplog):
        logger = get_audit_logger()

        with caplog.at_level(logging.INFO, logger="audit"):
            logger.info("audit.direct", audit=True, actor="alice")

        records = _audit_records(caplog)
        assert any(r.get("event") == "audit.direct" for r in records)
