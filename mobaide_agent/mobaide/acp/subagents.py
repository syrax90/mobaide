"""ACP subagent sessions: the parent's view of each child session.

A client that declares ``clientCapabilities.subagents`` gets an agent's
subagents (Claude's Agent/Task, Codex's spawned agents, Cursor's subagents)
as child sessions of their own. Two forms announce them:

* The stable form of the Subagents RFD (ACP v1 schema, 2026-11-20):
  ``subagent_update`` on the immediate parent patches a child's ``title``,
  ``description``, ``capabilities.cancel`` and ``state`` (``running``,
  ``requires_action``, ``idle`` with a ``stopReason``, ``unknown``).
  An omitted field keeps its value; ``null`` clears it. ``session_message``
  carries the task a session gives a child and a foreground child's answer.
* The earlier draft that Cursor still sends: ``subagent_spawned`` and one
  final ``subagent_state_update`` (``completed``, ``failed``,
  ``cancelled``, ``disconnected``).

Both feed one model, shown as a ``subagent`` chat item. The child's own
updates (its tool calls, its checklist) and requests carry the child's
session id. Everything here is untrusted agent data: an unknown shape
is ignored, an unknown state counts as idle.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from . import normalize as nz

SUBAGENT_UPDATE = "subagent_update"
DRAFT_SPAWNED = "subagent_spawned"
DRAFT_STATE = "subagent_state_update"
SESSION_MESSAGE = "session_message"
LIFECYCLE_UPDATES = frozenset({SUBAGENT_UPDATE, DRAFT_SPAWNED, DRAFT_STATE})
TOOL_UPDATES = frozenset({"tool_call", "tool_call_update"})

STATES = frozenset({"running", "requires_action", "idle", "unknown"})
RUNNING_STATES = frozenset({"running", "requires_action"})
# The draft's final states, as the stable form's idle state and stop reason.
_DRAFT_END = {
    "completed": "end_turn",
    "failed": "error",
    "disconnected": "error",
    "cancelled": "cancelled",
}
# Tool calls a child leaves running when it stops this way end with it.
ENDED_TOOL_STATUS = {"cancelled": "cancelled", "error": "failed"}

ITEM_PREFIX = "sa:"
DEFAULT_TITLE = "Subagent"
TITLE_MAX = 200
DESCRIPTION_MAX = 300
TASK_MAX = nz.TEXT_PART_MAX
RESULT_MAX = 64 * 1024
# Children kept at once: past it the oldest idle one is forgotten, and with
# none idle a new one is dropped (its traffic too).
CHILDREN_MAX = 500


@dataclass
class Subagent:
    child_id: str
    # The child whose session announced this one; None for the root session.
    parent_id: str | None
    title: str | None = None
    description: str | None = None
    task: str | None = None
    # None until the agent reports a state.
    state: str | None = None
    stop_reason: str | None = None
    can_cancel: bool = False
    result: str | None = None
    plan: list[dict[str, Any]] = field(default_factory=list)
    # The text of the child's latest message: its answer when the agent
    # sends none on the parent (Codex, Cursor).
    message_text: str = ""
    message_id: str | None = None
    # The parent's tool call that launched it (Cursor's draft names it).
    launch_tool_id: str | None = None

    @property
    def item_id(self) -> str:
        return ITEM_PREFIX + self.child_id

    @property
    def label(self) -> str:
        return self.title or self.description or DEFAULT_TITLE

    @property
    def running(self) -> bool:
        return self.state in RUNNING_STATES


@dataclass(frozen=True)
class Change:
    """A lifecycle update that changed ``child``."""

    child: Subagent
    new: bool = False
    # The child reported idle with this update: its work ended.
    ended: bool = False
    # The parent's tool call that launched the child (Cursor's draft).
    launch_tool_id: str | None = None


def _clean(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    line = " ".join(value.split())
    if not line:
        return None
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _text_of(content: Any) -> str | None:
    """The text of an ACP ``ContentBlock[]``, or None when it has none."""
    if not isinstance(content, list):
        return None
    texts = [
        block["text"]
        for block in content
        if isinstance(block, Mapping) and block.get("type") == "text" and isinstance(block.get("text"), str)
    ]
    text = "\n\n".join(t for t in texts if t.strip())
    return text or None


class Subagents:
    """The announced children of one conversation, by child session id."""

    def __init__(self) -> None:
        self._children: dict[str, Subagent] = {}
        # Parent tool call id → child id (Cursor names the call that launched it).
        self._launches: dict[str, str] = {}

    def clear(self) -> None:
        self._children.clear()
        self._launches.clear()

    def get(self, child_id: Any) -> Subagent | None:
        return self._children.get(child_id) if isinstance(child_id, str) else None

    def children(self) -> list[Subagent]:
        return list(self._children.values())

    def running(self) -> bool:
        return any(child.running for child in self._children.values())

    def launch_owner(self, tool_id: str) -> Subagent | None:
        return self.get(self._launches.get(tool_id))

    def lifecycle(
        self, session_id: str | None, root_id: str | None, update: Mapping[str, Any], *, replay: bool
    ) -> Change | None:
        """Apply a lifecycle update that came on ``session_id`` (the root
        session or an announced child). ``replay``: a ``session/load``
        replays it, so the child no longer runs and grants no cancel."""
        kind = update.get("sessionUpdate")
        if kind == SUBAGENT_UPDATE:
            return self._stable(session_id, root_id, update, replay=replay)
        if kind == DRAFT_SPAWNED:
            return self._spawned(session_id, root_id, update, replay=replay)
        if kind == DRAFT_STATE:
            return self._draft_state(update)
        return None

    def _announce(self, child_id: Any, session_id: str | None, root_id: str | None) -> tuple[Subagent, bool] | None:
        if not isinstance(child_id, str) or not child_id or child_id == root_id:
            return None
        child = self._children.get(child_id)
        if child is not None:
            return child, False
        if len(self._children) >= CHILDREN_MAX and not self._evict_idle():
            return None
        parent = session_id if session_id in self._children else None
        child = Subagent(child_id, parent)
        self._children[child_id] = child
        return child, True

    def _evict_idle(self) -> bool:
        """Forget the oldest idle child, to make room for a new one."""
        oldest = next((c for c in self._children.values() if c.state == "idle"), None)
        if oldest is None:
            return False
        del self._children[oldest.child_id]
        for tool_id in [t for t, c in self._launches.items() if c == oldest.child_id]:
            del self._launches[tool_id]
        return True

    def _stable(
        self, session_id: str | None, root_id: str | None, update: Mapping[str, Any], *, replay: bool
    ) -> Change | None:
        announced = self._announce(update.get("sessionId"), session_id, root_id)
        if announced is None:
            return None
        child, new = announced
        was_idle = child.state == "idle"
        if "title" in update:
            child.title = _clean(update.get("title"), TITLE_MAX)
        if "description" in update:
            child.description = _clean(update.get("description"), DESCRIPTION_MAX)
        if replay:
            child.can_cancel = False
        elif "capabilities" in update:
            caps = update.get("capabilities")
            child.can_cancel = isinstance(caps, Mapping) and isinstance(caps.get("cancel"), Mapping)
        if "state" in update:
            raw = update.get("state")
            if isinstance(raw, Mapping):
                state = raw.get("state")
                if was_idle and state in RUNNING_STATES:
                    # A reused child works again: its earlier answer is stale.
                    child.result = None
                    child.plan = []
                    child.message_text = ""
                    child.message_id = None
                child.state = state if state in STATES else "idle"
                reason = raw.get("stopReason")
                child.stop_reason = reason if child.state == "idle" and isinstance(reason, str) else None
            else:
                # ``null`` leaves the current activity unconfirmed.
                child.state = "unknown"
                child.stop_reason = None
        ended = not was_idle and child.state == "idle"
        if ended:
            self._finish(child)
        return Change(child, new=new, ended=ended)

    def _spawned(
        self, session_id: str | None, root_id: str | None, update: Mapping[str, Any], *, replay: bool
    ) -> Change | None:
        announced = self._announce(update.get("subagentSessionId"), session_id, root_id)
        if announced is None:
            return None
        child, new = announced
        child.title = _clean(update.get("name"), TITLE_MAX) or child.title
        task = update.get("prompt") if isinstance(update.get("prompt"), str) else update.get("task")
        if isinstance(task, str) and task.strip():
            child.task = nz.cap_text(task.strip(), TASK_MAX)[0]
        caps = update.get("capabilities")
        child.can_cancel = (
            not replay and isinstance(caps, Mapping) and isinstance(caps.get("cancel"), Mapping)
        )
        if new:
            child.state = "running"
        launch = None
        meta = update.get("_meta")
        cursor = meta.get("cursor") if isinstance(meta, Mapping) else None
        tool_id = cursor.get("toolCallId") if isinstance(cursor, Mapping) else None
        if isinstance(tool_id, str) and tool_id:
            announcer = self.get(session_id)
            if announcer is not None:
                # A nested launch is a tool call of the child that made it.
                tool_id = child_tool_id(announcer, tool_id)
            self._launches[tool_id] = child.child_id
            child.launch_tool_id = tool_id
            launch = tool_id
        return Change(child, new=new, launch_tool_id=launch)

    def _draft_state(self, update: Mapping[str, Any]) -> Change | None:
        child = self.get(update.get("subagentSessionId"))
        if child is None:
            return None
        was_idle = child.state == "idle"
        state = update.get("state")
        child.state = "idle"
        child.stop_reason = _DRAFT_END.get(state) if isinstance(state, str) else None
        # The draft's end is final: nothing is left to stop.
        child.can_cancel = False
        ended = not was_idle
        if ended:
            self._finish(child)
        return Change(child, ended=ended)

    @staticmethod
    def _finish(child: Subagent) -> None:
        """A child that ended its work with an answer and sent none: its last
        message is the answer."""
        if child.result is None and child.stop_reason in (None, "end_turn") and child.message_text.strip():
            child.result = nz.cap_text(child.message_text.strip(), RESULT_MAX)[0]
        child.message_text = ""
        child.message_id = None

    def message(self, update: Mapping[str, Any]) -> Subagent | None:
        """A ``session_message`` on any session of this conversation: a
        child's task, or a child's answer to its parent."""
        if "content" not in update:
            # Only metadata (a link between the two views of a message).
            return None
        text = _text_of(update.get("content"))
        sender = self.get(update.get("senderSessionId"))
        recipient = self.get(update.get("recipientSessionId"))
        # An answer goes to the sender's parent: the root session (not a
        # child) or the child that announced it.
        if sender is not None and (
            recipient is None if sender.parent_id is None else recipient is self.get(sender.parent_id)
        ):
            if text is None:
                return None
            result = nz.cap_text(text.strip(), RESULT_MAX)[0]
            if result == sender.result:
                return None
            sender.result = result
            return sender
        if recipient is not None:
            if text is None:
                return None
            task = nz.cap_text(text.strip(), TASK_MAX)[0]
            if task == recipient.task:
                # The other view of the same message.
                return None
            if recipient.task is not None:
                # A new delegation to a reused child: its earlier answer is stale.
                recipient.result = None
                recipient.plan = []
            recipient.task = task
            recipient.message_text = ""
            recipient.message_id = None
            return recipient
        return None

    @staticmethod
    def adopt_launch_result(child: Subagent, launch: Mapping[str, Any] | None) -> bool:
        """An ended child with no answer of its own takes the text of the
        finished tool call that launched it: Cursor puts the answer there."""
        if (
            child.result is not None
            or child.state != "idle"
            or child.stop_reason not in (None, "end_turn")
            or launch is None
            or launch.get("status") != "completed"
        ):
            return False
        text = (_text_of(launch.get("content")) or "").strip()
        if not text:
            return False
        child.result = nz.cap_text(text, RESULT_MAX)[0]
        return True

    @staticmethod
    def note_text(child: Subagent, update: Mapping[str, Any]) -> None:
        """An ``agent_message_chunk`` of the child: kept (not shown) as the
        answer it may end with."""
        part = nz.content_block_part(update.get("content"))
        if part is None or part.get("type") != "text":
            return
        message_id = update.get("messageId") if isinstance(update.get("messageId"), str) else None
        if message_id is not None and message_id != child.message_id:
            child.message_text = ""
        child.message_id = message_id
        if len(child.message_text) < RESULT_MAX:
            child.message_text += part["text"][: RESULT_MAX - len(child.message_text)]

    @staticmethod
    def note_tool(child: Subagent) -> None:
        """Text before a tool call is not the child's answer."""
        child.message_text = ""
        child.message_id = None

    def item(self, child: Subagent) -> dict[str, Any]:
        title = child.label
        return {
            "id": child.item_id,
            "type": "subagent",
            "subagent_id": child.child_id,
            "parent_subagent_id": child.parent_id,
            "title": title,
            "description": child.description if child.description != title else None,
            "task": child.task,
            "state": child.state or "unknown",
            "stop_reason": child.stop_reason,
            "result": child.result,
            "can_cancel": child.can_cancel and child.running,
            "plan": child.plan,
        }


def child_tool_update(child: Subagent, update: Mapping[str, Any]) -> dict[str, Any] | None:
    """A child's tool call update with an id that cannot collide with a tool
    call of the parent or of another child: ``<child id>:<tool call id>``."""
    tool_id = update.get("toolCallId")
    if not isinstance(tool_id, str) or not tool_id:
        return None
    projected = dict(update)
    projected["toolCallId"] = child_tool_id(child, tool_id)
    return projected


def child_tool_id(child: Subagent, tool_id: str) -> str:
    prefix = f"{child.child_id}:"
    return tool_id if tool_id.startswith(prefix) else prefix + tool_id


def label_tool(item: dict[str, Any], child: Subagent) -> None:
    """Mark a tool item as the child's. Its title starts with the child's
    title, so a client that shows no subagent cards still tells it apart."""
    item["subagent_id"] = child.child_id
    prefix = f"{child.label}: "
    title = item.get("title") or ""
    if not title.startswith(prefix):
        item["title"] = _clean(prefix + title, TITLE_MAX) or prefix.strip()
