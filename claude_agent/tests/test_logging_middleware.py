"""End-to-end test of the HTTP logging middleware + correlation ID."""
from __future__ import annotations

import logging
import re
import uuid

import pytest


@pytest.mark.asyncio
async def test_health_endpoint_returns_correlation_header(client):
    """The X-Request-ID header is added by CorrelationIdMiddleware."""
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    # asgi-correlation-id sets X-Request-ID on the response
    assert "x-request-id" in {k.lower() for k in resp.headers.keys()}


@pytest.mark.asyncio
async def test_request_emits_received_and_completed(client, caplog):
    """A real route should emit both http.request.received and http.request.completed."""
    with caplog.at_level(logging.INFO):
        resp = await client.post(
            "/api/auth/token",
            json={"username": "no-such-user", "password": "x"},
        )
    assert resp.status_code == 401

    msgs = [r.getMessage() for r in caplog.records]
    assert any("http.request.received" in m for m in msgs)
    assert any("http.request.completed" in m for m in msgs)


# ---------------------------------------------------------------------------
# Strengthened coverage:
#  - /api/health is excluded from the http logging middleware.
#  - X-Request-ID reflect/generate behaviour.
#  - Correlation-id uniqueness across consecutive requests.
#  - http.request.completed field propagation (status, duration_ms, username).
#  - No contextvars leakage between requests.
#
# For the exception path: per the plan, forcing a mid-handler exception
# without touching source or conftest is intrusive. We instead lock in the
# field contract of http.request.completed (duration_ms/status/username)
# and the contextvars cleanup invariant across back-to-back requests.
# ---------------------------------------------------------------------------


_HEX_32_RE = re.compile(r"^[0-9a-f]{32}$")


def _app_records(caplog) -> list[dict]:
    """Extract app-middleware LogRecords where ``msg`` is a dict.

    structlog routes these records through stdlib logging; caplog captures
    them before the queue handler runs, so ``record.msg`` is still a dict.
    """
    out: list[dict] = []
    for r in caplog.records:
        if r.name == "claude_mobile.agent.app" and isinstance(r.msg, dict):
            out.append(r.msg)
    return out


def _events_with(records: list[dict], event_name: str) -> list[dict]:
    return [r for r in records if r.get("event") == event_name]


class TestHealthEndpointSkipped:
    async def test_health_does_not_emit_http_request_received(
        self, client, caplog
    ):
        """``/api/health`` is excluded from the HTTP logging middleware."""
        with caplog.at_level(logging.INFO):
            resp = await client.get("/api/health")
        assert resp.status_code == 200

        received = _events_with(_app_records(caplog), "http.request.received")
        completed = _events_with(_app_records(caplog), "http.request.completed")

        # Neither received nor completed should appear for /api/health.
        assert not any(r.get("path") == "/api/health" for r in received)
        assert not any(r.get("path") == "/api/health" for r in completed)

    async def test_health_still_returns_correlation_id_header(self, client):
        """``CorrelationIdMiddleware`` is outer — it still runs for /api/health."""
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        header_keys = {k.lower() for k in resp.headers.keys()}
        assert "x-request-id" in header_keys


class TestCorrelationIdHeader:
    async def test_generates_32_char_hex_when_missing(self, client):
        """Absent header → server generates a W3C trace-id shaped value."""
        resp = await client.get("/api/health")
        rid = resp.headers.get("x-request-id", "")
        assert _HEX_32_RE.match(rid), (
            f"expected 32-char hex trace id, got: {rid!r}"
        )

    async def test_reflects_valid_uuid4_from_client(self, client):
        """Client-provided UUID4 is echoed back by CorrelationIdMiddleware."""
        supplied = str(uuid.uuid4())
        resp = await client.get(
            "/api/health",
            headers={"X-Request-ID": supplied},
        )
        assert resp.status_code == 200
        assert resp.headers.get("x-request-id") == supplied

    async def test_back_to_back_requests_get_distinct_ids(self, client):
        """Two requests without X-Request-ID must not share a correlation id."""
        r1 = await client.get("/api/health")
        r2 = await client.get("/api/health")

        id1 = r1.headers.get("x-request-id")
        id2 = r2.headers.get("x-request-id")

        assert id1 and id2
        assert id1 != id2


class TestCompletedEventFields:
    async def test_completed_contains_duration_status_and_none_username(
        self, client, caplog
    ):
        """Unauthenticated requests → username is None on completion."""
        with caplog.at_level(logging.INFO):
            resp = await client.post(
                "/api/auth/token",
                json={"username": "no-such-user", "password": "x"},
            )
        assert resp.status_code == 401

        completed = _events_with(
            _app_records(caplog), "http.request.completed"
        )
        # There should be exactly one record for this request path.
        matching = [
            r for r in completed if r.get("path") == "/api/auth/token"
        ]
        assert matching, "expected http.request.completed for /api/auth/token"
        rec = matching[-1]
        assert rec["method"] == "POST"
        assert rec["status"] == 401
        assert rec["username"] is None
        assert isinstance(rec["duration_ms"], (int, float))
        assert rec["duration_ms"] >= 0
        # Correlation id was bound into the event dict.
        assert "request_id" in rec and rec["request_id"]

    async def test_completed_propagates_authenticated_username(
        self, client, auth_headers, caplog
    ):
        """An authenticated route → username from request.state propagates."""
        with caplog.at_level(logging.INFO):
            resp = await client.get("/api/sessions", headers=auth_headers)
        assert resp.status_code == 200

        completed = _events_with(
            _app_records(caplog), "http.request.completed"
        )
        matching = [
            r for r in completed if r.get("path") == "/api/sessions"
        ]
        assert matching, "expected http.request.completed for /api/sessions"
        rec = matching[-1]
        assert rec["method"] == "GET"
        assert rec["status"] == 200
        # ``get_current_user`` assigns request.state.username on auth success.
        assert rec["username"] == "testuser"


class TestContextvarsHygiene:
    async def test_two_sequential_requests_do_not_share_request_id(
        self, client, caplog
    ):
        """If cleanup was broken, the 2nd request would log the 1st request id.

        The middleware calls ``clear_request_context`` at the end of each
        request. We assert that the second request's completed record has a
        *different* request_id than the first's — a leak would cause the
        same id (or a stale one) to appear on both.
        """
        with caplog.at_level(logging.INFO):
            r1 = await client.post(
                "/api/auth/token",
                json={"username": "u1", "password": "x"},
            )
            r2 = await client.post(
                "/api/auth/token",
                json={"username": "u2", "password": "x"},
            )
        assert r1.status_code == 401
        assert r2.status_code == 401

        completed = _events_with(
            _app_records(caplog), "http.request.completed"
        )
        auth_completed = [
            r for r in completed if r.get("path") == "/api/auth/token"
        ]
        assert len(auth_completed) >= 2, (
            "expected >=2 http.request.completed entries for /api/auth/token"
        )
        ids = [r["request_id"] for r in auth_completed[-2:]]
        assert ids[0] != ids[1]
        # HTTP-level header also differs.
        assert r1.headers.get("x-request-id") != r2.headers.get("x-request-id")
