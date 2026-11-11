"""End-to-end attention from OSC 7501 program-status reports.

A terminal session without Claude Code hooks raises attention from the status
reports its CLI writes; the agent sends it as a ``user_event`` and the server
relays it to the owner's user-events socket. Claude sessions keep their hooks
as the only source and never act on a report.

The agent runs with ``MOBAIDE_ENV=test``, so a terminal session is the
``FakePtyBackend`` echo: a report sent as input comes back as the session's
output, where a CLI would write it. Any input clears attention and cancels a
pending quiet window, so each report is the last write before the test waits.
"""
from __future__ import annotations

import asyncio
import sys
import time

import pytest
import websockets

from mobaide.core.program_status import (
    PROGRAM_STATUS_MIN_ALERT_SPACING_SECONDS,
    PROGRAM_STATUS_QUIET_SECONDS,
)
from mock_mobile_client import FRAME_INPUT, MockMobileClient

pytestmark = pytest.mark.timeout(120)

BLOCKED = b"\x1b]7501;state=blocked:kind=permission:app=codex\x07"
DONE = b"\x1b]7501;state=done:app=codex\x07"
ERROR = b"\x1b]7501;state=error:app=codex\x1b\\"

# A Claude terminal launch resolves the CLI binary; the fake PTY never runs it.
_CLAUDE_AGENT_ENV = {"MOBAIDE_CLAUDE_BIN": sys.executable}


def _wait_until(predicate, *, timeout: float = 30.0, interval: float = 0.25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise TimeoutError(f"condition not met within {timeout}s")


async def _report(
    client: MockMobileClient, agent_id: str, session_id: str, token: str, report: bytes
) -> None:
    """Write ``report`` to the session and wait for its echo: by then the
    agent's reader has handed it to the session's status gate."""
    await client.drive_session(
        agent_id, session_id, token, frame=FRAME_INPUT + report, expect=report
    )


async def _drive_shell(client: MockMobileClient, agent_id: str, token: str) -> None:
    async with websockets.connect(client.user_events_url(), open_timeout=15) as events:
        session_id = client.create_shell_session(agent_id, token)

        await _report(client, agent_id, session_id, token, BLOCKED)
        alert = (await client.attention_until(events, session_id))[-1]
        first_alert = time.monotonic()
        assert alert["reason"] == "awaiting_permission"
        assert alert["kind"] == "shell"
        assert alert["agent_id"] == agent_id
        assert "kind_label" not in alert

        sent = time.monotonic()
        await _report(client, agent_id, session_id, token, DONE)
        alert = (await client.attention_until(events, session_id))[-1]
        assert alert["reason"] == "turn_finished"
        # The window that lets a queued next turn cancel the alert is live.
        assert time.monotonic() - sent >= PROGRAM_STATUS_QUIET_SECONDS - 0.1
        # A session's alerts are spaced: this one waited out the gap after
        # the prompt's alert instead of being dropped.
        assert time.monotonic() - first_alert >= PROGRAM_STATUS_MIN_ALERT_SPACING_SECONDS - 0.25

        await _report(client, agent_id, session_id, token, ERROR)
        alert = (await client.attention_until(events, session_id))[-1]
        assert alert["reason"] == "turn_failed"
        assert alert["kind"] == "shell"


def test_shell_status_reports_reach_the_owner(e2e_stack):
    client = e2e_stack.client
    token = client.mint_mobile_token(e2e_stack.agent_id)

    asyncio.run(_drive_shell(client, e2e_stack.agent_id, token))


async def _claude_stays_quiet(
    client: MockMobileClient, agent_id: str, token: str, claude_id: str, shell_id: str
) -> list[dict]:
    async with websockets.connect(client.user_events_url(), open_timeout=15) as events:
        await _report(client, agent_id, claude_id, token, BLOCKED + DONE + ERROR)
        # The shell's report is written after, and its window opens later, so
        # any alert the Claude session raised would arrive first.
        await _report(client, agent_id, shell_id, token, DONE)
        return await client.attention_until(events, shell_id)


def test_claude_terminal_ignores_status_reports(e2e_stack, spawn_agent, tmp_path):
    client = e2e_stack.client
    agent = spawn_agent("e2e-attention-agent", extra_env=_CLAUDE_AGENT_ENV)
    agent_id = agent.agent_id
    _wait_until(
        lambda: any(a["agent_id"] == agent_id and a["online"] for a in client.list_agents())
    )
    token = client.mint_mobile_token(agent_id)

    claude_body = {"cli": "claude", "cwd": str(tmp_path), "claude_options": {"skip_permissions": False}}
    created = client.proxy_request(agent_id, token, "POST", "api/sessions", claude_body)
    assert created.status_code == 201, created.text
    assert created.json()["kind"] == "claude"
    claude_id = created.json()["session_id"]
    shell_id = client.create_shell_session(agent_id, token)

    seen = asyncio.run(_claude_stays_quiet(client, agent_id, token, claude_id, shell_id))

    assert [(a["session_id"], a["reason"]) for a in seen] == [(shell_id, "turn_finished")]
