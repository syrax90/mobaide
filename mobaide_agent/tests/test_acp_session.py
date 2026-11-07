"""Chat sessions driven end to end against the scripted fake ACP agent
(``mobaide/acp/fake_agent.py``, a real subprocess): start, prompts, the
message queue, cancel, permission and form requests, config changes, a
crash with restart, and opening a stored session.

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
from typing import Any, Callable

import orjson
import psutil
import pytest

from mobaide.acp import session as session_mod
from mobaide.acp.agents import ChatLaunchPlan, ConfigIntent, fake_agent_argv
from mobaide.acp.session import ChatSession
from mobaide.core.models import SessionConfig, SessionState

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
            elif ev["type"] == "state":
                self.state.update(ev["patch"])
            self.seq = ev["seq"]
        return False


@pytest.fixture
async def open_chat(tmp_path):
    sessions: list[ChatSession] = []

    async def open_(
        load_session_id: str | None = None, argv: list[str] | None = None, **plan_fields: Any
    ) -> TimelineMirror:
        config = SessionConfig(
            session_id=f"chat-{len(sessions) + 1}",
            command=[],
            cwd=str(tmp_path),
            ui="chat",
        )
        plan = ChatLaunchPlan(
            argv=argv or fake_agent_argv(), env=dict(os.environ), cwd=str(tmp_path), **plan_fields
        )
        session = ChatSession(
            config, plan, agent_title="Claude Code", load_session_id=load_session_id
        )
        sessions.append(session)
        await session.start()
        mirror = TimelineMirror(session)
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
        await m.until(lambda: bool(m.state.get("commands")))
        assert [c["name"] for c in m.state["commands"]] == ["review", "test", "compact"]

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

    async def test_initial_config_is_applied_to_an_opened_session(self, open_chat):
        m = await open_chat(
            load_session_id="external-1111",
            initial_config=[ConfigIntent("fast", category="model"), ConfigIntent(True, option_id="fast")],
        )

        assert m.status == "ready"
        values = {o["id"]: o["value"] for o in m.state["config"]}
        assert (values["model"], values["fast"]) == ("fast", True)

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

    async def test_unknown_command(self, open_chat):
        m = await open_chat()
        result = await m.command("explode")
        assert result["error"] == "Unknown command: explode"


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

    async def test_cancel_without_a_turn(self, open_chat):
        m = await open_chat()
        result = await m.command("cancel")
        assert result["data"] == {"cancelled": False}

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

    async def test_form_decline(self, open_chat):
        m = await open_chat()
        await m.command("prompt", text="question")
        await m.until(lambda: bool(m.state.get("requests")))

        await m.command("respond", request_id=m.state["requests"][0]["id"], action="decline")

        await m.until(_turn_done(m, "No problem — you chose to decline the question."))


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
