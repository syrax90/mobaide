"""Tests for the translators of Cursor's own ACP methods
(``mobaide/acp/cursor_ext.py``): ``cursor/ask_question`` as a form and its
answer, ``cursor/create_plan`` as a message plus a permission request and its
answer, the tasks of a plan and its phases, the plan of a replayed
``createPlan`` tool call, and the task list of ``cursor/update_todos``. The
size caps are the chat's own (``mobaide/acp/normalize.py``)."""
from __future__ import annotations

import pytest

from mobaide.acp import cursor_ext as cx
from mobaide.acp import normalize as nz
from mobaide.acp.cursor_ext import (
    TodoList,
    plan_choice,
    plan_outcome,
    plan_permission,
    plan_text,
    plan_todos,
    question_form,
    question_outcome,
    stored_plan,
)


def _question(qid: str = "q1", **over: object) -> dict:
    return {
        "id": qid,
        "prompt": "Which database?",
        "options": [{"id": "pg", "label": "PostgreSQL"}, {"id": "lite", "label": "SQLite"}],
        **over,
    }


def _todo(todo_id: str, content: str = "", status: str = "pending") -> dict:
    return {"id": todo_id, "content": content, "status": status}


def _entry(content: str, status: str = "pending") -> dict:
    return {"content": content, "status": status, "priority": "medium"}


class TestQuestionForm:
    def test_a_single_choice_question_is_a_one_of_field(self):
        form = question_form(
            {"toolCallId": "tool-1", "title": "Setup", "questions": [_question()]}
        )

        assert form == {
            "mode": "form",
            "message": "Setup",
            "toolCallId": "tool-1",
            "requestedSchema": {
                "type": "object",
                "properties": {
                    "q1": {
                        "type": "string",
                        "title": "Which database?",
                        "oneOf": [
                            {"const": "pg", "title": "PostgreSQL"},
                            {"const": "lite", "title": "SQLite"},
                        ],
                    }
                },
            },
        }

    def test_a_multiple_choice_question_is_an_array_field(self):
        form = question_form({"questions": [_question(allowMultiple=True)]})

        assert form["requestedSchema"]["properties"]["q1"] == {
            "type": "array",
            "title": "Which database?",
            "items": {
                "anyOf": [
                    {"const": "pg", "title": "PostgreSQL"},
                    {"const": "lite", "title": "SQLite"},
                ]
            },
        }

    def test_no_title_and_no_tool_call_get_the_default_message(self):
        form = question_form({"title": "  ", "toolCallId": "", "questions": [_question()]})

        assert form["message"] == "Cursor has a question"
        assert "toolCallId" not in form

    def test_a_blank_prompt_or_label_falls_back_to_its_id(self):
        form = question_form(
            {"questions": [_question(prompt=" ", options=[{"id": "pg", "label": None}])]}
        )

        assert form["requestedSchema"]["properties"]["q1"] == {
            "type": "string",
            "title": "q1",
            "oneOf": [{"const": "pg", "title": "pg"}],
        }

    @pytest.mark.parametrize(
        "question",
        [
            "not a question",
            _question(qid=""),
            {"prompt": "No id", "options": [{"id": "a", "label": "A"}]},
            _question(options=[]),
            _question(options="pg"),
            _question(options=[{"label": "No id"}, {"id": 7, "label": "Number"}, "pg"]),
        ],
        ids=["not-object", "empty-id", "no-id", "no-options", "options-not-list", "no-valid-option"],
    )
    def test_a_question_nobody_can_answer_is_left_out(self, question):
        form = question_form({"questions": [question, _question(qid="kept")]})

        assert list(form["requestedSchema"]["properties"]) == ["kept"]

    def test_the_first_of_two_questions_with_one_id_wins(self):
        form = question_form(
            {"questions": [_question(prompt="First"), _question(prompt="Second")]}
        )

        assert form["requestedSchema"]["properties"]["q1"]["title"] == "First"

    @pytest.mark.parametrize("questions", [None, "q", {"id": "q1"}], ids=["none", "text", "object"])
    def test_questions_that_are_not_a_list_give_an_empty_form(self, questions):
        form = question_form({"questions": questions})

        assert form["requestedSchema"] == {"type": "object", "properties": {}}

    def test_questions_and_options_are_capped(self):
        """The same caps as an ACP form: the chat reads this form next."""
        many_options = [{"id": f"o{i}", "label": f"O{i}"} for i in range(nz.FORM_OPTIONS_MAX + 5)]
        questions = [
            _question(qid=f"q{i}", options=many_options) for i in range(nz.FORM_FIELDS_MAX + 5)
        ]

        properties = question_form({"questions": questions})["requestedSchema"]["properties"]

        assert list(properties) == [f"q{i}" for i in range(nz.FORM_FIELDS_MAX)]
        assert len(properties["q0"]["oneOf"]) == nz.FORM_OPTIONS_MAX


