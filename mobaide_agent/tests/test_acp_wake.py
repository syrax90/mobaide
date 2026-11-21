"""Tests for the parsers of the signals an agent sends about work after a
prompt's answer (``mobaide/acp/wake.py``): OpenCode's
``opencode/session/child_update`` (a subagent's status and its tool calls)
and Copilot's ``github.com/copilot/sessionEvent``. Both are untrusted data:
an unknown shape is ignored."""
from __future__ import annotations

import pytest

from mobaide.acp.wake import ChildUpdate, child_update, copilot_event


def _child(**over: object) -> dict:
    return {
        "rootSessionId": "ses_root",
        "parentSessionId": "ses_root",
        "childSessionId": "ses_child",
        "depth": 1,
        "title": "Explore",
        **over,
    }


def _tool(tool_id: str = "read-1", **over: object) -> dict:
    return {
        "sessionUpdate": "tool_call",
        "toolCallId": tool_id,
        "title": "Read app.py",
        "kind": "read",
        "status": "in_progress",
        **over,
    }


class TestChildUpdate:
    @pytest.mark.parametrize("status", ["created", "running", "completed", "failed", "interrupted"])
    def test_a_known_status_is_kept(self, status):
        update = child_update(_child(type="status", status=status), "ses_root")

        assert update == ChildUpdate("ses_child", "Explore", status=status)

    def test_an_unknown_status_counts_as_an_end(self):
        update = child_update(_child(type="status", status="paused"), "ses_root")

        assert update == ChildUpdate("ses_child", "Explore", status="failed")

    @pytest.mark.parametrize("status", ["", None, 3])
    def test_a_missing_or_malformed_status_is_ignored(self, status):
        assert child_update(_child(type="status", status=status), "ses_root") is None

    def test_a_child_of_another_session_is_ignored(self):
        params = _child(rootSessionId="ses_other", type="status", status="running")

        assert child_update(params, "ses_root") is None

    def test_before_the_session_id_is_known_any_root_is_taken(self):
        params = _child(rootSessionId="ses_other", type="status", status="running")

        assert child_update(params, None) == ChildUpdate("ses_child", "Explore", status="running")

    @pytest.mark.parametrize(
        "params",
        [
            None,
            [],
            "status",
            _child(childSessionId="", type="status", status="running"),
            _child(childSessionId=None, type="status", status="running"),
            _child(type="message"),
            _child(type="update", update="tool_call"),
            _child(type="update", update=_tool("")),
        ],
        ids=[
            "none", "list", "string", "empty-child", "no-child", "unknown-type",
            "update-not-dict", "tool-without-id",
        ],
    )
    def test_an_unknown_shape_is_ignored(self, params):
        assert child_update(params, "ses_root") is None

    def test_a_tool_call_of_the_child_gets_its_own_card_id_and_the_child_title(self):
        update = child_update(_child(type="update", update=_tool()), "ses_root")

        assert update is not None
        assert update.status is None
        assert update.update == _tool("ses_child:read-1", title="Explore: Read app.py")

    def test_a_tool_call_opencode_already_projected_is_kept_as_it_is(self):
        projected = _tool("ses_child:read-1", title="Explore: Read app.py")

        update = child_update(_child(type="update", update=projected), "ses_root")

        assert update is not None
        assert update.update == projected

    @pytest.mark.parametrize(
        "kind", ["agent_message_chunk", "agent_thought_chunk", "plan", "usage_update"]
    )
    def test_the_childs_messages_and_metadata_are_dropped(self, kind):
        """They would interleave with the parent's answer."""
        params = _child(
            type="update", update={"sessionUpdate": kind, "content": {"type": "text", "text": "hi"}}
        )

        assert child_update(params, "ses_root") is None


class TestCopilotEvent:
    def test_an_event_of_this_session_gives_its_type_and_data(self):
        params = {"sessionId": "s1", "type": "assistant.idle", "data": {"aborted": True}}

        assert copilot_event(params, "s1") == ("assistant.idle", {"aborted": True})

    def test_an_event_of_another_session_is_ignored(self):
        params = {"sessionId": "s2", "type": "session.idle", "data": {}}

        assert copilot_event(params, "s1") is None

    def test_a_subagents_event_is_ignored(self):
        params = {"sessionId": "s1", "agentId": "a1", "type": "assistant.turn_start", "data": {}}

        assert copilot_event(params, "s1") is None

    @pytest.mark.parametrize(
        "params", [None, [], {"sessionId": "s1"}, {"sessionId": "s1", "type": 4}]
    )
    def test_an_unknown_shape_is_ignored(self, params):
        assert copilot_event(params, "s1") is None
