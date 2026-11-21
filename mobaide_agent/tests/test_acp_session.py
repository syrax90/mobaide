"""Chat sessions driven end to end against the scripted fake ACP agent
(``mobaide/acp/fake_agent.py``, a real subprocess): start, prompts, the
message queue, cancel, permission and form requests, config changes, a
crash with restart, a sandbox mode that every Restart must apply again,
a new conversation in the same chat (``new_conversation`` and ``/clear``),
opening a stored session, the adapter install on first
use, Send now (steering), command output (and how often it is re-sent),
signing in from the chat (``sign_in`` and ``/login``),
turns that another app on the PC runs in a shared Codex session (a Stop it
never answers, a turn already running when the chat opens), Codex's async
questions (a card that does not hold the turn), extra folders
(``additionalDirectories``), the Antigravity model name resolved into the
session's options, Antigravity's "conversation already open" lock,
Cursor's own requests (questions, a plan to accept or to start, tasks), and
Claude Code's hooks in a Claude chat (work a turn leaves behind, turns the
agent starts by itself).

``TimelineMirror`` keeps what a chat client keeps, applied from the
session's ``'a'`` frames the way the clients do it: a new epoch or a gap
sends ``sync`` and waits for the snapshot.
"""
from __future__ import annotations

import asyncio
import functools
import itertools
import os
import sys
import threading
from collections.abc import Awaitable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import orjson
import psutil
import pytest

from mobaide.acp import session as session_mod
from mobaide.acp import signin
from mobaide.acp.agents import (
    CODEX_ADAPTER,
    FAKE_SIGNED_OUT_ENV,
    JUNIE_BRAVE_OPTION,
    JUNIE_ERROR_HINTS,
    JUNIE_PLAN_REVIEW_PREFIX,
    PI_SIGN_IN_PROMPT,
    ChatLaunchPlan,
    ConfigIntent,
    claude_sign_in_methods,
    codex_sign_in_methods,
    copilot_sign_in_methods,
    cursor_sign_in_methods,
    fake_agent_argv,
    pi_sign_in_methods,
    resolve_antigravity_config,
)
from mobaide.acp.install import AdapterInstallError
from mobaide.acp.presence import ConversationPresence
from mobaide.acp.rpc import AcpConnection, AcpRpcError
from mobaide.acp.session import ChatSession
from mobaide.acp.wake import COPILOT_CLIENT_META, OPENCODE_CLIENT_META
from mobaide.core.attention_dedupe import SharedTurnDedupe, real_cwd
from mobaide.core.attention_gate import AttentionGate, HookKind
from mobaide.core.hook_settings import chat_hook_settings
from mobaide.core.models import SessionConfig, SessionKind, SessionState

pytestmark = pytest.mark.timeout(60)

PNG_B64 = "iVBORw0KGgo="

# A bare agent without ``loadSession``: a restart opens a new session. The
# prompt text picks the reply: ``crash`` exits, ``hang`` streams one chunk and
# never answers (``session/cancel`` included), ``order`` streams agent text
# around a thought, anything else is echoed.
MINI_AGENT = r"""
import json, sys

def send(message):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    sys.stdout.flush()

def update(kind, text):
    send({"method": "session/update", "params": {"sessionId": "mini", "update": {
        "sessionUpdate": kind, "content": {"type": "text", "text": text}}}})

for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg or "method" not in msg:
        continue
    method = msg["method"]
    if method == "initialize":
        result = {"protocolVersion": 1, "agentCapabilities": {}}
    elif method == "session/new":
        result = {"sessionId": "mini"}
    elif method == "session/prompt":
        text = " ".join(block.get("text", "") for block in msg["params"]["prompt"])
        if text == "crash":
            sys.exit(3)
        if text == "hang":
            update("agent_message_chunk", "Working")
            continue
        if text == "order":
            update("agent_message_chunk", "Before.")
            update("agent_thought_chunk", "Thinking.")
            update("agent_message_chunk", "After.")
        else:
            update("agent_message_chunk", "echo " + text)
        result = {"stopReason": "end_turn"}
    else:
        send({"id": msg["id"], "error": {"code": -32601, "message": "Method not found"}})
        continue
    send({"id": msg["id"], "result": result})
"""
MINI_AGENT_ARGV = [sys.executable, "-c", MINI_AGENT]

# Lists its own ``clear`` command after ``session/new``; a prompt is echoed.
OWN_CLEAR_AGENT = r"""
import json, sys

def send(message):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg or "method" not in msg:
        continue
    method = msg["method"]
    if method == "initialize":
        send({"id": msg["id"], "result": {"protocolVersion": 1, "agentCapabilities": {}}})
    elif method == "session/new":
        send({"id": msg["id"], "result": {"sessionId": "own-clear"}})
        send({"method": "session/update", "params": {"sessionId": "own-clear", "update": {
            "sessionUpdate": "available_commands_update",
            "availableCommands": [{"name": "clear", "description": "Clear the history"}]}}})
    elif method == "session/prompt":
        text = " ".join(block.get("text", "") for block in msg["params"]["prompt"])
        send({"method": "session/update", "params": {"sessionId": "own-clear", "update": {
            "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "echo " + text}}}})
        send({"id": msg["id"], "result": {"stopReason": "end_turn"}})
    else:
        send({"id": msg["id"], "error": {"code": -32601, "message": "Method not found"}})
"""
OWN_CLEAR_AGENT_ARGV = [sys.executable, "-c", OWN_CLEAR_AGENT]

# Logs every method it gets to the file named by argv[1]. Advertises
# ``session/close`` and steering; a prompt streams "Working" and is answered
# only by ``session/cancel``; ``_session/steering`` is never answered.
LOGGING_AGENT = r"""
import json, sys
log = open(sys.argv[1], "a", encoding="utf-8")
pending = None

def send(message):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method is None:
        continue
    log.write(method + "\n")
    log.flush()
    if method == "session/cancel":
        if pending is not None:
            send({"id": pending, "result": {"stopReason": "cancelled"}})
            pending = None
        continue
    if "id" not in msg:
        continue
    if method == "initialize":
        result = {"protocolVersion": 1, "_meta": {"steering": {"supported": True}},
                  "agentCapabilities": {"sessionCapabilities": {"close": {}}}}
    elif method == "session/new":
        result = {"sessionId": "logged"}
    elif method == "session/prompt":
        send({"method": "session/update", "params": {"sessionId": "logged", "update": {
            "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Working"}}}})
        pending = msg["id"]
        continue
    elif method == "_session/steering":
        continue
    elif method == "session/close":
        result = {}
    else:
        send({"id": msg["id"], "error": {"code": -32601, "message": "Method not found"}})
        continue
    send({"id": msg["id"], "result": result})
"""


def _logging_agent(log) -> list[str]:
    return [sys.executable, "-c", LOGGING_AGENT, str(log)]


def _methods(log) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


# A Codex-like agent on a shared app-server. Prompt ``external`` is answered,
# then another app's turn starts and never ends; ``session/cancel`` is ignored.
# ``--busy-at-start``: that turn runs at open and ends 1 s later.
SHARED_HOST_AGENT = r"""
import json, sys, threading, time
lock = threading.Lock()

def send(message):
    with lock:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        sys.stdout.flush()

def thread_status(kind):
    send({"method": "session/update", "params": {"sessionId": "shared", "update": {
        "sessionUpdate": "session_info_update", "_meta": {"codex": {"threadStatus": {"type": kind}}}}}})

for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg or "method" not in msg:
        continue
    method = msg["method"]
    if method == "initialize":
        result = {"protocolVersion": 1, "agentCapabilities": {}}
    elif method == "session/new":
        if "--busy-at-start" in sys.argv:
            thread_status("active")
            threading.Timer(1.0, thread_status, ("idle",)).start()
        result = {"sessionId": "shared"}
    elif method == "session/prompt":
        text = " ".join(block.get("text", "") for block in msg["params"]["prompt"])
        send({"method": "session/update", "params": {"sessionId": "shared", "update": {
            "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "echo " + text}}}})
        send({"id": msg["id"], "result": {"stopReason": "end_turn"}})
        if text == "external":
            time.sleep(0.2)
            thread_status("active")
        continue
    else:
        send({"id": msg["id"], "error": {"code": -32601, "message": "Method not found"}})
        continue
    send({"id": msg["id"], "result": result})
"""
SHARED_HOST_AGENT_ARGV = [sys.executable, "-c", SHARED_HOST_AGENT]

# Streams command output as terminal ``_meta`` the way codex-acp does. The
# prompt picks the script: ``retitle`` runs one command whose output arrives
# in chunks 0.1 s apart, one of them with a new title; ``interleave`` runs
# three commands whose output chunks alternate (a, b, a, c).
CONSOLE_AGENT = r"""
import json, sys, time

def send(message):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    sys.stdout.flush()

def update(body, pause):
    send({"method": "session/update", "params": {"sessionId": "console", "update": body}})
    time.sleep(pause)

def start(tool, pause=0.0):
    update({"sessionUpdate": "tool_call", "toolCallId": tool, "title": tool, "kind": "execute",
            "status": "in_progress", "content": [{"type": "terminal", "terminalId": tool}]}, pause)

def output(tool, data, pause=0.0, **fields):
    update({"sessionUpdate": "tool_call_update", "toolCallId": tool, **fields,
            "_meta": {"terminal_output_delta": {"terminal_id": tool, "data": data}}}, pause)

def finish(tool, data):
    update({"sessionUpdate": "tool_call_update", "toolCallId": tool, "status": "completed",
            "_meta": {"terminal_output_delta": {"terminal_id": tool, "data": data},
                      "terminal_exit": {"terminal_id": tool, "exit_code": 0, "signal": None}}}, 0.0)

for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg or "method" not in msg:
        continue
    method = msg["method"]
    if method == "initialize":
        result = {"protocolVersion": 1, "agentCapabilities": {}}
    elif method == "session/new":
        result = {"sessionId": "console"}
    elif method == "session/prompt":
        text = " ".join(block.get("text", "") for block in msg["params"]["prompt"])
        if text == "retitle":
            start("make", pause=0.1)
            for n in range(3):
                output("make", f"line {n}\n", pause=0.1)
            output("make", "line 3\n", pause=0.1, title="make test")
            for n in range(4, 7):
                output("make", f"line {n}\n", pause=0.1)
            finish("make", "done\n")
        elif text == "interleave":
            start("a")
            output("a", "a1\n")
            start("b")
            output("b", "b1\n")
            output("a", "a2\n")
            start("c")
            output("c", "c1\n")
            finish("a", "a3\n")
            finish("b", "b2\n")
            finish("c", "c2\n")
        result = {"stopReason": "end_turn"}
    else:
        send({"id": msg["id"], "error": {"code": -32601, "message": "Method not found"}})
        continue
    send({"id": msg["id"], "result": result})
"""
CONSOLE_AGENT_ARGV = [sys.executable, "-c", CONSOLE_AGENT]

# A Codex-like sandbox ``mode`` that starts at ``agent``. While the file named
# by argv[1] exists, ``session/set_config_option`` is refused. The prompt
# ``crash`` exits; anything else is echoed.
SANDBOX_AGENT = r"""
import json, os, sys
refuse = sys.argv[1]
mode = "agent"

def send(message):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    sys.stdout.flush()

def config():
    return [{"id": "mode", "name": "Mode", "category": "mode", "type": "select", "currentValue": mode,
             "options": [{"value": "read-only", "name": "Read-only"}, {"value": "agent", "name": "Agent"}]}]

for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg or "method" not in msg:
        continue
    method = msg["method"]
    if method == "initialize":
        result = {"protocolVersion": 1, "agentCapabilities": {}}
    elif method == "session/new":
        result = {"sessionId": "sandbox", "configOptions": config()}
    elif method == "session/set_config_option":
        if os.path.exists(refuse):
            send({"id": msg["id"], "error": {"code": -32603, "message": "Sandbox unavailable"}})
            continue
        mode = msg["params"]["value"]
        result = {"configOptions": config()}
    elif method == "session/prompt":
        text = " ".join(block.get("text", "") for block in msg["params"]["prompt"])
        if text == "crash":
            sys.exit(3)
        send({"method": "session/update", "params": {"sessionId": "sandbox", "update": {
            "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "echo " + text}}}})
        result = {"stopReason": "end_turn"}
    else:
        send({"id": msg["id"], "error": {"code": -32601, "message": "Method not found"}})
        continue
    send({"id": msg["id"], "result": result})
"""


def _sandbox_agent(refuse) -> list[str]:
    return [sys.executable, "-c", SANDBOX_AGENT, str(refuse)]


# Advertises steering. The prompt ``work`` streams "Working" and waits;
# ``_session/steering`` ends that turn at once and is refused only after the
# file named by argv[1] exists. Any other prompt is echoed.
LATE_STEER_AGENT = r"""
import json, os, sys, threading, time
release = sys.argv[1]
lock = threading.Lock()
pending = None

def send(message):
    with lock:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        sys.stdout.flush()

def chunk(text):
    send({"method": "session/update", "params": {"sessionId": "late", "update": {
        "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}}})

def refuse_when_released(steer_id):
    while not os.path.exists(release):
        time.sleep(0.02)
    send({"id": steer_id, "error": {"code": -32603, "message": "No turn to steer"}})

for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg or "method" not in msg:
        continue
    method = msg["method"]
    if method == "initialize":
        result = {"protocolVersion": 1, "_meta": {"steering": {"supported": True}}, "agentCapabilities": {}}
    elif method == "session/new":
        result = {"sessionId": "late"}
    elif method == "session/prompt":
        text = " ".join(block.get("text", "") for block in msg["params"]["prompt"])
        if text == "work":
            chunk("Working")
            pending = msg["id"]
            continue
        chunk("echo " + text)
        result = {"stopReason": "end_turn"}
    elif method == "_session/steering":
        send({"id": pending, "result": {"stopReason": "end_turn"}})
        pending = None
        threading.Thread(target=refuse_when_released, args=(msg["id"],), daemon=True).start()
        continue
    else:
        send({"id": msg["id"], "error": {"code": -32601, "message": "Method not found"}})
        continue
    send({"id": msg["id"], "result": result})
"""


def _late_steer_agent(release) -> list[str]:
    return [sys.executable, "-c", LATE_STEER_AGENT, str(release)]


class FakeTrust:
    """A ``WorkspaceTrust`` held in memory. ``records=False`` is a folder too
    broad to record (home, a root): ``grant`` saves nothing and says so."""

    prompt_title = "Trust this folder?"
    prompt_text = "Its settings can run commands."

    def __init__(
        self,
        trusted: bool = False,
        grant_error: Exception | None = None,
        records: bool = True,
    ) -> None:
        self.trusted = trusted
        self.grant_error = grant_error
        self.records = records
        self.grants = 0

    def is_trusted(self) -> bool:
        return self.trusted

    def grant(self) -> bool:
        self.grants += 1
        if self.grant_error is not None:
            raise self.grant_error
        if self.records:
            self.trusted = True
        return self.records


def _fake_agent_pids() -> set[int]:
    pids: set[int] = set()
    for proc in psutil.Process().children(recursive=True):
        try:
            if any(part.endswith("fake_agent.py") for part in proc.cmdline()):
                pids.add(proc.pid)
        except psutil.Error:
            continue
    return pids


