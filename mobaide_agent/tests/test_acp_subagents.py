"""The parent's view of ACP subagent sessions (``mobaide/acp/subagents.py``):
both lifecycle forms (the stable ``subagent_update`` and Cursor's draft
``subagent_spawned`` / ``subagent_state_update``) and ``session_message``
feed one model. All of it is untrusted agent data."""
from __future__ import annotations

from typing import Any

import pytest

from mobaide.acp import subagents as sa
from mobaide.acp.subagents import Subagent, Subagents

ROOT = "root"


def _update(child: str = "c1", **fields: Any) -> dict[str, Any]:
    return {"sessionUpdate": "subagent_update", "sessionId": child, **fields}


def _message(sender: str, recipient: str, text: str) -> dict[str, Any]:
    return {
        "sessionUpdate": "session_message",
        "senderSessionId": sender,
        "recipientSessionId": recipient,
        "content": [{"type": "text", "text": text}],
    }


def _chunk(text: str, message_id: str = "m1") -> dict[str, Any]:
    return {
        "sessionUpdate": "agent_message_chunk",
        "messageId": message_id,
        "content": {"type": "text", "text": text},
    }


def _announced(subs: Subagents, child: str = "c1", on: str = ROOT) -> Subagent:
    update = _update(child, title="Explore", state={"state": "running"})
    change = subs.lifecycle(on, ROOT, update, replay=False)
    assert change is not None
    return change.child


class TestLifecycle:
    def test_an_update_patches_only_the_fields_it_carries(self):
        subs = Subagents()
        subs.lifecycle(
            ROOT,
            ROOT,
            _update(
                title="Explore",
                description="Find it",
                capabilities={"cancel": {}},
                state={"state": "running"},
            ),
            replay=False,
        )

        ended = _update(description=None, state={"state": "idle", "stopReason": "cancelled"})
        change = subs.lifecycle(ROOT, ROOT, ended, replay=False)

        assert change is not None and change.ended and not change.new
        child = change.child
        assert (child.title, child.description) == ("Explore", None)
        assert (child.state, child.stop_reason) == ("idle", "cancelled")
        # The card offers Stop only while the child runs.
        assert subs.item(child)["can_cancel"] is False

    @pytest.mark.parametrize(
        ("state", "expected"),
        [({"state": "paused"}, "idle"), (None, "unknown"), ({"state": "requires_action"}, "requires_action")],
    )
    def test_an_unknown_state_counts_as_idle_and_null_as_unknown(self, state, expected):
        subs = Subagents()
        _announced(subs)

        change = subs.lifecycle(ROOT, ROOT, _update(state=state), replay=False)

        assert change is not None
        assert change.child.state == expected

    def test_a_replayed_child_grants_no_cancel(self):
        subs = Subagents()

        change = subs.lifecycle(
            ROOT, ROOT, _update(capabilities={"cancel": {}}, state={"state": "running"}), replay=True
        )

        assert change is not None and change.child.can_cancel is False

    def test_the_root_session_is_never_a_child_and_a_child_s_child_names_its_parent(self):
        subs = Subagents()
        _announced(subs, "c1")

        assert subs.lifecycle(ROOT, ROOT, _update(ROOT, title="Me"), replay=False) is None
        nested = _announced(subs, "c2", on="c1")
        stray = _announced(subs, "c3", on="never-announced")

        assert (nested.parent_id, stray.parent_id) == ("c1", None)
        assert subs.item(nested)["parent_subagent_id"] == "c1"

    @pytest.mark.parametrize(
        ("state", "stop_reason"),
        [
            ("completed", "end_turn"),
            ("failed", "error"),
            ("disconnected", "error"),
            ("cancelled", "cancelled"),
        ],
    )
    def test_the_draft_s_final_state_ends_the_child(self, state, stop_reason):
        subs = Subagents()
        spawned = subs.lifecycle(
            ROOT,
            ROOT,
            {
                "sessionUpdate": "subagent_spawned",
                "subagentSessionId": "agent-1",
                "name": "shell",
                "task": "Run the tests",
                "capabilities": {"cancel": {}},
                "_meta": {"cursor": {"toolCallId": "task-1"}},
            },
            replay=False,
        )
        assert spawned is not None and spawned.new and spawned.launch_tool_id == "task-1"
        assert subs.launch_owner("task-1") is spawned.child
        assert spawned.child.running and spawned.child.can_cancel

        final = {"sessionUpdate": "subagent_state_update", "subagentSessionId": "agent-1", "state": state}
        change = subs.lifecycle(ROOT, ROOT, final, replay=False)

        assert change is not None and change.ended
        child = change.child
        assert (child.state, child.stop_reason, child.can_cancel) == ("idle", stop_reason, False)

    def test_a_draft_child_launched_by_a_child_keys_its_launch_in_the_launcher_s_tools(self):
        subs = Subagents()
        _announced(subs, "outer")

        nested = subs.lifecycle(
            "outer",
            ROOT,
            {
                "sessionUpdate": "subagent_spawned",
                "subagentSessionId": "inner",
                "name": "shell",
                "_meta": {"cursor": {"toolCallId": "t1"}},
            },
            replay=False,
        )

        assert nested is not None and nested.launch_tool_id == "outer:t1"
        assert subs.launch_owner("outer:t1") is nested.child
        # A root tool with the same id is another call.
        assert subs.launch_owner("t1") is None


    def test_a_full_registry_forgets_the_oldest_idle_child_and_drops_a_new_one_while_all_run(
        self, monkeypatch
    ):
        monkeypatch.setattr(sa, "CHILDREN_MAX", 2)
        subs = Subagents()
        first, second = _announced(subs, "c1"), _announced(subs, "c2")

        assert subs.lifecycle(ROOT, ROOT, _update("c3", state={"state": "running"}), replay=False) is None
        assert subs.get("c3") is None

        idle = {"state": "idle", "stopReason": "end_turn"}
        subs.lifecycle(ROOT, ROOT, _update("c2", state=idle), replay=False)
        subs.lifecycle(ROOT, ROOT, _update("c1", state=idle), replay=False)
        change = subs.lifecycle(ROOT, ROOT, _update("c3", state={"state": "running"}), replay=False)

        assert change is not None and change.new
        assert subs.children() == [second, change.child]
        assert subs.get(first.child_id) is None


