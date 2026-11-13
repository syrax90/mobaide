"""A scripted ACP agent for tests and for UI checks on a local stack.

Run with ``python -m mobaide.acp.fake_agent [--flavor codex|antigravity|opencode|copilot|pi]``.
``MOBAIDE_ENV=test`` makes the chat sessions use it instead of a real agent,
so every chat UI state can be reached without an account or network. The
flavor picks the config options and names of the agent it stands in for. The
prompt text picks the scenario:

* ``tools`` — read, edit (with a diff) and execute tool calls (the Codex
  flavor streams the command output as terminal ``_meta``; the Antigravity
  flavor reports it as one "Command exited with code N" text; the Copilot
  flavor sends the output so far, then a last ``<shellId: …>`` line; the Pi
  flavor sends the output so far as terminal ``_meta``);
* ``permission`` — a command that waits for approval;
* ``question`` — a form with single choice, multiple choice and text;
* ``plan`` — a plan that progresses step by step;
* ``markdown`` — a long Markdown answer (headings, lists, code, table);
* ``slow`` — a slow stream, to try Stop, the message queue and Send now
  (``_session/steering``);
* ``external`` — after the answer, a turn that another app on the PC
  started (thread status ``_meta`` as Codex sends it);
* ``fail`` — the turn fails with an error;
* ``crash`` — the process exits;
* anything else — a short echo with a thought.

While the file named by ``MOBAIDE_FAKE_ACP_SIGNED_OUT`` exists the agent is
signed out: the Claude flavor pushes ``_auth/status_update`` and fails each
prompt, the others fail ``session/new``. ``fake_agent.py login [--flavor F]
[--device]`` is its login command: it prints a sign-in page and reads a code
(any code but ``bad``), or with ``--device`` shows a one-time code and
finishes alone; on success it removes that file.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import Any

MODES = [
    {"value": "default", "name": "Ask first", "description": "Ask before edits and commands"},
    {"value": "acceptEdits", "name": "Accept edits", "description": "Edit files without asking"},
    {"value": "plan", "name": "Plan", "description": "Plan before changing anything"},
    {"value": "bypassPermissions", "name": "Bypass permissions", "description": "Never ask"},
]
MODELS = [
    {"value": "fast", "name": "Fast model", "description": "Quick answers"},
    {"value": "smart", "name": "Smart model", "description": "Best quality"},
]
EFFORTS = [{"value": v, "name": v.capitalize()} for v in ("low", "medium", "high", "max")]

CODEX_MODES = [
    {"value": "read-only", "name": "Read-only", "description": "Requires approval to edit files and access the internet."},
    {"value": "workspace-write", "name": "Workspace access", "description": "Edit workspace files; ask before writing outside the workspace or accessing the network."},
    {"value": "agent", "name": "Auto review", "description": "Only ask for actions detected as potentially unsafe"},
    {"value": "agent-full-access", "name": "Full access", "description": "Unrestricted access to the internet and any file on your computer"},
]
CODEX_COLLABORATION = [{"value": "default", "name": "Default"}, {"value": "plan", "name": "Plan"}]
CODEX_MODELS = [
    {"value": "gpt-5.6-terra", "name": "5.6 Terra"},
    {"value": "gpt-6.1-sol", "name": "6.1 Sol"},
]
CODEX_EFFORTS = [{"value": v, "name": v.capitalize()} for v in ("low", "medium", "high", "xhigh")]

AGY_MODES = [
    {"value": "default", "name": "Default", "description": "Ask before edits and commands, per your permission settings."},
    {"value": "accept-edits", "name": "Accept edits", "description": "Approve file edits in the workspace without asking."},
    {"value": "plan", "name": "Plan", "description": "Plan carefully before executing a task."},
]
AGY_MODELS = [
    {"value": "gemini-3.8-flash", "name": "Gemini 3.8 Flash"},
    {"value": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6 (Thinking)"},
]
OPENCODE_MODES = [
    {"value": "build", "name": "Build", "description": "The default agent. Executes tools based on configured permissions."},
    {"value": "plan", "name": "Plan", "description": "Read-only agent for exploring the codebase and planning work before implementation."},
]
OPENCODE_MODELS = [
    {"value": "opencode/big-pickle", "name": "opencode/Big Pickle"},
    {"value": "anthropic/claude-sonnet-5-5", "name": "anthropic/Claude Sonnet 5.5"},
]
OPENCODE_EFFORTS = [{"value": v, "name": v.capitalize()} for v in ("default", "low", "high")]
COPILOT_MODE_PREFIX = "https://agentclientprotocol.com/protocol/session-modes#"
COPILOT_MODES = [
    {"value": COPILOT_MODE_PREFIX + "agent", "name": "Agent", "description": "Default agent mode for conversational interactions"},
    {"value": COPILOT_MODE_PREFIX + "plan", "name": "Plan", "description": "Plan mode for creating and executing multi-step plans"},
    {"value": COPILOT_MODE_PREFIX + "autopilot", "name": "Autopilot", "description": "Autonomous mode that enables allow-all and runs until task completion without user interaction (experimental)"},
]
COPILOT_ALLOW_ALL = [
    {"value": "on", "name": "On", "description": "Automatically approve all tool, path, and URL requests"},
    {"value": "off", "name": "Off", "description": "Require approval for tool, path, and URL requests"},
]
PI_MODELS = [
    {"value": "openai-codex/gpt-6.1-sol", "name": "GPT-6.1 Sol"},
    {"value": "openai-codex/gpt-5.6-luna", "name": "GPT-5.6 Luna"},
]
PI_LEVELS = [
    {"value": v, "name": n}
    for v, n in (("off", "Off"), ("low", "Low"), ("medium", "Medium"), ("high", "High"), ("xhigh", "Extra high"))
]
SIGNED_OUT_ENV = "MOBAIDE_FAKE_ACP_SIGNED_OUT"
AUTH_REQUIRED = -32000


def signed_out() -> bool:
    marker = os.environ.get(SIGNED_OUT_ENV)
    return bool(marker) and os.path.exists(marker)


AGY_LEVELS = [{"value": v, "name": v.capitalize()} for v in ("low", "medium", "high")]
# ``models.availableModels`` of ``session/new``: one entry per thinking level.
AGY_AVAILABLE_MODELS = [
    *({"modelId": f"gemini-3.8-flash-{lvl}", "name": f"Gemini 3.8 Flash ({lvl.capitalize()})"} for lvl in ("high", "medium", "low")),
    {"modelId": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6 (Thinking)"},
]

MARKDOWN = """## Result