class TimelineMirror:
    def __init__(self, session: ChatSession) -> None:
        self.session = session
        self.frames: list[dict[str, Any]] = []
        self.items: dict[str, dict[str, Any]] = {}
        self.state: dict[str, Any] = {}
        self.results: dict[str, dict[str, Any]] = {}
        self.attention: list[str | None] = []
        self.session_events: list[str] = []
        self.epoch: str | None = None
        self.seq = 0
        self._waiting_snapshot = False
        self._ids = itertools.count(1)

    @property
    def status(self) -> str | None:
        return self.state.get("status")

    def of_type(self, kind: str) -> list[dict[str, Any]]:
        return [item for item in self.items.values() if item["type"] == kind]

    def texts(self, kind: str) -> list[str]:
        return [item.get("text", "") for item in self.of_type(kind)]

    async def until(self, predicate: Callable[[], bool], timeout: float = 20.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not predicate():
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise AssertionError(
                    f"timed out; state={self.state!r} items={list(self.items.values())!r}"
                )
            try:
                event = await asyncio.wait_for(self.session.event_queue.get(), remaining)
            except TimeoutError:
                continue
            await self._on_event(event)

    async def command(self, op: str, **fields: Any) -> dict[str, Any]:
        cmd_id = f"cmd-{next(self._ids)}"
        await self.session.handle_command(orjson.dumps({"id": cmd_id, "op": op, **fields}))
        await self.until(lambda: cmd_id in self.results)
        return self.results[cmd_id]

    async def _on_event(self, event) -> None:
        self.session_events.append(event.event_type)
        if event.event_type == "attention":
            self.attention.append(event.attention)
        if event.event_type != "chat":
            return
        frame = orjson.loads(event.data)
        self.frames.append(frame)
        if self._apply(frame):
            await self.session.handle_command(b'{"op": "sync"}')

    def _apply(self, frame: dict[str, Any]) -> bool:
        kind = frame["t"]
        if kind == "result":
            self.results[frame["id"]] = frame
            return False
        if kind == "snapshot":
            self._waiting_snapshot = False
            self.epoch, self.seq = frame["epoch"], frame["seq"]
            self.state = dict(frame["state"])
            self.items = {item["id"]: item for item in frame["items"]}
            return False
        if self._waiting_snapshot:
            return False
        if frame["epoch"] != self.epoch:
            self._waiting_snapshot = True
            return True
        for ev in frame["events"]:
            if ev["seq"] <= self.seq:
                continue
            if ev["seq"] != self.seq + 1:
                self._waiting_snapshot = True
                return True
            if ev["type"] == "item":
                self.items[ev["item"]["id"]] = ev["item"]
            elif ev["type"] == "append":
                item = self.items[ev["id"]]
                item["text"] = (item.get("text") or "") + ev["text"]
            elif ev["type"] == "remove":
                self.items.pop(ev["id"], None)
            elif ev["type"] == "state":
                self.state.update(ev["patch"])
            self.seq = ev["seq"]
        return False


@pytest.fixture
async def open_chat(tmp_path):
    sessions: list[ChatSession] = []

    async def open_(
        load_session_id: str | None = None,
        argv: list[str] | None = None,
        settle: bool = True,
        kind: SessionKind = SessionKind.CLAUDE,
        attention_dedupe: SharedTurnDedupe | None = None,
        **plan_fields: Any,
    ) -> TimelineMirror:
        config = SessionConfig(
            session_id=f"chat-{len(sessions) + 1}",
            command=[],
            cwd=str(tmp_path),
            ui="chat",
            kind=kind,
        )
        plan = ChatLaunchPlan(
            argv=argv or fake_agent_argv(), env=dict(os.environ), cwd=str(tmp_path), **plan_fields
        )
        session = ChatSession(
            config,
            plan,
            agent_title="Claude Code",
            load_session_id=load_session_id,
            attention_dedupe=attention_dedupe,
        )
        sessions.append(session)
        await session.start()
        mirror = TimelineMirror(session)
        if settle:
            await mirror.until(
                lambda: mirror.status in ("ready", "error") or bool(mirror.state.get("requests"))
            )
        return mirror

    yield open_
    for session in sessions:
        await session.stop(force=True)


def _turn_done(m: TimelineMirror, agent_text: str) -> Callable[[], bool]:
    return lambda: m.status == "ready" and agent_text in m.texts("agent")


def _asked(frames: list[dict[str, Any]]) -> bool:
    """Whether any of ``frames`` shows a pending request to the user."""
    for frame in frames:
        if frame["t"] == "snapshot" and frame["state"].get("requests"):
            return True
        if frame["t"] == "events" and any(
            ev["type"] == "state" and ev["patch"].get("requests") for ev in frame["events"]
        ):
            return True
    return False


class TestStart:
    async def test_bootstrap_reports_agent_config_and_commands(self, open_chat, tmp_path):
        m = await open_chat()

        assert m.state["agent"] == {"name": "fake-acp-agent", "title": "Fake Agent", "version": "1.0.0"}
        assert m.state["prompt_caps"] == {"image": True}
        assert m.state["cwd"] == str(tmp_path)
        assert isinstance(m.state["acp_session_id"], str) and m.state["acp_session_id"]
        assert [o["id"] for o in m.state["config"]] == ["mode", "model", "effort", "fast"]
        await m.until(lambda: "review" in [c["name"] for c in m.state.get("commands") or []])
        assert [c["name"] for c in m.state["commands"]] == ["review", "test", "compact", "clear"]

        info = m.session.to_info()
        assert info.ui == "chat"
        assert info.state == SessionState.RUNNING
        assert info.pid is not None

    async def test_initial_config_and_prompt_are_applied(self, open_chat):
        m = await open_chat(
            initial_config=[
                ConfigIntent("plan", category="mode"),
                ConfigIntent("fast", category="model"),
                ConfigIntent(True, option_id="fast"),
                ConfigIntent("turbo", category="thought_level"),
            ],
            initial_prompt="hello there",
        )

        await m.until(_turn_done(m, "You said: hello there"))
        values = {o["id"]: o["value"] for o in m.state["config"]}
        assert values == {"mode": "plan", "model": "fast", "effort": "high", "fast": True}
        assert m.texts("user") == ["hello there"]

    async def test_a_required_mode_the_agent_does_not_offer_fails_the_start(self, open_chat):
        m = await open_chat(
            initial_config=[ConfigIntent("no-such-mode", category="mode", required=True)]
        )
        await m.until(lambda: m.status == "error")

        message = "Could not set the no-such-mode mode, so the session did not start"
        assert message in m.state["error"]

    async def test_load_opens_a_stored_session_with_its_history(self, open_chat):
        m = await open_chat(load_session_id="external-1111", initial_prompt="not sent on load")
        await m.until(lambda: m.state.get("title") == "Earlier session")

        assert m.status == "ready"
        assert m.state["acp_session_id"] == "external-1111"
        assert m.session.acp_session_id == "external-1111"
        assert m.texts("user") == ["Can you check the login handler?"]
        assert m.texts("agent") == ["Sure — I read the handler and fixed the null check."]
        tool = m.items["replay-edit"]
        assert (tool["kind"], tool["status"]) == ("edit", "completed")
        diff = tool["content"][0]
        assert (diff["type"], diff["added"], diff["removed"]) == ("diff", 1, 1)

    async def test_replayed_tools_go_out_with_the_rest_of_the_history(
        self, open_chat, monkeypatch
    ):
        """A finished tool is sent at once during a turn, but not while a
        stored session replays: there it waits for the batch. The batching
        timer never fires here, so only an urgent send splits the frames."""
        monkeypatch.setattr(
            session_mod,
            "ChatTimeline",
            functools.partial(session_mod.ChatTimeline, flush_interval=3600.0),
        )
        m = await open_chat(load_session_id="external-1111")

        carrying = [
            f
            for f in m.frames
            if f["t"] == "events"
            and any(ev["type"] == "item" and ev["item"]["id"] == "replay-edit" for ev in f["events"])
        ]
        assert len(carrying) == 1
        events = carrying[0]["events"]
        assert [ev["item"]["type"] for ev in events if ev["type"] == "item"] == ["user", "agent", "tool"]
        # The title is replayed after the tool, so it shares the tool's frame
        # only when the tool was not sent on its own.
        assert any(
            ev["type"] == "state" and ev["patch"].get("title") == "Earlier session" for ev in events
        )


    async def test_agent_process_runs_outside_the_session_folder(self, open_chat, tmp_path):
        home = tmp_path / "home"
        home.mkdir()

        m = await open_chat(process_cwd=str(home))

        assert os.path.realpath(psutil.Process(m.session.pid).cwd()) == os.path.realpath(home)
        assert m.state["cwd"] == str(tmp_path)


class TestFolderTrust:
    async def _answer(self, m: TimelineMirror, option_id: str) -> None:
        request = m.state["requests"][0]
        result = await m.command("respond", request_id=request["id"], option_id=option_id)
        assert result["ok"] is True

    async def test_untrusted_folder_is_asked_about_before_the_session_opens(
        self, open_chat, tmp_path
    ):
        trust = FakeTrust()
        m = await open_chat(trust=trust)
        await m.until(lambda: "awaiting_permission" in m.attention)

        request = m.state["requests"][0]
        assert request["kind"] == "permission"
        assert request["tool_call_id"] is None
        assert (request["title"], request["subtitle"]) == ("Trust this folder?", str(tmp_path))
        assert request["content"] == [{"type": "text", "text": "Its settings can run commands."}]
        assert [o["id"] for o in request["options"]] == ["trust", "decline"]
        assert m.status == "starting"
        assert m.state.get("acp_session_id") is None

        await self._answer(m, "trust")
        await m.until(lambda: m.status == "ready")

        assert trust.grants == 1
        assert m.state["requests"] == []
        assert m.state["acp_session_id"]
        assert m.session.to_info().attention is None

    async def test_decline_fails_the_start_and_restart_asks_again(self, open_chat):
        trust = FakeTrust()
        m = await open_chat(trust=trust)

        await self._answer(m, "decline")
        await m.until(lambda: m.status == "error")

        message = "The folder is not trusted, so the session did not start. Use Restart to choose again."
        assert m.state["error"] == message
        assert m.texts("error") == [message]
        assert trust.grants == 0
        assert m.state.get("acp_session_id") is None
        refused = await m.command("prompt", text="hello")
        assert refused["error"] == "The agent is not running. Use Restart to start it again."

        assert (await m.command("restart"))["ok"] is True
        await m.until(lambda: bool(m.state.get("requests")))
        await self._answer(m, "trust")
        await m.until(lambda: m.status == "ready")

        assert trust.grants == 1

    async def test_trusted_folder_starts_without_asking(self, open_chat):
        trust = FakeTrust(trusted=True)

        m = await open_chat(trust=trust)

        assert m.status == "ready"
        assert trust.grants == 0
        patches = [
            ev["patch"]
            for f in m.frames
            if f["t"] == "events"
            for ev in f["events"]
            if ev["type"] == "state"
        ]
        assert not any(patch.get("requests") for patch in patches)
        assert "awaiting_permission" not in m.attention

    async def test_restart_after_a_decline_sends_no_ended_alert(self, open_chat):
        """The Restart closes the adapter that waited on the declined
        prompt; that exit is the Restart's own, not a session that ended."""
        trust = FakeTrust()
        m = await open_chat(trust=trust)
        await self._answer(m, "decline")
        await m.until(lambda: m.status == "error")

        assert (await m.command("restart"))["ok"] is True
        await m.until(lambda: bool(m.state.get("requests")))

        assert "ended" not in m.attention
        assert m.session.to_info().exit_reason is None
        assert m.status == "starting"

    async def test_trust_that_is_not_recorded_still_covers_a_restart(self, open_chat):
        """A folder too broad to record (home, a root) is trusted for this
        session only: a Restart after a crash must not ask again."""
        trust = FakeTrust(records=False)
        m = await open_chat(trust=trust)
        await self._answer(m, "trust")
        await m.until(lambda: m.status == "ready")
        await m.command("prompt", text="crash now")
        await m.until(lambda: m.status == "error")
        before = len(m.frames)

        assert (await m.command("restart"))["ok"] is True
        await m.until(
            lambda: bool(m.state.get("requests"))
            or (m.status == "ready" and m.state.get("title") == "Earlier session")
        )

        assert not _asked(m.frames[before:])
        assert m.status == "ready"
        assert (trust.grants, trust.trusted) == (1, False)

    async def test_a_failed_grant_still_opens_the_session(self, open_chat):
        trust = FakeTrust(grant_error=PermissionError(13, "Permission denied"))
        m = await open_chat(trust=trust)

        await self._answer(m, "trust")
        await m.until(lambda: m.status in ("ready", "error"))

        assert m.status == "ready"
        assert trust.grants == 1


class TestTurns:
    async def test_prompt_streams_thought_and_answer(self, open_chat):
        m = await open_chat()

        result = await m.command("prompt", text="hello")
        await m.until(_turn_done(m, "You said: hello"))
        await m.until(lambda: "turn_finished" in m.attention)

        assert result["ok"] is True
        assert result["data"] == {"queued": False, "queue_id": None}
        assert [i["type"] for i in m.items.values()] == ["user", "thought", "agent"]
        assert m.texts("thought") == ["Let me think about that."]
        assert m.state["turn_started_at"] is None
        assert m.state["usage"]["used"] == 3700
        assert m.state["usage"]["cost"]["currency"] == "USD"
        assert m.state["title"] == "hello"
        assert m.session.to_info().title == "hello"
        assert m.session.to_info().attention == "turn_finished"

    async def test_image_prompt_reaches_the_agent(self, open_chat):
        m = await open_chat()

        await m.command("prompt", text="look", images=[{"mime": "image/png", "data": PNG_B64}])
        await m.until(_turn_done(m, "You said: look (with 1 image)"))

        assert m.of_type("user")[0]["attachments"] == [{"kind": "image", "mime": "image/png"}]

    @pytest.mark.parametrize(
        ("fields", "error"),
        [
            ({"text": "   "}, "The message is empty"),
            ({"text": "x", "images": [{"mime": "image/bmp", "data": PNG_B64}]}, "Unsupported image type"),
            ({"text": "x", "images": [{"mime": "image/png", "data": "@@@"}]}, "An image is not valid base64"),
            ({"text": "x", "images": [{"mime": "image/png", "data": PNG_B64}] * 5}, "Attach at most 4 images"),
            ({"text": "x", "images": "nope"}, "Invalid images"),
        ],
        ids=["empty", "mime", "base64", "count", "shape"],
    )
    async def test_invalid_prompts_are_refused(self, open_chat, fields, error):
        m = await open_chat()

        result = await m.command("prompt", **fields)

        assert result == {"t": "result", "id": result["id"], "ok": False, "error": error}
        assert m.of_type("user") == []

    async def test_images_over_the_total_cap_are_refused(self, open_chat):
        """Each image is under its own cap; together they would push the
        ``'c'`` frame past the relay's 4 MiB limit."""
        m = await open_chat()
        each = "A" * session_mod.IMAGE_B64_MAX
        count = session_mod.IMAGES_B64_TOTAL_MAX // len(each) + 1
        assert count <= session_mod.IMAGES_MAX

        result = await m.command("prompt", text="x", images=[{"mime": "image/png", "data": each}] * count)

        assert result["error"] == "The images are too large together"
        assert m.of_type("user") == []

    async def test_text_after_a_thought_starts_a_new_agent_item(self, open_chat):
        m = await open_chat(argv=MINI_AGENT_ARGV)

        await m.command("prompt", text="order")
        await m.until(_turn_done(m, "After."))

        assert [(i["type"], i["text"]) for i in m.items.values()] == [
            ("user", "order"),
            ("agent", "Before."),
            ("thought", "Thinking."),
            ("agent", "After."),
        ]

    async def test_failed_turn_shows_the_error_and_keeps_the_agent(self, open_chat):
        m = await open_chat()

        await m.command("prompt", text="please fail")
        await m.until(lambda: m.status == "ready" and bool(m.of_type("error")))

        assert m.texts("error") == ["The model request failed (fake error)"]
        assert m.session.pid is not None


class TestActivity:
    """The session row's ``activity`` follows the turn: ``working`` while one
    runs, ``idle`` between turns, nothing once the session is gone. Each change
    is an ``activity`` event, which the fanout turns into ``session.updated``."""


    async def test_a_running_turn_is_working_and_its_end_is_idle(self, open_chat):
        m = await open_chat()

        await m.command("prompt", text="slow")
        await m.until(lambda: m.status == "running" and bool(m.of_type("agent")))
        assert m.session.to_info().activity == "working"

        await m.command("cancel")
        await m.until(lambda: m.status == "ready")
        assert m.session.to_info().activity == "idle"


class TestQueueAndCancel:
    async def test_message_sent_during_a_turn_runs_after_cancel(self, open_chat):
        m = await open_chat()
        await m.command("prompt", text="slow")
        await m.until(lambda: m.status == "running" and bool(m.of_type("agent")))

        queued = await m.command("prompt", text="second")
        queue_id = queued["data"]["queue_id"]
        assert queued["data"]["queued"] is True
        assert m.state["queue"] == [{"id": queue_id, "text": "second", "images": 0}]

        cancelled = await m.command("cancel")
        assert cancelled["data"] == {"cancelled": True}
        await m.until(_turn_done(m, "You said: second"))

        turn_end = m.of_type("turn_end")
        assert [(t["stop_reason"], t["text"]) for t in turn_end] == [("cancelled", "Stopped")]
        assert m.state["queue"] == []
        assert m.state["cancelling"] is False
        assert m.texts("user") == ["slow", "second"]

    async def test_dequeue_removes_a_waiting_message(self, open_chat):
        m = await open_chat()
        await m.command("prompt", text="slow")
        await m.until(lambda: bool(m.of_type("agent")))
        queued = await m.command("prompt", text="never sent")
        queue_id = queued["data"]["queue_id"]

        assert (await m.command("dequeue", queue_id=queue_id))["ok"] is True
        assert m.state["queue"] == []
        again = await m.command("dequeue", queue_id=queue_id)
        assert again["error"] == "This message was already sent"

        await m.command("cancel")
        await m.until(lambda: m.status == "ready" and bool(m.of_type("turn_end")))
        assert m.texts("user") == ["slow"]

    async def test_cancel_right_after_the_prompt_stops_the_turn(self, open_chat):
        """Prompt and Stop handled in one loop tick: the turn task has not
        sent ``session/prompt`` yet when ``session/cancel`` goes out."""
        m = await open_chat()

        await m.session.handle_command(b'{"op": "prompt", "text": "slow"}')
        cancelled = await m.command("cancel")

        assert cancelled["data"] == {"cancelled": True}
        await m.until(lambda: m.status == "ready", timeout=8.0)
        assert [t["stop_reason"] for t in m.of_type("turn_end")] == ["cancelled"]


    async def test_agent_that_ignores_cancel_is_stopped_for_a_restart(
        self, open_chat, monkeypatch
    ):
        monkeypatch.setattr(session_mod, "CANCEL_GRACE", 0.2)
        m = await open_chat(argv=MINI_AGENT_ARGV)
        await m.command("prompt", text="hang")
        await m.until(lambda: "Working" in m.texts("agent"))

        cancelled = await m.command("cancel")
        assert cancelled["data"] == {"cancelled": True}
        await m.until(lambda: m.status == "error" and m.state["cancelling"] is False)

        assert m.texts("error") == [m.state["error"]]
        assert m.state["error"].startswith("The agent did not stop, so Mobaide closed it.")
        # The user pressed Stop: no "session ended" alert.
        assert "ended" not in m.attention

        assert (await m.command("restart"))["ok"] is True
        await m.until(lambda: m.status == "ready")
        await m.command("prompt", text="hello")
        await m.until(_turn_done(m, "echo hello"))


# A prompt streams "Working" and is answered only by ``session/cancel`` — with
# an error, as an agent that reports its interrupted request as failed does.
FAIL_ON_CANCEL_AGENT = r"""
import json, sys
pending = None

def send(message):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method == "session/cancel":
        if pending is not None:
            send({"id": pending, "error": {"code": -32603, "message": "Request interrupted"}})
            pending = None
        continue
    if method is None or "id" not in msg:
        continue
    if method == "initialize":
        result = {"protocolVersion": 1, "agentCapabilities": {}}
    elif method == "session/new":
        result = {"sessionId": "failing"}
    elif method == "session/prompt":
        send({"method": "session/update", "params": {"sessionId": "failing", "update": {
            "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Working"}}}})
        pending = msg["id"]
        continue
    else:
        send({"id": msg["id"], "error": {"code": -32601, "message": "Method not found"}})
        continue
    send({"id": msg["id"], "result": result})
"""


class TestTurnAttention:
    """A turn the agent answers with an error needs the user as much as a
    finished one; a turn a terminal on the same live Codex thread already
    announced must not alert a second time."""

    async def test_a_failed_turn_alerts_turn_failed(self, open_chat):
        m = await open_chat()

        await m.command("prompt", text="please fail")
        await m.until(lambda: "turn_failed" in m.attention)

        assert "turn_finished" not in m.attention
        assert m.session.to_info().attention == "turn_failed"

    async def test_an_error_answering_the_users_stop_does_not_alert(self, open_chat):
        m = await open_chat(argv=[sys.executable, "-c", FAIL_ON_CANCEL_AGENT])
        await m.command("prompt", text="go")
        await m.until(lambda: "Working" in m.texts("agent"))

        assert (await m.command("cancel"))["data"] == {"cancelled": True}
        await m.until(lambda: m.status == "ready" and bool(m.of_type("error")))

        assert m.session.to_info().attention is None
        assert "turn_failed" not in m.attention

    async def test_a_failure_with_a_message_waiting_alerts_only_for_the_last_turn(
        self, open_chat
    ):
        m = await open_chat()
        await m.command("prompt", text="please fail")
        queued = await m.command("prompt", text="next")
        assert queued["data"]["queued"] is True

        await m.until(_turn_done(m, "You said: next"))
        await m.until(lambda: "turn_finished" in m.attention)

        assert m.texts("error") == ["The model request failed (fake error)"]
        assert "turn_failed" not in m.attention

    async def test_a_turn_the_terminal_already_announced_stays_quiet(self, open_chat, tmp_path):
        dedupe = SharedTurnDedupe()
        assert dedupe.admit(
            session_id="term-1", ui="terminal", app="codex",
            cwd=real_cwd(str(tmp_path)), reason="turn_finished",
        )
        m = await open_chat(kind=SessionKind.CODEX, attention_dedupe=dedupe, shared_host=True)

        await m.command("prompt", text="hello")
        await m.until(_turn_done(m, "You said: hello"))

        assert m.session.to_info().attention is None
        assert "turn_finished" not in m.attention

    async def test_a_failed_turn_the_terminal_announced_as_done_stays_quiet(
        self, open_chat, tmp_path
    ):
        # The TUI may report the turn the chat saw fail as plain ``done``.
        dedupe = SharedTurnDedupe()
        assert dedupe.admit(
            session_id="term-1", ui="terminal", app="codex",
            cwd=real_cwd(str(tmp_path)), reason="turn_finished",
        )
        m = await open_chat(kind=SessionKind.CODEX, attention_dedupe=dedupe, shared_host=True)

        await m.command("prompt", text="please fail")
        await m.until(lambda: m.status == "ready" and bool(m.of_type("error")))

        assert m.session.to_info().attention is None
        assert "turn_failed" not in m.attention

    async def test_a_chat_on_a_private_app_server_is_never_quieted(self, open_chat, tmp_path):
        # It shares its thread with no one, whatever a terminal in the folder did.
        dedupe = SharedTurnDedupe()
        assert dedupe.admit(
            session_id="term-1", ui="terminal", app="codex",
            cwd=real_cwd(str(tmp_path)), reason="turn_finished",
        )
        m = await open_chat(kind=SessionKind.CODEX, attention_dedupe=dedupe, shared_host=False)

        await m.command("prompt", text="hello")
        await m.until(lambda: "turn_finished" in m.attention)

        assert m.session.to_info().attention == "turn_finished"


    async def test_a_claude_chat_next_to_a_codex_terminal_still_alerts(
        self, open_chat, tmp_path
    ):
        # Only Codex threads are shared; the app is the chat's own kind.
        dedupe = SharedTurnDedupe()
        dedupe.admit(
            session_id="term-1", ui="terminal", app="codex",
            cwd=real_cwd(str(tmp_path)), reason="turn_finished",
        )
        m = await open_chat(attention_dedupe=dedupe, shared_host=True)

        await m.command("prompt", text="hello")
        await m.until(lambda: "turn_finished" in m.attention)

        assert m.session.to_info().attention == "turn_finished"


class TestUsageLimitWait:
    """A Claude prompt refused for a usage limit queues a continue that goes
    out once the limit resets, without a ``turn_failed`` alert."""

    @staticmethod
    def _wait(m: TimelineMirror) -> dict[str, Any] | None:
        return m.state.get("limit_wait")

    async def test_the_continue_waits_first_in_the_queue_until_the_user_writes(self, open_chat):
        m = await open_chat()
        await m.command("prompt", text="usagelimit")
        await m.until(lambda: self._wait(m) is not None and m.status == "ready")

        wait = self._wait(m)
        assert m.state["queue"] == [
            {"id": wait["queue_id"], "text": session_mod.AUTO_CONTINUE_PROMPT, "images": 0}
        ]
        assert wait["limit_type"] == "five_hour"
        resets_at = datetime.fromisoformat(wait["resets_at"])
        continues_at = datetime.fromisoformat(wait["continues_at"])
        assert continues_at - resets_at == timedelta(seconds=session_mod.LIMIT_WAIT_MARGIN)
        assert "turn_failed" not in m.attention

        steered = await m.command("steer", queue_id=wait["queue_id"])
        assert steered["error"] == "This message waits for the usage limit to reset"

        await m.command("prompt", text="hello")
        await m.until(_turn_done(m, "You said: hello"))
        assert self._wait(m) is None
        assert m.state["queue"] == []
        assert m.texts("user") == ["usagelimit", "hello"]

    async def test_the_continue_goes_out_once_the_limit_resets(self, open_chat, monkeypatch):
        monkeypatch.setattr(session_mod, "LIMIT_WAIT_MARGIN", 0.0)
        monkeypatch.setenv("MOBAIDE_FAKE_ACP_LIMIT_RESET", "0")
        m = await open_chat()

        await m.command("prompt", text="usagelimit")
        await m.until(_turn_done(m, f"You said: {session_mod.AUTO_CONTINUE_PROMPT}"))

        assert m.texts("user") == ["usagelimit", session_mod.AUTO_CONTINUE_PROMPT]
        assert self._wait(m) is None
        assert m.state["queue"] == []
        assert "turn_failed" not in m.attention

    async def test_automatic_continues_stop_after_three_in_a_row(self, open_chat, monkeypatch):
        monkeypatch.setattr(session_mod, "LIMIT_WAIT_MARGIN", 0.0)
        monkeypatch.setenv("MOBAIDE_FAKE_ACP_LIMIT_RESET", "0")
        m = await open_chat()

        await m.command("prompt", text="usagelimit 9")
        await m.until(lambda: "turn_failed" in m.attention and m.status == "ready")

        assert m.texts("user") == ["usagelimit 9"] + [session_mod.AUTO_CONTINUE_PROMPT] * 3
        assert self._wait(m) is None
        assert m.state["queue"] == []

    async def test_a_reset_too_far_away_alerts_instead(self, open_chat):
        m = await open_chat()

        await m.command("prompt", text="weeklylimit")
        await m.until(lambda: "turn_failed" in m.attention and m.status == "ready")

        assert self._wait(m) is None
        assert m.state["queue"] == []

    @pytest.mark.parametrize("op", ["dequeue", "new_conversation", "clear", "agent_exit"])
    async def test_the_wait_ends_with_the_conversation_or_on_request(self, open_chat, op):
        m = await open_chat()
        await m.command("prompt", text="usagelimit")
        await m.until(lambda: self._wait(m) is not None and m.status == "ready")

        if op == "agent_exit":
            psutil.Process(m.session.pid).kill()
            await m.until(lambda: m.status == "error" and self._wait(m) is None)
        else:
            if op == "clear":
                op, fields = "prompt", {"text": "/clear"}
            elif op == "dequeue":
                fields = {"queue_id": self._wait(m)["queue_id"]}
            else:
                fields = {}
            assert (await m.command(op, **fields))["ok"] is True
            await m.until(lambda: m.status == "ready" and self._wait(m) is None)

        assert m.state["queue"] == []
        assert m.session._limit_task is None

    @pytest.mark.parametrize("ends_by", ["dequeue", "send_now_in_agents_own_turn"])
    async def test_a_message_queued_behind_the_continue_goes_out_without_it(
        self, open_chat, ends_by
    ):
        """The continue holds the messages queued behind it. Removing it
        sends them; so does Send now on one of them while the agent runs a
        turn by itself, which starts that message as a prompt."""
        m = await open_chat(hook_port=HOOK_PORT)
        await m.command("prompt", text="usagelimit")
        behind = await m.command("prompt", text="after")
        await m.until(lambda: self._wait(m) is not None and m.status == "ready")
        assert [q["text"] for q in m.state["queue"]] == [
            session_mod.AUTO_CONTINUE_PROMPT,
            "after",
        ]

        if ends_by == "dequeue":
            assert (await m.command("dequeue", queue_id=self._wait(m)["queue_id"]))["ok"] is True
        else:
            m.session.on_claude_hook(
                HookKind.PROMPT_SUBMIT, {"prompt_id": "p1", "prompt": TASK_NOTIFICATION}
            )
            await m.until(lambda: m.status == "running")
            steered = await m.command("steer", queue_id=behind["data"]["queue_id"])
            assert steered["data"] == {"steered": True}
            assert self._wait(m) is None
        await m.until(_turn_done(m, "You said: after"))

        assert self._wait(m) is None
        assert m.state["queue"] == []
        assert m.session._limit_task is None
        assert m.texts("user") == ["usagelimit", "after"]


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"errorKind": "rate_limit", "rateLimit": {"rateLimitType": "five_hour", "resetsAt": 1_800_000_000}}, (1_800_000_000.0, "five_hour")),
        ({"errorKind": "rate_limit", "rateLimit": {"resetsAt": 1_800_000_000_000}}, (1_800_000_000.0, None)),
        ({"errorKind": "rate_limit", "rateLimit": {"resetsAt": True}}, None),
        ({"errorKind": "rate_limit", "rateLimit": {"resetsAt": 0}}, None),
        ({"errorKind": "overloaded", "rateLimit": {"resetsAt": 1_800_000_000}}, None),
        (None, None),
    ],
    ids=["seconds", "milliseconds", "bool", "zero", "other-kind", "no-data"],
)
def test_usage_limit_reset_reads_the_adapter_error_data(data, expected):
    exc = AcpRpcError(-32603, "You've hit your session limit", data)
    assert session_mod._usage_limit_reset(exc) == expected


class TestRequests:
    async def _permission(self, m: TimelineMirror) -> dict[str, Any]:
        await m.command("prompt", text="permission")
        await m.until(lambda: bool(m.state.get("requests")))
        return m.state["requests"][0]

    async def test_permission_waits_for_the_chosen_option(self, open_chat):
        m = await open_chat()
        request = await self._permission(m)
        await m.until(lambda: "awaiting_permission" in m.attention)

        assert request["kind"] == "permission"
        assert (request["title"], request["tool_kind"]) == ("rm -rf build/", "execute")
        assert request["subtitle"] == "rm -rf build/"
        assert [o["id"] for o in request["options"]] == ["allow", "always", "reject"]
        assert m.session.to_info().attention == "awaiting_permission"

        bad = await m.command("respond", request_id=request["id"], option_id="maybe")
        assert bad["error"] == "Unknown option"

        ok = await m.command("respond", request_id=request["id"], option_id="allow")
        assert ok["ok"] is True
        await m.until(_turn_done(m, "The build folder is gone."))

        assert m.items[request["tool_call_id"]]["status"] == "completed"
        assert m.state["requests"] == []
        late = await m.command("respond", request_id=request["id"], option_id="allow")
        assert late["error"] == "This request was already answered"

    async def test_rejected_permission_fails_the_tool(self, open_chat):
        m = await open_chat()
        request = await self._permission(m)

        await m.command("respond", request_id=request["id"], option_id="reject")
        await m.until(_turn_done(m, "OK, I won't delete it."))

        tool = m.items[request["tool_call_id"]]
        assert tool["status"] == "failed"
        assert tool["content"] == [{"type": "text", "text": "The user rejected the command"}]

    async def test_cancel_answers_a_pending_permission(self, open_chat):
        m = await open_chat()
        await self._permission(m)

        await m.command("cancel")
        await m.until(lambda: m.status == "ready" and bool(m.of_type("turn_end")))

        assert m.state["requests"] == []
        assert m.of_type("turn_end")[0]["stop_reason"] == "cancelled"
        assert m.attention[-1] is None

    async def test_form_validates_then_accepts(self, open_chat):
        m = await open_chat()
        await m.command("prompt", text="question")
        await m.until(lambda: bool(m.state.get("requests")))
        request = m.state["requests"][0]

        assert request["kind"] == "form"
        assert request["title"] == "How should I set up the project?"
        assert [f["key"] for f in request["fields"]] == ["framework", "extras", "name", "strict"]

        missing = await m.command("respond", request_id=request["id"], action="accept", values={})
        assert missing["error"] == "“Framework” is required"
        assert m.state["requests"] != []

        ok = await m.command(
            "respond",
            request_id=request["id"],
            action="accept",
            values={"framework": "react", "extras": ["lint"], "strict": False},
        )
        assert ok["ok"] is True
        await m.until(
            _turn_done(m, 'Got it: {"extras": ["lint"], "framework": "react", "strict": false}')
        )


# A prompt asks a permission (id 900) and a form question (id 901) and never
# ends. The agent sends ``$/cancel_request`` for 900 once the file
# ``<argv[1]>.permission`` exists, then for 901 once ``<argv[1]>.form``
# exists. Each reply is shown as agent text: "<id> <error code or answer>".
CANCELS_ITS_CARDS_AGENT = r"""
import json, os, sys, threading, time
release = sys.argv[1]
lock = threading.Lock()

def send(message):
    with lock:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        sys.stdout.flush()

def cancel_when_released():
    for suffix, req_id in ((".permission", 900), (".form", 901)):
        while not os.path.exists(release + suffix):
            time.sleep(0.02)
        send({"method": "$/cancel_request", "params": {"requestId": req_id}})

for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method is None:
        error = msg.get("error")
        answer = error["code"] if error else msg.get("result")
        send({"method": "session/update", "params": {"sessionId": "cards", "update": {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": f"{msg['id']} {json.dumps(answer)}\n"}}}})
        continue
    if "id" not in msg:
        continue
    if method == "initialize":
        result = {"protocolVersion": 1, "agentCapabilities": {}}
    elif method == "session/new":
        result = {"sessionId": "cards"}
    elif method == "session/prompt":
        send({"id": 900, "method": "session/request_permission", "params": {
            "sessionId": "cards",
            "toolCall": {"toolCallId": "t1", "title": "git push", "kind": "execute"},
            "options": [
                {"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                {"optionId": "reject", "name": "Reject", "kind": "reject_once"},
            ]}})
        send({"id": 901, "method": "elicitation/create", "params": {
            "sessionId": "cards", "mode": "form", "message": "Which branch?",
            "requestedSchema": {"type": "object", "properties": {
                "branch": {"type": "string", "title": "Branch"}}}}})
        threading.Thread(target=cancel_when_released, daemon=True).start()
        continue
    else:
        send({"id": msg["id"], "error": {"code": -32601, "message": "Method not found"}})
        continue
    send({"id": msg["id"], "result": result})
"""


def _cancels_its_cards_agent(release) -> list[str]:
    return [sys.executable, "-c", CANCELS_ITS_CARDS_AGENT, str(release)]


class TestAgentCancelsRequest:
    """``$/cancel_request`` from the agent: the card it names goes away."""

    async def _both_cards(self, m: TimelineMirror) -> dict[str, dict[str, Any]]:
        await m.command("prompt", text="go")
        await m.until(lambda: len(m.state.get("requests") or []) == 2)
        await m.until(lambda: "awaiting_permission" in m.attention)
        return {request["kind"]: request for request in m.state["requests"]}

    async def test_each_cancelled_card_goes_and_the_last_clears_the_alert(
        self, open_chat, tmp_path
    ):
        release = tmp_path / "release"
        m = await open_chat(argv=_cancels_its_cards_agent(release))
        cards = await self._both_cards(m)

        Path(f"{release}.permission").touch()
        await m.until(lambda: any("900 -32800" in text for text in m.texts("agent")))

        assert m.state["requests"] == [cards["form"]]
        assert m.session.to_info().attention == "awaiting_permission"

        Path(f"{release}.form").touch()
        await m.until(lambda: any("901 -32800" in text for text in m.texts("agent")))

        assert m.state["requests"] == []
        assert m.session.to_info().attention is None
        assert m.attention[-1] is None
        late = await m.command("respond", request_id=cards["form"]["id"], action="decline")
        assert late["error"] == "This request was already answered"

    async def test_an_answer_given_before_the_cancel_is_handled_wins(self, open_chat, tmp_path):
        m = await open_chat(argv=_cancels_its_cards_agent(tmp_path / "release"))
        cards = await self._both_cards(m)
        conn = m.session._conn
        assert conn is not None
        handler = conn._inbound[900]

        # In one loop tick: the answer resolves the card, then the agent's
        # cancel reaches the handler before it wakes up.
        await m.session.handle_command(
            orjson.dumps({"op": "respond", "request_id": cards["permission"]["id"],
                          "option_id": "allow"})
        )
        assert not handler.done()
        conn._dispatch(
            {"jsonrpc": "2.0", "method": "$/cancel_request", "params": {"requestId": 900}}
        )
        await m.until(lambda: any(text.startswith("900 ") for text in m.texts("agent")))

        agent_text = "".join(m.texts("agent"))
        assert '900 {"outcome": {"outcome": "selected", "optionId": "allow"}}' in agent_text
        assert "-32800" not in agent_text
        assert m.state["requests"] == [cards["form"]]

    async def test_a_local_cancel_after_the_answer_still_ends_the_handler(
        self, open_chat, tmp_path
    ):
        """Only the agent's ``$/cancel_request`` lets an answer win. Stop,
        restart and close cancel without that message and must still end the
        task, or the work they cancel goes on."""
        m = await open_chat(argv=_cancels_its_cards_agent(tmp_path / "release"))
        cards = await self._both_cards(m)
        conn = m.session._conn
        assert conn is not None
        handler = conn._inbound[900]

        await m.session.handle_command(
            orjson.dumps({"op": "respond", "request_id": cards["permission"]["id"],
                          "option_id": "allow"})
        )
        assert not handler.done()
        handler.cancel()
        await m.until(lambda: any(text.startswith("900 ") for text in m.texts("agent")))

        agent_text = "".join(m.texts("agent"))
        assert "900 -32800" in agent_text
        assert '"optionId": "allow"' not in agent_text


# Asks one permission whose only options are ``allow_always`` and
# ``reject_once``, then echoes the option the client chose.
NO_ALLOW_ONCE_AGENT = r"""
import json, sys
pending = None

def send(message):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method is None:
        if msg.get("id") == 900 and pending is not None:
            outcome = (msg.get("result") or {}).get("outcome") or {}
            send({"method": "session/update", "params": {"sessionId": "strict", "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "chose " + str(outcome.get("optionId"))}}}})
            send({"id": pending, "result": {"stopReason": "end_turn"}})
            pending = None
        continue
    if "id" not in msg:
        continue
    if method == "initialize":
        result = {"protocolVersion": 1, "agentCapabilities": {}}
    elif method == "session/new":
        result = {"sessionId": "strict"}
    elif method == "session/prompt":
        pending = msg["id"]
        send({"id": 900, "method": "session/request_permission", "params": {
            "sessionId": "strict",
            "toolCall": {"toolCallId": "t1", "title": "git push", "kind": "execute"},
            "options": [
                {"optionId": "always", "name": "Always allow", "kind": "allow_always"},
                {"optionId": "reject", "name": "Reject", "kind": "reject_once"},
            ]}})
        continue
    else:
        send({"id": msg["id"], "error": {"code": -32601, "message": "Method not found"}})
        continue
    send({"id": msg["id"], "result": result})
"""
NO_ALLOW_ONCE_AGENT_ARGV = [sys.executable, "-c", NO_ALLOW_ONCE_AGENT]


