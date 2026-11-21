"""Signals of the work an agent does after a prompt's answer.

OpenCode, GitHub Copilot and Antigravity run no Claude Code hooks, but a
finished background task (a subagent, a shell) can wake them after the
``session/prompt`` answer, and the turn it starts streams as plain
``session/update`` notifications. Two agents also send an exact signal when the client
opts in through ``clientCapabilities._meta``:

* OpenCode 2.3+: ``opencode/session/child_update`` reports each subagent's
  status (``created``, ``running``, ``completed``, ``failed``,
  ``interrupted``) and its own updates. With the opt-in, those updates no
  longer reach the parent ``session/update`` stream.
* Copilot: ``github.com/copilot/sessionEvent`` with ``assistant.turn_start``,
  ``assistant.idle`` (the foreground settled; attached background work may
  still hold the prompt) and ``session.idle`` (nothing runs at all).

Both are parsed as untrusted data: an unknown shape is ignored.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

OPENCODE_CHILD_UPDATES = "opencode/child-session-updates"
OPENCODE_CHILD_UPDATE = "opencode/session/child_update"
OPENCODE_CHILD_META = "opencode/child-session"
OPENCODE_CLIENT_META: Mapping[str, Any] = {OPENCODE_CHILD_UPDATES: True}

COPILOT_META = "github.com/copilot"
COPILOT_SESSION_EVENT = "github.com/copilot/sessionEvent"
COPILOT_TURN_START = "assistant.turn_start"
COPILOT_FOREGROUND_IDLE = "assistant.idle"
COPILOT_SESSION_IDLE = "session.idle"
COPILOT_CLIENT_META: Mapping[str, Any] = {
    COPILOT_META: {"events": [COPILOT_TURN_START, COPILOT_FOREGROUND_IDLE, COPILOT_SESSION_IDLE]}
}

# Updates that show the agent at work. Metadata (usage, commands, title,
# mode, config) also comes while the agent is idle, so it never starts a turn.
CONTENT_UPDATES = frozenset(
    {"agent_message_chunk", "agent_thought_chunk", "tool_call", "tool_call_update"}
)
CHILD_RUNNING = frozenset({"created", "running"})
CHILD_ENDED = frozenset({"completed", "failed", "interrupted"})
_CHILD_TOOL_UPDATES = frozenset({"tool_call", "tool_call_update"})
_TITLE_MAX = 200


def method_is(method: str, name: str) -> bool:
    """An extension method, with or without the ACP ``_`` prefix."""
    return method == name or method == f"_{name}"


@dataclass(frozen=True)
class ChildUpdate:
    child_id: str
    title: str | None
    # One of the two: a status change, or a tool call update of the child.
    status: str | None = None
    update: dict[str, Any] | None = None


def child_update(params: Any, root_session_id: str | None) -> ChildUpdate | None:
    """One ``opencode/session/child_update`` of this session, or None.

    Of the child's own updates only its tool calls are kept, as tool cards of
    their own (id ``<child id>:<tool call id>``, title prefixed with the
    child's title), the way OpenCode showed them in the parent stream. Its
    messages would interleave with the parent's answer: the parent reports
    the child's result when it wakes.
    """
    if not isinstance(params, dict):
        return None
    root = params.get("rootSessionId")
    if root_session_id is not None and root != root_session_id:
        return None
    child_id = params.get("childSessionId")
    if not isinstance(child_id, str) or not child_id:
        return None
    raw_title = params.get("title")
    title = raw_title.strip()[:_TITLE_MAX] if isinstance(raw_title, str) and raw_title.strip() else None
    kind = params.get("type")
    if kind == "status":
        status = params.get("status")
        if not isinstance(status, str) or not status:
            return None
        if status not in CHILD_RUNNING and status not in CHILD_ENDED:
            # A status this version does not know counts as an end: a child
            # kept as running would hold the alert for good.
            status = "failed"
        return ChildUpdate(child_id, title, status=status)
    if kind != "update":
        return None
    update = params.get("update")
    if not isinstance(update, dict) or update.get("sessionUpdate") not in _CHILD_TOOL_UPDATES:
        return None
    tool_id = update.get("toolCallId")
    if not isinstance(tool_id, str) or not tool_id:
        return None
    projected = dict(update)
    if not tool_id.startswith(f"{child_id}:"):
        # OpenCode projects the update itself; an unprojected one still must
        # not collide with a tool call of the parent.
        projected["toolCallId"] = f"{child_id}:{tool_id}"
        tool_title = projected.get("title")
        if title and isinstance(tool_title, str) and tool_title:
            projected["title"] = f"{title}: {tool_title}"
    return ChildUpdate(child_id, title, update=projected)


def copilot_event(params: Any, session_id: str | None) -> tuple[str, dict[str, Any]] | None:
    """``(type, data)`` of a foreground ``github.com/copilot/sessionEvent``
    of this session, or None (another session, or a subagent's event)."""
    if not isinstance(params, dict):
        return None
    if session_id is not None and params.get("sessionId") != session_id:
        return None
    if params.get("agentId") is not None:
        return None
    kind = params.get("type")
    if not isinstance(kind, str):
        return None
    data = params.get("data")
    return kind, data if isinstance(data, dict) else {}
