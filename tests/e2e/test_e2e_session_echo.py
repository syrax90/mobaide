"""End-to-end test: drive the full mobile <-> server <-> agent path over real sockets.

Both servers run as real subprocesses on ephemeral loopback ports (see conftest's
``e2e_stack`` fixture). The headless client speaks the actual REST + WebSocket
protocol the Android app uses, exercising the real relay auth handshake, proxy
routing, and binary envelope framing that in-process ASGI tests cannot reach.

Sessions are SHELL sessions backed by the agent's FakePtyBackend (pure byte echo),
so no real shell or claude/codex CLI is spawned and a sent marker comes straight back.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from mock_mobile_client import FRAME_INPUT, FRAME_SUBMIT

pytestmark = pytest.mark.timeout(120)


def _unique(prefix: str) -> bytes:
    return f"{prefix}-{uuid.uuid4().hex[:8]}".encode()


def test_health_reports_agent_online(e2e_stack):
    """Readiness gate contract: the agent's relay tunnel is up (no flaky 4012 closes)."""
    health = e2e_stack.client.health()
    assert health["status"] == "ok"
    assert health["agents_online"] >= 1
    assert health["agents_registered"] >= 1


def test_session_echo_over_real_sockets(e2e_stack):
    """The core acceptance path: login -> agent -> session -> input -> echoed output."""
    client = e2e_stack.client
    mobile_token = client.mint_mobile_token(e2e_stack.agent_id)
    session_id = client.create_shell_session(e2e_stack.agent_id, mobile_token)
    assert session_id

    marker = _unique("E2E-PING")
    echoed = asyncio.run(
        client.drive_session(
            e2e_stack.agent_id,
            session_id,
            mobile_token,
            frame=FRAME_INPUT + marker,
            expect=marker,
        )
    )
    assert marker in echoed


def test_submit_frame_appends_carriage_return(e2e_stack):
    """A second C->S frame type: submit echoes the payload followed by a carriage return."""
    client = e2e_stack.client
    mobile_token = client.mint_mobile_token(e2e_stack.agent_id)
    session_id = client.create_shell_session(e2e_stack.agent_id, mobile_token)

    marker = _unique("E2E-SUBMIT")
    echoed = asyncio.run(
        client.drive_session(
            e2e_stack.agent_id,
            session_id,
            mobile_token,
            frame=FRAME_SUBMIT + marker,
            expect=marker + b"\r",
        )
    )
    assert marker + b"\r" in echoed


def test_sessions_list_channel_lists_session(e2e_stack):
    """The JSON relay channel routes too: the proxied session appears in the live list."""
    client = e2e_stack.client
    mobile_token = client.mint_mobile_token(e2e_stack.agent_id)
    session_id = client.create_shell_session(e2e_stack.agent_id, mobile_token)

    sessions = asyncio.run(
        client.read_sessions_list(e2e_stack.agent_id, mobile_token)
    )
    assert any(s.get("session_id") == session_id for s in sessions)