class TestAutoApprove:
    """OpenCode's auto-approve in the chat UI. ``opencode acp`` has no
    ``--auto``, so the session answers each permission request itself: with
    the one-time allow, never with a lasting rule, and without a request card
    or an alert for a question nobody has to answer."""

    async def test_a_permission_is_allowed_once_without_asking(self, open_chat):
        m = await open_chat(auto_approve=True)

        await m.command("prompt", text="permission")
        await m.until(_turn_done(m, "The build folder is gone."))

        assert not _asked(m.frames)
        assert "awaiting_permission" not in m.attention
        (tool,) = [t for t in m.of_type("tool") if t["title"] == "rm -rf build/"]
        assert tool["status"] == "completed"

    async def test_a_request_without_a_one_time_allow_is_still_asked(self, open_chat):
        m = await open_chat(argv=NO_ALLOW_ONCE_AGENT_ARGV, auto_approve=True)

        await m.command("prompt", text="push it")
        await m.until(lambda: bool(m.state.get("requests")))
        request = m.state["requests"][0]

        assert [o["id"] for o in request["options"]] == ["always", "reject"]
        await m.command("respond", request_id=request["id"], option_id="reject")
        await m.until(_turn_done(m, "chose reject"))


class TestConfig:
    async def test_set_config_changes_the_option(self, open_chat):
        m = await open_chat()

        assert (await m.command("set_config", config_id="model", value="fast"))["ok"] is True
        assert (await m.command("set_config", config_id="fast", value=True))["ok"] is True

        def values() -> tuple[Any, Any]:
            by_id = {o["id"]: o["value"] for o in m.state["config"]}
            return by_id["model"], by_id["fast"]

        await m.until(lambda: values() == ("fast", True))

    @pytest.mark.parametrize(
        ("config_id", "value", "error"),
        [
            ("model", "huge", "Invalid value"),
            ("fast", "yes", "Invalid value"),
            ("nope", "x", "Unknown setting"),
        ],
    )
    async def test_invalid_values_are_refused(self, open_chat, config_id, value, error):
        m = await open_chat()
        result = await m.command("set_config", config_id=config_id, value=value)
        assert result["error"] == error


class TestCrashAndRestart:
    async def test_crash_reports_the_exit_and_restart_reloads(self, open_chat):
        m = await open_chat()
        await m.command("prompt", text="crash now")
        await m.until(lambda: m.status == "error")
        await m.until(lambda: "ended" in m.attention)

        errors = m.texts("error")
        assert len(errors) == 1
        assert errors[0].startswith("The agent process exited with code 3")
        assert m.state["error"] == errors[0]
        assert m.session.to_info().exit_reason == "process_exited"
        refused = await m.command("prompt", text="hello?")
        assert refused["error"] == "The agent is not running. Use Restart to start it again."

        old_epoch = m.epoch
        restarted = await m.command("restart")
        assert restarted["ok"] is True
        await m.until(lambda: m.status == "ready" and m.state.get("title") == "Earlier session")

        assert m.epoch != old_epoch
        assert m.texts("user") == ["Can you check the login handler?"]
        assert m.of_type("error") == []
        assert "replay-edit" in m.items
        assert m.session.to_info().attention is None
        assert m.session.to_info().exit_reason is None

    async def test_restart_while_running_is_refused(self, open_chat):
        m = await open_chat()
        result = await m.command("restart")
        assert result["error"] == "The agent is running"

    async def test_second_restart_while_one_starts_is_refused(self, open_chat):
        """Two Restart taps in one tick (double tap, or two clients) must not
        start two agent processes."""
        m = await open_chat()
        await m.command("prompt", text="crash now")
        await m.until(lambda: m.status == "error")

        await m.session.handle_command(b'{"id": "r1", "op": "restart"}')
        await m.session.handle_command(b'{"id": "r2", "op": "restart"}')
        await m.until(lambda: {"r1", "r2"} <= m.results.keys())

        assert m.results["r1"]["ok"] is True
        assert m.results["r2"]["error"] == "The agent is already starting"
        await m.until(lambda: m.status == "ready" and m.state.get("title") == "Earlier session")
        assert _fake_agent_pids() == {m.session.pid}

    async def test_restart_keeps_the_config_values_in_use(self, open_chat):
        m = await open_chat()
        assert (await m.command("set_config", config_id="model", value="fast"))["ok"] is True
        assert (await m.command("set_config", config_id="effort", value="low"))["ok"] is True
        await m.command("prompt", text="crash now")
        await m.until(lambda: m.status == "error")

        await m.command("restart")
        await m.until(lambda: m.status == "ready" and m.state.get("title") == "Earlier session")

        values = {o["id"]: o["value"] for o in m.state["config"]}
        assert values == {"mode": "default", "model": "fast", "effort": "low", "fast": False}

    @pytest.mark.parametrize(
        ("flavor", "kind", "launch", "picked"),
        [
            (None, SessionKind.CLAUDE, ConfigIntent("plan", category="mode", required=True), "acceptEdits"),
            ("junie", SessionKind.JUNIE, ConfigIntent("plan", option_id="mode", required=True), "goal"),
        ],
        ids=["by-category", "by-option-id"],
    )
    async def test_restart_keeps_the_mode_the_user_picked_over_the_required_launch_mode(
        self, open_chat, flavor, kind, launch, picked
    ):
        """Plan mode must be in place when the chat starts. Once the user
        picks another mode, a Restart restores that pick, not Plan mode."""
        m = await open_chat(argv=fake_agent_argv(flavor), kind=kind, initial_config=[launch])

        def mode() -> Any:
            return {o["id"]: o["value"] for o in m.state.get("config") or []}.get("mode")

        await m.until(lambda: mode() == "plan")
        assert (await m.command("set_config", config_id="mode", value=picked))["ok"] is True
        await m.until(lambda: mode() == picked)
        await m.command("prompt", text="crash now")
        await m.until(lambda: m.status == "error")

        assert (await m.command("restart"))["ok"] is True
        await m.until(lambda: m.status == "ready" and m.state.get("title") == "Earlier session")

        assert mode() == picked

    async def test_a_restart_does_not_set_again_an_option_the_agent_saves_itself(
        self, open_chat
    ):
        """Junie saves Brave mode for all of its sessions, and a new process
        reads the saved value. Set again, a stale value would be saved. The
        fake agent starts each session at "brave-auto": a set shows there."""
        m = await open_chat(
            argv=fake_agent_argv("junie"),
            kind=SessionKind.JUNIE,
            initial_config=[ConfigIntent("plan", option_id="mode", required=True)],
            restore_skip_option_ids=(JUNIE_BRAVE_OPTION,),
        )
        set_brave = await m.command("set_config", config_id=JUNIE_BRAVE_OPTION, value="on")
        assert set_brave["ok"] is True
        assert (await m.command("set_config", config_id="effort", value="low"))["ok"] is True
        await m.command("prompt", text="crash now")
        await m.until(lambda: m.status == "error")

        assert (await m.command("restart"))["ok"] is True
        await m.until(lambda: m.status == "ready" and m.state.get("title") == "Earlier session")

        assert {o["id"]: o["value"] for o in m.state["config"]} == {
            "mode": "plan",
            "model": "v1:7:jetbrains-ai:claude-sonnet-5-5",
            "effort": "low",
            JUNIE_BRAVE_OPTION: "brave-auto",
        }

    async def test_initial_prompt_is_sent_once(self, open_chat):
        """A restart that opens a new session (no ``session/load``) must not
        send the wizard's first prompt again."""
        m = await open_chat(argv=MINI_AGENT_ARGV, initial_prompt="first")
        await m.until(_turn_done(m, "echo first"))
        await m.command("prompt", text="crash")
        await m.until(lambda: m.status == "error")

        assert (await m.command("restart"))["ok"] is True
        await m.until(lambda: m.status == "ready")
        await m.command("prompt", text="second")
        await m.until(_turn_done(m, "echo second"))

        assert m.texts("user") == ["first", "crash", "second"]
        assert m.texts("agent") == ["echo first", "echo second"]

    async def test_a_refused_sandbox_mode_fails_each_restart_until_the_agent_applies_it(
        self, open_chat, tmp_path
    ):
        """The agent's default (``agent``) allows more than the user chose: a
        Restart must retry the launch mode, not start with that default."""
        refuse = tmp_path / "refuse"
        refuse.touch()
        m = await open_chat(
            argv=_sandbox_agent(refuse),
            initial_config=[ConfigIntent("read-only", category="mode", required=True)],
        )

        message = "Could not set the read-only mode, so the session did not start"
        assert m.status == "error"
        assert m.state["error"].startswith(message)
        [first] = m.of_type("error")

        def failed_again(earlier: dict[str, Any]) -> bool:
            return any(item["id"] != earlier["id"] for item in m.of_type("error"))

        assert (await m.command("restart"))["ok"] is True
        await m.until(lambda: m.status == "ready" or failed_again(first))

        assert m.status == "error"
        assert m.state["error"].startswith(message)
        # Each start shows its own failure only, not the one before it.
        [second] = m.of_type("error")
        refused = await m.command("prompt", text="hello")
        assert refused["error"] == "The agent is not running. Use Restart to start it again."

        refuse.unlink()
        assert (await m.command("restart"))["ok"] is True
        await m.until(lambda: m.status == "ready" or failed_again(second))

        assert m.status == "ready"
        assert m.of_type("error") == []
        assert {o["id"]: o["value"] for o in m.state["config"]} == {"mode": "read-only"}

    async def test_a_restart_after_a_crash_keeps_the_sandbox_mode_required(
        self, open_chat, tmp_path
    ):
        refuse = tmp_path / "refuse"
        m = await open_chat(
            argv=_sandbox_agent(refuse),
            initial_config=[ConfigIntent("read-only", category="mode", required=True)],
        )
        assert m.status == "ready"
        assert {o["id"]: o["value"] for o in m.state["config"]} == {"mode": "read-only"}
        await m.command("prompt", text="crash")
        await m.until(lambda: m.status == "error")
        refuse.touch()

        assert (await m.command("restart"))["ok"] is True
        await m.until(lambda: m.status == "ready" or len(m.texts("error")) == 2)

        assert m.status == "error"
        assert m.state["error"].startswith(
            "Could not set the read-only mode, so the session did not start"
        )


class TestNewConversation:
    NOTICE = "Started a new conversation"

    async def _started_new(self, m: TimelineMirror, old_epoch: str | None) -> None:
        await m.until(lambda: m.epoch != old_epoch and m.status == "ready")

    async def test_starts_an_empty_conversation_with_the_settings_in_use(self, open_chat):
        m = await open_chat()
        assert (await m.command("set_config", config_id="model", value="fast"))["ok"] is True
        await m.command("prompt", text="hi")
        await m.until(_turn_done(m, "You said: hi"))
        old_epoch, old_id = m.epoch, m.state["acp_session_id"]
        old_pid = m.session.pid

        assert (await m.command("new_conversation"))["ok"] is True
        await self._started_new(m, old_epoch)

        assert m.state["acp_session_id"] not in (None, old_id)
        assert [item["type"] for item in m.items.values()] == ["notice"]
        assert _notices(m) == [self.NOTICE]
        assert {o["id"]: o["value"] for o in m.state["config"]}["model"] == "fast"
        assert m.session.pid != old_pid
        assert _fake_agent_pids() == {m.session.pid}

        await m.command("prompt", text="again")
        await m.until(_turn_done(m, "You said: again"))
        assert m.texts("user") == ["again"]

    STORED = (
        "Claude Code keeps the previous one on your PC. To open it again, "
        "use Continue a session in New session."
    )

    @pytest.mark.parametrize(
        ("argv", "load_session_id", "first_prompt", "stored"),
        [
            (None, None, None, False),
            (None, "conv-1", None, True),
            (MINI_AGENT_ARGV, None, "hi", False),
        ],
        ids=["nothing-stored-yet", "resumed-session", "agent-without-load-session"],
    )
    async def test_notice_always_shows_and_names_the_stored_conversation_only_when_kept(
        self, open_chat, argv, load_session_id, first_prompt, stored
    ):
        m = await open_chat(argv=argv, load_session_id=load_session_id)
        if first_prompt is not None:
            await m.command("prompt", text=first_prompt)
            await m.until(lambda: m.status == "ready" and bool(m.texts("agent")))
        old_epoch = m.epoch

        assert (await m.command("new_conversation"))["ok"] is True
        await self._started_new(m, old_epoch)

        notices = m.of_type("notice")
        assert [n["title"] for n in notices] == [self.NOTICE]
        assert notices[0]["severity"] == "info"
        assert notices[0]["text"] == (self.STORED if stored else "")
        assert m.texts("user") == []
        assert m.state.get("title") != "Earlier session"

    async def test_a_second_request_while_one_starts_is_refused(self, open_chat):
        """Two taps in one tick (double tap, or two clients) must not start
        two agent processes."""
        m = await open_chat()
        old_epoch = m.epoch

        await m.session.handle_command(b'{"id": "n1", "op": "new_conversation"}')
        await m.session.handle_command(b'{"id": "n2", "op": "new_conversation"}')
        await m.until(lambda: {"n1", "n2"} <= m.results.keys())

        assert m.results["n1"]["ok"] is True
        assert m.results["n2"]["error"] == "The agent is already starting"
        await self._started_new(m, old_epoch)
        assert _fake_agent_pids() == {m.session.pid}

    async def test_a_running_turn_stops_and_a_message_sent_meanwhile_goes_to_the_new_one(
        self, open_chat, monkeypatch
    ):
        m = await open_chat()
        await m.command("prompt", text="slow")
        await m.until(lambda: m.status == "running")
        old_epoch = m.epoch
        spawn = m.session._spawn_with_install
        queue_at_spawn: list[list[str]] = []

        async def note_queue_then_spawn() -> bool:
            queue_at_spawn.append([q["text"] for q in m.session.timeline.state["queue"]])
            return await spawn()

        monkeypatch.setattr(m.session, "_spawn_with_install", note_queue_then_spawn)

        await m.session.handle_command(b'{"id": "n1", "op": "new_conversation"}')
        # One loop turn: the command claims the transition before its first await.
        await asyncio.sleep(0)
        sent = await m.command("prompt", text="also this")
        await m.until(lambda: "n1" in m.results)

        assert sent["data"]["queued"] is True
        assert m.results["n1"]["ok"] is True
        await m.until(lambda: m.epoch != old_epoch and "You said: also this" in m.texts("agent"))
        await m.until(lambda: m.status == "ready")
        assert m.texts("user") == ["also this"]
        assert _fake_agent_pids() == {m.session.pid}
        # Clients can see (and remove) the message until the new conversation sends it.
        assert queue_at_spawn == [["also this"]]
        assert m.state["queue"] == []

    @pytest.mark.parametrize("before", ["idle", "crashed", "running"])
    @pytest.mark.parametrize(
        "trigger",
        [{"op": "new_conversation"}, {"op": "prompt", "text": "/clear"}],
        ids=["command", "slash-clear"],
    )
    async def test_a_message_in_the_same_loop_turn_as_the_request_goes_to_the_new_one(
        self, open_chat, trigger, before
    ):
        """A burst of frames (iOS after a reconnect) is handled with no yield
        in between: the message must not start on the closing process, go
        into its turn by Send now, or be refused after a crash."""
        m = await open_chat()
        if before == "crashed":
            await m.command("prompt", text="crash now")
            await m.until(lambda: m.status == "error")
        elif before == "running":
            await m.command("prompt", text="slow")
            await m.until(lambda: m.status == "running")
        old_epoch = m.epoch

        await m.session.handle_command(orjson.dumps({"id": "n1", **trigger}))
        await m.session.handle_command(b'{"id": "p1", "op": "prompt", "text": "also this"}')
        queue_id = m.session.timeline.state["queue"][0]["id"]
        await m.session.handle_command(orjson.dumps({"id": "s1", "op": "steer", "queue_id": queue_id}))
        await m.until(lambda: {"n1", "p1", "s1"} <= m.results.keys())

        assert m.results["n1"]["ok"] is True
        assert m.results["p1"]["data"] == {"queued": True, "queue_id": queue_id}
        assert m.results["s1"]["data"] == {"steered": False}
        await m.until(lambda: m.epoch != old_epoch and "You said: also this" in m.texts("agent"))
        await m.until(lambda: m.status == "ready")
        assert m.texts("user") == ["also this"]
        assert _fake_agent_pids() == {m.session.pid}

    async def test_a_turn_answered_just_before_the_request_leaves_the_message_to_the_new_one(
        self, open_chat, monkeypatch
    ):
        """The agent's answer is in, but its turn runs on only after the
        request and a message in the same burst: that turn must not send the
        message on the closing process."""
        m = await open_chat()
        request = AcpConnection.request
        answered = asyncio.Event()
        resume = asyncio.get_running_loop().create_future()

        async def hold_the_first_answer(conn, method, params, timeout=None):
            result = await request(conn, method, params, timeout)
            if method == "session/prompt" and not answered.is_set():
                answered.set()
                await resume
            return result

        monkeypatch.setattr(AcpConnection, "request", hold_the_first_answer)
        await m.command("prompt", text="hi")
        await asyncio.wait_for(answered.wait(), 20)
        old_epoch = m.epoch

        # The turn wakes up first: it is in the loop's queue before the request's task.
        resume.set_result(None)
        await m.session.handle_command(b'{"id": "n1", "op": "new_conversation"}')
        await m.session.handle_command(b'{"id": "p1", "op": "prompt", "text": "also this"}')
        await m.until(lambda: {"n1", "p1"} <= m.results.keys())

        assert m.results["n1"]["ok"] is True
        assert m.results["p1"]["data"]["queued"] is True
        await m.until(lambda: m.epoch != old_epoch and "You said: also this" in m.texts("agent"))
        await m.until(lambda: m.status == "ready")
        assert m.texts("user") == ["also this"]
        assert _fake_agent_pids() == {m.session.pid}

    async def test_a_failure_while_starting_offers_restart(self, open_chat, monkeypatch):
        m = await open_chat()
        spawn = m.session._spawn_with_install
        failures = [RuntimeError("spawn broke")]

        async def fail_once_then_spawn() -> bool:
            if failures:
                raise failures.pop()
            return await spawn()

        monkeypatch.setattr(m.session, "_spawn_with_install", fail_once_then_spawn)

        result = await m.command("new_conversation")

        message = "Could not start a new conversation. Use Restart to try again."
        assert result["ok"] is False
        await m.until(lambda: m.status == "error")
        assert m.state["error"] == message
        assert m.texts("error") == [message]

        assert (await m.command("restart"))["ok"] is True
        await m.until(lambda: m.status == "ready")
        await m.command("prompt", text="hi")
        await m.until(_turn_done(m, "You said: hi"))

    async def test_slash_clear_starts_a_new_conversation_instead_of_a_prompt(self, open_chat):
        m = await open_chat()
        await m.command("prompt", text="hi")
        await m.until(_turn_done(m, "You said: hi"))
        old_epoch, old_id = m.epoch, m.state["acp_session_id"]

        result = await m.command("prompt", text="/Clear")
        await self._started_new(m, old_epoch)

        assert result["data"] == {"queued": False, "queue_id": None}
        assert m.state["acp_session_id"] not in (None, old_id)
        assert m.texts("user") == []

    async def test_an_agent_with_its_own_clear_gets_slash_clear_as_a_prompt(self, open_chat):
        m = await open_chat(argv=OWN_CLEAR_AGENT_ARGV)
        await m.until(
            lambda: any(
                c.get("description") == "Clear the history" for c in m.state.get("commands") or []
            )
        )
        assert [c["name"] for c in m.state["commands"]] == ["clear"]
        old_epoch = m.epoch

        await m.command("prompt", text="/clear")
        await m.until(_turn_done(m, "echo /clear"))

        assert m.epoch == old_epoch
        assert m.texts("user") == ["/clear"]


class TestSyncAndStop:
    async def test_sync_replays_missed_events_or_sends_a_snapshot(self, open_chat):
        m = await open_chat()
        await m.command("prompt", text="hi")
        await m.until(_turn_done(m, "You said: hi"))
        session = m.session
        tl = session.timeline
        tl.flush()
        epoch, seq = tl.epoch, tl.seq
        await m.until(lambda: m.seq == seq)

        up_to_date = orjson.loads(session.sync_frame(epoch, seq))
        assert (up_to_date["t"], up_to_date["events"]) == ("events", [])

        missed = orjson.loads(session.sync_frame(epoch, seq - 2))
        assert [e["seq"] for e in missed["events"]] == [seq - 1, seq]

        snapshot = orjson.loads(session.sync_frame("another-epoch", 0))
        assert snapshot["t"] == "snapshot"
        assert {i["id"]: i for i in snapshot["items"]} == m.items

    async def test_stop_closes_the_agent(self, open_chat):
        m = await open_chat()
        pid = m.session.pid

        await m.session.stop()
        await m.until(lambda: "stopped" in m.session_events)

        assert m.session.state == SessionState.STOPPED
        assert m.status == "stopped"
        assert m.session.is_alive() is False
        assert not psutil.pid_exists(pid)


def _notices(m: TimelineMirror) -> list[str]:
    return [n.get("title", "") for n in m.of_type("notice")]


class TestAdapterInstall:
    SETUP = "Setting up the Claude Code chat on your PC"

    async def test_the_adapter_is_installed_before_the_first_start(self, open_chat):
        installs: list[int] = []

        def ensure() -> list[str]:
            installs.append(1)
            return fake_agent_argv()

        m = await open_chat(ensure_argv=ensure)

        assert m.status == "ready"
        assert installs == [1]
        assert self.SETUP not in _notices(m)
        assert m.session.pid is not None

    async def test_the_setup_notice_shows_while_installing_and_goes_away_after(self, open_chat):
        release = threading.Event()

        def ensure() -> list[str]:
            release.wait(20)
            return fake_agent_argv()

        m = await open_chat(ensure_argv=ensure, settle=False)
        try:
            await m.until(lambda: self.SETUP in _notices(m))
            assert m.status == "starting"
        finally:
            release.set()

        await m.until(lambda: m.status == "ready")

        assert self.SETUP not in _notices(m)

    async def test_each_start_shows_only_its_own_failure(self, open_chat):
        """A start that fails before the agent runs must not leave its error
        above the next try, nor above the chat once a try works."""
        outcomes: list[BaseException | None] = [
            AdapterInstallError("fail one"),
            AdapterInstallError("fail two"),
            None,
        ]

        def ensure() -> list[str]:
            failure = outcomes.pop(0)
            if failure is not None:
                raise failure
            return fake_agent_argv()

        m = await open_chat(ensure_argv=ensure)
        [first] = m.of_type("error")

        assert (await m.command("restart"))["ok"] is True
        await m.until(
            lambda: m.status == "error" and [i["id"] for i in m.of_type("error")] not in ([], [first["id"]])
        )
        assert len(m.of_type("error")) == 1

        assert (await m.command("restart"))["ok"] is True
        await m.until(lambda: m.status == "ready")
        assert m.of_type("error") == []

    @pytest.mark.parametrize(
        "failure",
        [
            AdapterInstallError(f"Could not download {CODEX_ADAPTER.spec}: E404"),
            RuntimeError("the adapter folder is locked"),
        ],
        ids=["install-error", "unexpected-error"],
    )
    async def test_a_failed_install_drops_the_setup_notice(self, open_chat, failure):
        def ensure() -> list[str]:
            raise failure

        m = await open_chat(ensure_argv=ensure)

        assert m.status == "error"
        assert self.SETUP not in _notices(m)
        assert len(m.texts("error")) == 1

    async def test_a_failed_install_shows_the_error_and_restart_tries_again(self, open_chat):
        outcomes: list[Any] = [
            AdapterInstallError(f"Could not download {CODEX_ADAPTER.spec}: E404"),
            fake_agent_argv(),
        ]

        def ensure() -> list[str]:
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        m = await open_chat(ensure_argv=ensure)

        message = f"Could not download {CODEX_ADAPTER.spec}: E404"
        assert m.status == "error"
        assert m.state["error"] == message
        assert m.texts("error") == [message]
        refused = await m.command("prompt", text="hello")
        assert refused["error"] == "The agent is not running. Use Restart to start it again."

        assert (await m.command("restart"))["ok"] is True
        await m.until(lambda: m.status == "ready")

        assert outcomes == []
        await m.command("prompt", text="hello")
        await m.until(_turn_done(m, "You said: hello"))

    async def test_an_unexpected_install_failure_also_offers_restart(self, open_chat):
        outcomes: list[Any] = [RuntimeError("the adapter folder is locked"), fake_agent_argv()]

        def ensure() -> list[str]:
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        m = await open_chat(ensure_argv=ensure)

        message = "Could not set up the chat: the adapter folder is locked"
        assert m.status == "error"
        assert m.state["error"] == message
        assert m.texts("error") == [message]

        assert (await m.command("restart"))["ok"] is True
        await m.until(lambda: m.status == "ready")

        assert outcomes == []


class TestSteering:
    async def test_send_now_puts_a_queued_message_into_the_running_turn(self, open_chat):
        m = await open_chat()
        assert m.state["steering"] is True
        await m.command("prompt", text="slow")
        await m.until(lambda: m.status == "running" and bool(m.of_type("agent")))
        queued = await m.command("prompt", text="also check the README")

        steered = await m.command("steer", queue_id=queued["data"]["queue_id"])

        assert steered["data"] == {"steered": True}
        await m.until(lambda: m.state["queue"] == [] and len(m.texts("user")) == 2)
        assert m.texts("user") == ["slow", "also check the README"]
        await m.until(
            lambda: any("(Noted mid-turn: also check the README)" in t for t in m.texts("agent"))
        )
        assert m.status == "running"

        await m.command("cancel")
        await m.until(lambda: m.status == "ready")
        # Steered, so never sent again as a prompt of its own.
        assert m.texts("user") == ["slow", "also check the README"]
        assert not any("You said:" in t for t in m.texts("agent"))

    async def test_an_agent_without_steering_keeps_the_message_queued(self, open_chat):
        m = await open_chat(argv=MINI_AGENT_ARGV)
        assert m.state["steering"] is False
        await m.command("prompt", text="hang")
        await m.until(lambda: "Working" in m.texts("agent"))
        queued = await m.command("prompt", text="later")
        queue_id = queued["data"]["queue_id"]

        refused = await m.command("steer", queue_id=queue_id)

        assert refused["error"] == "This agent cannot take a message during a turn"
        assert m.state["queue"] == [{"id": queue_id, "text": "later", "images": 0}]


    async def test_a_message_being_steered_is_neither_dequeued_nor_sent_by_the_queue(
        self, open_chat, tmp_path
    ):
        log = tmp_path / "agent.log"
        m = await open_chat(argv=_logging_agent(log))
        await m.command("prompt", text="work")
        await m.until(lambda: "Working" in m.texts("agent"))
        queued = await m.command("prompt", text="later")
        queue_id = queued["data"]["queue_id"]
        # The agent never answers: the steer request stays in flight.
        await m.session.handle_command(orjson.dumps({"id": "s1", "op": "steer", "queue_id": queue_id}))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 10
        while "_session/steering" not in _methods(log):
            assert loop.time() < deadline, _methods(log)
            await asyncio.sleep(0.02)

        refused = await m.command("dequeue", queue_id=queue_id)
        assert refused["error"] == "This message is being sent now"

        await m.command("cancel")
        await m.until(lambda: m.status == "ready")

        assert m.texts("user") == ["work"]
        assert _methods(log).count("session/prompt") == 1
        assert [q["id"] for q in m.state["queue"]] == [queue_id]
        assert "s1" not in m.results

    async def test_a_refused_send_now_after_the_turn_ended_sends_the_message_next(
        self, open_chat, tmp_path
    ):
        """The turn ended while Send now was in flight, so the queue skipped
        the message; the agent then refused it. It must not stay queued."""
        release = tmp_path / "release"
        m = await open_chat(argv=_late_steer_agent(release))
        await m.command("prompt", text="work")
        await m.until(lambda: "Working" in m.texts("agent"))
        queued = await m.command("prompt", text="later")
        queue_id = queued["data"]["queue_id"]

        await m.session.handle_command(orjson.dumps({"id": "s1", "op": "steer", "queue_id": queue_id}))
        await m.until(lambda: m.status == "ready")
        assert [q["id"] for q in m.state["queue"]] == [queue_id]
        assert "s1" not in m.results
        release.touch()

        await m.until(lambda: "s1" in m.results)

        assert m.results["s1"]["error"] == "No turn to steer"
        assert m.state["queue"] == []
        assert m.texts("user") == ["work", "later"]
        await m.until(_turn_done(m, "echo later"))


