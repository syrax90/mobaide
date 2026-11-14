"""Cursor's own ACP methods (``cursor/…``), as the chat's generic requests.

``cursor-agent acp`` asks its client three things outside the ACP standard.
All arrive as requests, also the ones Cursor treats as notices:

* ``cursor/ask_question`` — questions with fixed answers. Shown as a form,
  the same as an ACP elicitation.
* ``cursor/create_plan`` — a plan to accept or reject. Shown as a message
  with the plan, and a permission request that asks for the answer.
* ``cursor/update_todos`` — the task list. Shown as the chat's plan.

This module only translates the payloads; the chat session owns the cards.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import normalize as nz

PREFIX = "cursor/"
ASK_QUESTION = "cursor/ask_question"
CREATE_PLAN = "cursor/create_plan"
UPDATE_TODOS = "cursor/update_todos"
# Notices that need no card: a sub-agent's task, a generated image.
NOTICES = frozenset({"cursor/task", "cursor/generate_image"})

PLAN_ACCEPT = "accept"
PLAN_REJECT = "reject"
PLAN_START = "start"
# What "Accept and start" sends for the user, in Agent mode.
PLAN_START_PROMPT = "Implement the plan."
PLAN_MODE = "plan"
AGENT_MODE = "agent"


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def question_form(params: Mapping[str, Any]) -> dict[str, Any]:
    """``cursor/ask_question`` as the parameters of an ACP form elicitation:
    one field per question, keyed by the question id."""
    properties: dict[str, Any] = {}
    raw_questions = params.get("questions")
    for question in raw_questions if isinstance(raw_questions, list) else []:
        if len(properties) >= nz.FORM_FIELDS_MAX or not isinstance(question, Mapping):
            continue
        qid = question.get("id")
        raw_options = question.get("options")
        if not isinstance(qid, str) or not qid or qid in properties:
            continue
        choices = [
            {"const": option["id"], "title": _text(option.get("label")) or option["id"]}
            for option in (raw_options if isinstance(raw_options, list) else [])[: nz.FORM_OPTIONS_MAX]
            if isinstance(option, Mapping) and isinstance(option.get("id"), str) and option["id"]
        ]
        if not choices:
            continue
        title = _text(question.get("prompt")) or qid
        if question.get("allowMultiple") is True:
            properties[qid] = {"type": "array", "title": title, "items": {"anyOf": choices}}
        else:
            properties[qid] = {"type": "string", "title": title, "oneOf": choices}
    form: dict[str, Any] = {
        "mode": "form",
        "message": _text(params.get("title")) or "Cursor has a question",
        "requestedSchema": {"type": "object", "properties": properties},
    }
    tool_id = params.get("toolCallId")
    if isinstance(tool_id, str) and tool_id:
        form["toolCallId"] = tool_id
    return form


def question_outcome(answer: Any) -> dict[str, Any]:
    """The answer to a form elicitation as the result of
    ``cursor/ask_question``."""
    action = answer.get("action") if isinstance(answer, Mapping) else None
    if action == "cancel":
        return {"outcome": {"outcome": "cancelled"}}
    content = answer.get("content") if action == "accept" else None
    answers = []
    for qid, value in (content if isinstance(content, Mapping) else {}).items():
        selected = [value] if isinstance(value, str) else value
        if isinstance(selected, list) and selected and all(isinstance(v, str) for v in selected):
            answers.append({"questionId": qid, "selectedOptionIds": selected})
    if not answers:
        return {"outcome": {"outcome": "skipped", "reason": "The user skipped the questions"}}
    return {"outcome": {"outcome": "answered", "answers": answers}}


def plan_text(params: Mapping[str, Any]) -> str:
    """The plan of ``cursor/create_plan`` as Markdown: its overview, then
    its steps. The chat cuts a long text where it shows it."""
    parts = (_text(params.get("overview")), _text(params.get("plan")))
    return "\n\n".join(part for part in parts if part)


def plan_todos(params: Mapping[str, Any]) -> dict[str, Any]:
    """The tasks of ``cursor/create_plan`` in the shape of
    ``cursor/update_todos``: its own tasks, then the tasks of its phases."""
    todos: list[Any] = []
    raw = params.get("todos")
    todos.extend(raw if isinstance(raw, list) else [])
    phases = params.get("phases")
    for phase in phases if isinstance(phases, list) else []:
        raw = phase.get("todos") if isinstance(phase, Mapping) else None
        todos.extend(raw if isinstance(raw, list) else [])
    return {"todos": todos}


def plan_title(name: Any) -> str:
    name = _text(name)
    return f"Plan: {name}" if name else "Plan"


def plan_permission(params: Mapping[str, Any], *, can_start: bool = False) -> dict[str, Any]:
    """``cursor/create_plan`` as the parameters of an ACP permission request
    whose options accept or reject the plan. The plan itself is not in it:
    the chat shows it as a message, where it stays after the answer.

    ``can_start`` adds the option that also starts the work: Cursor only
    stores an accepted plan while it is in Plan mode."""
    tool_call: dict[str, Any] = {
        "title": plan_title(params.get("name")),
        "kind": "think",
        "status": "pending",
    }
    tool_id = params.get("toolCallId")
    if isinstance(tool_id, str) and tool_id:
        tool_call["toolCallId"] = tool_id
    options = [
        {"optionId": PLAN_ACCEPT, "name": "Accept plan", "kind": "allow_once"},
        {"optionId": PLAN_REJECT, "name": "Reject plan", "kind": "reject_once"},
    ]
    if can_start:
        options.insert(0, {"optionId": PLAN_START, "name": "Accept and start", "kind": "allow_once"})
    return {"toolCall": tool_call, "options": options}


def plan_choice(answer: Any) -> str | None:
    """The option the user picked on the plan card; None when the card was
    cancelled."""
    outcome = answer.get("outcome") if isinstance(answer, Mapping) else None
    if not isinstance(outcome, Mapping) or outcome.get("outcome") != "selected":
        return None
    selected = outcome.get("optionId")
    return selected if isinstance(selected, str) else None


def plan_outcome(choice: str | None) -> dict[str, Any]:
    """The user's choice on the plan card as the result of
    ``cursor/create_plan``."""
    if choice in (PLAN_ACCEPT, PLAN_START):
        return {"outcome": {"outcome": "accepted"}}
    if choice is None:
        return {"outcome": {"outcome": "cancelled"}}
    return {"outcome": {"outcome": "rejected", "reason": "The user rejected the plan"}}


def stored_plan(raw_input: Any) -> tuple[str, str] | None:
    """``(title, text)`` of the plan in the input of a ``createPlan`` tool
    call. Cursor replays a stored session with that tool call only, and does
    not ask ``cursor/create_plan`` again."""
    if not isinstance(raw_input, Mapping) or raw_input.get("_toolName") != "createPlan":
        return None
    text = plan_text(raw_input)
    return (plan_title(raw_input.get("name")), text) if text else None


class TodoList:
    """Cursor's task list. An update names its tasks by id and either
    replaces the list or merges into it."""

    def __init__(self) -> None:
        self._todos: dict[str, dict[str, Any]] = {}

    def clear(self) -> None:
        self._todos.clear()

    def apply(self, params: Mapping[str, Any], *, merge: bool | None = None) -> list[dict[str, Any]]:
        """Apply the ``todos`` of ``cursor/update_todos`` (or of a new plan)
        and return the list as the chat's plan entries."""
        if merge is None:
            merge = params.get("merge") is True
        if not merge:
            self._todos.clear()
        raw = params.get("todos")
        for todo in raw if isinstance(raw, list) else []:
            if not isinstance(todo, Mapping):
                continue
            todo_id = todo.get("id")
            content = _text(todo.get("content"))
            if not isinstance(todo_id, str) or not todo_id:
                continue
            status = todo.get("status")
            if status == "cancelled":
                self._todos.pop(todo_id, None)
                continue
            known = self._todos.get(todo_id)
            if known is None and len(self._todos) >= nz.PLAN_MAX:
                continue
            # The same form as the entries of an ACP ``plan`` update.
            entries = nz.plan_entries(
                [{"content": content or (known or {}).get("content", ""), "status": status}]
            )
            if entries:
                self._todos[todo_id] = entries[0]
        return [dict(todo) for todo in self._todos.values()]