class TestQuestionOutcome:
    def test_an_accepted_form_is_answered_per_question(self):
        outcome = question_outcome(
            {"action": "accept", "content": {"q1": "pg", "q2": ["a", "b"]}}
        )

        assert outcome == {
            "outcome": {
                "outcome": "answered",
                "answers": [
                    {"questionId": "q1", "selectedOptionIds": ["pg"]},
                    {"questionId": "q2", "selectedOptionIds": ["a", "b"]},
                ],
            }
        }

    def test_a_cancelled_form_is_cancelled(self):
        assert question_outcome({"action": "cancel"}) == {"outcome": {"outcome": "cancelled"}}

    @pytest.mark.parametrize(
        "answer",
        [
            {"action": "decline"},
            {"action": "accept"},
            {"action": "accept", "content": {}},
            {"action": "accept", "content": {"q1": [], "q2": 3, "q3": ["a", 1], "q4": None}},
            {"action": "decline", "content": {"q1": "pg"}},
            None,
            "accept",
        ],
        ids=[
            "declined", "no-content", "empty-content", "no-usable-value",
            "declined-with-content", "none", "not-object",
        ],
    )
    def test_an_answer_with_no_selection_is_skipped(self, answer):
        assert question_outcome(answer) == {
            "outcome": {"outcome": "skipped", "reason": "The user skipped the questions"}
        }

    def test_values_that_are_not_selections_are_left_out_of_the_answers(self):
        outcome = question_outcome(
            {"action": "accept", "content": {"q1": "pg", "q2": [], "q3": True}}
        )

        assert outcome["outcome"]["answers"] == [
            {"questionId": "q1", "selectedOptionIds": ["pg"]}
        ]


class TestPlanText:
    def test_the_overview_goes_before_the_steps(self):
        text = plan_text({"overview": " Add a cache. ", "plan": "1. Write it\n2. Test it\n"})

        assert text == "Add a cache.\n\n1. Write it\n2. Test it"

    @pytest.mark.parametrize(
        ("params", "text"),
        [
            ({"plan": "1. Write it"}, "1. Write it"),
            ({"overview": "Add a cache.", "plan": "  "}, "Add a cache."),
            ({"overview": None, "plan": 5}, ""),
            ({}, ""),
        ],
        ids=["steps-only", "overview-only", "not-text", "empty"],
    )
    def test_a_missing_part_leaves_no_gap(self, params, text):
        assert plan_text(params) == text


class TestPlanTodos:
    def test_the_tasks_of_the_phases_follow_the_plan_s_own_tasks(self):
        params = {
            "todos": [_todo("a", "Write the cache")],
            "phases": [
                {"name": "Checks", "todos": [_todo("b", "Run the tests"), _todo("c", "Lint")]},
                {"name": "No tasks"},
                "not a phase",
                {"name": "Ship", "todos": [_todo("d", "Open the PR")]},
            ],
        }

        assert plan_todos(params) == {
            "todos": [
                _todo("a", "Write the cache"),
                _todo("b", "Run the tests"),
                _todo("c", "Lint"),
                _todo("d", "Open the PR"),
            ]
        }

    @pytest.mark.parametrize(
        "params",
        [{}, {"todos": "a", "phases": "b"}, {"todos": None, "phases": [{"todos": "c"}]}],
        ids=["empty", "not-lists", "phase-todos-not-a-list"],
    )
    def test_a_plan_without_usable_tasks_has_none(self, params):
        assert plan_todos(params) == {"todos": []}

    def test_a_later_update_merges_into_a_task_of_a_phase(self):
        """The list of a new plan holds the tasks of its phases, so an update
        that names one of them by id changes it in place."""
        todos = TodoList()
        params = {
            "todos": [_todo("a", "Write the cache")],
            "phases": [{"name": "Checks", "todos": [_todo("b", "Run the tests")]}],
        }
        todos.apply(plan_todos(params), merge=False)

        entries = todos.apply({"merge": True, "todos": [_todo("b", "", "completed")]})

        assert entries == [_entry("Write the cache"), _entry("Run the tests", "completed")]