class TestExternalTurns:
    """Codex's shared app-server: a message typed into Codex on the PC runs
    a turn this chat did not start (``threadStatus`` in ``_meta``)."""

    async def _external_turn_running(self, m: TimelineMirror) -> None:
        await m.command("prompt", text="external")
        await m.until(_turn_done(m, "Sure. Watch for a message from your PC next."))
        await m.until(lambda: "Message sent from another app on your PC" in _notices(m))
        assert m.status == "running"

    async def test_a_message_sent_meanwhile_waits_for_the_other_turn(self, open_chat):
        m = await open_chat(argv=fake_agent_argv("codex"))
        await self._external_turn_running(m)

        queued = await m.command("prompt", text="hello")

        assert queued["data"]["queued"] is True
        await m.until(_turn_done(m, "You said: hello"))
        assert "This answer is for a message typed into Codex on the PC." in m.texts("agent")
        assert m.texts("user") == ["external", "hello"]
        assert m.state["queue"] == []

    async def test_the_other_turn_ending_makes_the_chat_ready(self, open_chat):
        m = await open_chat(argv=fake_agent_argv("codex"))
        await self._external_turn_running(m)

        await m.until(lambda: m.status == "ready")

        assert m.state["turn_started_at"] is None
        assert "This answer is for a message typed into Codex on the PC." in m.texts("agent")

    async def test_stop_cancels_the_other_turn(self, open_chat):
        m = await open_chat(argv=fake_agent_argv("codex"))
        await self._external_turn_running(m)

        cancelled = await m.command("cancel")

        assert cancelled["data"] == {"cancelled": True}
        await m.until(lambda: m.status == "ready" and m.state["cancelling"] is False)
        assert "This answer is for a message typed into Codex on the PC." not in m.texts("agent")

    async def test_a_stop_the_other_app_never_answers_frees_the_chat(self, open_chat, monkeypatch):
        """Its app-server went away: no idle status ever comes."""
        monkeypatch.setattr(session_mod, "CANCEL_GRACE", 0.5)
        m = await open_chat(argv=SHARED_HOST_AGENT_ARGV)
        await m.command("prompt", text="external")
        await m.until(
            lambda: m.status == "running" and "Message sent from another app on your PC" in _notices(m)
        )
        queued = await m.command("prompt", text="hello")
        assert queued["data"]["queued"] is True

        cancelled = await m.command("cancel")

        assert cancelled["data"] == {"cancelled": True}
        assert m.state["cancelling"] is True
        await m.until(_turn_done(m, "echo hello"))
        assert m.texts("user") == ["external", "hello"]
        assert m.state["cancelling"] is False
        assert m.state["queue"] == []

    async def test_a_turn_running_when_the_chat_opens_holds_the_first_message(self, open_chat):
        m = await open_chat(argv=[*SHARED_HOST_AGENT_ARGV, "--busy-at-start"], initial_prompt="hello")

        await m.until(
            lambda: m.status == "running" and [q["text"] for q in m.state["queue"]] == ["hello"]
        )
        assert "Message sent from another app on your PC" in _notices(m)
        assert m.texts("user") == []

        await m.until(_turn_done(m, "echo hello"))
        assert m.texts("user") == ["hello"]
        assert m.state["queue"] == []


class TestCodexAsyncQuestion:
    """codex-acp relays ``request_user_input_async`` as a form
    (``_meta.codex.delivery: "async"``) that does not hold the turn: the
    card shows while Codex works on, and goes when the turn ends."""

    async def test_the_card_shows_while_the_turn_runs_and_the_answer_goes_back(
        self, open_chat, monkeypatch
    ):
        monkeypatch.setenv("MOBAIDE_FAKE_ACP_ASYNC_WORK", "30")
        m = await open_chat(argv=fake_agent_argv("codex"))
        await m.command("prompt", text="async question")
        await m.until(lambda: bool(m.state.get("requests")))
        (request,) = m.state["requests"]

        assert request["kind"] == "form"
        assert [f["key"] for f in request["fields"]] == ["question_0", "question_0_note"]
        await m.until(lambda: "awaiting_permission" in m.attention)
        before = "".join(m.texts("agent")).count("Working")
        await m.until(lambda: "".join(m.texts("agent")).count("Working") > before)
        assert m.status == "running"

        ok = await m.command(
            "respond",
            request_id=request["id"],
            action="accept",
            values={"question_0": "Production", "question_0_note": "after the tests"},
        )

        assert ok["ok"] is True
        said = 'Using your answer: {"question_0": "Production", "question_0_note": "after the tests"}'
        await m.until(lambda: m.status == "ready" and _says(m, said))
        assert m.state["requests"] == []

    async def test_an_unanswered_card_is_withdrawn_when_the_turn_ends(self, open_chat, monkeypatch):
        monkeypatch.setenv("MOBAIDE_FAKE_ACP_ASYNC_WORK", "0.3")
        m = await open_chat(argv=fake_agent_argv("codex"))

        await m.command("prompt", text="async question")
        await m.until(lambda: m.status == "ready" and _says(m, "No answer came, so I used Staging."))

        assert _asked(m.frames)
        assert m.state["requests"] == []
        assert m.session.to_info().attention != "awaiting_permission"
        request_id = next(
            ev["patch"]["requests"][0]["id"]
            for f in m.frames
            if f["t"] == "events"
            for ev in f["events"]
            if ev["type"] == "state" and ev["patch"].get("requests")
        )
        late = await m.command("respond", request_id=request_id, action="decline")
        assert late["error"] == "This request was already answered"


def _sent_items(m: TimelineMirror, item_id: str) -> list[dict[str, Any]]:
    """Each full copy of the item the session sent, in order."""
    return [
        ev["item"]
        for f in m.frames
        if f["t"] == "events"
        for ev in f["events"]
        if ev["type"] == "item" and ev["item"]["id"] == item_id
    ]


