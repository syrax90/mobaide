"""End-to-end test of the HTTP logging middleware + correlation ID."""
from __future__ import annotations

import logging

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