class TestPlanPermission:
    def test_the_request_asks_to_accept_or_reject_the_named_plan(self):
        request = plan_permission(
            {"toolCallId": "tool-9", "name": " Cache layer ", "plan": "1. Write it"}
        )

        assert request == {
            "toolCall": {
                "toolCallId": "tool-9",
                "title": "Plan: Cache layer",
                "kind": "think",
                "status": "pending",
            },
            "options": [
                {"optionId": "accept", "name": "Accept plan", "kind": "allow_once"},
                {"optionId": "reject", "name": "Reject plan", "kind": "reject_once"},
            ],
        }

    def test_a_plan_without_a_name_or_a_tool_call(self):
        request = plan_permission({"name": "", "toolCallId": None})

        assert request["toolCall"] == {"title": "Plan", "kind": "think", "status": "pending"}

    def test_a_plan_that_can_start_the_work_offers_that_first(self):
        """Each option is a one-time answer: none of them is remembered."""
        request = plan_permission({"name": "Cache layer"}, can_start=True)

        assert [(o["optionId"], o["kind"]) for o in request["options"]] == [
            (cx.PLAN_START, "allow_once"),
            (cx.PLAN_ACCEPT, "allow_once"),
            (cx.PLAN_REJECT, "reject_once"),
        ]


class TestPlanChoice:
    @pytest.mark.parametrize("option", [cx.PLAN_START, cx.PLAN_ACCEPT, cx.PLAN_REJECT])
    def test_a_selected_option_is_the_choice(self, option):
        answer = {"outcome": {"outcome": "selected", "optionId": option}}

        assert plan_choice(answer) == option

    @pytest.mark.parametrize(
        "answer",
        [
            {"outcome": {"outcome": "cancelled"}},
            {"outcome": {"outcome": "cancelled", "optionId": cx.PLAN_ACCEPT}},
            {"outcome": {"outcome": "selected"}},
            {"outcome": {"outcome": "selected", "optionId": 7}},
            {"outcome": "selected"},
            {},
            None,
        ],
        ids=[
            "cancelled", "cancelled-with-option", "selected-without-option",
            "option-not-text", "outcome-not-object", "empty", "none",
        ],
    )
    def test_an_answer_without_a_selected_option_is_no_choice(self, answer):
        assert plan_choice(answer) is None


class TestPlanOutcome:
    @pytest.mark.parametrize("choice", [cx.PLAN_ACCEPT, cx.PLAN_START])
    def test_accept_and_start_both_accept(self, choice):
        """Cursor knows no "start": the chat sends the next message itself."""
        assert plan_outcome(choice) == {"outcome": {"outcome": "accepted"}}

    @pytest.mark.parametrize("choice", [cx.PLAN_REJECT, "something-else", ""])
    def test_any_other_choice_rejects(self, choice):
        assert plan_outcome(choice) == {
            "outcome": {"outcome": "rejected", "reason": "The user rejected the plan"}
        }

    def test_no_choice_is_cancelled(self):
        assert plan_outcome(None) == {"outcome": {"outcome": "cancelled"}}


class TestStoredPlan:
    def test_the_input_of_a_create_plan_tool_is_the_plan_s_title_and_text(self):
        raw_input = {
            "_toolName": "createPlan",
            "name": " Cache layer ",
            "overview": "Add a cache.",
            "plan": "1. Write it\n2. Test it\n",
        }

        assert stored_plan(raw_input) == (
            "Plan: Cache layer",
            "Add a cache.\n\n1. Write it\n2. Test it",
        )

    def test_a_stored_plan_without_a_name(self):
        raw_input = {"_toolName": "createPlan", "plan": "1. Write it"}

        assert stored_plan(raw_input) == ("Plan", "1. Write it")

    @pytest.mark.parametrize(
        "raw_input",
        [
            {"_toolName": "editFile", "name": "Cache layer", "plan": "1. Write it"},
            {"name": "Cache layer", "plan": "1. Write it"},
            {"_toolName": "createPlan", "name": "Cache layer"},
            {"_toolName": "createPlan", "name": "Cache layer", "plan": "  ", "overview": None},
            "createPlan",
            None,
        ],
        ids=["another-tool", "no-tool-name", "no-plan", "blank-plan", "text", "none"],
    )
    def test_any_other_tool_input_is_not_a_plan(self, raw_input):
        assert stored_plan(raw_input) is None