_RUNNING_MAKE = {
    "sessionUpdate": "tool_call",
    "toolCallId": "make",
    "title": "make",
    "kind": "execute",
    "status": "pending",
}
_AGENT_TEXT = {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Still running."}}


def _agent_sends(m: TimelineMirror, update: dict[str, Any]) -> None:
    """Apply one ``session/update`` as if ``MINI_AGENT`` sent it."""
    m.session._on_notification("session/update", {"sessionId": "mini", "update": update})


def _whole_output(tool: str, text: str, **fields: Any) -> dict[str, Any]:
    """An update the way Copilot sends command output: all of it so far, as
    ``content``."""
    return {
        "sessionUpdate": "tool_call_update",
        "toolCallId": tool,
        **fields,
        "content": [{"type": "content", "content": {"type": "text", "text": text}}],
    }


class TestCommandOutput:
    async def test_terminal_output_becomes_one_console_part(self, open_chat, monkeypatch):
        monkeypatch.setattr(session_mod, "CONSOLE_RENDER_INTERVAL", 0.05)
        m = await open_chat(argv=fake_agent_argv("codex"))

        await m.command("prompt", text="tools")
        await m.until(_turn_done(m, "Done: I updated `main()` and the tests pass."))

        run = next(t for t in m.of_type("tool") if t["title"] == "pytest -q")
        assert run["status"] == "completed"
        assert run["content"] == [
            {
                "type": "text",
                "text": (
                    "```console\n"
                    "============ test session starts ============\n"
                    "collected 4 items\n"
                    "\n"
                    "tests/test_app.py ....\n"
                    "\n"
                    "4 passed in 0.12s\n"
                    "```"
                ),
            }
        ]
        sent = [
            ev["item"]
            for f in m.frames
            if f["t"] == "events"
            for ev in f["events"]
            if ev["type"] == "item" and ev["item"]["id"] == run["id"]
        ]
        assert all(p["type"] != "terminal" for item in sent for p in item["content"])
        running = [
            p["text"]
            for item in sent
            if item["status"] == "in_progress"
            for p in item["content"]
            if p["type"] == "text"
        ]
        assert any(
            text.startswith("```console\n============ test session starts") and "4 passed" not in text
            for text in running
        ), running

    async def test_output_alone_waits_for_the_render_and_other_changes_go_out_at_once(
        self, open_chat, monkeypatch
    ):
        """No render runs here before the command ends: only the start, the
        new title and the end re-send the tool, not each output chunk."""
        monkeypatch.setattr(session_mod, "CONSOLE_RENDER_INTERVAL", 3600.0)
        m = await open_chat(argv=CONSOLE_AGENT_ARGV)

        await m.command("prompt", text="retitle")
        await m.until(lambda: m.status == "ready" and m.items.get("make", {}).get("status") == "completed")

        sent = _sent_items(m, "make")
        assert [(item["status"], item["title"]) for item in sent] == [
            ("in_progress", "make"),
            ("in_progress", "make test"),
            ("completed", "make test"),
        ]
        [part] = m.items["make"]["content"]
        assert "line 0" in part["text"] and "done" in part["text"]

    async def test_whole_output_updates_wait_for_their_frame_and_other_changes_go_out_at_once(
        self, open_chat, monkeypatch
    ):
        """Copilot sends the whole output so far with each chunk. No frame is
        due here before the command ends: only the start, the new title and
        the end re-send the tool."""
        monkeypatch.setattr(session_mod, "CONSOLE_RENDER_INTERVAL", 3600.0)
        m = await open_chat(argv=MINI_AGENT_ARGV)
        _agent_sends(m, _RUNNING_MAKE)
        await m.until(lambda: "make" in m.items)

        _agent_sends(m, _whole_output("make", "line 0\n"))
        _agent_sends(m, _whole_output("make", "line 0\nline 1\n"))
        # A frame from the two updates above would come no later than this text.
        _agent_sends(m, _AGENT_TEXT)
        await m.until(lambda: _AGENT_TEXT["content"]["text"] in m.texts("agent"))
        assert len(_sent_items(m, "make")) == 1

        _agent_sends(m, _whole_output("make", "line 0\nline 1\nline 2\n", title="make test"))
        await m.until(lambda: m.items["make"]["title"] == "make test")
        _agent_sends(
            m,
            _whole_output(
                "make",
                "line 0\nline 1\nline 2\ndone\n<shellId: 0 completed with exit code 0>",
                status="completed",
            ),
        )
        await m.until(lambda: m.items["make"]["status"] == "completed")

        assert [(item["status"], item["title"], item["content"]) for item in _sent_items(m, "make")] == [
            ("pending", "make", []),
            ("pending", "make test", [{"type": "text", "text": "line 0\nline 1\nline 2\n"}]),
            (
                "completed",
                "make test",
                [{"type": "text", "text": "```console\nline 0\nline 1\nline 2\ndone\n```"}],
            ),
        ]

    async def test_whole_output_goes_out_one_frame_later_and_the_end_drops_a_frame_not_yet_sent(
        self, open_chat, monkeypatch
    ):
        # The frame of an output update is due on the next loop pass.
        monkeypatch.setattr(session_mod, "CONSOLE_RENDER_INTERVAL", 0.0)
        m = await open_chat(argv=MINI_AGENT_ARGV)
        _agent_sends(m, _RUNNING_MAKE)
        await m.until(lambda: "make" in m.items)

        _agent_sends(m, _whole_output("make", "line 0\n"))
        _agent_sends(m, _whole_output("make", "line 0\nline 1\n"))
        await m.until(lambda: bool(m.items["make"]["content"]))

        _agent_sends(m, _whole_output("make", "line 0\nline 1\nline 2\n"))
        _agent_sends(
            m,
            _whole_output(
                "make", "line 0\nline 1\nline 2\n<shellId: 0 completed with exit code 0>", status="completed"
            ),
        )
        # A frame the end did not cancel would come no later than this text.
        _agent_sends(m, _AGENT_TEXT)
        await m.until(lambda: _AGENT_TEXT["content"]["text"] in m.texts("agent"))

        assert [(item["status"], item["content"]) for item in _sent_items(m, "make")] == [
            ("pending", []),
            ("pending", [{"type": "text", "text": "line 0\nline 1\n"}]),
            ("completed", [{"type": "text", "text": "```console\nline 0\nline 1\nline 2\n```"}]),
        ]

    async def test_the_least_recently_updated_command_loses_its_output_first(
        self, open_chat, monkeypatch
    ):
        monkeypatch.setattr(session_mod, "CONSOLE_RENDER_INTERVAL", 3600.0)
        monkeypatch.setattr(session_mod, "CONSOLE_TOOLS_MAX", 2)
        m = await open_chat(argv=CONSOLE_AGENT_ARGV)

        await m.command("prompt", text="interleave")
        await m.until(
            lambda: m.status == "ready"
            and all(m.items.get(t, {}).get("status") == "completed" for t in ("a", "b", "c"))
        )

        def output(tool: str) -> str:
            return "".join(p.get("text", "") for p in m.items[tool]["content"])

        assert "a1\na2\na3" in output("a")
        assert "b1" not in output("b") and "b2" in output("b")


_CODE = 'await tools.bash({command: "make"});\n'
_SCRIPT_PART = {"type": "text", "text": '```js\nawait tools.bash({command: "make"});\n```'}


def _text_update(tool: str, text: str, **fields: Any) -> dict[str, Any]:
    return {
        "sessionUpdate": "tool_call_update",
        "toolCallId": tool,
        **fields,
        "content": [{"type": "content", "content": {"type": "text", "text": text}}],
    }


class TestCodeModeScripts:
    """A Code Mode call (Pi's ``codemode``, OpenCode's ``execute``) shows as a
    script: later updates do not repeat its input, so the session keeps it."""

    _START = {
        "sessionUpdate": "tool_call",
        "toolCallId": "cm-1",
        "title": "codemode",
        "kind": "other",
        "status": "in_progress",
        "rawInput": {"code": _CODE},
    }

    async def test_the_script_stays_below_the_output_of_each_later_update(self, open_chat):
        m = await open_chat(argv=MINI_AGENT_ARGV)
        _agent_sends(m, self._START)
        await m.until(lambda: "cm-1" in m.items)

        assert m.items["cm-1"]["title"] == "Run script"
        assert m.items["cm-1"]["subtitle"] == "Calls bash"
        assert m.items["cm-1"]["content"] == [_SCRIPT_PART]

        _agent_sends(m, _text_update("cm-1", "building", title="codemode"))
        _agent_sends(m, _text_update("cm-1", "built", status="completed"))
        await m.until(lambda: m.items["cm-1"]["status"] == "completed")

        tool = m.items["cm-1"]
        assert tool["title"] == "Run script"
        assert tool["input"] is None
        assert tool["content"] == [{"type": "text", "text": "built"}, _SCRIPT_PART]

    async def test_a_failed_script_shows_its_error_and_the_script(self, open_chat):
        m = await open_chat(argv=MINI_AGENT_ARGV)
        _agent_sends(m, self._START)
        await m.until(lambda: "cm-1" in m.items)

        _agent_sends(
            m,
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "cm-1",
                "status": "failed",
                "rawOutput": "ReferenceError: x is not defined",
            },
        )
        await m.until(lambda: m.items["cm-1"]["status"] == "failed")

        assert m.items["cm-1"]["content"] == [
            {"type": "text", "text": "ReferenceError: x is not defined"},
            _SCRIPT_PART,
        ]

    async def test_a_permission_request_for_a_script_shows_the_script(self, open_chat):
        m = await open_chat(argv=CLIENT_REQUEST_AGENT_ARGV)
        call = {
            "method": "session/request_permission",
            "params": {
                "sessionId": "asks",
                "toolCall": {
                    "toolCallId": "cm-2",
                    "title": "execute",
                    "name": "execute",
                    "kind": "other",
                    "rawInput": {"code": _CODE},
                },
                "options": [
                    {"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                    {"optionId": "reject", "name": "Reject", "kind": "reject_once"},
                ],
            },
        }

        await m.command("prompt", text=orjson.dumps(call).decode())
        await m.until(lambda: bool(m.state.get("requests")))

        request = m.state["requests"][0]
        assert (request["kind"], request["title"]) == ("permission", "Run script")
        assert request["subtitle"] == "Calls bash"
        assert request["content"] == [_SCRIPT_PART]


class TestSharedHost:
    @pytest.mark.parametrize(
        ("shared", "busy", "sent"),
        [
            (True, True, ["session/cancel"]),
            (True, False, []),
            (False, False, ["session/close"]),
        ],
        ids=["shared-own-turn", "shared-idle", "private"],
    )
    async def test_closing_the_chat(self, open_chat, tmp_path, shared, busy, sent):
        """On a shared host, ``session/close`` would also end the session for
        the other apps: only this chat's own turn is cancelled."""
        log = tmp_path / "agent.log"
        m = await open_chat(argv=_logging_agent(log), shared_host=shared)
        if busy:
            await m.command("prompt", text="work")
            await m.until(lambda: "Working" in m.texts("agent"))

        await m.session.stop()

        assert m.session.state == SessionState.STOPPED
        assert [x for x in _methods(log) if x in ("session/cancel", "session/close")] == sent

    async def test_a_new_conversation_lets_the_other_app_stop_the_own_turn_first(
        self, open_chat, tmp_path, monkeypatch
    ):
        """Closing this process does not stop the turn in the shared server:
        the agent must answer ``session/cancel`` before the old process closes."""
        log = tmp_path / "agent.log"
        m = await open_chat(argv=_logging_agent(log), shared_host=True)
        await m.command("prompt", text="work")
        await m.until(lambda: "Working" in m.texts("agent"))
        turn = m.session._turn_task
        old_epoch = m.epoch
        close = AcpConnection.close
        at_close: list[tuple[bool, list[str]]] = []

        async def note_turn_then_close(conn, grace=2.0):
            if not at_close:
                at_close.append((turn.done() and not turn.cancelled(), _methods(log)))
            await close(conn, grace)

        monkeypatch.setattr(AcpConnection, "close", note_turn_then_close)

        assert (await m.command("new_conversation"))["ok"] is True
        await m.until(lambda: m.epoch != old_epoch and m.status == "ready")

        ended_by_the_agent, methods = at_close[0]
        assert ended_by_the_agent
        assert "session/cancel" in methods
        assert "session/close" not in _methods(log)


# Logs each request as one JSON line ``{"method", "params"}`` to argv[1].
# argv[2] ``dirs`` advertises ``sessionCapabilities.additionalDirectories``.
PARAMS_AGENT = r"""
import json, sys
log = open(sys.argv[1], "a", encoding="utf-8")
session_caps = {"additionalDirectories": {}} if sys.argv[2] == "dirs" else {}

def send(message):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg or "method" not in msg:
        continue
    method = msg["method"]
    log.write(json.dumps({"method": method, "params": msg.get("params")}) + "\n")
    log.flush()
    if method == "initialize":
        caps = {"loadSession": True, "sessionCapabilities": session_caps}
        result = {"protocolVersion": 1, "agentCapabilities": caps}
    elif method == "session/new":
        result = {"sessionId": "params-1"}
    elif method == "session/load":
        result = {}
    else:
        send({"id": msg["id"], "error": {"code": -32601, "message": "Method not found"}})
        continue
    send({"id": msg["id"], "result": result})
"""


def _params_agent(log, *, advertises_dirs: bool) -> list[str]:
    return [sys.executable, "-c", PARAMS_AGENT, str(log), "dirs" if advertises_dirs else "none"]


def _setup_params(log) -> dict[str, Any]:
    for line in log.read_text(encoding="utf-8").splitlines():
        entry = orjson.loads(line)
        if entry["method"] in ("session/new", "session/load"):
            return entry["params"]
    raise AssertionError("the agent got neither session/new nor session/load")


class TestAdditionalDirectories:
    @pytest.mark.parametrize("load_session_id", [None, "conv-9"], ids=["new", "load"])
    async def test_extra_folders_go_to_an_agent_that_takes_them(
        self, open_chat, tmp_path, load_session_id
    ):
        log = tmp_path / "agent.log"

        m = await open_chat(
            load_session_id=load_session_id,
            argv=_params_agent(log, advertises_dirs=True),
            additional_dirs=["/srv/shared", "/srv/docs"],
        )

        assert m.status == "ready"
        params = _setup_params(log)
        assert params["cwd"] == str(tmp_path)
        assert params["additionalDirectories"] == ["/srv/shared", "/srv/docs"]


class TestAntigravityModel:
    @pytest.mark.parametrize(
        ("model", "values"),
        [
            (
                "Gemini 3.8 Flash (Low)",
                {"model": "gemini-3.8-flash", "thought_level": "low", "mode": "default"},
            ),
            ("Claude Sonnet 4.6 (Thinking)", {"model": "claude-sonnet-4-6", "mode": "default"}),
        ],
        ids=["model-with-level", "model-without-levels"],
    )
    async def test_the_wizard_model_name_sets_the_session_options(self, open_chat, model, values):
        m = await open_chat(
            argv=fake_agent_argv("antigravity"),
            initial_config=[ConfigIntent(model, category="model")],
            config_resolver=resolve_antigravity_config,
        )

        assert m.status == "ready"
        await m.until(lambda: {o["id"]: o["value"] for o in m.state["config"]} == values)


class TestCopilotChat:
    """A chat on ``copilot --acp``, against the fake agent's Copilot flavor."""

    AGENT_MODE = "https://agentclientprotocol.com/protocol/session-modes#agent"

    @staticmethod
    def _config(m: TimelineMirror) -> dict[str, Any]:
        return {o["id"]: o["value"] for o in m.state["config"]}

    async def test_allow_all_from_the_wizard_turns_on_the_session_option(self, open_chat):
        m = await open_chat(
            argv=fake_agent_argv("copilot"),
            initial_config=[ConfigIntent("on", option_id="allow_all")],
        )

        assert m.status == "ready"
        await m.until(lambda: self._config(m) == {"mode": self.AGENT_MODE, "allow_all": "on"})

    async def test_without_the_wizard_option_copilot_keeps_asking(self, open_chat):
        m = await open_chat(argv=fake_agent_argv("copilot"))

        assert m.status == "ready"
        await m.until(lambda: bool(m.state.get("config")))
        assert self._config(m) == {"mode": self.AGENT_MODE, "allow_all": "off"}

    async def test_a_finished_command_shows_its_output_without_the_shell_line(self, open_chat):
        m = await open_chat(argv=fake_agent_argv("copilot"))

        await m.command("prompt", text="tools")
        await m.until(_turn_done(m, "Done: I updated `main()` and the tests pass."))

        run = next(t for t in m.of_type("tool") if t["title"] == "Run the tests")
        assert run["status"] == "completed"
        assert run["content"] == [
            {"type": "text", "text": "```console\n....\n4 passed in 0.12s\n```"}
        ]
        sent = [
            p.get("text", "")
            for f in m.frames
            if f["t"] == "events"
            for ev in f["events"]
            if ev["type"] == "item" and ev["item"]["id"] == run["id"]
            for p in ev["item"]["content"]
        ]
        assert not any("shellId" in text for text in sent), sent


class TestJunieChat:
    """A chat on ``junie --acp=true``, against the fake agent's Junie flavor."""

    @staticmethod
    def _config(m: TimelineMirror) -> dict[str, Any]:
        return {o["id"]: o["value"] for o in m.state["config"]}

    async def test_plan_mode_from_the_wizard_leaves_brave_mode_alone(self, open_chat):
        """Junie's ``brave_mode`` option carries the category "mode"; Plan mode
        is its uncategorized ``mode`` option."""
        m = await open_chat(
            argv=fake_agent_argv("junie"),
            kind=SessionKind.JUNIE,
            initial_config=[ConfigIntent("plan", option_id="mode", required=True)],
        )

        assert m.status == "ready"
        await m.until(lambda: self._config(m).get("mode") == "plan")
        assert self._config(m)["brave_mode"] == "brave-auto"

    async def test_the_plan_review_is_asked_also_when_the_chat_accepts_every_request(
        self, open_chat
    ):
        """Brave mode through an operator command accepts the tool approvals;
        "Implement this plan?" stays the user's own choice."""
        m = await open_chat(
            argv=fake_agent_argv("junie"),
            kind=SessionKind.JUNIE,
            initial_config=[ConfigIntent("plan", option_id="mode", required=True)],
            auto_approve=True,
            decision_tool_prefixes=(JUNIE_PLAN_REVIEW_PREFIX,),
        )
        await m.until(lambda: self._config(m).get("mode") == "plan")

        await m.command("prompt", text="permission")
        await m.until(_turn_done(m, "The build folder is gone."))
        assert not _asked(m.frames)

        await m.command("prompt", text="plan")
        await m.until(lambda: bool(m.state.get("requests")))
        request = m.state["requests"][0]

        assert [o["id"] for o in request["options"]] == [
            "implement",
            "spawn-agentic-team",
            "revise",
            "stop",
        ]
        # The plan is a message of its own.
        assert request["content"] == []
        await m.until(lambda: "awaiting_permission" in m.attention)

        await m.command("respond", request_id=request["id"], option_id="stop")
        await m.until(lambda: m.status == "ready" and m.state["requests"] == [])

    async def test_the_plan_review_is_a_request_card_and_never_a_tool_card(self, open_chat):
        """Junie sends tool call updates for its "Implement this plan?" request,
        and after the work one more for a review id that no tool call
        announced. The request card shows the review; no tool card repeats it."""
        m = await open_chat(
            argv=fake_agent_argv("junie"),
            kind=SessionKind.JUNIE,
            initial_config=[ConfigIntent("plan", option_id="mode", required=True)],
            decision_tool_prefixes=(JUNIE_PLAN_REVIEW_PREFIX,),
        )
        await m.until(lambda: self._config(m).get("mode") == "plan")

        await m.command("prompt", text="plan")
        await m.until(lambda: bool(m.state.get("requests")))
        request = m.state["requests"][0]
        assert request["tool_call_id"].startswith(JUNIE_PLAN_REVIEW_PREFIX)
        assert len(request["options"]) == 4

        answer = await m.command("respond", request_id=request["id"], option_id="implement")
        assert answer["ok"] is True
        await m.until(_turn_done(m, "The login handler now checks for a missing user."))

        assert self._config(m)["mode"] == "default"
        sent = {
            item["id"]
            for f in m.frames
            for item in (
                f["items"]
                if f["t"] == "snapshot"
                else [ev["item"] for ev in f.get("events", []) if ev["type"] == "item"]
            )
        }
        assert {i for i in sent if i.startswith(JUNIE_PLAN_REVIEW_PREFIX)} == set()

    async def test_no_model_for_the_chat_shows_what_to_do(self, open_chat):
        m = await open_chat(
            argv=fake_agent_argv("junie"), kind=SessionKind.JUNIE, error_hints=JUNIE_ERROR_HINTS
        )

        await m.command("prompt", text="nomodel")
        await m.until(lambda: m.status == "ready" and bool(m.of_type("error")))

        assert m.texts("error") == [JUNIE_ERROR_HINTS["model_not_available"]]
        assert m.session.pid is not None

    async def test_an_error_without_a_hint_shows_the_agent_s_message(self, open_chat):
        m = await open_chat(argv=fake_agent_argv("junie"), kind=SessionKind.JUNIE)

        await m.command("prompt", text="nomodel")
        await m.until(lambda: m.status == "ready" and bool(m.of_type("error")))

        assert m.texts("error") == ["No models are currently available for submission"]


# Opens the stored session ``conv-9`` and lists it second, as "Fix the login
# handler". Logs each request as one JSON line ``{"method", "params"}`` to
# argv[1] and echoes prompts. argv[2] picks the rest: ``plain`` replays no
# title; ``replayed`` replays the title "Replayed title"; ``no-list`` does not
# advertise ``session/list``; ``late`` sends the title "Live title" just
# before its ``session/list`` answer.
STORED_TITLE_AGENT = r"""
import json, sys
log = open(sys.argv[1], "a", encoding="utf-8")
script = sys.argv[2]

def send(message):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    sys.stdout.flush()

def update(body):
    send({"method": "session/update", "params": {"sessionId": "conv-9", "update": body}})

for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg or "method" not in msg:
        continue
    method, params = msg["method"], msg.get("params") or {}
    log.write(json.dumps({"method": method, "params": params}) + "\n")
    log.flush()
    if method == "initialize":
        session_caps = {} if script == "no-list" else {"list": {}}
        caps = {"loadSession": True, "sessionCapabilities": session_caps}
        result = {"protocolVersion": 1, "agentCapabilities": caps}
    elif method == "session/load":
        update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Earlier answer."}})
        if script == "replayed":
            update({"sessionUpdate": "session_info_update", "title": "Replayed title"})
        result = {}
    elif method == "session/list":
        if script == "late":
            update({"sessionUpdate": "session_info_update", "title": "Live title"})
        result = {"sessions": [
            {"sessionId": "conv-8", "cwd": params.get("cwd"), "title": "Another session"},
            {"sessionId": "conv-9", "cwd": params.get("cwd"), "title": "Fix the login handler"}]}
    elif method == "session/prompt":
        text = " ".join(block.get("text", "") for block in params["prompt"])
        update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "echo " + text}})
        result = {"stopReason": "end_turn"}
    else:
        send({"id": msg["id"], "error": {"code": -32601, "message": "Method not found"}})
        continue
    send({"id": msg["id"], "result": result})
"""


def _stored_title_agent(log, script: str) -> list[str]:
    return [sys.executable, "-c", STORED_TITLE_AGENT, str(log), script]


def _params_of(log, method: str) -> list[dict[str, Any]]:
    """The params of each ``method`` request in a ``{"method", "params"}`` log."""
    entries = [orjson.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return [entry["params"] for entry in entries if entry["method"] == method]


class TestContinuedSessionTitle:
    """Copilot replays a stored session without its title: the chat takes the
    title from the agent's own session list."""

    async def test_a_replay_without_a_title_takes_it_from_the_session_list(
        self, open_chat, tmp_path
    ):
        log = tmp_path / "agent.log"

        m = await open_chat(load_session_id="conv-9", argv=_stored_title_agent(log, "plain"))
        await m.until(lambda: m.state.get("title") == "Fix the login handler")

        # The sessions list of the clients reads the row, not the chat state.
        assert m.session.to_info().title == "Fix the login handler"
        assert "title" in m.session_events
        assert _params_of(log, "session/list") == [{"cwd": str(tmp_path)}]

    @pytest.mark.parametrize(
        ("script", "title"),
        [("replayed", "Replayed title"), ("no-list", None)],
        ids=["title-in-the-replay", "agent-without-session-list"],
    )
    async def test_the_session_list_is_not_asked(self, open_chat, tmp_path, script, title):
        log = tmp_path / "agent.log"
        m = await open_chat(load_session_id="conv-9", argv=_stored_title_agent(log, script))

        # A lookup would reach the agent before this prompt.
        await m.command("prompt", text="hello")
        await m.until(_turn_done(m, "echo hello"))

        assert _params_of(log, "session/list") == []
        assert m.state.get("title") == title

    async def test_a_title_that_arrives_during_the_lookup_wins(self, open_chat, tmp_path):
        log = tmp_path / "agent.log"
        m = await open_chat(load_session_id="conv-9", argv=_stored_title_agent(log, "late"))

        # The lookup is answered before this turn ends.
        await m.command("prompt", text="hello")
        await m.until(_turn_done(m, "echo hello"))

        assert len(_params_of(log, "session/list")) == 1
        assert m.state["title"] == "Live title"
        assert m.session.to_info().title == "Live title"


# Sends the request in the prompt text (JSON ``{"method", "params"}``) to the
# client, then answers with the client's reply: ``{"result": …}`` or ``{"error":
# <code>}``. ``"updates"`` are sent first; ``"notify": true`` sends a
# notification and answers ``{"notified": true}``.
CLIENT_REQUEST_AGENT = r"""
import json, sys
pending = None

def send(message):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    sys.stdout.flush()

def update(body):
    send({"method": "session/update", "params": {"sessionId": "asks", "update": body}})

def end_turn(prompt_id, answer):
    update({"sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": json.dumps(answer, sort_keys=True)}})
    send({"id": prompt_id, "result": {"stopReason": "end_turn"}})

for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method is None:
        if msg.get("id") == 900 and pending is not None:
            answer = {"error": msg["error"]["code"]} if "error" in msg else {"result": msg.get("result")}
            end_turn(pending, answer)
            pending = None
        continue
    if "id" not in msg:
        continue
    if method == "initialize":
        result = {"protocolVersion": 1, "agentCapabilities": {}}
    elif method == "session/new":
        result = {"sessionId": "asks"}
    elif method == "session/prompt":
        request = json.loads(" ".join(block.get("text", "") for block in msg["params"]["prompt"]))
        for body in request.get("updates") or []:
            update(body)
        call = {"method": request["method"], "params": request.get("params") or {}}
        if request.get("notify"):
            send(call)
            end_turn(msg["id"], {"notified": True})
            continue
        pending = msg["id"]
        send({"id": 900, **call})
        continue
    else:
        send({"id": msg["id"], "error": {"code": -32601, "message": "Method not found"}})
        continue
    send({"id": msg["id"], "result": result})
"""
CLIENT_REQUEST_AGENT_ARGV = [sys.executable, "-c", CLIENT_REQUEST_AGENT]

# Cursor in Plan mode that refuses ``session/set_config_option`` (modes ``agent``,
# ``plan``). Prompt ``plan`` asks ``cursor/create_plan`` and ends the turn at the
# answer; ``plan, then wait`` stays open until ``session/cancel``. Any other
# prompt is echoed as ``sent: <text>``.
PLAN_MODE_AGENT = r"""
import json, sys
pending = None
wait = False
MODE = {"id": "mode", "name": "Mode", "category": "mode", "type": "select", "currentValue": "plan",
        "options": [{"value": "agent", "name": "Agent"}, {"value": "plan", "name": "Plan"}]}

def send(message):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method is None:
        if msg.get("id") == 900 and pending is not None and not wait:
            send({"id": pending, "result": {"stopReason": "end_turn"}})
            pending = None
        continue
    if method == "session/cancel":
        if pending is not None:
            send({"id": pending, "result": {"stopReason": "cancelled"}})
            pending = None
        continue
    if "id" not in msg:
        continue
    if method == "initialize":
        result = {"protocolVersion": 1, "agentCapabilities": {}}
    elif method == "session/new":
        result = {"sessionId": "plans", "configOptions": [MODE]}
    elif method == "session/set_config_option":
        send({"id": msg["id"], "error": {"code": -32603, "message": "The mode is locked"}})
        continue
    elif method == "session/prompt":
        text = " ".join(block.get("text", "") for block in msg["params"]["prompt"])
        if text.startswith("plan"):
            pending, wait = msg["id"], text != "plan"
            send({"id": 900, "method": "cursor/create_plan",
                  "params": {"name": "Cache", "plan": "1. Write it"}})
            continue
        send({"method": "session/update", "params": {"sessionId": "plans", "update": {
            "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "sent: " + text}}}})
        result = {"stopReason": "end_turn"}
    else:
        send({"id": msg["id"], "error": {"code": -32601, "message": "Method not found"}})
        continue
    send({"id": msg["id"], "result": result})
"""
PLAN_MODE_AGENT_ARGV = [sys.executable, "-c", PLAN_MODE_AGENT]

# Cursor in Plan mode with a slow mode change. Logs each method to argv[1]; while
# argv[2] exists, ``session/set_config_option`` goes unanswered. Prompt ``plan``
# asks ``cursor/create_plan`` and ends the turn at the answer; any other prompt
# is echoed as ``sent: <text>``.
SLOW_MODE_AGENT = r"""
import json, os, sys, time
log = open(sys.argv[1], "a", encoding="utf-8")
hold = sys.argv[2]
mode = "plan"
pending = None

def send(message):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    sys.stdout.flush()

def config():
    return [{"id": "mode", "name": "Mode", "category": "mode", "type": "select", "currentValue": mode,
             "options": [{"value": "agent", "name": "Agent"}, {"value": "plan", "name": "Plan"}]}]

for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method is None:
        if msg.get("id") == 900 and pending is not None:
            send({"id": pending, "result": {"stopReason": "end_turn"}})
            pending = None
        continue
    log.write(method + "\n")
    log.flush()
    if "id" not in msg:
        continue
    if method == "initialize":
        result = {"protocolVersion": 1, "agentCapabilities": {}}
    elif method == "session/new":
        result = {"sessionId": "slow", "configOptions": config()}
    elif method == "session/set_config_option":
        while os.path.exists(hold):
            time.sleep(0.02)
        mode = msg["params"]["value"]
        result = {"configOptions": config()}
    elif method == "session/prompt":
        text = " ".join(block.get("text", "") for block in msg["params"]["prompt"])
        if text == "plan":
            pending = msg["id"]
            send({"id": 900, "method": "cursor/create_plan",
                  "params": {"name": "Cache", "plan": "1. Write it"}})
            continue
        send({"method": "session/update", "params": {"sessionId": "slow", "update": {
            "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "sent: " + text}}}})
        result = {"stopReason": "end_turn"}
    else:
        send({"id": msg["id"], "error": {"code": -32601, "message": "Method not found"}})
        continue
    send({"id": msg["id"], "result": result})
"""


def _slow_mode_agent(log, hold) -> list[str]:
    return [sys.executable, "-c", SLOW_MODE_AGENT, str(log), str(hold)]

# Opens any stored session by replaying the session updates that argv[1]
# holds as a JSON list.
REPLAY_AGENT = r"""
import json, sys
updates = json.loads(sys.argv[1])

def send(message):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg or "method" not in msg:
        continue
    if msg["method"] == "initialize":
        result = {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}}
    elif msg["method"] == "session/load":
        for body in updates:
            send({"method": "session/update",
                  "params": {"sessionId": msg["params"]["sessionId"], "update": body}})
        result = {}
    else:
        send({"id": msg["id"], "error": {"code": -32601, "message": "Method not found"}})
        continue
    send({"id": msg["id"], "result": result})
"""


def _replay_agent(updates: list[dict[str, Any]]) -> list[str]:
    return [sys.executable, "-c", REPLAY_AGENT, orjson.dumps(updates).decode()]


class TestCursorChat:
    """A chat on ``cursor-agent acp``, against the fake agent's Cursor flavor:
    its command output in ``rawOutput``, and its own ``cursor/…`` requests as
    the chat's form, permission request and plan. In Plan mode Cursor only
    stores an accepted plan, so there the plan's card can also start the work."""

    PLAN_TEXT = (
        "The login handler fails for a user without a profile.\n\n"
        "## Steps\n\n1. Read the code\n2. Write the fix\n3. Run the tests"
    )
    STEPS = ["Read the code", "Write the fix", "Run the tests"]

    @staticmethod
    async def _request(m: TimelineMirror, prompt: str) -> dict[str, Any]:
        await m.command("prompt", text=prompt)
        await m.until(lambda: bool(m.state.get("requests")))
        return m.state["requests"][0]

    @staticmethod
    def _call(method: str, params: dict[str, Any], **fields: Any) -> str:
        """A prompt for ``CLIENT_REQUEST_AGENT``."""
        return orjson.dumps({"method": method, "params": params, **fields}).decode()

    async def _answer_of(
        self, m: TimelineMirror, method: str, params: dict[str, Any], **fields: Any
    ) -> dict[str, Any]:
        """What the chat answers to one request of ``CLIENT_REQUEST_AGENT``."""
        await m.command("prompt", text=self._call(method, params, **fields))
        await m.until(lambda: m.status == "ready" and bool(m.texts("agent")))
        return orjson.loads(m.texts("agent")[-1])

    def _plan(self, m: TimelineMirror) -> list[tuple[str, str]]:
        return [(entry["content"], entry["status"]) for entry in m.state.get("plan") or []]

    @staticmethod
    def _mode(m: TimelineMirror) -> str | None:
        return next((o["value"] for o in m.state.get("config") or [] if o["id"] == "mode"), None)

    @staticmethod
    def _ever_queued(m: TimelineMirror) -> list[str]:
        """The text of each message that the queue state has shown."""
        return [
            queued["text"]
            for frame in m.frames
            if frame["t"] == "events"
            for ev in frame["events"]
            if ev["type"] == "state"
            for queued in ev["patch"].get("queue") or []
        ]

    async def _open_in_plan_mode(self, open_chat, **plan_fields: Any) -> TimelineMirror:
        m = await open_chat(argv=fake_agent_argv("cursor"), kind=SessionKind.CURSOR, **plan_fields)
        await m.until(lambda: bool(m.state.get("config")))
        assert (await m.command("set_config", config_id="mode", value="plan"))["ok"] is True
        await m.until(lambda: self._mode(m) == "plan")
        return m

    async def test_bootstrap_reports_the_options_of_cursor(self, open_chat):
        """Cursor sends no ``agentInfo``, and the settings of a model only
        once that model is picked."""
        m = await open_chat(argv=fake_agent_argv("cursor"), kind=SessionKind.CURSOR)

        assert m.status == "ready"
        await m.until(lambda: bool(m.state.get("config")))
        assert {o["id"]: o["value"] for o in m.state["config"]} == {
            "mode": "agent",
            "model": "default",
        }
        assert m.session.to_info().kind is SessionKind.CURSOR

    async def test_a_finished_command_shows_the_output_of_its_raw_output(self, open_chat, tmp_path):
        m = await open_chat(argv=fake_agent_argv("cursor"), markdown_titles=True)

        await m.command("prompt", text="tools")
        await m.until(_turn_done(m, "Done: I updated `main()` and the tests pass."))

        # Cursor writes the command as a Markdown code span.
        run = next(t for t in m.of_type("tool") if t["kind"] == "execute")
        assert run["title"] == "pytest -q"
        assert run["status"] == "completed"
        assert run["content"] == [
            {"type": "text", "text": "```console\n....\n4 passed in 0.12s\n```"}
        ]

    async def test_a_new_file_is_a_diff_without_the_leaked_file_lines(self, open_chat, tmp_path):
        m = await open_chat(argv=fake_agent_argv("cursor"), markdown_titles=True)

        await m.command("prompt", text="tools")
        await m.until(_turn_done(m, "Done: I updated `main()` and the tests pass."))

        # The title names the file; its whole path is the subtitle.
        notes = os.path.join(str(tmp_path), "notes.txt")
        create = next(t for t in m.of_type("tool") if t["title"] == "Edit notes.txt")
        assert (create["subtitle"], create["status"]) == (notes, "completed")
        (diff,) = create["content"]
        assert (diff["type"], diff["path"], diff["new_file"]) == ("diff", notes, True)
        assert (diff["added"], diff["removed"]) == (1, 0)
        assert "The tests pass." in diff["diff"]
        assert "/dev/null" not in diff["diff"] and "++ b/" not in diff["diff"]

    async def test_a_question_is_a_form_and_its_answer_goes_back_per_question(self, open_chat):
        m = await open_chat(argv=fake_agent_argv("cursor"))
        request = await self._request(m, "question")
        await m.until(lambda: "awaiting_permission" in m.attention)

        assert request["kind"] == "form"
        assert request["title"] == "How should I set up the project?"
        assert [(f["key"], f["title"], f["type"]) for f in request["fields"]] == [
            ("framework", "Which framework?", "select"),
            ("extras", "Which extras?", "multiselect"),
        ]
        assert [o["value"] for o in request["fields"][0]["options"]] == ["react", "vue"]

        ok = await m.command(
            "respond",
            request_id=request["id"],
            action="accept",
            values={"framework": "vue", "extras": ["lint", "tests"]},
        )
        assert ok["ok"] is True
        # Each answer is a list of option ids, also for a single choice.
        await m.until(_turn_done(m, 'Got it: {"extras": ["lint", "tests"], "framework": ["vue"]}'))
        assert m.state["requests"] == []

    @pytest.mark.parametrize(
        ("answer", "said"),
        [
            ({"action": "accept", "values": {}}, "skipped"),
            ({"action": "decline"}, "skipped"),
        ],
        ids=["nothing-selected", "declined"],
    )
    async def test_a_question_nobody_answered_is_skipped(self, open_chat, answer, said):
        m = await open_chat(argv=fake_agent_argv("cursor"))
        request = await self._request(m, "question")

        ok = await m.command("respond", request_id=request["id"], **answer)

        assert ok["ok"] is True
        await m.until(_turn_done(m, f"No problem — the questions were {said}."))

    async def test_a_plan_is_a_message_and_a_request_to_accept_it(self, open_chat):
        m = await open_chat(argv=fake_agent_argv("cursor"))
        request = await self._request(m, "plan")
        await m.until(lambda: "awaiting_permission" in m.attention)

        assert request["kind"] == "permission"
        assert request["title"] == "Plan: Fix the login"
        assert [(o["id"], o["name"]) for o in request["options"]] == [
            ("accept", "Accept plan"),
            ("reject", "Reject plan"),
        ]
        # The plan stays in the chat after the answer: it is not in the card.
        assert self.PLAN_TEXT in m.texts("agent")
        assert self._plan(m) == [(step, "pending") for step in self.STEPS]

        ok = await m.command("respond", request_id=request["id"], option_id="accept")
        assert ok["ok"] is True
        await m.until(_turn_done(m, "All three steps are done."))

        assert m.items[request["tool_call_id"]]["status"] == "completed"
        # Each ``cursor/update_todos`` named only the tasks that changed.
        assert self._plan(m) == [(step, "completed") for step in self.STEPS]
        assert self.PLAN_TEXT in m.texts("agent")
        assert m.state["requests"] == []

    async def test_a_plan_card_holds_no_text_and_the_plan_lists_the_tasks_of_its_phases(
        self, open_chat
    ):
        m = await open_chat(argv=CLIENT_REQUEST_AGENT_ARGV)
        # Cursor writes its progress over the content of the plan's tool call.
        progress = {
            "sessionUpdate": "tool_call",
            "toolCallId": "plan-1",
            "title": "Create Plan",
            "kind": "other",
            "status": "pending",
            "content": [{"type": "content", "content": {"type": "text", "text": "Reading the code"}}],
            # The plan is in the tool's input too: only a replay shows it from there.
            "rawInput": {
                "_toolName": "createPlan",
                "name": "Cache",
                "overview": "Add a cache.",
                "plan": "1. Write it\n2. Test it",
            },
        }
        params = {
            "toolCallId": "plan-1",
            "name": "Cache",
            "overview": "Add a cache.",
            "plan": "1. Write it\n2. Test it",
            "todos": [{"id": "a", "content": "Write the cache", "status": "pending"}],
            "phases": [
                {"name": "Checks", "todos": [{"id": "b", "content": "Run the tests", "status": "pending"}]},
                {"name": "Ship", "todos": [{"id": "c", "content": "Open the PR", "status": "pending"}]},
            ],
        }

        request = await self._request(m, self._call("cursor/create_plan", params, updates=[progress]))

        assert (request["kind"], request["title"]) == ("permission", "Plan: Cache")
        assert request["content"] == []
        assert m.items["plan-1"]["content"] == [{"type": "text", "text": "Reading the code"}]
        assert m.texts("agent") == ["Add a cache.\n\n1. Write it\n2. Test it"]

        await m.command("respond", request_id=request["id"], option_id="accept")
        await m.until(lambda: m.status == "ready" and len(m.texts("agent")) == 2)
        assert orjson.loads(m.texts("agent")[-1]) == {"result": {"outcome": {"outcome": "accepted"}}}

        # An update that names one task of a phase changes it in the plan's list.
        merge = {"merge": True, "todos": [{"id": "b", "content": "", "status": "completed"}]}
        await m.command("prompt", text=self._call("cursor/update_todos", merge))
        expected = [
            ("Write the cache", "pending"),
            ("Run the tests", "completed"),
            ("Open the PR", "pending"),
        ]
        await m.until(lambda: m.status == "ready" and self._plan(m) == expected)

    async def test_a_long_plan_is_cut_in_its_message_with_the_chat_s_mark(self, open_chat):
        m = await open_chat(argv=CLIENT_REQUEST_AGENT_ARGV)
        steps = "1. First step\n" + "x" * 40_000 + "\n9. Last step"
        params = {"name": "Big", "overview": "Add a cache.", "plan": steps}

        request = await self._request(m, self._call("cursor/create_plan", params))

        (shown,) = m.texts("agent")
        assert shown.startswith("Add a cache.\n\n1. First step\n")
        assert shown.endswith("\n9. Last step")
        assert "characters not shown" in shown
        assert len(shown) < 40_000

        await m.command("respond", request_id=request["id"], option_id="reject")
        await m.until(lambda: m.status == "ready" and m.state["requests"] == [])

    async def test_a_rejected_plan_is_dropped(self, open_chat):
        m = await open_chat(argv=fake_agent_argv("cursor"))
        request = await self._request(m, "plan")

        await m.command("respond", request_id=request["id"], option_id="reject")
        await m.until(_turn_done(m, "OK, I dropped the plan."))

        assert m.items[request["tool_call_id"]]["status"] == "failed"
        # The tasks of a plan that was not accepted are not a plan.
        assert self._plan(m) == []

    async def test_a_plan_is_asked_also_when_the_chat_accepts_every_request(self, open_chat):
        """Accepting a plan is the user's own choice, unlike a tool approval."""
        m = await open_chat(argv=fake_agent_argv("cursor"), auto_approve=True)

        await m.command("prompt", text="permission")
        await m.until(_turn_done(m, "The build folder is gone."))
        assert not _asked(m.frames)

        request = await self._request(m, "plan")
        assert [o["id"] for o in request["options"]] == ["accept", "reject"]
        await m.until(lambda: "awaiting_permission" in m.attention)

        await m.command("respond", request_id=request["id"], option_id="reject")
        await m.until(_turn_done(m, "OK, I dropped the plan."))

    async def test_cancel_answers_a_pending_plan(self, open_chat):
        m = await open_chat(argv=fake_agent_argv("cursor"))
        await self._request(m, "plan")

        await m.command("cancel")
        await m.until(lambda: m.status == "ready" and bool(m.of_type("turn_end")))

        assert m.state["requests"] == []
        assert m.attention[-1] is None
        assert self._plan(m) == []

    async def test_accept_and_start_in_plan_mode_does_the_work_in_agent_mode(self, open_chat):
        m = await self._open_in_plan_mode(open_chat)
        request = await self._request(m, "plan")

        assert [o["id"] for o in request["options"]] == ["start", "accept", "reject"]

        ok = await m.command("respond", request_id=request["id"], option_id="start")
        assert ok["ok"] is True
        await m.until(_turn_done(m, "All three steps are done."))

        # The chat sent the next message itself, after the turn of the plan.
        assert m.texts("user") == ["plan", "Implement the plan."]
        assert self._mode(m) == "agent"
        assert m.items[request["tool_call_id"]]["status"] == "completed"
        assert self._plan(m) == [(step, "completed") for step in self.STEPS]
        assert m.state["queue"] == []

    async def test_accept_in_plan_mode_only_stores_the_plan(self, open_chat):
        m = await self._open_in_plan_mode(open_chat)
        request = await self._request(m, "plan")
        tool_id = request["tool_call_id"]

        await m.command("respond", request_id=request["id"], option_id="accept")
        await m.until(lambda: m.status == "ready" and bool(m.items[tool_id]["content"]))

        assert m.items[tool_id]["status"] == "completed"
        assert m.texts("user") == ["plan"]
        # A start message would be in the queue before the turn ends.
        assert self._ever_queued(m) == []
        assert self._mode(m) == "plan"
        assert self._plan(m) == [(step, "pending") for step in self.STEPS]

    async def test_the_start_message_waits_in_the_queue_where_it_can_be_removed(self, open_chat):
        m = await open_chat(argv=PLAN_MODE_AGENT_ARGV, kind=SessionKind.CURSOR)
        request = await self._request(m, "plan, then wait")

        await m.command("respond", request_id=request["id"], option_id="start")
        await m.until(lambda: bool(m.state.get("queue")))
        (queued,) = m.state["queue"]
        assert (queued["text"], queued["images"]) == ("Implement the plan.", 0)

        assert (await m.command("dequeue", queue_id=queued["id"]))["ok"] is True
        await m.command("cancel")
        await m.until(lambda: m.status == "ready" and bool(m.of_type("turn_end")))

        assert m.texts("user") == ["plan, then wait"]
        assert m.state["queue"] == []
        assert self._mode(m) == "plan"

    async def test_a_mode_that_cannot_be_switched_stops_the_start_message(self, open_chat):
        """In Plan mode Cursor would answer the message with another plan."""
        m = await open_chat(argv=PLAN_MODE_AGENT_ARGV, kind=SessionKind.CURSOR)
        request = await self._request(m, "plan")

        await m.command("respond", request_id=request["id"], option_id="start")
        await m.until(lambda: m.status == "ready" and bool(m.of_type("error")))

        assert m.texts("error") == ["Could not switch the mode, so the message was not sent."]
        assert m.texts("user") == ["plan", "Implement the plan."]
        # The agent echoes each message it gets: only the plan is there.
        assert m.texts("agent") == ["1. Write it"]
        assert self._mode(m) == "plan"
        assert m.state["queue"] == []

        # The chat still works: the next message goes out as it is.
        await m.command("prompt", text="hello")
        await m.until(_turn_done(m, "sent: hello"))

    async def test_a_stop_while_the_mode_switches_never_sends_the_start_message(
        self, open_chat, tmp_path
    ):
        """The Stop finds no prompt at the agent to cancel, so the turn itself
        must drop the message when the mode is switched at last."""
        log, hold = tmp_path / "agent.log", tmp_path / "hold"
        m = await open_chat(argv=_slow_mode_agent(log, hold), kind=SessionKind.CURSOR)
        request = await self._request(m, "plan")
        hold.touch()

        await m.command("respond", request_id=request["id"], option_id="start")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 10
        while "session/set_config_option" not in _methods(log):
            assert loop.time() < deadline, _methods(log)
            await asyncio.sleep(0.02)
        assert (await m.command("cancel"))["data"] == {"cancelled": True}
        hold.unlink()
        await m.until(lambda: m.status == "ready" and bool(m.of_type("turn_end")))

        (end,) = m.of_type("turn_end")
        assert (end["stop_reason"], end["text"]) == ("cancelled", "Stopped")
        assert m.texts("user") == ["plan", "Implement the plan."]
        assert m.state["cancelling"] is False
        assert m.state["queue"] == []
        # The switch itself went through before the turn saw the Stop.
        assert self._mode(m) == "agent"

        # The chat still works, and the stopped message never reached the agent.
        await m.command("prompt", text="hello")
        await m.until(_turn_done(m, "sent: hello"))
        assert m.texts("agent") == ["1. Write it", "sent: hello"]
        assert _methods(log).count("session/prompt") == 2

    async def test_a_start_message_refused_for_the_sign_in_is_sent_in_agent_mode_later(
        self, open_chat, tmp_path, monkeypatch
    ):
        """The message that goes out after the sign-in is still the start
        message: it switches to Agent mode, whatever the mode is by then."""
        marker = tmp_path / "signed-out"
        monkeypatch.setenv(FAKE_SIGNED_OUT_ENV, str(marker))
        m = await self._open_in_plan_mode(
            open_chat, sign_in=cursor_sign_in_methods(None, os.environ, fake=True)
        )
        request = await self._request(m, "plan")
        marker.write_text("", encoding="utf-8")

        await m.command("respond", request_id=request["id"], option_id="start")
        await m.until(lambda: m.status == "ready" and _sign_in(m) is not None)
        assert self._mode(m) == "agent"
        # The user goes back to Plan mode while the message waits.
        assert (await m.command("set_config", config_id="mode", value="plan"))["ok"] is True
        await m.until(lambda: self._mode(m) == "plan")
        assert (await m.command("sign_in", action="start", method="cursor"))["ok"] is True
        await m.until(_turn_done(m, "All three steps are done."))

        assert not marker.exists()
        assert self._mode(m) == "agent"
        assert m.texts("user").count("Implement the plan.") == 1
        assert m.state["queue"] == []

    async def test_a_replayed_plan_is_shown_by_its_name_with_its_text(self, open_chat):
        """Cursor replays a plan as its ``createPlan`` tool call only."""
        m = await open_chat(
            load_session_id="external-1111",
            argv=fake_agent_argv("cursor"),
            kind=SessionKind.CURSOR,
        )
        await m.until(lambda: m.state.get("title") == "Earlier session")

        tool = m.items["replay-plan"]
        assert (tool["title"], tool["kind"], tool["status"]) == (
            "Plan: Fix the login",
            "think",
            "completed",
        )
        assert m.texts("agent") == [
            "Sure — I read the handler and fixed the null check.",
            "## Steps\n\n1. Read the code\n2. Write the fix",
        ]
        # The plan was answered in the stored session: nothing is asked again.
        assert not _asked(m.frames)

    async def test_a_replayed_plan_with_a_later_update_is_shown_once(self, open_chat):
        raw_input = {"_toolName": "createPlan", "name": "Cache", "plan": "1. Write it"}
        replay = [
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "plan-1",
                "title": "Create Plan: Cache",
                "kind": "other",
                "status": "pending",
                "rawInput": raw_input,
            },
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "plan-1",
                "status": "completed",
                "rawInput": raw_input,
            },
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "The plan is saved."},
            },
        ]

        m = await open_chat(load_session_id="conv-1", argv=_replay_agent(replay))
        await m.until(lambda: m.status == "ready")

        tool = m.items["plan-1"]
        assert (tool["title"], tool["kind"], tool["status"]) == ("Plan: Cache", "think", "completed")
        assert m.texts("agent") == ["1. Write it", "The plan is saved."]

    async def test_a_replayed_plan_with_a_long_name_has_a_title_of_one_cut_line(self, open_chat):
        """The name of a stored plan is the agent's own text, so its title
        goes the way of a live title."""
        name = "Cache `get()`\nfor every user " + "x" * 300
        replay = [
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "plan-1",
                "title": "Create Plan",
                "kind": "other",
                "status": "completed",
                "rawInput": {"_toolName": "createPlan", "name": name, "plan": "1. Write it"},
            },
        ]

        m = await open_chat(
            load_session_id="conv-1", argv=_replay_agent(replay), markdown_titles=True
        )
        await m.until(lambda: m.status == "ready")

        tool = m.items["plan-1"]
        shown = ("Plan: Cache get() for every user " + "x" * 300)[:199] + "…"
        assert (tool["title"], tool["kind"]) == (shown, "think")
        assert m.texts("agent") == ["1. Write it"]

    async def test_the_task_list_replaces_or_merges_by_id(self, open_chat):
        m = await open_chat(argv=CLIENT_REQUEST_AGENT_ARGV)
        first = [
            {"id": "a", "content": "Read the code", "status": "in_progress"},
            {"id": "b", "content": "Write the fix", "status": "pending"},
        ]

        assert await self._answer_of(m, "cursor/update_todos", {"todos": first}) == {"result": {}}
        assert self._plan(m) == [("Read the code", "in_progress"), ("Write the fix", "pending")]

        merged = [
            {"id": "a", "content": "", "status": "completed"},
            {"id": "b", "content": "", "status": "cancelled"},
            {"id": "c", "content": "Run the tests", "status": "pending"},
        ]
        await self._answer_of(m, "cursor/update_todos", {"todos": merged, "merge": True})
        assert self._plan(m) == [("Read the code", "completed"), ("Run the tests", "pending")]

        replaced = [{"id": "z", "content": "Start over", "status": "pending"}]
        await self._answer_of(m, "cursor/update_todos", {"todos": replaced})
        assert self._plan(m) == [("Start over", "pending")]

    async def test_a_task_list_sent_as_a_notification_updates_the_plan(self, open_chat):
        """Cursor documents ``cursor/update_todos`` as a notification."""
        m = await open_chat(argv=CLIENT_REQUEST_AGENT_ARGV)
        todos = [
            {"id": "a", "content": "Read the code", "status": "in_progress"},
            {"id": "b", "content": "Write the fix", "status": "pending"},
        ]

        answer = await self._answer_of(m, "cursor/update_todos", {"todos": todos}, notify=True)

        assert answer == {"notified": True}
        assert self._plan(m) == [("Read the code", "in_progress"), ("Write the fix", "pending")]

    @pytest.mark.parametrize(
        ("method", "answer"),
        [
            ("cursor/task", {"result": {}}),
            ("cursor/generate_image", {"result": {}}),
            ("cursor/open_browser", {"error": -32601}),
            ("other/ask_question", {"error": -32601}),
        ],
        ids=["task-notice", "image-notice", "unknown-cursor-method", "another-prefix"],
    )
    async def test_the_other_requests_need_no_card(self, open_chat, method, answer):
        m = await open_chat(argv=CLIENT_REQUEST_AGENT_ARGV)

        assert await self._answer_of(m, method, {"toolCallId": "t1"}) == answer

        assert not _asked(m.frames)
        assert m.of_type("tool") == []

    async def test_a_question_with_nothing_to_pick_is_skipped_without_a_card(self, open_chat):
        m = await open_chat(argv=CLIENT_REQUEST_AGENT_ARGV)
        params = {"title": "Pick", "questions": [{"id": "q", "prompt": "Which?", "options": []}]}

        answer = await self._answer_of(m, "cursor/ask_question", params)

        assert answer["result"]["outcome"]["outcome"] == "skipped"
        assert not _asked(m.frames)