Here is a **summary** of the change, with `inline code` and a [link](https://example.com).

1. Parse the input
2. Validate each field
   - reject empty names
   - trim whitespace
3. Save the record

```python
def save(record: dict) -> None:
    if not record.get("name"):
        raise ValueError("name is required")
    db.insert(record)
```

| File | Lines |
|------|------:|
| `app.py` | 120 |
| `models.py` | 48 |

> Note: run the tests before you merge.

---

That's all."""


class FakeAgent:
    def __init__(self, loop: asyncio.AbstractEventLoop, flavor: str = "claude") -> None:
        self.loop = loop
        self.codex = flavor == "codex"
        self.agy = flavor == "antigravity"
        self.opencode = flavor == "opencode"
        self.copilot = flavor == "copilot"
        self.pi = flavor == "pi"
        self.steers: dict[str, list[str]] = {}
        self.write_lock = threading.Lock()
        self.next_id = 1000
        self.pending: dict[int, asyncio.Future[Any]] = {}
        self.sessions: dict[str, dict[str, Any]] = {}
        self.cancelled: set[str] = set()
        self.turns: dict[str, asyncio.Task[Any]] = {}
        self.tool_seq = 0

    def send(self, message: dict[str, Any]) -> None:
        data = (json.dumps(message) + "\n").encode("utf-8")
        with self.write_lock:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()

    def update(self, sid: str, update: dict[str, Any]) -> None:
        self.send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": update}})

    async def call(self, method: str, params: dict[str, Any]) -> Any:
        self.next_id += 1
        req_id = self.next_id
        fut: asyncio.Future[Any] = self.loop.create_future()
        self.pending[req_id] = fut
        self.send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        return await fut

    def config(self, sid: str) -> list[dict[str, Any]]:
        s = self.sessions[sid]
        if self.agy:
            options = [
                {"id": "model", "name": "Model", "category": "model", "type": "select", "currentValue": s["model"], "options": AGY_MODELS},
                {"id": "thought_level", "name": "Thinking Level", "category": "thought_level", "type": "select", "currentValue": s["thought_level"], "options": AGY_LEVELS},
                {"id": "mode", "name": "Mode", "category": "mode", "type": "select", "currentValue": s["mode"], "options": AGY_MODES},
            ]
            # Like agy: a model without thinking levels drops the option.
            if s["model"] != "gemini-3.8-flash":
                options.pop(1)
            return options
        if self.opencode:
            options = [
                {"id": "model", "name": "Model", "category": "model", "type": "select", "currentValue": s["model"], "options": OPENCODE_MODELS},
                {"id": "effort", "name": "Effort", "category": "thought_level", "type": "select", "currentValue": s["effort"], "options": OPENCODE_EFFORTS},
                {"id": "mode", "name": "Session Mode", "category": "mode", "type": "select", "currentValue": s["mode"], "options": OPENCODE_MODES},
            ]
            # Like OpenCode: only a model with variants has the effort option.
            if s["model"] == "opencode/big-pickle":
                options.pop(1)
            return options
        if self.pi:
            # Like Pi: a model and a thinking level, and no mode to pick.
            return [
                {"id": "model", "name": "Model", "category": "model", "type": "select", "currentValue": s["model"], "options": PI_MODELS},
                {"id": "thought_level", "name": "Thinking", "category": "thought_level", "type": "select", "currentValue": s["thought_level"], "options": PI_LEVELS},
            ]
        if self.copilot:
            # Like Copilot on a plan with one model: no model option.
            return [
                {"id": "mode", "name": "Mode", "category": "mode", "type": "select", "currentValue": s["mode"], "options": COPILOT_MODES},
                {"id": "allow_all", "name": "Allow All", "category": "permissions", "type": "select", "currentValue": s["allow_all"], "options": COPILOT_ALLOW_ALL},
            ]
        if self.codex:
            return [
                {"id": "mode", "name": "Mode", "category": "mode", "type": "select", "currentValue": s["mode"], "options": CODEX_MODES},
                {"id": "collaboration_mode", "name": "Collaboration mode", "category": "collaboration_mode", "type": "select", "currentValue": s["collaboration_mode"], "options": CODEX_COLLABORATION},
                {"id": "model", "name": "Model", "category": "model", "type": "select", "currentValue": s["model"], "options": CODEX_MODELS},
                {"id": "reasoning_effort", "name": "Reasoning effort", "category": "thought_level", "type": "select", "currentValue": s["reasoning_effort"], "options": CODEX_EFFORTS},
                {"id": "fast-mode", "name": "Fast mode", "category": "model_config", "type": "boolean", "currentValue": s["fast-mode"]},
            ]
        return [
            {"id": "mode", "name": "Mode", "category": "mode", "type": "select", "currentValue": s["mode"], "options": MODES},
            {"id": "model", "name": "Model", "category": "model", "type": "select", "currentValue": s["model"], "options": MODELS},
            {"id": "effort", "name": "Effort", "category": "thought_level", "type": "select", "currentValue": s["effort"], "options": EFFORTS},
            {"id": "fast", "name": "Fast mode", "category": "model_config", "type": "boolean", "currentValue": s["fast"]},
        ]

    def new_session(self, cwd: str, sid: str | None = None, title: str | None = None) -> str:
        sid = sid or str(uuid.uuid4())
        self.sessions[sid] = {
            "cwd": cwd,
            "mode": "default",
            "model": "smart",
            "effort": "high",
            "fast": False,
            "title": title,
            "used": 1200,
        }
        if self.codex:
            self.sessions[sid].update(
                {"mode": "agent", "collaboration_mode": "default", "model": "gpt-5.6-terra",
                 "reasoning_effort": "medium", "fast-mode": False}
            )
        if self.agy:
            self.sessions[sid].update({"model": "gemini-3.8-flash", "thought_level": "high"})
        if self.opencode:
            self.sessions[sid].update(
                {"mode": "build", "model": "opencode/big-pickle", "effort": "default"}
            )
        if self.copilot:
            self.sessions[sid].update({"mode": COPILOT_MODE_PREFIX + "agent", "allow_all": "off"})
        if self.pi:
            self.sessions[sid].update({"model": "openai-codex/gpt-6.1-sol", "thought_level": "medium"})
        return sid

    def models_block(self, sid: str) -> dict[str, Any]:
        """agy's ``models`` field of ``session/new`` and ``session/load``."""
        if not self.agy:
            return {}
        s = self.sessions[sid]
        current = s["model"] if s["model"] != "gemini-3.8-flash" else f"gemini-3.8-flash-{s['thought_level']}"
        return {"models": {"availableModels": AGY_AVAILABLE_MODELS, "currentModelId": current}}

    def config_ids(self) -> tuple[str, ...]:
        if self.agy:
            return ("model", "thought_level", "mode")
        if self.codex:
            return ("mode", "collaboration_mode", "model", "reasoning_effort", "fast-mode")
        if self.opencode:
            return ("model", "effort", "mode")
        if self.copilot:
            return ("mode", "allow_all")
        if self.pi:
            return ("model", "thought_level")
        return ("mode", "model", "effort", "fast")

    def commands(self, sid: str) -> None:
        if self.agy:
            available = [
                {"name": "goal", "description": "Run until the specified goal is completely finished."},
                {"name": "compact", "description": "Compact the conversation now (summarize history to free up the context window)."},
                {"name": "plan", "description": "Plan carefully before executing a task."},
                {"name": "browser", "description": "Invoke a browser agent for web tasks."},
                {"name": "learn", "description": "Reflect on recent successes or corrections to capture reusable skills or rules."},
            ]
        elif self.opencode:
            available = [
                {"name": "compact", "description": "Compact the session"},
                {"name": "init", "description": "Create or update AGENTS.md"},
                {"name": "review", "description": "Review changes [commit|branch|pr], defaults to uncommitted"},
            ]
        elif self.pi:
            available = [
                {"name": "compact", "description": "Summarize the conversation so far to free up context", "input": {"hint": "what the summary must keep (optional)"}},
                {"name": "mcp", "description": "Manage MCP servers: sign in, reconnect, enable or disable, and change exposure"},
            ]
        elif self.copilot:
            available = [
                {"name": "compact", "description": "Summarize conversation history to reduce context window usage. Optionally provide focus instructions.", "input": {"hint": "focus instructions"}},
                {"name": "model", "description": "Select the AI model for this session (use 'auto' to let Copilot pick automatically).", "input": {"hint": "model"}},
                {"name": "review", "description": "Run code review agent to analyze changes", "input": {"hint": "additional instructions"}},
                {"name": "usage", "description": "Display session usage metrics and statistics"},
            ]
        elif self.codex:
            available = [
                {"name": "plan", "description": "Turn plan mode on.", "input": None},
                {"name": "review", "description": "Review uncommitted changes, or review with custom instructions.", "input": {"hint": "optional review instructions"}},
                {"name": "status", "description": "Display session configuration and token usage.", "input": None},
                {"name": "compact", "description": "Summarize conversation to avoid hitting the context limit.", "input": None},
            ]
        else:
            available = [
                {"name": "review", "description": "Review the current changes", "input": {"hint": "what to focus on"}},
                {"name": "test", "description": "Run the project tests"},
                {"name": "compact", "description": "Compact the conversation context"},
            ]
        self.update(sid, {"sessionUpdate": "available_commands_update", "availableCommands": available})

    async def handle(self, msg: dict[str, Any]) -> None:
        method = msg.get("method")
        msg_id = msg.get("id")
        if method is None:
            fut = self.pending.pop(msg_id, None) if isinstance(msg_id, int) else None
            if fut is not None and not fut.done():
                fut.set_result(msg.get("result") if "result" in msg else {"error": msg.get("error")})
            return
        params = msg.get("params") or {}
        try:
            result = await self.dispatch(method, params)
        except _RpcError as exc:
            if msg_id is not None:
                self.send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": exc.code, "message": exc.message}})
            return
        if msg_id is not None:
            self.send({"jsonrpc": "2.0", "id": msg_id, "result": result})

    async def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "initialize" and signed_out() and not (
            self.codex or self.agy or self.copilot or self.pi
        ):
            # claude-agent-acp reports a missing login right after initialize.
            self.loop.call_later(0.05, lambda: self.send({
                "jsonrpc": "2.0", "method": "_auth/status_update",
                "params": {"authStatus": {"kind": "none", "label": "Not logged in"}},
            }))
        if method in ("session/new", "session/load") and signed_out() and (
            self.codex or self.agy or self.copilot or self.pi
        ):
            raise _RpcError(AUTH_REQUIRED, "Authentication required")
        if method == "initialize":
            if self.agy:
                return {
                    "protocolVersion": 1,
                    "agentCapabilities": {
                        "loadSession": True,
                        "promptCapabilities": {"image": True, "audio": False, "embeddedContext": True},
                        "sessionCapabilities": {"resume": {}, "close": {}, "load": {}, "list": {}, "additionalDirectories": {}},
                    },
                    "agentInfo": {"name": "Antigravity ACP Server", "version": "1.5.3"},
                    "authMethods": [],
                }
            if self.opencode:
                return {
                    "protocolVersion": 1,
                    "agentCapabilities": {
                        "loadSession": True,
                        "promptCapabilities": {"image": True, "embeddedContext": True},
                        "sessionCapabilities": {"additionalDirectories": {}, "close": {}, "delete": {}, "fork": {}, "list": {}, "resume": {}},
                    },
                    "agentInfo": {"name": "OpenCode", "version": "2.2.5"},
                    "authMethods": [],
                }
            if self.copilot:
                return {
                    "protocolVersion": 1,
                    "agentCapabilities": {
                        "auth": {"logout": {}},
                        "loadSession": True,
                        "promptCapabilities": {"image": True, "audio": False, "embeddedContext": True},
                        "sessionCapabilities": {"close": {}, "list": {}},
                    },
                    "agentInfo": {"name": "Copilot", "title": "Copilot", "version": "1.0.109"},
                    "authMethods": [],
                }
            if self.pi:
                return {
                    "protocolVersion": 1,
                    "agentCapabilities": {
                        "loadSession": True,
                        "promptCapabilities": {"image": True},
                        "sessionCapabilities": {"list": {}, "close": {}},
                    },
                    "agentInfo": {"name": "pi", "title": "Pi", "version": "1.5.2"},
                    "authMethods": [],
                    "_meta": {"steering": {"supported": True}},
                }
            info = (
                {"name": "fake-codex-acp", "title": "Codex", "version": "1.0.0"}
                if self.codex
                else {"name": "fake-acp-agent", "title": "Fake Agent", "version": "1.0.0"}
            )
            return {
                "protocolVersion": 1,
                "agentCapabilities": {
                    "loadSession": True,
                    "promptCapabilities": {"image": True, "embeddedContext": True},
                    "sessionCapabilities": {"list": {}, "close": {}, "resume": {}, "systemPrompt": {}},
                },
                "agentInfo": info,
                "authMethods": [],
                "_meta": {"steering": {"supported": True}},
            }
        if method == "session/new":
            # agy works in its own working directory, whatever ``cwd`` says.
            sid = self.new_session(os.getcwd() if self.agy else params.get("cwd") or os.getcwd())
            self.loop.call_later(0.05, self.commands, sid)
            return {"sessionId": sid, "configOptions": self.config(sid), **self.models_block(sid)}
        if method == "session/list" and self.agy:
            # Like agy: every folder's conversations, not newest first, and
            # the ``cwd`` filter ignored.
            here = os.getcwd()
            return {
                "sessions": [
                    {"sessionId": "external-3333", "cwd": "/elsewhere/other-project", "title": "Write release notes", "updatedAt": "2026-11-08T09:00:00Z"},
                    {"sessionId": "external-2222", "cwd": params.get("cwd") or here, "title": "Fix flaky upload test", "updatedAt": "2026-11-08T10:00:00Z"},
                    {"sessionId": "external-1111", "cwd": params.get("cwd") or here, "title": "Refactor the login flow", "updatedAt": "2026-11-09T08:30:00Z"},
                ]
            }
        if method == "session/list":
            cwd = params.get("cwd") or os.getcwd()
            now = datetime.now(UTC).isoformat()
            titles = (
                ("Add retries to the sync job", "Explain the cache layer")
                if self.codex
                else ("Refactor the login flow", "Fix flaky upload test")
            )
            return {
                "sessions": [
                    {"sessionId": "external-1111", "cwd": cwd, "title": titles[0], "updatedAt": now},
                    {"sessionId": "external-2222", "cwd": cwd, "title": titles[1], "updatedAt": now},
                ]
            }
        if method == "session/load":
            sid = params["sessionId"]
            self.new_session(params.get("cwd") or os.getcwd(), sid=sid, title="Earlier session")
            await self.replay(sid)
            self.commands(sid)
            return {"configOptions": self.config(sid), **self.models_block(sid)}
        if method == "session/resume":
            sid = params["sessionId"]
            self.new_session(params.get("cwd") or os.getcwd(), sid=sid)
            return {"configOptions": self.config(sid)}
        if method == "session/close":
            self.sessions.pop(str(params.get("sessionId")), None)
            return {}
        if method == "session/set_config_option":
            sid = params["sessionId"]
            s = self.sessions.get(sid)
            if s is None:
                raise _RpcError(-32002, "Unknown session")
            config_id = params.get("configId")
            if config_id not in self.config_ids():
                raise _RpcError(-32602, "Unknown config option")
            s[config_id] = params.get("value")
            return {"configOptions": self.config(sid)}
        if method == "session/set_mode":
            s = self.sessions[params["sessionId"]]
            s["mode"] = params.get("modeId")
            return {}
        if method == "session/prompt":
            sid = params["sessionId"]
            if sid not in self.sessions:
                raise _RpcError(-32002, "Unknown session")
            self.cancelled.discard(sid)
            task = asyncio.ensure_future(self.turn(sid, params.get("prompt") or []))
            self.turns[sid] = task
            return await task
        if method == "session/cancel":
            self.cancelled.add(str(params.get("sessionId")))
            return None
        if method == "_session/steering" and not (self.agy or self.copilot):
            sid = params.get("sessionId")
            turn = self.turns.get(sid) if isinstance(sid, str) else None
            if turn is None or turn.done():
                return {"outcome": "promptRequired", "reason": "noRunningTurn"}
            text = " ".join(b.get("text", "") for b in params.get("prompt") or [] if b.get("type") == "text")
            self.steers.setdefault(sid, []).append(text.strip())
            return {"outcome": "injected"}
        raise _RpcError(-32601, f"Method not found: {method}")

    async def replay(self, sid: str) -> None:
        self.update(sid, {"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": "Can you check the login handler?"}})
        self.update(sid, {"sessionUpdate": "agent_message_chunk", "messageId": "r1", "content": {"type": "text", "text": "Sure — I read the handler and fixed the null check."}})
        self.update(sid, {"sessionUpdate": "tool_call", "toolCallId": "replay-edit", "title": "Edit login.py", "kind": "edit", "status": "completed",
                          "locations": [{"path": "/project/login.py", "line": 12}],
                          "content": [{"type": "diff", "path": "/project/login.py", "oldText": "if user:\n    login(user)\n", "newText": "if user is not None:\n    login(user)\n"}]})
        if not self.copilot:
            # Copilot replays a stored session without its title.
            self.update(sid, {"sessionUpdate": "session_info_update", "title": "Earlier session"})

    def stopped(self, sid: str) -> bool:
        return sid in self.cancelled

    async def stream(self, sid: str, text: str, kind: str = "agent_message_chunk", delay: float = 0.02, message_id: str | None = None) -> bool:
        mid = message_id or str(uuid.uuid4())
        words = text.split(" ")
        for i, word in enumerate(words):
            if self.stopped(sid):
                return False
            steers = self.steers.pop(sid, None)
            if steers:
                mid = str(uuid.uuid4())
                for note in steers:
                    self.update(sid, {"sessionUpdate": kind, "messageId": mid,
                                      "content": {"type": "text", "text": f"(Noted mid-turn: {note}) "}})
            chunk = word if i == 0 else " " + word
            self.update(sid, {"sessionUpdate": kind, "messageId": mid, "content": {"type": "text", "text": chunk}})
            await asyncio.sleep(delay)
        return True

    async def turn(self, sid: str, prompt: list[dict[str, Any]]) -> dict[str, Any]:
        text = " ".join(b.get("text", "") for b in prompt if b.get("type") == "text").strip()
        images = sum(1 for b in prompt if b.get("type") == "image")
        lower = text.lower()
        s = self.sessions[sid]
        if not s.get("title"):
            s["title"] = (text[:40] or "Image question").strip()
            self.update(sid, {"sessionUpdate": "session_info_update", "title": s["title"]})
        if "crash" in lower:
            await asyncio.sleep(0.2)
            sys.stderr.write("fake agent: crashing on purpose\n")
            sys.stderr.flush()
            os._exit(3)
        if "fail" in lower:
            await asyncio.sleep(0.2)
            raise _RpcError(-32603, "The model request failed (fake error)")
        if signed_out():
            await asyncio.sleep(0.2)
            raise _RpcError(AUTH_REQUIRED, "Authentication required")
        ok = True
        if "tool" in lower and self.agy:
            ok = await self.agy_tools(sid)
        elif "tool" in lower:
            ok = await self.tools(sid)
        elif "permission" in lower:
            ok = await self.permission(sid)
        elif "question" in lower:
            ok = await self.question(sid)
        elif "plan" in lower:
            ok = await self.plan(sid)
        elif "markdown" in lower:
            ok = await self.stream(sid, MARKDOWN, delay=0.01)
        elif "slow" in lower:
            ok = await self.stream(sid, "Working slowly " + "and steadily " * 40 + "— done.", delay=0.15)
        elif "external" in lower:
            ok = await self.stream(sid, "Sure. Watch for a message from your PC next.")
            self.loop.call_later(1.0, lambda: asyncio.ensure_future(self.external_turn(sid)))
        else:
            ok = await self.stream(sid, "Let me think about that.", kind="agent_thought_chunk")
            if ok:
                reply = f"You said: {text}" if text else "You sent no text."
                if images:
                    reply += f" (with {images} image{'s' if images > 1 else ''})"
                ok = await self.stream(sid, reply)
        s["used"] += 2500
        if self.agy:
            # agy reports token counts in the prompt result only.
            return {"stopReason": "end_turn" if ok else "cancelled",
                    "usage": {"inputTokens": s["used"], "outputTokens": 120, "totalTokens": s["used"] + 120}}
        if self.pi:
            # The Pi bridge reports the context use, but no cost.
            self.update(sid, {"sessionUpdate": "usage_update", "used": s["used"], "size": 500000})
            return {"stopReason": "end_turn" if ok else "cancelled"}
        if self.copilot:
            # Copilot reports the context use, but no cost.
            self.update(sid, {"sessionUpdate": "usage_update", "used": s["used"], "size": 128000})
            return {"stopReason": "end_turn" if ok else "cancelled",
                    "usage": {"inputTokens": s["used"], "outputTokens": 120, "totalTokens": s["used"] + 120}}
        self.update(sid, {"sessionUpdate": "usage_update", "used": s["used"], "size": 200000, "cost": {"amount": round(s["used"] / 1e6, 4), "currency": "USD"}})
        return {"stopReason": "end_turn" if ok else "cancelled"}

    def thread_status(self, sid: str, kind: str) -> None:
        status: dict[str, Any] = {"type": kind}
        if kind == "active":
            status["activeFlags"] = []
        self.update(sid, {"sessionUpdate": "session_info_update", "_meta": {"codex": {"threadStatus": status}}})

    async def external_turn(self, sid: str) -> None:
        self.thread_status(sid, "active")
        await asyncio.sleep(0.3)
        await self.stream(sid, "This answer is for a message typed into Codex on the PC.", delay=0.05)
        self.thread_status(sid, "idle")

    def tool_id(self, name: str) -> str:
        self.tool_seq += 1
        return f"{name}-{self.tool_seq}"

    async def tools(self, sid: str) -> bool:
        read, edit, run, search = (self.tool_id(n) for n in ("read", "edit", "exec", "search"))
        cwd = self.sessions[sid]["cwd"]
        path = os.path.join(cwd, "app.py")
        await self.stream(sid, "I'll look at the code first.")
        self.update(sid, {"sessionUpdate": "tool_call", "toolCallId": read, "title": "Read app.py", "kind": "read", "status": "in_progress",
                          "rawInput": {"file_path": path}, "locations": [{"path": path}]})
        await asyncio.sleep(0.4)
        self.update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": read, "status": "completed",
                          "content": [{"type": "content", "content": {"type": "text", "text": "```python\ndef main():\n    print('hi')\n```"}}]})
        self.update(sid, {"sessionUpdate": "tool_call", "toolCallId": edit, "title": "Edit app.py", "kind": "edit", "status": "pending",
                          "rawInput": {"file_path": path}, "locations": [{"path": path, "line": 2}]})
        await asyncio.sleep(0.3)
        self.update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": edit, "status": "completed",
                          "content": [{"type": "diff", "path": path, "oldText": "def main():\n    print('hi')\n", "newText": "def main() -> None:\n    print('hello, world')\n    return None\n"}]})
        if self.copilot:
            # Copilot keeps the tool ``pending`` while the command runs and
            # sends the whole output so far in each update.
            self.update(sid, {"sessionUpdate": "tool_call", "toolCallId": run, "title": "Run the tests", "kind": "execute", "status": "pending",
                              "rawInput": {"command": "pytest -q", "description": "Run the tests", "mode": "sync", "initial_wait": 30}})
            output = ""
            for chunk in ("....\n", "4 passed in 0.12s\n"):
                await asyncio.sleep(0.3)
                output += chunk
                self.update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": run,
                                  "content": [{"type": "content", "content": {"type": "text", "text": output}}]})
            if self.stopped(sid):
                return False
            done = output + "<shellId: 0 completed with exit code 0>"
            self.update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": run, "status": "completed",
                              "content": [{"type": "content", "content": {"type": "text", "text": done}}],
                              "rawOutput": {"content": done, "contents": [{"type": "shell_exit", "shellId": "0", "exitCode": 0, "cwd": cwd}]}})
        elif self.pi:
            # The Pi bridge sends the whole output so far as terminal ``_meta``.
            self.update(sid, {"sessionUpdate": "tool_call", "toolCallId": run, "title": "pytest -q", "kind": "execute", "status": "in_progress",
                              "rawInput": {"command": "pytest -q", "timeout": 120}})
            output = ""
            for chunk in ("....\n", "4 passed in 0.12s\n"):
                await asyncio.sleep(0.3)
                output += chunk
                self.update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": run,
                                  "_meta": {"terminal_output": {"data": output}}})
            if self.stopped(sid):
                return False
            self.update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": run, "status": "completed",
                              "_meta": {"terminal_output": {"data": output}, "terminal_exit": {"exit_code": 0}}})
        elif self.codex:
            self.update(sid, {"sessionUpdate": "tool_call", "toolCallId": run, "title": "pytest -q", "kind": "execute", "status": "in_progress",
                              "rawInput": {"command": "pytest -q", "cwd": cwd}, "content": [{"type": "terminal", "terminalId": run}]})
            for chunk in ("============ test session starts ============\n", "collected 4 items\n\n", "tests/test_app.py \x1b[32m....\x1b[0m"):
                await asyncio.sleep(0.25)
                self.update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": run,
                                  "_meta": {"terminal_output_delta": {"terminal_id": run, "data": chunk}}})
            await asyncio.sleep(0.3)
            if self.stopped(sid):
                return False
            self.update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": run, "status": "completed",
                              "_meta": {"terminal_output_delta": {"terminal_id": run, "data": "\n\n4 passed in 0.12s\n"},
                                        "terminal_exit": {"terminal_id": run, "exit_code": 0, "signal": None}}})
        else:
            self.update(sid, {"sessionUpdate": "tool_call", "toolCallId": run, "title": "pytest -q", "kind": "execute", "status": "in_progress",
                              "rawInput": {"command": "pytest -q", "description": "Run the tests"}})
            await asyncio.sleep(0.6)
            if self.stopped(sid):
                return False
            self.update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": run, "status": "completed",
                              "content": [{"type": "content", "content": {"type": "text", "text": "```console\n....\n4 passed in 0.12s\n```"}}]})
        self.update(sid, {"sessionUpdate": "tool_call", "toolCallId": search, "title": "Search for TODO", "kind": "search", "status": "failed",
                          "rawInput": {"pattern": "TODO"}, "rawOutput": "rg: permission denied: secrets/"})
        return await self.stream(sid, "Done: I updated `main()` and the tests pass.")

    async def agy_call(self, sid: str, tool: str, name: str, kind: str, title: str, args: dict[str, Any],
                       *, locations: list[dict[str, Any]] | None = None, ask: bool = False,
                       content: list[dict[str, Any]] | None = None, output: str | None = None,
                       exit_code: int = 0) -> bool:
        """One tool call the way agy reports it: ``tool_call`` in progress,
        ``pending`` while it asks, then ``completed`` with its content."""
        base = {"toolCallId": tool, "name": name, "args": args, "kind": kind, "title": title, "rawInput": args}
        if locations:
            base["locations"] = locations
        self.update(sid, {"sessionUpdate": "tool_call", **base, "status": "in_progress"})
        if ask:
            self.update(sid, {"sessionUpdate": "tool_call_update", **base, "status": "pending"})
            result = await self.call("session/request_permission", {
                "sessionId": sid,
                "toolCall": {"sessionUpdate": "tool_call", **base, "status": "pending", **({"content": content} if content else {})},
                "options": [
                    {"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                    {"optionId": "deny", "name": "Deny", "kind": "reject_once"},
                ],
            })
            outcome = (result or {}).get("outcome") or {}
            if outcome.get("outcome") != "selected" or outcome.get("optionId") != "allow":
                self.update(sid, {"sessionUpdate": "tool_call_update", **base, "status": "failed"})
                return False
            self.update(sid, {"sessionUpdate": "tool_call_update", **base, "status": "in_progress"})
        await asyncio.sleep(0.3)
        done: dict[str, Any] = {"sessionUpdate": "tool_call_update", **base, "status": "completed", "rawOutput": {}}
        if output is not None:
            body = f"Command exited with code {exit_code}.\n\n" + (
                f"Command output:\n{output}" if output else "No output from the command."
            )
            done["content"] = [{"type": "content", "content": {"type": "text", "text": body}}]
            done["rawOutput"] = {"output": body, "exit_code": exit_code}
        elif content:
            done["content"] = content
        self.update(sid, done)
        return True

    async def agy_tools(self, sid: str) -> bool:
        cwd = self.sessions[sid]["cwd"]
        path = os.path.join(cwd, "app.py")
        await self.stream(sid, "I'll look at the code first.")
        await self.agy_call(sid, self.tool_id("read"), "read_file", "read", "File app.py", {"Path": path},
                            locations=[{"path": path}])
        diff = [{"type": "diff", "path": path, "oldText": "def main():\n    print('hi')\n",
                 "newText": "def main() -> None:\n    print('hello, world')\n"}]
        if not await self.agy_call(sid, self.tool_id("edit"), "edit_file", "edit", "File app.py",
                                   {"Path": path, "ReplacementContent": "..."}, locations=[{"path": path}],
                                   ask=True, content=diff):
            return await self.stream(sid, "OK, I left the file as it was.")
        if self.stopped(sid):
            return False
        if not await self.agy_call(sid, self.tool_id("exec"), "run_command", "execute", "pytest -q",
                                   {"CommandLine": "pytest -q", "WaitMsBeforeAsync": 5000, "toolSummary": "Run the tests"},
                                   ask=True, output="....\r\n4 passed in 0.12s\r\n"):
            return await self.stream(sid, "OK, I did not run the tests.")
        await self.agy_call(sid, self.tool_id("exec"), "run_command", "execute", "git diff --quiet",
                            {"CommandLine": "git diff --quiet", "toolSummary": "Check for changes"},
                            output="", exit_code=1)
        return await self.stream(sid, "Done: I updated `main()` and the tests pass.")

    async def permission(self, sid: str) -> bool:
        await self.stream(sid, "I need to run a command that changes files.")
        tool = self.tool_id("perm")
        self.update(sid, {"sessionUpdate": "tool_call", "toolCallId": tool, "title": "rm -rf build/", "kind": "execute", "status": "pending",
                          "rawInput": {"command": "rm -rf build/", "description": "Delete the build folder"}})
        result = await self.call("session/request_permission", {
            "sessionId": sid,
            "toolCall": {"toolCallId": tool, "title": "rm -rf build/", "kind": "execute"},
            "options": [
                {"optionId": "allow", "name": "Allow once", "kind": "allow_once"},
                {"optionId": "always", "name": "Always allow rm in this project", "kind": "allow_always"},
                {"optionId": "reject", "name": "Reject", "kind": "reject_once"},
            ],
        })
        outcome = (result or {}).get("outcome") or {}
        if outcome.get("outcome") != "selected" or outcome.get("optionId") == "reject":
            self.update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": tool, "status": "failed", "rawOutput": "The user rejected the command"})
            if self.stopped(sid):
                return False
            return await self.stream(sid, "OK, I won't delete it.")
        self.update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": tool, "status": "in_progress"})
        await asyncio.sleep(0.3)
        self.update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": tool, "status": "completed",
                          "content": [{"type": "content", "content": {"type": "text", "text": "```console\nremoved build/\n```"}}]})
        return await self.stream(sid, "The build folder is gone.")

    async def question(self, sid: str) -> bool:
        result = await self.call("elicitation/create", {
            "sessionId": sid,
            "mode": "form",
            "message": "How should I set up the project?",
            "requestedSchema": {
                "type": "object",
                "properties": {
                    "framework": {"type": "string", "title": "Framework", "oneOf": [
                        {"const": "react", "title": "React", "description": "Component UI"},
                        {"const": "vue", "title": "Vue"},
                    ]},
                    "extras": {"type": "array", "title": "Extras", "items": {"anyOf": [
                        {"const": "lint", "title": "Linting"},
                        {"const": "tests", "title": "Tests"},
                    ]}},
                    "name": {"type": "string", "title": "Project name", "description": "Lowercase, no spaces"},
                    "strict": {"type": "boolean", "title": "Strict mode", "default": True},
                },
                "required": ["framework"],
            },
        })
        action = (result or {}).get("action")
        if action != "accept":
            return await self.stream(sid, f"No problem — you chose to {action or 'skip'} the question.")
        content = (result or {}).get("content") or {}
        return await self.stream(sid, f"Got it: {json.dumps(content, sort_keys=True)}")

    async def plan(self, sid: str) -> bool:
        steps = ["Read the code", "Write the fix", "Run the tests"]
        for done in range(len(steps) + 1):
            if self.stopped(sid):
                return False
            entries = [
                {"content": step, "priority": "high" if i == 0 else "medium",
                 "status": "completed" if i < done else ("in_progress" if i == done else "pending")}
                for i, step in enumerate(steps)
            ]
            self.update(sid, {"sessionUpdate": "plan", "entries": entries})
            await asyncio.sleep(0.5)
        return await self.stream(sid, "All three steps are done.")