class TestTodoList:
    def test_an_update_without_merge_replaces_the_list(self):
        todos = TodoList()
        todos.apply({"todos": [_todo("a", "Old task")]})

        entries = todos.apply({"todos": [_todo("b", "New task", "in_progress")]})

        assert entries == [_entry("New task", "in_progress")]

    def test_a_merge_changes_known_tasks_in_place_and_adds_new_ones_last(self):
        todos = TodoList()
        todos.apply({"todos": [_todo("a", "First"), _todo("b", "Second")]})

        entries = todos.apply(
            {"merge": True, "todos": [_todo("c", "Third"), _todo("a", "First", "completed")]}
        )

        assert entries == [
            _entry("First", "completed"),
            _entry("Second"),
            _entry("Third"),
        ]

    def test_a_merge_without_content_keeps_the_known_content(self):
        todos = TodoList()
        todos.apply({"todos": [_todo("a", "Write the cache")]})

        entries = todos.apply({"merge": True, "todos": [_todo("a", "", "completed")]})

        assert entries == [_entry("Write the cache", "completed")]

    def test_a_cancelled_task_leaves_the_list(self):
        todos = TodoList()
        todos.apply({"todos": [_todo("a", "First"), _todo("b", "Second")]})

        entries = todos.apply(
            {"merge": True, "todos": [_todo("a", "First", "cancelled"), _todo("x", "", "cancelled")]}
        )

        assert entries == [_entry("Second")]

    def test_the_merge_argument_wins_over_the_payload(self):
        """A new plan replaces the list, whatever its ``merge`` says."""
        todos = TodoList()
        todos.apply({"todos": [_todo("a", "Old task")]})

        entries = todos.apply({"merge": True, "todos": [_todo("b", "New task")]}, merge=False)

        assert entries == [_entry("New task")]

    @pytest.mark.parametrize("merge", ["true", 1, None], ids=["text", "number", "none"])
    def test_only_a_true_merge_flag_merges(self, merge):
        todos = TodoList()
        todos.apply({"todos": [_todo("a", "Old task")]})

        entries = todos.apply({"merge": merge, "todos": [_todo("b", "New task")]})

        assert entries == [_entry("New task")]

    @pytest.mark.parametrize(
        "todo",
        ["text", {"content": "No id"}, _todo("", "Empty id"), {"id": 3, "content": "Number id"}, _todo("n", "  ")],
        ids=["not-object", "no-id", "empty-id", "number-id", "new-without-content"],
    )
    def test_a_task_that_cannot_be_shown_is_left_out(self, todo):
        entries = TodoList().apply({"todos": [todo, _todo("ok", "Kept")]})

        assert entries == [_entry("Kept")]

    def test_an_unknown_status_is_pending_and_the_content_is_one_short_line(self):
        entries = TodoList().apply(
            {"todos": [_todo("a", "Write\n  the   cache", "blocked"), _todo("b", "x" * 600)]}
        )

        assert entries[0] == _entry("Write the cache")
        # Cut like an entry of an ACP ``plan`` update, with its mark.
        assert entries[1]["content"] == "x" * 499 + "…"

    def test_new_tasks_stop_at_the_cap_but_known_ones_still_change(self):
        todos = TodoList()
        todos.apply({"todos": [_todo(f"t{i}", f"Task {i}") for i in range(nz.PLAN_MAX + 3)]})

        entries = todos.apply(
            {"merge": True, "todos": [_todo("late", "Late"), _todo("t0", "", "completed")]}
        )

        assert len(entries) == nz.PLAN_MAX
        assert entries[0] == _entry("Task 0", "completed")
        assert _entry("Late") not in entries

    def test_clear_empties_the_list_and_entries_are_copies(self):
        todos = TodoList()
        first = todos.apply({"todos": [_todo("a", "First")]})
        first[0]["content"] = "changed by the caller"

        assert todos.apply({"merge": True, "todos": []}) == [_entry("First")]

        todos.clear()

        assert todos.apply({"merge": True, "todos": []}) == []

    def test_todos_that_are_not_a_list_change_nothing_on_a_merge(self):
        todos = TodoList()
        todos.apply({"todos": [_todo("a", "First")]})

        assert todos.apply({"merge": True, "todos": "a"}) == [_entry("First")]