class TestClientCapabilities:
    """``clientCapabilities`` of ``initialize``: the chat's own, plus the
    ``_meta`` keys an agent's launch plan asks for (Cursor's model picker)."""

    @staticmethod
    def _capabilities(log) -> dict[str, Any]:
        (params,) = _params_of(log, "initialize")
        return params["clientCapabilities"]

    async def test_the_meta_of_the_launch_plan_goes_to_initialize(self, open_chat, tmp_path):
        log = tmp_path / "agent.log"

        await open_chat(
            load_session_id="conv-9",
            argv=_stored_title_agent(log, "replayed"),
            client_meta={"parameterizedModelPicker": True},
        )

        assert self._capabilities(log) == {
            **session_mod.CLIENT_CAPABILITIES,
            "_meta": {"parameterizedModelPicker": True},
        }
        assert "_meta" not in session_mod.CLIENT_CAPABILITIES

    async def test_a_plan_without_meta_sends_the_capabilities_as_they_are(self, open_chat, tmp_path):
        log = tmp_path / "agent.log"

        await open_chat(load_session_id="conv-9", argv=_stored_title_agent(log, "replayed"))

        assert self._capabilities(log) == session_mod.CLIENT_CAPABILITIES


_NOTES_PATH = "/project/notes.md"
_CREATED_NOTES = {
    "sessionUpdate": "tool_call",
    "toolCallId": "create",
    "title": "Create notes.md",
    "kind": "edit",
    "status": "completed",
    "locations": [{"path": _NOTES_PATH}],
}


def _agent_text(text: str, **fields: Any) -> dict[str, Any]:
    return {"sessionUpdate": "agent_message_chunk", **fields, "content": {"type": "text", "text": text}}


class TestToolPathNote:
    """Copilot follows a file tool with a message that only names the file
    again ("Info: <path>"); the tool item already shows that path."""

    async def test_the_note_that_repeats_the_tool_path_is_not_shown(self, open_chat):
        m = await open_chat(argv=MINI_AGENT_ARGV)

        _agent_sends(m, _CREATED_NOTES)
        _agent_sends(m, _agent_text(f"Info: {_NOTES_PATH}"))
        _agent_sends(m, _agent_text("The notes are ready."))
        await m.until(lambda: bool(m.texts("agent")))

        assert m.texts("agent") == ["The notes are ready."]
        assert m.items["create"]["locations"] == [{"path": _NOTES_PATH}]

    @pytest.mark.parametrize(
        ("tool", "note"),
        [
            (_CREATED_NOTES, _agent_text(f"Note: {_NOTES_PATH}")),
            (_CREATED_NOTES, _agent_text("Info: /project/other.md")),
            (_CREATED_NOTES, _agent_text(f"Info: {_NOTES_PATH}", messageId="answer-1")),
            (None, _agent_text(f"Info: {_NOTES_PATH}")),
        ],
        ids=["other-prefix", "another-path", "part-of-a-message", "no-tool-before"],
    )
    async def test_any_other_agent_text_is_shown(self, open_chat, tool, note):
        m = await open_chat(argv=MINI_AGENT_ARGV)

        if tool is not None:
            _agent_sends(m, tool)
        _agent_sends(m, note)
        _agent_sends(m, _agent_text(" Done."))
        await m.until(lambda: "".join(m.texts("agent")).endswith(" Done."))

        assert "".join(m.texts("agent")) == note["content"]["text"] + " Done."


# Logs each request to argv[1] as one line: the method, and for a prompt its
# text after a space. Opens earlier sessions and echoes prompts. While the
# file named by argv[2] exists, ``session/load`` is refused.
RELOAD_AGENT = r"""
import json, os, sys
log = open(sys.argv[1], "a", encoding="utf-8")
refuse = sys.argv[2]

def send(message):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg or "method" not in msg:
        continue
    method, params = msg["method"], msg.get("params") or {}
    text = " ".join(block.get("text", "") for block in params.get("prompt") or [])
    log.write(f"{method} {text}".strip() + "\n")
    log.flush()
    if method == "initialize":
        result = {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}}
    elif method == "session/load":
        if os.path.exists(refuse):
            send({"id": msg["id"], "error": {"code": -32603, "message": "Conversation unavailable"}})
            continue
        result = {}
    elif method == "session/prompt":
        send({"method": "session/update", "params": {"sessionId": params["sessionId"], "update": {
            "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "echo " + text}}}})
        result = {"stopReason": "end_turn"}
    else:
        send({"id": msg["id"], "error": {"code": -32601, "message": "Method not found"}})
        continue
    send({"id": msg["id"], "result": result})
"""


def _reload_agent(log, refuse) -> list[str]:
    return [sys.executable, "-c", RELOAD_AGENT, str(log), str(refuse)]


@contextmanager
def _other_app_holds(path: Path) -> Iterator[None]:
    """Another app's ``agy`` presence lock (``flock`` belongs to an open
    file, so a second descriptor here stands in for another process)."""
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def _free_for_another_app(path: Path) -> bool:
    import fcntl

    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    finally:
        os.close(fd)
    return True


@pytest.mark.skipif(sys.platform == "win32", reason="the presence lock is POSIX only")
class TestConversationPresence:
    """A chat on an Antigravity conversation holds ``agy``'s presence lock
    while the conversation is open, and warns once when another app on the
    PC has it open. When that app closes it, the next message loads the
    conversation again before it is sent."""

    WARNING = "This conversation is also open in another app on your PC"
    LOADED = "Loaded the latest messages"

    @staticmethod
    def _lock(tmp_path: Path, session_id: str) -> Path:
        return tmp_path / "agy" / "presence" / f"{session_id}.lock"

    async def _open(
        self,
        open_chat,
        tmp_path: Path,
        load_session_id: str | None = "external-1111",
        argv: list[str] | None = None,
    ):
        return await open_chat(
            load_session_id=load_session_id,
            argv=argv or fake_agent_argv("antigravity"),
            presence=ConversationPresence(tmp_path / "agy"),
        )

    async def _open_while_another_app_has_it(self, open_chat, tmp_path: Path, log: Path):
        """The chat opens with the warning; then the other app closes it."""
        with _other_app_holds(self._lock(tmp_path, "external-1111")):
            m = await self._open(open_chat, tmp_path, argv=_reload_agent(log, tmp_path / "refuse"))
            await m.until(lambda: self.WARNING in _notices(m))
        return m

    @staticmethod
    def _before_the_reload(
        monkeypatch, session: ChatSession, step: Callable[[], Awaitable[None]]
    ) -> None:
        """Runs ``step`` after the turn gave its message to the reload, before
        the old agent process starts to close."""
        reload = session._reload_conversation

        async def step_then_reload() -> None:
            await step()
            await reload()

        monkeypatch.setattr(session, "_reload_conversation", step_then_reload)

    async def test_the_chat_holds_the_conversation_until_it_stops(self, open_chat, tmp_path):
        lock = self._lock(tmp_path, "external-1111")

        m = await self._open(open_chat, tmp_path)

        assert m.status == "ready"
        assert _free_for_another_app(lock) is False
        assert self.WARNING not in _notices(m)

        await m.session.stop()
        await m.until(lambda: "stopped" in m.session_events)

        assert _free_for_another_app(lock) is True


    async def test_another_app_on_the_conversation_gets_one_warning(self, open_chat, tmp_path):
        lock = self._lock(tmp_path, "external-1111")
        with _other_app_holds(lock):
            m = await self._open(open_chat, tmp_path)
            assert m.status == "ready"
            await m.until(lambda: self.WARNING in _notices(m))

            await m.command("prompt", text="hello")
            await m.until(_turn_done(m, "You said: hello"))

            assert _notices(m).count(self.WARNING) == 1
            notice = next(n for n in m.of_type("notice") if n.get("title") == self.WARNING)
            assert notice["severity"] == "warning"

        # The other app closed it, maybe after turns of its own: the next
        # turn takes the conversation and loads it again before it sends.
        old_epoch = m.epoch
        await m.command("prompt", text="again")
        await m.until(_turn_done(m, "You said: again"))

        assert m.epoch != old_epoch
        assert _free_for_another_app(lock) is False
        assert _notices(m) == [self.LOADED]

    async def test_the_message_after_the_other_app_closes_it_is_sent_once_after_the_reload(
        self, open_chat, tmp_path
    ):
        log = tmp_path / "agent.log"
        m = await self._open_while_another_app_has_it(open_chat, tmp_path, log)
        old_epoch = m.epoch

        await m.command("prompt", text="again")
        await m.until(_turn_done(m, "echo again"))

        assert m.epoch != old_epoch
        assert _methods(log) == [
            "initialize",
            "session/load",
            "initialize",
            "session/load",
            "session/prompt again",
        ]
        assert [item["type"] for item in m.items.values()] == ["notice", "user", "agent"]
        assert _notices(m) == [self.LOADED]
        assert m.texts("user") == ["again"]
        assert m.state["queue"] == []

    async def test_a_message_sent_while_the_conversation_reloads_goes_out_after_the_first(
        self, open_chat, tmp_path, monkeypatch
    ):
        """The old agent process is about to close: neither message may
        start on it."""
        log = tmp_path / "agent.log"
        m = await self._open_while_another_app_has_it(open_chat, tmp_path, log)
        queues: list[list[str]] = []

        async def second_message() -> None:
            await m.session.handle_command(b'{"id": "second", "op": "prompt", "text": "second"}')
            queues.append([q["text"] for q in m.session.timeline.state["queue"]])

        self._before_the_reload(monkeypatch, m.session, second_message)

        await m.command("prompt", text="again")
        await m.until(_turn_done(m, "echo second"))

        assert m.results["second"]["data"]["queued"] is True
        assert queues == [["again", "second"]]
        assert _methods(log) == [
            "initialize",
            "session/load",
            "initialize",
            "session/load",
            "session/prompt again",
            "session/prompt second",
        ]
        assert m.texts("user") == ["again", "second"]
        assert m.texts("agent") == ["echo again", "echo second"]
        assert m.state["queue"] == []
        assert _notices(m) == [self.LOADED]
        notice = m.of_type("notice")[0]
        assert notice["severity"] == "info"
        assert "your message" not in notice["text"]

    async def test_while_the_conversation_reloads_stop_has_no_turn_and_keeps_the_message(
        self, open_chat, tmp_path, monkeypatch
    ):
        log = tmp_path / "agent.log"
        m = await self._open_while_another_app_has_it(open_chat, tmp_path, log)
        seen: list[dict[str, Any]] = []

        async def stop() -> None:
            state = m.session.timeline.state
            seen.append({k: state.get(k) for k in ("status", "turn_started_at", "cancelling")})
            await m.session.handle_command(b'{"id": "stop", "op": "cancel"}')
            seen.append({"queue": [q["text"] for q in state["queue"]]})

        self._before_the_reload(monkeypatch, m.session, stop)

        await m.command("prompt", text="again")
        await m.until(_turn_done(m, "echo again"))

        assert seen == [
            {"status": "starting", "turn_started_at": None, "cancelling": False},
            {"queue": ["again"]},
        ]
        assert m.results["stop"]["data"] == {"cancelled": False}
        assert _methods(log) == [
            "initialize",
            "session/load",
            "initialize",
            "session/load",
            "session/prompt again",
        ]
        assert m.of_type("turn_end") == []
        assert m.state["queue"] == []
        assert m.state["cancelling"] is False

    @pytest.mark.parametrize("lock_at_open", [True, False], ids=["held-at-open", "taken-by-a-turn"])
    async def test_without_another_app_messages_are_sent_without_a_reload(
        self, open_chat, tmp_path, lock_at_open
    ):
        """``taken-by-a-turn``: the presence folder cannot be made while the
        chat opens, so the first turn takes the lock with no conflict seen."""
        log = tmp_path / "agent.log"
        blocker = tmp_path / "agy" / "presence"
        if not lock_at_open:
            blocker.parent.mkdir(parents=True)
            blocker.write_text("")
        m = await self._open(open_chat, tmp_path, argv=_reload_agent(log, tmp_path / "refuse"))
        assert self.WARNING not in _notices(m)
        if not lock_at_open:
            blocker.unlink()
        epoch = m.epoch

        await m.command("prompt", text="one")
        await m.until(_turn_done(m, "echo one"))
        await m.command("prompt", text="two")
        await m.until(_turn_done(m, "echo two"))

        assert m.epoch == epoch
        assert _methods(log) == [
            "initialize",
            "session/load",
            "session/prompt one",
            "session/prompt two",
        ]
        assert self.LOADED not in _notices(m)
        assert _free_for_another_app(self._lock(tmp_path, "external-1111")) is False

    async def test_while_another_app_still_has_it_messages_are_sent_without_a_reload(
        self, open_chat, tmp_path
    ):
        log = tmp_path / "agent.log"
        with _other_app_holds(self._lock(tmp_path, "external-1111")):
            m = await self._open(open_chat, tmp_path, argv=_reload_agent(log, tmp_path / "refuse"))
            await m.until(lambda: self.WARNING in _notices(m))
            epoch = m.epoch

            await m.command("prompt", text="one")
            await m.until(_turn_done(m, "echo one"))
            await m.command("prompt", text="two")
            await m.until(_turn_done(m, "echo two"))

            assert m.epoch == epoch
            assert _methods(log) == [
                "initialize",
                "session/load",
                "session/prompt one",
                "session/prompt two",
            ]
            assert _notices(m) == [self.WARNING]

    async def test_a_stop_while_the_turn_takes_the_conversation_sends_nothing(
        self, open_chat, tmp_path
    ):
        """Prompt and Stop handled in one loop tick: the turn takes the lock
        with the Stop already pending, so it neither sends nor reloads."""
        log = tmp_path / "agent.log"
        m = await self._open_while_another_app_has_it(open_chat, tmp_path, log)
        epoch = m.epoch

        await m.session.handle_command(b'{"op": "prompt", "text": "again"}')
        cancelled = await m.command("cancel")

        assert cancelled["data"] == {"cancelled": True}
        await m.until(lambda: m.status == "ready" and bool(m.of_type("turn_end")))
        assert [t["stop_reason"] for t in m.of_type("turn_end")] == ["cancelled"]
        assert m.epoch == epoch
        assert _methods(log) == ["initialize", "session/load"]
        assert self.LOADED not in _notices(m)
        assert m.state["queue"] == []

    async def test_after_such_a_stop_the_next_message_still_reloads_first(
        self, open_chat, tmp_path
    ):
        """The stopped turn took the lock but sent nothing: the agent still
        has the copy from before the other app's turns."""
        log = tmp_path / "agent.log"
        m = await self._open_while_another_app_has_it(open_chat, tmp_path, log)
        await m.session.handle_command(b'{"op": "prompt", "text": "stopped"}')
        await m.command("cancel")
        await m.until(lambda: m.status == "ready" and bool(m.of_type("turn_end")))
        epoch = m.epoch

        await m.command("prompt", text="again")
        await m.until(_turn_done(m, "echo again"))

        assert _methods(log) == [
            "initialize",
            "session/load",
            "initialize",
            "session/load",
            "session/prompt again",
        ]
        assert m.epoch != epoch
        assert _notices(m) == [self.LOADED]

    async def test_a_turn_that_takes_the_conversation_after_a_new_one_is_asked_sends_nothing(
        self, open_chat, tmp_path, monkeypatch
    ):
        """The turn has the lock (so it would reload), but runs on only after
        a new conversation's request and a message in the same burst: its
        message must neither go out nor wait for the new conversation."""
        with _other_app_holds(self._lock(tmp_path, "external-1111")):
            m = await self._open(open_chat, tmp_path)
            await m.until(lambda: self.WARNING in _notices(m))
        hold = m.session._hold_presence
        taken = asyncio.Event()
        resume = asyncio.get_running_loop().create_future()

        async def take_then_wait() -> None:
            await hold()
            if not taken.is_set():
                taken.set()
                await resume

        monkeypatch.setattr(m.session, "_hold_presence", take_then_wait)
        await m.session.handle_command(b'{"id": "a", "op": "prompt", "text": "A"}')
        await asyncio.wait_for(taken.wait(), 20)
        old_epoch = m.epoch

        # The turn wakes up first: it is in the loop's queue before the request's task.
        resume.set_result(None)
        await m.session.handle_command(b'{"id": "n1", "op": "new_conversation"}')
        await m.session.handle_command(b'{"id": "b", "op": "prompt", "text": "B"}')
        await m.until(lambda: {"n1", "b"} <= m.results.keys())

        assert m.results["n1"]["ok"] is True
        assert m.results["b"]["data"]["queued"] is True
        await m.until(lambda: m.epoch != old_epoch and "You said: B" in m.texts("agent"))
        await m.until(lambda: m.status == "ready")
        assert m.texts("user") == ["B"]
        assert m.state["queue"] == []
        assert self.LOADED not in _notices(m)
        assert _fake_agent_pids() == {m.session.pid}

    async def test_a_reload_the_agent_refuses_keeps_the_message_for_restart(
        self, open_chat, tmp_path
    ):
        log, refuse = tmp_path / "agent.log", tmp_path / "refuse"
        m = await self._open_while_another_app_has_it(open_chat, tmp_path, log)
        refuse.touch()

        await m.command("prompt", text="again")
        await m.until(lambda: m.status == "error")

        assert "session/prompt again" not in _methods(log)
        assert [q["text"] for q in m.state["queue"]] == ["again"]
        assert self.LOADED not in _notices(m)

        refuse.unlink()
        assert (await m.command("restart"))["ok"] is True
        await m.until(_turn_done(m, "echo again"))

        assert _methods(log).count("session/prompt again") == 1
        assert self.LOADED not in _notices(m)
        assert m.state["queue"] == []

    async def test_a_restart_warns_again_while_another_app_still_has_it(self, open_chat, tmp_path):
        """The reload clears the timeline, warning included."""
        lock = self._lock(tmp_path, "external-1111")
        with _other_app_holds(lock):
            m = await self._open(open_chat, tmp_path)
            await m.until(lambda: self.WARNING in _notices(m))
            await m.command("prompt", text="crash now")
            await m.until(lambda: m.status == "error")
            old_epoch = m.epoch

            assert (await m.command("restart"))["ok"] is True
            await m.until(lambda: m.status == "ready" and m.epoch != old_epoch)
            await m.until(lambda: self.WARNING in _notices(m))

            assert _notices(m).count(self.WARNING) == 1

    async def test_a_crash_lets_the_conversation_go_and_restart_takes_it_back(
        self, open_chat, tmp_path
    ):
        lock = self._lock(tmp_path, "external-1111")
        m = await self._open(open_chat, tmp_path)
        await m.command("prompt", text="crash now")
        await m.until(lambda: m.status == "error")

        assert _free_for_another_app(lock) is True

        assert (await m.command("restart"))["ok"] is True
        await m.until(lambda: m.status == "ready" and m.state.get("title") == "Earlier session")

        assert _free_for_another_app(lock) is False


def _sign_in(m: TimelineMirror) -> dict[str, Any] | None:
    return next((r for r in m.state.get("requests") or [] if r["kind"] == "sign_in"), None)