class _RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def fake_login(flavor: str, device: bool) -> int:
    """A login command shaped like the real ones: a sign-in page, then a
    pasted code (or, with ``device``, a one-time code to enter there)."""
    url = f"https://example.com/{flavor}/sign-in?state=fake"
    if device and flavor == "pi":
        # The real ``pi auth login openai-codex --no-browser`` prints this.
        print("Select OpenAI Codex login method: Device code login (headless)", flush=True)
        print(f"Open {url} and enter code: FAKE-12345\nWaiting for authentication...", flush=True)
        time.sleep(3)
    elif device and flavor == "copilot":
        # The real ``copilot login --device-code`` prints one line.
        print(f"To authenticate, visit {url} and enter code FAKE-1234\nWaiting for authorization...", flush=True)
        time.sleep(3)
    elif device:
        print("Follow these steps to sign in:\n")
        print(f"1. Open this link in your browser and sign in\n   {url}\n")
        print("2. Enter this one-time code (expires in 15 minutes)\n   FAKE-12345\n", flush=True)
        time.sleep(3)
    else:
        # The real Claude login prints its address as an OSC 8 hyperlink.
        print(f"If the browser didn't open, visit: \x1b]8;;{url}\x07{url}\x1b]8;;\x07", flush=True)
        while True:
            sys.stdout.write("Paste code here if prompted > ")
            sys.stdout.flush()
            line = sys.stdin.readline()
            if not line:
                return 1
            code = line.strip()
            if code == "bad":
                print("Invalid code. Check the code and try again.", flush=True)
                continue
            if code == "fail":
                print("Login failed: the sign-in server refused the code.", flush=True)
                return 1
            if code:
                break
    marker = os.environ.get(SIGNED_OUT_ENV)
    if marker:
        try:
            os.remove(marker)
        except FileNotFoundError:
            pass
    print("Login successful.", flush=True)
    return 0


def main() -> None:
    flavor = "claude"
    if "--flavor" in sys.argv[1:-1]:
        flavor = sys.argv[sys.argv.index("--flavor") + 1]
    if sys.argv[1:2] == ["login"]:
        sys.exit(fake_login(flavor, "--device" in sys.argv[2:]))
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    agent = FakeAgent(loop, flavor)
    inbox: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    def reader() -> None:
        for raw in sys.stdin.buffer:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            loop.call_soon_threadsafe(inbox.put_nowait, msg)
        loop.call_soon_threadsafe(inbox.put_nowait, None)

    threading.Thread(target=reader, daemon=True).start()

    async def run() -> None:
        tasks: set[asyncio.Task[None]] = set()
        while True:
            msg = await inbox.get()
            if msg is None:
                break
            task = asyncio.ensure_future(agent.handle(msg))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    loop.run_until_complete(run())


if __name__ == "__main__":
    main()
