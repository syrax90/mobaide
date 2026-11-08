"""A scripted ACP agent for tests and for UI checks on a local stack.

Run with ``python -m mobaide.acp.fake_agent [--flavor codex]``.
``MOBAIDE_ENV=test`` makes the chat sessions use it instead of a real agent,
so every chat UI state can be reached without an account or network. The
flavor picks the config options and names of the agent it stands in for. The
prompt text picks the scenario:

* ``tools`` — read, edit (with a diff) and execute tool calls (the Codex
  flavor streams the command output as terminal ``_meta``);
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
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
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
        return sid

    def config_ids(self) -> tuple[str, ...]:
        if self.codex:
            return ("mode", "collaboration_mode", "model", "reasoning_effort", "fast-mode")
        return ("mode", "model", "effort", "fast")

    def commands(self, sid: str) -> None:
        if self.codex:
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
        if method == "initialize":
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
            sid = self.new_session(params.get("cwd") or os.getcwd())
            self.loop.call_later(0.05, self.commands, sid)
            return {"sessionId": sid, "configOptions": self.config(sid)}
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
            return {"configOptions": self.config(sid)}
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
        if method == "_session/steering":
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
        ok = True
        if "tool" in lower:
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
        if self.codex:
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


def main() -> None:
    flavor = "claude"
    if "--flavor" in sys.argv[1:-1]:
        flavor = sys.argv[sys.argv.index("--flavor") + 1]
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