class TestSignIn:
    """The fake agent is signed out while the marker file exists; its fake
    login prints a sign-in page, refuses the code ``bad``, fails on ``fail``
    and removes the marker on any other code."""

    NOT_SIGNED_IN = "Claude Code is not signed in on your PC. Sign in to continue."
    SIGNED_IN = "Signed in to Claude Code"

    @pytest.fixture
    def signed_out(self, tmp_path, monkeypatch) -> Path:
        marker = tmp_path / "signed-out"
        marker.write_text("", encoding="utf-8")
        monkeypatch.setenv(FAKE_SIGNED_OUT_ENV, str(marker))
        monkeypatch.setattr(signin, "SUBMIT_SETTLE", 0.3)
        return marker

    @staticmethod
    def _claude() -> dict[str, Any]:
        return {"sign_in": claude_sign_in_methods(None, os.environ, fake=True)}

    @staticmethod
    def _codex() -> dict[str, Any]:
        return {
            "argv": fake_agent_argv("codex"),
            "sign_in": codex_sign_in_methods(None, os.environ, fake=True),
        }

    @staticmethod
    def _copilot() -> dict[str, Any]:
        return {
            "argv": fake_agent_argv("copilot"),
            "sign_in": copilot_sign_in_methods(None, os.environ, fake=True),
        }

    @staticmethod
    def _cursor() -> dict[str, Any]:
        return {
            "argv": fake_agent_argv("cursor"),
            "sign_in": cursor_sign_in_methods(None, os.environ, fake=True),
        }

    @staticmethod
    def _pi() -> dict[str, Any]:
        return {
            "argv": fake_agent_argv("pi"),
            "sign_in": pi_sign_in_methods(None, os.environ, fake=True),
            "sign_in_prompt": PI_SIGN_IN_PROMPT,
        }

    async def _at_the_code_field(self, m: TimelineMirror) -> dict[str, Any]:
        started = await m.command("sign_in", action="start", method="claude_ai")
        assert started["ok"] is True
        await m.until(lambda: _sign_in(m) is not None and _sign_in(m)["input"] is not None)
        request = _sign_in(m)
        assert request is not None
        return request

    async def test_a_signed_out_agent_offers_the_sign_in(self, open_chat, signed_out):
        m = await open_chat(**self._claude())
        await m.until(lambda: _sign_in(m) is not None)

        request = _sign_in(m)
        assert request is not None
        assert request["id"] == "sign-in"
        assert request["title"] == "Sign in to Claude Code"
        assert request["stage"] == "choose"
        assert [x["id"] for x in request["methods"]] == ["claude_ai", "console"]
        await m.until(lambda: "review" in [c["name"] for c in m.state.get("commands") or []])
        assert [c["name"] for c in m.state["commands"]] == [
            "review", "test", "compact", "clear", "login"
        ]

    async def test_a_signed_in_chat_offers_nothing(self, open_chat):
        m = await open_chat(**self._claude())
        await m.command("prompt", text="hi")
        await m.until(_turn_done(m, "You said: hi"))

        assert _sign_in(m) is None

    async def test_the_sign_in_page_and_the_code_field(self, open_chat, signed_out):
        m = await open_chat(**self._claude())

        request = await self._at_the_code_field(m)

        assert request["stage"] == "waiting"
        assert request["url"] == "https://example.com/claude/sign-in?state=fake"
        assert request["input"] == {"label": "Code from the sign-in page", "secret": False}
        assert (request["busy"], request["error"]) == (False, None)

    async def test_a_refused_message_is_sent_once_signed_in(self, open_chat, signed_out):
        m = await open_chat(**self._claude())
        await m.until(lambda: m.status == "ready")
        first_session = m.state["acp_session_id"]

        await m.command("prompt", text="hello")
        await m.until(lambda: self.NOT_SIGNED_IN in m.texts("error"))
        await self._at_the_code_field(m)
        submitted = await m.command("sign_in", action="submit", value="good")
        assert submitted["ok"] is True
        await m.until(
            lambda: self.SIGNED_IN in _notices(m) and "You said: hello" in m.texts("agent")
        )

        assert _sign_in(m) is None
        assert not signed_out.exists()
        # Nothing was stored before the sign-in, so a new session started.
        assert m.state["acp_session_id"] != first_session
        assert _notices(m).count(self.SIGNED_IN) == 1

    async def test_a_chat_with_turns_reloads_its_conversation_after_the_sign_in(
        self, open_chat, tmp_path, monkeypatch
    ):
        marker = tmp_path / "signed-out"
        monkeypatch.setenv(FAKE_SIGNED_OUT_ENV, str(marker))
        m = await open_chat(**self._claude())
        await m.command("prompt", text="hi")
        await m.until(_turn_done(m, "You said: hi"))
        session_id = m.state["acp_session_id"]
        marker.write_text("", encoding="utf-8")

        await m.command("prompt", text="again")
        await m.until(lambda: _sign_in(m) is not None)
        await self._at_the_code_field(m)
        await m.command("sign_in", action="submit", value="good")
        await m.until(lambda: self.SIGNED_IN in _notices(m) and "You said: again" in m.texts("agent"))

        assert m.state["acp_session_id"] == session_id
        assert m.state.get("title") == "Earlier session"

    async def test_a_wrong_code_is_shown_and_a_failed_login_goes_back_to_the_methods(
        self, open_chat, signed_out
    ):
        m = await open_chat(**self._claude())
        await self._at_the_code_field(m)

        await m.command("sign_in", action="submit", value="bad")
        await m.until(lambda: _sign_in(m)["error"] is not None)
        wrong = _sign_in(m)
        assert wrong["error"] == "Invalid code. Check the code and try again."
        assert (wrong["stage"], wrong["busy"]) == ("waiting", False)

        await m.command("sign_in", action="submit", value="fail")
        await m.until(lambda: _sign_in(m)["stage"] == "choose")

        assert _sign_in(m)["error"] == (
            "The sign-in did not finish.\nLogin failed: the sign-in server refused the code."
        )
        assert signed_out.exists()

    async def test_cancel_drops_the_sign_in(self, open_chat, signed_out):
        m = await open_chat(**self._claude())
        await self._at_the_code_field(m)

        cancelled = await m.command("sign_in", action="cancel")

        assert cancelled["ok"] is True
        assert m.state["requests"] == []
        assert signed_out.exists()

    @pytest.mark.parametrize(
        ("fields", "error"),
        [
            ({"action": "dance"}, "Unknown action"),
            ({"action": "start", "method": "nope"}, "Unknown sign-in method"),
            ({"action": "submit", "value": "abc"}, "Nothing to submit"),
        ],
    )
    async def test_invalid_commands_are_refused(self, open_chat, fields, error):
        m = await open_chat(**self._claude())

        result = await m.command("sign_in", **fields)

        assert result["error"] == error

    async def test_slash_login_opens_the_sign_in_instead_of_a_prompt(self, open_chat):
        m = await open_chat(**self._claude())

        result = await m.command("prompt", text="/Login")
        await m.until(lambda: _sign_in(m) is not None)

        assert result["data"] == {"queued": False, "queue_id": None}
        assert _sign_in(m)["stage"] == "choose"
        assert m.texts("user") == []

    async def test_slash_login_during_a_turn_is_refused(self, open_chat):
        m = await open_chat(**self._claude())
        await m.command("prompt", text="slow")
        await m.until(lambda: m.status == "running")

        result = await m.command("prompt", text="/login")

        assert result["error"] == "Wait for the current turn to end, or stop it, then sign in."
        assert _sign_in(m) is None

    async def test_without_sign_in_methods_slash_login_goes_to_the_agent(self, open_chat):
        m = await open_chat()

        await m.command("prompt", text="/login")

        await m.until(_turn_done(m, "You said: /login"))
        assert _sign_in(m) is None

    async def test_without_sign_in_methods_the_agents_own_hint_is_shown(
        self, open_chat, signed_out
    ):
        # OpenCode has no account of its own: the generic "sign in on the PC"
        # text would send the user to a login that does not exist.
        hint = "Connect a model provider in OpenCode on the PC."
        m = await open_chat(argv=fake_agent_argv("opencode"), sign_in_hint=hint)

        await m.command("prompt", text="hi")
        await m.until(lambda: m.status == "ready" and bool(m.of_type("error")))

        assert m.texts("error") == [hint]
        assert _sign_in(m) is None

    async def test_a_codex_start_refused_for_the_login_offers_the_sign_in(
        self, open_chat, signed_out
    ):
        m = await open_chat(**self._codex())
        await m.until(lambda: m.status == "error" and _sign_in(m) is not None)

        assert m.state["error"] == self.NOT_SIGNED_IN
        assert [x["id"] for x in _sign_in(m)["methods"]] == ["chatgpt", "api_key"]

    async def test_a_codex_api_key_starts_the_agent(self, open_chat, signed_out):
        m = await open_chat(**self._codex())
        await m.until(lambda: _sign_in(m) is not None)

        await m.command("sign_in", action="start", method="api_key")
        assert _sign_in(m)["input"] == {"label": "OpenAI API key", "secret": True}
        refused = await m.command("sign_in", action="submit", value="bad")
        assert refused["ok"] is True
        await m.until(lambda: _sign_in(m)["error"] is not None)
        assert _sign_in(m)["error"] == "Incorrect API key provided (fake agent)."

        await m.command("sign_in", action="submit", value="sk-test")
        await m.until(lambda: m.status == "ready" and self.SIGNED_IN in _notices(m))

        assert m.state["error"] is None
        assert _sign_in(m) is None

    async def test_a_codex_device_login_shows_the_one_time_code_and_finishes_alone(
        self, open_chat, signed_out
    ):
        m = await open_chat(**self._codex())
        await m.until(lambda: _sign_in(m) is not None)

        await m.command("sign_in", action="start", method="chatgpt")
        await m.until(lambda: _sign_in(m)["url"] is not None)
        request = _sign_in(m)
        assert request["url"] == "https://example.com/codex/sign-in?state=fake"
        assert request["user_code"] == "FAKE-12345"
        assert (request["input"], request["busy"]) == (None, True)

        await m.until(lambda: m.status == "ready" and self.SIGNED_IN in _notices(m))
        assert not signed_out.exists()

    async def test_a_copilot_device_login_reads_the_page_and_the_code_from_one_line(
        self, open_chat, signed_out
    ):
        """``copilot login --device-code`` prints the page and the code in one
        line; Copilot refuses ``session/new`` until the login is done."""
        m = await open_chat(**self._copilot())
        await m.until(lambda: m.status == "error" and _sign_in(m) is not None)
        assert m.state["error"] == self.NOT_SIGNED_IN
        assert [x["id"] for x in _sign_in(m)["methods"]] == ["github"]

        await m.command("sign_in", action="start", method="github")
        await m.until(lambda: _sign_in(m)["url"] is not None)
        request = _sign_in(m)
        assert request["url"] == "https://example.com/copilot/sign-in?state=fake"
        assert request["user_code"] == "FAKE-1234"
        assert (request["input"], request["busy"]) == (None, True)

        await m.until(lambda: m.status == "ready" and self.SIGNED_IN in _notices(m))
        assert not signed_out.exists()

    async def test_a_cursor_login_shows_only_the_page_and_finishes_alone(
        self, open_chat, signed_out
    ):
        """``cursor-agent login`` prints a sign-in page and no code; Cursor
        refuses ``session/new`` until the login is done."""
        m = await open_chat(**self._cursor())
        await m.until(lambda: m.status == "error" and _sign_in(m) is not None)
        assert m.state["error"] == self.NOT_SIGNED_IN
        assert [x["id"] for x in _sign_in(m)["methods"]] == ["cursor"]

        await m.command("sign_in", action="start", method="cursor")
        await m.until(lambda: _sign_in(m)["url"] is not None)
        request = _sign_in(m)
        assert request["url"] == "https://example.com/cursor/sign-in?state=fake"
        assert request["user_code"] is None
        assert (request["input"], request["busy"]) == (None, True)

        await m.until(lambda: m.status == "ready" and self.SIGNED_IN in _notices(m))
        assert not signed_out.exists()

    async def test_a_pi_sign_in_offers_two_accounts_and_reads_the_page_and_the_code(
        self, open_chat, signed_out
    ):
        """A Pi with no provider refuses ``session/new``. The card says what
        the chat can sign in to; ``pi auth login openai-codex --no-browser``
        prints the page and the code in one line."""
        m = await open_chat(**self._pi())
        await m.until(lambda: m.status == "error" and _sign_in(m) is not None)
        assert m.state["error"] == self.NOT_SIGNED_IN
        assert [x["id"] for x in _sign_in(m)["methods"]] == [
            "openai-codex",
            "anthropic",
            "openrouter-key",
            "google-key",
            "openai-key",
            "anthropic-key",
        ]
        assert _sign_in(m)["text"] == PI_SIGN_IN_PROMPT

        await m.command("sign_in", action="start", method="openai-codex")
        await m.until(lambda: _sign_in(m)["url"] is not None)
        request = _sign_in(m)
        assert request["url"] == "https://example.com/pi/sign-in?state=fake"
        assert request["user_code"] == "FAKE-12345"
        assert (request["input"], request["busy"]) == (None, True)

        await m.until(lambda: m.status == "ready" and self.SIGNED_IN in _notices(m))
        assert not signed_out.exists()

    async def test_refused_messages_over_the_queue_limit_drop_the_oldest(
        self, open_chat, signed_out, monkeypatch
    ):
        monkeypatch.setattr(session_mod, "QUEUE_MAX", 2)
        m = await open_chat(**self._claude())
        await m.until(lambda: m.status == "ready")

        for text in ("one", "two", "three"):
            assert (await m.command("prompt", text=text))["ok"] is True
        await m.until(lambda: m.texts("error").count(self.NOT_SIGNED_IN) == 3 and m.status == "ready")
        await self._at_the_code_field(m)
        await m.command("sign_in", action="submit", value="good")
        await m.until(
            lambda: "You said: three" in m.texts("agent") and m.status == "ready"
        )

        assert m.texts("agent") == ["You said: two", "You said: three"]

    async def test_a_finished_turn_withdraws_an_offer_nobody_took(self, open_chat):
        m = await open_chat(**self._claude())
        await m.command("prompt", text="/login")
        await m.until(lambda: _sign_in(m) is not None)

        await m.command("prompt", text="hi")
        await m.until(_turn_done(m, "You said: hi"))

        assert _sign_in(m) is None

    async def test_a_finished_turn_keeps_a_running_login(self, open_chat):
        m = await open_chat(**self._claude())
        await self._at_the_code_field(m)

        await m.command("prompt", text="hi")
        await m.until(_turn_done(m, "You said: hi"))

        assert _sign_in(m) is not None and _sign_in(m)["stage"] == "waiting"

    async def test_a_login_the_agent_reports_withdraws_the_offer(self, open_chat, signed_out):
        m = await open_chat(**self._claude())
        await m.until(lambda: _sign_in(m) is not None)

        # claude-agent-acp pushes the login it found once the user signed in on the PC.
        m.session._on_notification(
            "_auth/status_update", {"authStatus": {"kind": "apiKey", "label": "API key"}}
        )
        await m.until(lambda: _sign_in(m) is None)

        assert m.state["requests"] == []

    async def test_a_restart_after_signing_in_on_the_pc_withdraws_the_offer(
        self, open_chat, signed_out
    ):
        m = await open_chat(**self._codex())
        await m.until(lambda: m.status == "error" and _sign_in(m) is not None)

        signed_out.unlink()
        assert (await m.command("restart"))["ok"] is True
        await m.until(lambda: m.status == "ready" and _sign_in(m) is None)

        assert m.state["error"] is None

    async def test_a_sign_in_during_a_turn_restarts_the_agent_once_the_turn_ends(
        self, open_chat, signed_out
    ):
        m = await open_chat(**self._claude())
        await m.until(lambda: m.status == "ready")
        await m.command("prompt", text="hello")
        await m.until(lambda: self.NOT_SIGNED_IN in m.texts("error") and m.status == "ready")
        signed_out.unlink()
        await m.command("prompt", text="slow")
        await m.until(lambda: m.status == "running")
        assert (await m.command("prompt", text="next"))["data"]["queued"] is True
        turn_pid = m.session.pid
        early: list[str] = []

        def done() -> bool:
            if self.SIGNED_IN in _notices(m) and m.session.pid == turn_pid:
                early.append(m.status or "")
            return m.status == "ready" and "You said: next" in m.texts("agent")

        m.session._on_signed_in()
        await m.until(done)

        assert early == []
        assert m.session.pid != turn_pid
        # The message refused before the sign-in goes first, then the queue.
        assert m.texts("agent")[-2:] == ["You said: hello", "You said: next"]
        assert _notices(m).count(self.SIGNED_IN) == 1

    async def test_a_sign_in_during_another_apps_turn_restarts_the_agent_once_it_ends(
        self, open_chat
    ):
        m = await open_chat(argv=fake_agent_argv("codex"))
        await m.command("prompt", text="external")
        await m.until(
            lambda: m.status == "running" and "Message sent from another app on your PC" in _notices(m)
        )
        turn_pid = m.session.pid
        early: list[str] = []

        def done() -> bool:
            if self.SIGNED_IN in _notices(m) and m.session.pid == turn_pid:
                early.append(m.status or "")
            return m.status == "ready" and self.SIGNED_IN in _notices(m)

        m.session._on_signed_in()
        await m.until(done)

        assert early == []
        assert m.session.pid != turn_pid
        assert _notices(m).count(self.SIGNED_IN) == 1

    async def test_a_sign_in_during_a_restart_starts_the_agent_again_once_it_is_ready(
        self, open_chat, signed_out
    ):
        m = await open_chat(**self._codex())
        await m.until(lambda: m.status == "error" and _sign_in(m) is not None)
        signed_out.unlink()
        failed_pid = m.session.pid
        pids: set[int] = set()

        def done() -> bool:
            if m.session.pid is not None and m.session.pid != failed_pid:
                pids.add(m.session.pid)
            return m.status == "ready" and self.SIGNED_IN in _notices(m)

        await m.session.handle_command(orjson.dumps({"id": "restart", "op": "restart"}))
        m.session._on_signed_in()
        await m.until(done)

        assert m.results["restart"]["ok"] is True
        # The restart the user asked for, then the one for the new login.
        assert len(pids) == 2
        assert _notices(m).count(self.SIGNED_IN) == 1
        assert m.state["error"] is None

    async def test_a_turn_that_ends_after_a_sign_in_still_alerts_it_finished(self, open_chat):
        m = await open_chat(**self._claude())
        await m.command("prompt", text="slow")
        await m.until(lambda: m.status == "running")
        turn_pid = m.session.pid

        m.session._on_signed_in()
        await m.until(
            lambda: m.status == "ready" and self.SIGNED_IN in _notices(m)
            and m.session.pid != turn_pid
        )

        assert "turn_finished" in m.attention
        assert "turn_failed" not in m.attention

    async def test_a_turn_that_fails_after_a_sign_in_still_alerts_it_failed(self, open_chat):
        m = await open_chat(**self._claude())
        await m.until(lambda: m.status == "ready")
        turn_pid = m.session.pid
        await m.command("prompt", text="please fail")

        m.session._on_signed_in()
        await m.until(
            lambda: m.status == "ready" and self.SIGNED_IN in _notices(m)
            and m.session.pid != turn_pid
        )

        assert m.texts("error") == ["The model request failed (fake error)"]
        assert "turn_failed" in m.attention
        assert "turn_finished" not in m.attention

    async def test_a_message_sent_while_the_sign_in_restart_closes_the_agent_waits_for_it(
        self, open_chat
    ):
        m = await open_chat(**self._claude())
        await m.until(lambda: m.status == "ready")
        old_pid = m.session.pid

        m.session._on_signed_in()
        # The restart has begun and now waits for the old agent to close.
        await asyncio.sleep(0)
        sent = await m.command("prompt", text="hello")
        await m.until(_turn_done(m, "You said: hello"))

        assert sent["data"]["queued"] is True
        assert m.session.pid != old_pid
        assert m.texts("user") == ["hello"]
        assert m.texts("error") == []


# Every prompt streams "Working" and is answered (``end_turn``) only once the
# file named by argv[1] exists. Advertises steering and answers it as
# claude-agent-acp does with no prompt of its own in flight (``promptRequired``).
HELD_TURN_AGENT = r"""
import json, os, sys, time
release = sys.argv[1]
options = dict(arg.split("=", 1) for arg in sys.argv[2:])
after_answer = options.get("after_answer")
log = open(options["log"], "a", encoding="utf-8") if "log" in options else None

def send(message):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    sys.stdout.flush()

def chunk(text):
    send({"method": "session/update", "params": {"sessionId": "held", "update": {
        "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}}})

for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg or "method" not in msg:
        continue
    method = msg["method"]
    if log is not None:
        log.write(method + "\n")
        log.flush()
    if method == "initialize":
        result = {"protocolVersion": 1, "_meta": {"steering": {"supported": True}}, "agentCapabilities": {}}
    elif method == "session/new":
        result = {"sessionId": "held"}
    elif method == "_session/steering":
        result = {"outcome": "promptRequired"}
    elif method == "session/prompt":
        chunk("Working")
        while not os.path.exists(release):
            time.sleep(0.02)
        send({"id": msg["id"], "result": {"stopReason": "end_turn"}})
        if after_answer:
            chunk(after_answer)
        continue
    else:
        send({"id": msg["id"], "error": {"code": -32601, "message": "Method not found"}})
        continue
    send({"id": msg["id"], "result": result})
"""


def _held_turn_agent(release, **options: Any) -> list[str]:
    return [
        sys.executable,
        "-c",
        HELD_TURN_AGENT,
        str(release),
        *(f"{name}={value}" for name, value in options.items()),
    ]


class _GateWindow:
    def __init__(self, callback: Callable[[], None]) -> None:
        self.callback = callback
        self.done = False

    def cancel(self) -> None:
        self.done = True