class TestMessages:
    def test_a_task_goes_to_the_recipient_and_an_answer_back_to_the_sender(self):
        subs = Subagents()
        child = _announced(subs)

        assert subs.message(_message(ROOT, "c1", "Find the handler.")) is child
        # The child's own view of the same message changes nothing.
        assert subs.message(_message(ROOT, "c1", "Find the handler.")) is None
        assert subs.message(_message("c1", ROOT, "It is in login.py.")) is child

        assert (child.task, child.result) == ("Find the handler.", "It is in login.py.")

    def test_a_nested_child_answers_its_parent_child(self):
        subs = Subagents()
        parent = _announced(subs, "c1")
        nested = _announced(subs, "c2", on="c1")

        assert subs.message(_message("c2", "c1", "Two tests fail.")) is nested

        assert nested.result == "Two tests fail."
        assert parent.task is None

    def test_a_new_task_for_a_reused_child_drops_its_earlier_answer(self):
        subs = Subagents()
        child = _announced(subs)
        subs.message(_message(ROOT, "c1", "Find the handler."))
        subs.message(_message("c1", ROOT, "It is in login.py."))

        subs.message(_message(ROOT, "c1", "Now fix it."))

        assert (child.task, child.result) == ("Now fix it.", None)


class TestAnswerFromTheLastMessage:
    """An agent that sends no answer on the parent (Codex, Cursor): the
    child's last message is its answer when it ends its turn."""

    def _end(self, subs: Subagents, stop_reason: str) -> Subagent:
        ended = _update(state={"state": "idle", "stopReason": stop_reason})
        change = subs.lifecycle(ROOT, ROOT, ended, replay=False)
        assert change is not None and change.ended
        return change.child

    def test_the_last_message_is_the_answer_of_a_finished_child(self):
        subs = Subagents()
        child = _announced(subs)
        Subagents.note_text(child, _chunk("I will read the code."))
        Subagents.note_tool(child)
        Subagents.note_text(child, _chunk("Old ", "m2"))
        Subagents.note_text(child, _chunk("The handler ", "m3"))
        Subagents.note_text(child, _chunk("checks the user.", "m3"))

        assert self._end(subs, "end_turn").result == "The handler checks the user."

    def test_a_stopped_child_has_no_answer(self):
        subs = Subagents()
        child = _announced(subs)
        Subagents.note_text(child, _chunk("Half way"))

        assert self._end(subs, "cancelled").result is None

    def test_a_draft_child_takes_its_launch_s_output_only_once_it_ended_with_no_answer(self):
        subs = Subagents()
        spawned = {
            "sessionUpdate": "subagent_spawned",
            "subagentSessionId": "agent-1",
            "name": "shell",
            "_meta": {"cursor": {"toolCallId": "task-1"}},
        }
        change = subs.lifecycle(ROOT, ROOT, spawned, replay=False)
        assert change is not None
        child = change.child
        launch = {"status": "completed", "content": [{"type": "text", "text": "All 12 tests passed."}]}

        # The launch can finish before the child reports its end.
        assert Subagents.adopt_launch_result(child, launch) is False
        assert child.result is None

        final = {"sessionUpdate": "subagent_state_update", "subagentSessionId": "agent-1", "state": "completed"}
        subs.lifecycle(ROOT, ROOT, final, replay=False)

        assert Subagents.adopt_launch_result(child, {**launch, "status": "in_progress"}) is False
        assert Subagents.adopt_launch_result(child, launch) is True
        assert child.result == "All 12 tests passed."
        assert Subagents.adopt_launch_result(child, {**launch, "content": [{"type": "text", "text": "Other"}]}) is False
        assert child.result == "All 12 tests passed."

    def test_an_answer_the_agent_sent_wins(self):
        subs = Subagents()
        child = _announced(subs)
        subs.message(_message("c1", ROOT, "The real answer."))
        Subagents.note_text(child, _chunk("Some last words."))

        assert self._end(subs, "end_turn").result == "The real answer."

    def test_a_reused_child_that_runs_again_answers_with_its_new_last_message(self):
        subs = Subagents()
        child = _announced(subs)
        Subagents.note_text(child, _chunk("First answer."))
        assert self._end(subs, "end_turn").result == "First answer."

        _announced(subs)
        Subagents.note_text(child, _chunk("Second answer.", "m2"))

        assert self._end(subs, "end_turn").result == "Second answer."