class GateWindows:
    """The quiet windows the sessions' attention gates arm with
    ``loop.call_later``. One elapses only on ``elapse()``; every other timer
    of the loop runs as usual."""

    def __init__(self, call_later: Callable[..., Any]) -> None:
        self._call_later = call_later
        self._windows: list[_GateWindow] = []

    def call_later(self, delay: float, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if not isinstance(getattr(callback, "__self__", None), AttentionGate):
            return self._call_later(delay, callback, *args, **kwargs)
        window = _GateWindow(callback)
        self._windows.append(window)
        return window

    @property
    def pending(self) -> int:
        return sum(1 for window in self._windows if not window.done)

    def elapse(self) -> int:
        """Run every armed window; return how many were still armed."""
        due = [window for window in self._windows if not window.done]
        for window in due:
            window.done = True
            window.callback()
        return len(due)


@pytest.fixture
async def gate_windows(monkeypatch) -> GateWindows:
    loop = asyncio.get_running_loop()
    windows = GateWindows(loop.call_later)
    monkeypatch.setattr(loop, "call_later", windows.call_later)
    return windows


HOOK_PORT = 9100
# What the CLI submits by itself when background work reports back.
TASK_NOTIFICATION = "<task-notification>\n"


def _background(task_type: str, status: str = "running") -> dict[str, Any]:
    return {"background_tasks": [{"id": "t1", "type": task_type, "status": status}]}


def _status_patch(m: TimelineMirror, status: str) -> dict[str, Any]:
    """The last state patch that set ``status``: what a client got with it."""
    patches = [
        ev["patch"]
        for frame in m.frames
        if frame["t"] == "events"
        for ev in frame["events"]
        if ev["type"] == "state" and ev["patch"].get("status") == status
    ]
    return patches[-1]


class TestClaudeHooks:
    """A Claude chat with a hook port reads Claude Code's hooks, which the
    ``/internal/hook`` router hands to ``on_claude_hook``. The prompt answer
    still ends a turn on the chat screen; the hooks decide when the session
    reads idle and alerts, and they show a turn the agent starts by itself."""

    async def _turn(
        self, m: TimelineMirror, release: Path, *hooks: tuple[HookKind, dict[str, Any]]
    ) -> None:
        """One turn the user sends; ``hooks`` arrive before the prompt is answered."""
        await m.command("prompt", text="work")
        await m.until(lambda: "Working" in m.texts("agent"))
        for kind, payload in hooks:
            m.session.on_claude_hook(kind, payload)
        release.touch()
        await m.until(lambda: m.status == "ready")

    async def _agent_starts_a_turn(self, m: TimelineMirror, prompt_id: str = "p1") -> None:
        m.session.on_claude_hook(
            HookKind.PROMPT_SUBMIT, {"prompt_id": prompt_id, "prompt": TASK_NOTIFICATION}
        )
        await m.until(lambda: m.status == "running")

    @pytest.mark.parametrize(
        ("load_session_id", "port"),
        [(None, HOOK_PORT), ("conv-9", HOOK_PORT), (None, None)],
        ids=["new", "load", "no-hook-port"],
    )
    async def test_hook_settings_go_to_the_agent_with_the_session(
        self, open_chat, tmp_path, load_session_id, port
    ):
        """The URLs carry the Mobaide session id (the router finds the
        session by it), not the agent's conversation id."""
        log = tmp_path / "agent.log"

        m = await open_chat(
            load_session_id=load_session_id,
            argv=_params_agent(log, advertises_dirs=False),
            hook_port=port,
        )

        assert m.status == "ready"
        expected = None
        if port is not None:
            settings = chat_hook_settings(m.session.session_id, port)
            expected = {"claudeCode": {"options": {"settings": settings}}}
        assert _setup_params(log).get("_meta") == expected

    @pytest.mark.parametrize("port", [HOOK_PORT, None], ids=["hook-port", "no-hook-port"])
    async def test_sdk_options_go_to_the_agent_with_the_hook_settings(
        self, open_chat, tmp_path, port
    ):
        log = tmp_path / "agent.log"

        m = await open_chat(
            argv=_params_agent(log, advertises_dirs=False),
            hook_port=port,
            claude_sdk_options={"debug": True},
        )

        assert m.status == "ready"
        expected: dict[str, Any] = {"debug": True}
        if port is not None:
            expected["settings"] = chat_hook_settings(m.session.session_id, port)
        assert _setup_params(log)["_meta"] == {"claudeCode": {"options": expected}}

    @pytest.mark.parametrize(
        ("left_behind", "activity"), [("workflow", "working"), ("shell", "waiting")]
    )
    async def test_work_a_turn_leaves_behind_holds_ready_back(
        self, open_chat, gate_windows, tmp_path, left_behind, activity
    ):
        release = tmp_path / "release"
        m = await open_chat(argv=_held_turn_agent(release), hook_port=HOOK_PORT)

        await self._turn(
            m,
            release,
            (HookKind.PROMPT_SUBMIT, {"prompt_id": "p1", "prompt": "work"}),
            (HookKind.STOP, {"prompt_id": "p1", **_background(left_behind)}),
        )

        info = m.session.to_info()
        assert info.activity == activity
        assert info.attention is None
        assert gate_windows.pending == 0

    @pytest.mark.parametrize(
        ("left_behind", "background"), [("workflow", "working"), ("shell", "waiting")]
    )
    async def test_the_chat_shows_the_work_a_turn_leaves_behind_until_the_next_turn(
        self, open_chat, gate_windows, tmp_path, left_behind, background
    ):
        """The turn's end and the work it left come in one patch, so the chat
        never shows "Ready" in between. The work does not hold the next
        message back. A turn that leaves nothing shows "Ready" at once, while
        the session row still reads ``working`` for the quiet window."""
        release = tmp_path / "release"
        m = await open_chat(argv=_held_turn_agent(release), hook_port=HOOK_PORT)
        await self._turn(
            m,
            release,
            (HookKind.PROMPT_SUBMIT, {"prompt_id": "p1", "prompt": "work"}),
            (HookKind.STOP, {"prompt_id": "p1", **_background(left_behind)}),
        )

        assert _status_patch(m, "ready")["background"] == background

        release.unlink()
        sent = await m.command("prompt", text="more")
        await m.until(lambda: m.status == "running")

        assert sent["data"] == {"queued": False, "queue_id": None}
        assert m.state["background"] is None

        m.session.on_claude_hook(HookKind.PROMPT_SUBMIT, {"prompt_id": "p2", "prompt": "more"})
        m.session.on_claude_hook(
            HookKind.STOP, {"prompt_id": "p2", **_background(left_behind, "completed")}
        )
        release.touch()
        await m.until(lambda: m.status == "ready")

        assert m.session.to_info().activity == "working"
        assert m.state["background"] is None
        assert gate_windows.elapse() == 1
        await m.until(lambda: "turn_finished" in m.attention)
        assert m.state["background"] is None

    async def test_a_turn_the_agent_starts_by_itself_shows_its_work_with_its_end(
        self, open_chat, gate_windows
    ):
        """That end is sent at once, and the work it left must come with it."""
        m = await open_chat(argv=MINI_AGENT_ARGV, hook_port=HOOK_PORT)
        await self._agent_starts_a_turn(m)
        m.session.on_claude_hook(HookKind.STOP, {"prompt_id": "p1", **_background("subagent")})
        await m.until(lambda: m.status == "ready")

        assert _status_patch(m, "ready")["background"] == "working"

        # The sub-agent reports back in a turn the agent starts by itself.
        await self._agent_starts_a_turn(m, "p2")

        assert m.state["background"] is None

    @pytest.mark.parametrize("stop_hook", [True, False], ids=["stop-hook", "stop-hook-lost"])
    async def test_a_finished_turn_alerts_after_the_quiet_window(
        self, open_chat, gate_windows, tmp_path, stop_hook
    ):
        """The window absorbs a task notification that is already on its way.
        A turn whose ``Stop`` hook was lost ends the same way."""
        release = tmp_path / "release"
        m = await open_chat(argv=_held_turn_agent(release), hook_port=HOOK_PORT)
        hooks = [(HookKind.PROMPT_SUBMIT, {"prompt_id": "p1", "prompt": "work"})]
        if stop_hook:
            hooks.append((HookKind.STOP, {"prompt_id": "p1", "background_tasks": []}))

        await self._turn(m, release, *hooks)

        info = m.session.to_info()
        assert (info.activity, info.attention) == ("working", None)
        assert gate_windows.elapse() == 1
        await m.until(lambda: "turn_finished" in m.attention)
        assert m.session.to_info().activity == "idle"

    async def test_a_stop_hook_waits_for_the_prompt_answer(
        self, open_chat, gate_windows, tmp_path
    ):
        """The answer can come long after the ``Stop`` hook. Until it does the
        chat shows the turn as running, so "turn finished" would be wrong."""
        release = tmp_path / "release"
        m = await open_chat(argv=_held_turn_agent(release), hook_port=HOOK_PORT)
        await m.command("prompt", text="work")
        await m.until(lambda: "Working" in m.texts("agent"))
        m.session.on_claude_hook(HookKind.PROMPT_SUBMIT, {"prompt_id": "p1", "prompt": "work"})
        m.session.on_claude_hook(HookKind.STOP, {"prompt_id": "p1", "background_tasks": []})

        gate_windows.elapse()

        info = m.session.to_info()
        assert (info.activity, info.attention) == ("working", None)
        assert m.status == "running"

        release.touch()
        await m.until(lambda: m.status == "ready")
        assert m.session.to_info().attention is None
        assert gate_windows.elapse() == 1
        await m.until(lambda: "turn_finished" in m.attention)
        assert m.attention == ["turn_finished"]

    async def _next_turn_begins_before_the_answer(
        self, m: TimelineMirror, release: Path
    ) -> None:
        """A task notification that already waited starts the agent's next
        turn before the prompt of the turn before is answered."""
        await m.command("prompt", text="work")
        await m.until(lambda: "Working" in m.texts("agent"))
        m.session.on_claude_hook(HookKind.PROMPT_SUBMIT, {"prompt_id": "p1", "prompt": "work"})
        m.session.on_claude_hook(HookKind.STOP, {"prompt_id": "p1", "background_tasks": []})
        m.session.on_claude_hook(
            HookKind.PROMPT_SUBMIT, {"prompt_id": "p2", "prompt": TASK_NOTIFICATION}
        )
        release.touch()
        # The agent sends this text after the answer.
        await m.until(lambda: any("Next turn" in text for text in m.texts("agent")))

    @pytest.mark.parametrize("ended_by", ["stop-hook", "the-user"])
    async def test_a_turn_that_begins_before_the_answer_takes_the_chat_over(
        self, open_chat, gate_windows, tmp_path, ended_by
    ):
        """The ``Stop`` of the answered prompt ends nothing any more: the
        chat stays running, as the agent's own turn, until that turn ends."""
        release = tmp_path / "release"
        m = await open_chat(
            argv=_held_turn_agent(release, after_answer="Next turn"), hook_port=HOOK_PORT
        )

        await self._next_turn_begins_before_the_answer(m, release)

        info = m.session.to_info()
        assert m.session.timeline.state["status"] == "running"
        assert (info.activity, info.attention) == ("working", None)
        assert gate_windows.pending == 0

        if ended_by == "the-user":
            cancelled = await m.command("cancel")
            await m.until(lambda: m.status == "ready" and m.state["cancelling"] is False)

            assert cancelled["data"] == {"cancelled": True}
            info = m.session.to_info()
            assert (info.activity, info.attention) == ("idle", None)
            assert gate_windows.pending == 0
        else:
            m.session.on_claude_hook(HookKind.STOP, {"prompt_id": "p2", "background_tasks": []})
            await m.until(lambda: m.status == "ready")

            assert m.session.to_info().attention is None
            assert gate_windows.elapse() == 1
            await m.until(lambda: "turn_finished" in m.attention)
            assert m.session.to_info().activity == "idle"
            assert m.attention == ["turn_finished"]

    async def test_a_sub_agent_that_hands_back_before_the_answer_ends_the_turn_once(
        self, open_chat, gate_windows, tmp_path
    ):
        """The prompt is held open across the hand-back: the second ``Stop``
        is the one that counts, and the prompt answer ends the turn as usual."""
        release = tmp_path / "release"
        m = await open_chat(argv=_held_turn_agent(release), hook_port=HOOK_PORT)

        await self._turn(
            m,
            release,
            (HookKind.PROMPT_SUBMIT, {"prompt_id": "p1", "prompt": "work"}),
            (HookKind.STOP, {"prompt_id": "p1", **_background("subagent")}),
            (HookKind.PROMPT_SUBMIT, {"prompt_id": "p2", "prompt": "<agent-message from="}),
            (HookKind.STOP, {"prompt_id": "p2", **_background("subagent", "completed")}),
        )

        assert m.session.to_info().attention is None
        assert gate_windows.elapse() == 1
        await m.until(lambda: "turn_finished" in m.attention)
        assert m.session.to_info().activity == "idle"
        assert m.attention == ["turn_finished"]

    async def test_a_hand_back_that_fails_before_the_answer_leaves_no_running_turn(
        self, open_chat, gate_windows, tmp_path
    ):
        """The hand-back cycle hit an API error, so no ``Stop`` follows its
        ``UserPromptSubmit``. The prompt answer must still end the turn: a
        chat that waits for that ``Stop`` would hold every new message."""
        release = tmp_path / "release"
        m = await open_chat(argv=_held_turn_agent(release), hook_port=HOOK_PORT)

        await self._turn(
            m,
            release,
            (HookKind.PROMPT_SUBMIT, {"prompt_id": "p1", "prompt": "work"}),
            (HookKind.STOP, {"prompt_id": "p1", **_background("subagent")}),
            (HookKind.PROMPT_SUBMIT, {"prompt_id": "p2", "prompt": "<agent-message from="}),
            (HookKind.STOP_FAILURE, {"prompt_id": "p2"}),
        )

        assert m.state["turn_started_at"] is None
        assert m.session.to_info().attention is None
        assert gate_windows.elapse() == 1
        await m.until(lambda: "turn_finished" in m.attention)
        assert m.session.to_info().activity == "idle"
        assert m.attention == ["turn_finished"]

        sent = await m.command("prompt", text="next")
        await m.until(lambda: m.status == "ready" and m.texts("agent").count("Working") == 2)

        assert sent["data"] == {"queued": False, "queue_id": None}
        assert m.texts("user") == ["work", "next"]

    async def test_work_left_behind_survives_a_queued_turn_with_no_hooks(
        self, open_chat, gate_windows, tmp_path
    ):
        """No alert comes between two prompts, but the workflow the first one
        left behind still runs when the second one, a local command, ends."""
        release = tmp_path / "release"
        m = await open_chat(argv=_held_turn_agent(release), hook_port=HOOK_PORT)
        await m.command("prompt", text="work")
        await m.until(lambda: "Working" in m.texts("agent"))
        queued = await m.command("prompt", text="second")
        assert queued["data"]["queued"] is True
        m.session.on_claude_hook(HookKind.PROMPT_SUBMIT, {"prompt_id": "p1", "prompt": "work"})
        m.session.on_claude_hook(HookKind.STOP, {"prompt_id": "p1", **_background("workflow")})

        release.touch()
        await m.until(
            lambda: m.status == "ready" and m.texts("agent").count("Working") == 2
        )

        assert m.texts("user") == ["work", "second"]
        info = m.session.to_info()
        assert (info.activity, info.attention) == ("working", None)
        assert gate_windows.pending == 0
        assert "turn_finished" not in m.attention

    async def test_a_turn_with_no_hooks_keeps_the_work_left_behind(
        self, open_chat, gate_windows
    ):
        """A local slash command runs no hook. It says nothing about the
        workflow the turn before left behind, so that one still runs."""
        m = await open_chat(argv=MINI_AGENT_ARGV, hook_port=HOOK_PORT)
        await self._agent_starts_a_turn(m)
        m.session.on_claude_hook(HookKind.STOP, {"prompt_id": "p1", **_background("workflow")})
        await m.until(lambda: m.status == "ready")

        await m.command("prompt", text="hello")
        await m.until(_turn_done(m, "echo hello"))

        info = m.session.to_info()
        assert (info.activity, info.attention) == ("working", None)
        assert gate_windows.pending == 0

    async def test_a_hook_after_the_agent_process_exited_changes_nothing(
        self, open_chat, gate_windows
    ):
        """A hook that was on its way when the process died must not show a
        running turn in a chat that waits for Restart."""
        m = await open_chat(hook_port=HOOK_PORT)
        await m.command("prompt", text="crash now")
        await m.until(lambda: m.status == "error")

        m.session.on_claude_hook(
            HookKind.PROMPT_SUBMIT, {"prompt_id": "p1", "prompt": TASK_NOTIFICATION}
        )
        m.session.on_claude_hook(HookKind.STOP, {"prompt_id": "p1"})
        refused = await m.command("prompt", text="hello?")

        assert refused["error"] == "The agent is not running. Use Restart to start it again."
        assert m.status == "error"
        info = m.session.to_info()
        assert (info.activity, info.attention) == (None, "ended")
        assert gate_windows.pending == 0

    async def test_the_next_prompt_ends_the_quiet_window_of_the_turn_before(
        self, open_chat, gate_windows, tmp_path
    ):
        """The user answers inside the window, and the ``UserPromptSubmit``
        hook of the new prompt is not there yet (or was lost): the turn before
        must not say "turn finished" while this one runs."""
        release = tmp_path / "release"
        m = await open_chat(argv=_held_turn_agent(release), hook_port=HOOK_PORT)
        await self._turn(
            m,
            release,
            (HookKind.PROMPT_SUBMIT, {"prompt_id": "p1", "prompt": "work"}),
            (HookKind.STOP, {"prompt_id": "p1", "background_tasks": []}),
        )
        assert gate_windows.pending == 1

        release.unlink()
        await m.command("prompt", text="work")
        await m.until(lambda: m.status == "running")
        gate_windows.elapse()

        info = m.session.to_info()
        assert (info.activity, info.attention) == ("working", None)

        # The new turn still alerts at its own end, once.
        release.touch()
        await m.until(lambda: m.status == "ready")
        assert gate_windows.elapse() == 1
        await m.until(lambda: "turn_finished" in m.attention)
        assert m.attention == ["turn_finished"]

    async def test_a_cli_that_sends_no_hooks_alerts_when_the_prompt_is_answered(
        self, open_chat, gate_windows
    ):
        m = await open_chat(hook_port=HOOK_PORT)

        await m.command("prompt", text="hello")
        await m.until(_turn_done(m, "You said: hello"))

        info = m.session.to_info()
        assert (info.activity, info.attention) == ("idle", "turn_finished")
        assert gate_windows.pending == 0

    async def test_a_turn_the_agent_starts_by_itself_runs_and_alerts_at_its_end(
        self, open_chat, gate_windows, tmp_path
    ):
        """The reported bug: the turn left a workflow behind, the chat said
        Ready at once, and nothing told the user when the work really ended."""
        release = tmp_path / "release"
        m = await open_chat(argv=_held_turn_agent(release), hook_port=HOOK_PORT)
        await self._turn(
            m,
            release,
            (HookKind.PROMPT_SUBMIT, {"prompt_id": "p1", "prompt": "work"}),
            (HookKind.STOP, {"prompt_id": "p1", **_background("workflow")}),
        )

        await self._agent_starts_a_turn(m, "p2")

        assert m.state["turn_started_at"] is not None
        assert _notices(m) == []
        assert m.session.to_info().activity == "working"

        m.session.on_claude_hook(
            HookKind.STOP, {"prompt_id": "p2", **_background("workflow", "completed")}
        )
        await m.until(lambda: m.status == "ready")

        assert m.state["turn_started_at"] is None
        assert m.session.to_info().attention is None
        assert gate_windows.elapse() == 1
        await m.until(lambda: "turn_finished" in m.attention)
        assert m.session.to_info().activity == "idle"
        assert m.attention == ["turn_finished"]

        # The agent wakes once more: the alert of the turn before is void.
        await self._agent_starts_a_turn(m, "p3")

        assert m.session.to_info().attention is None

    @pytest.mark.parametrize("end", [HookKind.STOP, HookKind.STOP_FAILURE], ids=["stop", "failure"])
    async def test_a_message_sent_during_the_agents_own_turn_waits_for_its_end(
        self, open_chat, gate_windows, end
    ):
        """After a failure too: the message that starts now reports its own end."""
        m = await open_chat(argv=MINI_AGENT_ARGV, hook_port=HOOK_PORT)
        await self._agent_starts_a_turn(m)
        queued = await m.command("prompt", text="hello")
        assert queued["data"]["queued"] is True
        assert m.texts("user") == []

        m.session.on_claude_hook(end, {"prompt_id": "p1"})
        await m.until(_turn_done(m, "echo hello"))

        assert m.texts("user") == ["hello"]
        assert m.state["queue"] == []
        assert m.session.to_info().attention is None
        assert gate_windows.elapse() == 1
        await m.until(lambda: "turn_finished" in m.attention)
        assert "turn_failed" not in m.attention

    async def test_send_now_during_the_agents_own_turn_sends_the_message_as_a_prompt(
        self, open_chat, gate_windows, tmp_path
    ):
        """The adapter steers only a turn it got as a prompt, so
        ``_session/steering`` would do nothing here. A prompt sent now joins
        the turn the agent runs, and this session's turn takes it over."""
        release = tmp_path / "release"
        log = tmp_path / "agent.log"
        m = await open_chat(argv=_held_turn_agent(release, log=log), hook_port=HOOK_PORT)
        assert m.state["steering"] is True
        await self._agent_starts_a_turn(m)
        queued = await m.command("prompt", text="also check the README")
        assert queued["data"]["queued"] is True

        steered = await m.command("steer", queue_id=queued["data"]["queue_id"])
        await m.until(lambda: "Working" in m.texts("agent"))

        assert steered["data"] == {"steered": True}
        assert m.state["queue"] == []
        assert m.texts("user") == ["also check the README"]
        assert m.status == "running"
        sent = [x for x in _methods(log) if x in ("session/prompt", "_session/steering")]
        assert sent == ["session/prompt"]

        m.session.on_claude_hook(
            HookKind.PROMPT_SUBMIT, {"prompt_id": "p2", "prompt": "also check the README"}
        )
        m.session.on_claude_hook(HookKind.STOP, {"prompt_id": "p2", "background_tasks": []})
        release.touch()
        await m.until(lambda: m.status == "ready")

        assert m.session.to_info().attention is None
        assert gate_windows.elapse() == 1
        await m.until(lambda: "turn_finished" in m.attention)
        assert m.session.to_info().activity == "idle"
        assert m.attention == ["turn_finished"]

    async def test_a_failed_turn_of_the_agents_own_alerts_turn_failed(
        self, open_chat, gate_windows
    ):
        m = await open_chat(argv=MINI_AGENT_ARGV, hook_port=HOOK_PORT)
        await self._agent_starts_a_turn(m)

        m.session.on_claude_hook(HookKind.STOP_FAILURE, {"prompt_id": "p1"})
        await m.until(lambda: m.status == "ready")

        info = m.session.to_info()
        assert (info.activity, info.attention) == ("idle", "turn_failed")
        assert gate_windows.pending == 0

    @pytest.mark.parametrize("own_turn", [True, False], ids=["sent-by-the-user", "started-by-the-agent"])
    async def test_a_stopped_turn_reads_idle_and_a_late_tool_call_does_not_revive_it(
        self, open_chat, gate_windows, tmp_path, own_turn
    ):
        """An interrupt fires no ``Stop`` hook, and no prompt answer ends a
        turn the agent started: the user's Stop has to end both."""
        log = tmp_path / "agent.log"
        m = await open_chat(argv=_logging_agent(log), hook_port=HOOK_PORT)
        if own_turn:
            await m.command("prompt", text="work")
            await m.until(lambda: "Working" in m.texts("agent"))
        await self._agent_starts_a_turn(m)
        m.session.on_claude_hook(HookKind.TOOL, {"prompt_id": "p1"})

        cancelled = await m.command("cancel")
        await m.until(lambda: m.status == "ready" and m.state["cancelling"] is False)
        # A tool call that was on its way when the turn was stopped.
        m.session.on_claude_hook(HookKind.TOOL, {"prompt_id": "p1"})

        assert cancelled["data"] == {"cancelled": True}
        info = m.session.to_info()
        assert (info.activity, info.attention) == ("idle", None)
        assert gate_windows.pending == 0
        await m.session.stop()
        assert "session/cancel" in _methods(log)

    async def test_a_restart_forgets_what_the_old_agent_process_reported(
        self, open_chat, gate_windows
    ):
        m = await open_chat(hook_port=HOOK_PORT)
        await self._agent_starts_a_turn(m)
        m.session.on_claude_hook(HookKind.STOP, {"prompt_id": "p1", **_background("workflow")})
        await m.until(lambda: m.status == "ready")
        assert m.session.to_info().activity == "working"

        await m.command("prompt", text="crash now")
        await m.until(lambda: m.status == "error")
        assert (await m.command("restart"))["ok"] is True
        await m.until(lambda: m.status == "ready")

        assert m.session.to_info().activity == "idle"
        # The new process has sent no hook yet, so the prompt answer alerts.
        await m.command("prompt", text="hello")
        await m.until(_turn_done(m, "You said: hello"))
        assert m.session.to_info().attention == "turn_finished"
        assert gate_windows.pending == 0


# Answers ``work`` at once. Then, as a finished background task would, it
# wakes when the file argv[1] + ".wake" appears: a tool call that runs until
# argv[1] + ".done" appears, then a closing message.
WAKING_AGENT = r"""
import json, os, sys, time
base = sys.argv[1]

def send(message):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    sys.stdout.flush()

def update(body):
    send({"method": "session/update", "params": {"sessionId": "woken", "update": body}})

def wait_for(path):
    while not os.path.exists(path):
        time.sleep(0.02)

for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg or "method" not in msg:
        continue
    method = msg["method"]
    if method == "initialize":
        send({"id": msg["id"], "result": {"protocolVersion": 1, "agentCapabilities": {}}})
    elif method == "session/new":
        send({"id": msg["id"], "result": {"sessionId": "woken"}})
    elif method == "session/prompt":
        update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Started"}})
        send({"id": msg["id"], "result": {"stopReason": "end_turn"}})
        wait_for(base + ".wake")
        update({"sessionUpdate": "tool_call", "toolCallId": "w1", "title": "Check",
                "kind": "execute", "status": "in_progress"})
        wait_for(base + ".done")
        update({"sessionUpdate": "tool_call_update", "toolCallId": "w1", "status": "completed"})
        update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Done"}})
    else:
        send({"id": msg["id"], "error": {"code": -32601, "message": "Method not found"}})
"""


# WAKING_AGENT with Copilot's session events. On argv[1] + ".wake" it sends
# ``assistant.turn_start`` only when argv[2] is "signalled", then a message;
# on argv[1] + ".done" it sends ``session.idle``.
COPILOT_WAKING_AGENT = r"""
import json, os, sys, time
base, flavor = sys.argv[1], sys.argv[2]

def send(message):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    sys.stdout.flush()

def update(body):
    send({"method": "session/update", "params": {"sessionId": "woken", "update": body}})

def event(kind):
    send({"method": "github.com/copilot/sessionEvent",
          "params": {"sessionId": "woken", "type": kind, "data": {}}})

def wait_for(path):
    while not os.path.exists(path):
        time.sleep(0.02)

for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg or "method" not in msg:
        continue
    method = msg["method"]
    if method == "initialize":
        send({"id": msg["id"], "result": {"protocolVersion": 1, "agentCapabilities": {}}})
    elif method == "session/new":
        send({"id": msg["id"], "result": {"sessionId": "woken"}})
    elif method == "session/prompt":
        event("assistant.turn_start")
        update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Started"}})
        event("assistant.idle")
        event("session.idle")
        send({"id": msg["id"], "result": {"stopReason": "end_turn"}})
        wait_for(base + ".wake")
        if flavor == "signalled":
            event("assistant.turn_start")
        update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Woke"}})
        wait_for(base + ".done")
        event("assistant.idle")
        event("session.idle")
    else:
        send({"id": msg["id"], "error": {"code": -32601, "message": "Method not found"}})
"""


class WakeWindows(GateWindows):
    """The quiet windows that end a turn the agent woke for by itself. One
    elapses only on ``elapse()``; every other timer runs as usual."""

    def call_later(self, delay: float, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if getattr(callback, "__func__", None) is not ChatSession._on_wake_quiet:
            return self._call_later(delay, callback, *args, **kwargs)
        window = _GateWindow(callback)
        self._windows.append(window)
        return window


@pytest.fixture
async def wake_windows(monkeypatch) -> WakeWindows:
    loop = asyncio.get_running_loop()
    windows = WakeWindows(loop.call_later)
    monkeypatch.setattr(loop, "call_later", windows.call_later)
    # The fake agent's work ends this long after the answer; no grace after
    # the answer, so the test need not wait it out.
    monkeypatch.setenv("MOBAIDE_FAKE_ACP_WAKE_DELAY", "0.3")
    monkeypatch.setattr(session_mod, "WAKE_GRACE_SECONDS", 0.0)
    return windows


def _says(m: TimelineMirror, text: str) -> bool:
    return any(text in said for said in m.texts("agent"))


class TestWakeAfterTurn:
    """OpenCode, Copilot and Antigravity run no hooks, but finished
    background work wakes them after the prompt's answer. The chat shows that
    work and alerts at its real end, not at the answer."""

    async def _woken(self, m: TimelineMirror, prompt: str, answer: str) -> None:
        """The answer of ``prompt``, then the turn the agent wakes for."""
        await m.command("prompt", text=prompt)
        await m.until(lambda: m.status == "ready" and _says(m, answer))
        await m.until(lambda: m.status == "running")

    async def test_a_turn_the_agent_wakes_for_runs_until_its_quiet_window_and_alerts(
        self, open_chat, wake_windows
    ):
        m = await open_chat(argv=fake_agent_argv("antigravity"), wakes_after_turn=True)
        await self._woken(m, "background", "I started the tests in the background.")

        assert m.state["turn_started_at"] is not None
        assert m.session.to_info().attention is None
        await m.until(lambda: _says(m, "The background tests passed."))
        assert m.status == "running"

        assert wake_windows.elapse() == 1
        await m.until(lambda: m.status == "ready")

        assert m.session.to_info().attention == "turn_finished"
        # One alert at the answer (no work was known then), one at the real end.
        await m.until(lambda: m.attention.count("turn_finished") == 2)

    async def test_an_agent_that_does_not_wake_after_a_turn_shows_no_turn_of_its_own(
        self, open_chat, wake_windows
    ):
        """Every other agent keeps the behaviour it had."""
        m = await open_chat(argv=fake_agent_argv("antigravity"))
        await m.command("prompt", text="background")
        await m.until(lambda: m.status == "ready" and _says(m, "The background tests passed."))

        assert m.state["turn_started_at"] is None
        assert wake_windows.pending == 0
        statuses = [
            ev["patch"]["status"]
            for frame in m.frames
            if frame["t"] == "events"
            for ev in frame["events"]
            if ev["type"] == "state" and "status" in ev["patch"]
        ]
        # Only the prompt the user sent ran.
        assert statuses.count("running") == 1

    async def test_a_tool_still_running_keeps_the_woken_turn_open(
        self, open_chat, wake_windows, tmp_path
    ):
        base = tmp_path / "agent"
        m = await open_chat(argv=[sys.executable, "-c", WAKING_AGENT, str(base)], wakes_after_turn=True)
        await m.command("prompt", text="work")
        await m.until(lambda: m.status == "ready" and _says(m, "Started"))
        (tmp_path / "agent.wake").touch()
        await m.until(lambda: m.status == "running" and "w1" in m.items)

        assert wake_windows.elapse() == 1

        assert m.status == "running"
        assert wake_windows.pending == 1
        (tmp_path / "agent.done").touch()
        await m.until(lambda: _says(m, "Done") and m.items["w1"]["status"] == "completed")
        assert wake_windows.elapse() == 1
        await m.until(lambda: m.status == "ready")
        assert m.session.to_info().attention == "turn_finished"

    async def test_stop_ends_a_woken_turn_with_no_alert(self, open_chat, wake_windows, tmp_path):
        base = tmp_path / "agent"
        m = await open_chat(argv=[sys.executable, "-c", WAKING_AGENT, str(base)], wakes_after_turn=True)
        await m.command("prompt", text="work")
        await m.until(lambda: m.status == "ready" and _says(m, "Started"))
        (tmp_path / "agent.wake").touch()
        await m.until(lambda: m.status == "running" and "w1" in m.items)

        cancelled = await m.command("cancel")
        await m.until(lambda: m.status == "ready")

        assert cancelled["data"] == {"cancelled": True}
        assert m.items["w1"]["status"] == "cancelled"
        assert m.session.to_info().attention is None
        assert wake_windows.pending == 0

    async def test_opencode_subagents_show_as_background_work_and_hold_the_alert(
        self, open_chat, wake_windows
    ):
        m = await open_chat(
            argv=fake_agent_argv("opencode"),
            wakes_after_turn=True,
            client_meta=OPENCODE_CLIENT_META,
        )
        await m.command("prompt", text="background")
        await m.until(
            lambda: m.status == "ready" and _says(m, "I started a subagent to explore the code.")
        )

        assert m.state["background"] == "working"
        info = m.session.to_info()
        assert (info.activity, info.attention) == ("working", None)
        child_tools = [t for t in m.of_type("tool") if t["id"].startswith("ses_child")]
        assert [t["title"] for t in child_tools] == ["Explore: Read app.py"]
        assert not _says(m, "child text")

        # The subagent ends and the parent wakes to report its result.
        await m.until(lambda: m.status == "running")
        await m.until(lambda: _says(m, "The subagent finished: the code looks fine."))
        assert m.state["background"] is None
        assert wake_windows.elapse() == 1
        await m.until(lambda: m.status == "ready")

        assert m.session.to_info().attention == "turn_finished"
        await m.until(lambda: "turn_finished" in m.attention)
        assert m.attention.count("turn_finished") == 1

    async def test_a_copilot_turn_that_waits_on_its_async_shell_reads_background_task(
        self, open_chat, wake_windows
    ):
        m = await open_chat(
            argv=fake_agent_argv("copilot"),
            wakes_after_turn=True,
            client_meta=COPILOT_CLIENT_META,
        )
        await m.command("prompt", text="background")
        await m.until(lambda: m.state.get("background") == "waiting")

        assert m.status == "running"
        assert m.session.to_info().activity == "waiting"

        await m.until(lambda: m.status == "ready" and _says(m, "The background tests passed."))
        assert m.state["background"] is None
        assert m.session.to_info().attention == "turn_finished"
        assert wake_windows.pending == 0

    async def test_copilot_session_idle_ends_a_woken_turn_with_no_quiet_window(
        self, open_chat, wake_windows
    ):
        m = await open_chat(
            argv=fake_agent_argv("copilot"),
            wakes_after_turn=True,
            client_meta=COPILOT_CLIENT_META,
        )
        await self._woken(m, "detached", "I started the tests in the background.")

        await m.until(lambda: m.status == "ready" and _says(m, "The background tests passed."))

        assert m.session.to_info().attention == "turn_finished"
        assert wake_windows.pending == 0
        await m.until(lambda: m.attention.count("turn_finished") == 2)

    async def test_a_woken_turn_whose_tool_runs_on_ends_after_the_silent_limit(
        self, open_chat, wake_windows, tmp_path, monkeypatch
    ):
        """A dev server or a watcher never completes: the turn must not hold
        the chat for good."""
        monkeypatch.setattr(session_mod, "WAKE_MAX_SILENT_SECONDS", 0.0)
        base = tmp_path / "agent"
        m = await open_chat(argv=[sys.executable, "-c", WAKING_AGENT, str(base)], wakes_after_turn=True)
        await m.command("prompt", text="work")
        await m.until(lambda: m.status == "ready" and _says(m, "Started"))
        (tmp_path / "agent.wake").touch()
        await m.until(lambda: m.status == "running" and "w1" in m.items)

        assert wake_windows.elapse() == 1
        await m.until(lambda: m.status == "ready")

        assert m.items["w1"]["status"] == "in_progress"
        assert m.session.to_info().attention == "turn_finished"
        assert wake_windows.pending == 0

    @pytest.mark.parametrize("flavor", ["signalled", "unsignalled"])
    async def test_a_copilot_woken_turn_ends_at_session_idle_only_when_turn_start_opened_it(
        self, open_chat, wake_windows, tmp_path, flavor
    ):
        """With ``assistant.turn_start`` the model may think quietly past the
        quiet window, as ``session.idle`` ends the turn. Without it the
        quiet window ends the turn."""
        base = tmp_path / "agent"
        m = await open_chat(
            argv=[sys.executable, "-c", COPILOT_WAKING_AGENT, str(base), flavor],
            wakes_after_turn=True,
            client_meta=COPILOT_CLIENT_META,
        )
        await m.command("prompt", text="work")
        await m.until(lambda: m.status == "ready" and _says(m, "Started"))
        (tmp_path / "agent.wake").touch()
        await m.until(lambda: m.status == "running" and _says(m, "Woke"))

        assert wake_windows.elapse() == 1

        if flavor == "signalled":
            assert m.status == "running"
            assert m.session.to_info().attention is None
            assert wake_windows.pending == 1
            (tmp_path / "agent.done").touch()
        await m.until(lambda: m.status == "ready")
        assert m.session.to_info().attention == "turn_finished"
        assert wake_windows.pending == 0
