"""End-to-end chat session (ACP) over the real relay: the chat capability,
the stored-sessions route, a chat create through the proxy, and the ``'c'``
command / ``'a'`` frame traffic on the relay session socket.

The agent runs with ``MOBAIDE_ENV=test``, so a chat session starts the
scripted fake ACP agent (``mobaide/acp/fake_agent.py``) instead of the real
adapter. It is a dedicated agent (``spawn_agent``) whose
``MOBAIDE_CLAUDE_BIN`` points at an existing file: building a Claude launch
resolves the CLI binary even though the fake agent never runs it.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import sys
import time
from urllib.parse import quote

import pytest
import websockets

from mobaide.acp.wire import FRAME_CHAT, FRAME_CHAT_COMMAND, LEGACY_CLIENT_NOTICE
from mobaide.agent.ws.session_io import FRAME_OUTPUT, FRAME_REPLAY_END

pytestmark = pytest.mark.timeout(180)

_CHAT_AGENT_ENV = {
    "MOBAIDE_CLAUDE_BIN": sys.executable,
    "MOBAIDE_ACP_REAL": "",
    "MOBAIDE_ACP_CLAUDE_COMMAND": "",
    "MOBAIDE_ACP_ANTIGRAVITY_COMMAND": "",
}


def _wait_until(predicate, *, timeout: float = 30.0, interval: float = 0.25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    raise TimeoutError(f"condition not met within {timeout}s")


class ChatSocket:
    """A chat client on the relay session socket: sends ``'c'`` commands and
    applies ``'a'`` frames; a new epoch or a gap sends ``sync``."""

    def __init__(self, ws) -> None:
        self.ws = ws
        self.items: dict[str, dict] = {}
        self.state: dict = {}
        self.results: dict[str, dict] = {}
        self.snapshots = 0
        self.epoch: str | None = None
        self.seq = 0
        self._waiting_snapshot = False
        self._ids = itertools.count(1)

    @property
    def status(self) -> str | None:
        return self.state.get("status")

    def texts(self, kind: str) -> list[str]:
        return [i.get("text", "") for i in self.items.values() if i["type"] == kind]

    async def attach(self, timeout: float = 15.0) -> bytes:
        """Consume the attach handshake; return the ``'o'`` output inside it."""
        output = bytearray()

        async def loop() -> bytes:
            while True:
                msg = await self.ws.recv()
                if isinstance(msg, str) or not msg:
                    continue
                if msg[:1] == FRAME_OUTPUT:
                    output.extend(msg[1:])
                elif msg[:1] == FRAME_REPLAY_END:
                    return bytes(output)

        return await asyncio.wait_for(loop(), timeout)

    async def send(self, op: str, **fields) -> str:
        cmd_id = f"e2e-{next(self._ids)}"
        body = {"id": cmd_id, "op": op, **fields}
        await self.ws.send(FRAME_CHAT_COMMAND + json.dumps(body).encode())
        return cmd_id

    async def command(self, op: str, **fields) -> dict:
        cmd_id = await self.send(op, **fields)
        await self.until(lambda: cmd_id in self.results)
        return self.results[cmd_id]

    async def until(self, predicate, timeout: float = 30.0) -> None:
        async def loop() -> None:
            while not predicate():
                msg = await self.ws.recv()
                if isinstance(msg, str) or msg[:1] != FRAME_CHAT:
                    continue
                if self._apply(json.loads(msg[1:])):
                    await self.ws.send(FRAME_CHAT_COMMAND + b'{"op": "sync"}')

        await asyncio.wait_for(loop(), timeout)

    def _apply(self, frame: dict) -> bool:
        if frame["t"] == "result":
            self.results[frame["id"]] = frame
            return False
        if frame["t"] == "snapshot":
            self.snapshots += 1
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


async def _drive_chat(url: str) -> ChatSocket:
    async with websockets.connect(url, open_timeout=15, max_size=8 * 1024 * 1024) as ws:
        chat = ChatSocket(ws)
        assert await chat.attach() == LEGACY_CLIENT_NOTICE

        await chat.send("sync")
        await chat.until(lambda: chat.snapshots > 0 and chat.status == "ready")
        assert chat.state["agent"]["title"] == "Fake Agent"
        assert [o["id"] for o in chat.state["config"]] == ["mode", "model", "effort", "fast"]

        sent = await chat.command("prompt", text="hello over the relay")
        assert sent["ok"] is True
        await chat.until(
            lambda: chat.status == "ready" and "You said: hello over the relay" in chat.texts("agent")
        )

        await chat.command("prompt", text="permission please")
        await chat.until(lambda: bool(chat.state.get("requests")))
        request = chat.state["requests"][0]
        assert request["kind"] == "permission"
        assert [o["id"] for o in request["options"]] == ["allow", "always", "reject"]

        answered = await chat.command("respond", request_id=request["id"], option_id="allow")
        assert answered["ok"] is True
        await chat.until(
            lambda: chat.status == "ready" and "The build folder is gone." in chat.texts("agent")
        )
        assert chat.state["requests"] == []
        assert chat.items[request["tool_call_id"]]["status"] == "completed"
        return chat


def test_chat_session_over_the_relay(e2e_stack, spawn_agent, tmp_path):
    client = e2e_stack.client
    agent = spawn_agent("e2e-chat-agent", extra_env=_CHAT_AGENT_ENV)
    agent_id = agent.agent_id
    _wait_until(
        lambda: any(a["agent_id"] == agent_id and a["online"] for a in client.list_agents())
    )
    token = client.mint_mobile_token(agent_id)
    cwd = str(tmp_path)

    version = client.proxy_request(agent_id, token, "GET", "api/version")
    assert version.status_code == 200
    assert version.json()["capabilities"]["chat"] == {
        "claude": {"resume": True, "live": False},
        "codex": {"resume": True, "live": False},
        "antigravity": {"resume": True, "live": False},
    }

    stored_path = f"api/chat/claude/sessions?cwd={quote(cwd)}"
    stored = client.proxy_request(agent_id, token, "GET", stored_path)
    assert stored.status_code == 200, stored.text
    rows = stored.json()["sessions"]
    assert [r["session_id"] for r in rows] == ["external-1111", "external-2222"]
    assert {r["cwd"] for r in rows} == {cwd}
    assert {r["open_session_id"] for r in rows} == {None}

    # Antigravity lists every folder: the agent keeps this one's rows, newest
    # first, each saying whether another app on the PC has it open.
    agy_path = f"api/chat/antigravity/sessions?cwd={quote(cwd)}"
    agy_rows = client.proxy_request(agent_id, token, "GET", agy_path)
    assert agy_rows.status_code == 200, agy_rows.text
    assert [(r["session_id"], r["open_elsewhere"]) for r in agy_rows.json()["sessions"]] == [
        ("external-1111", False),
        ("external-2222", False),
    ]

    chat_body = {
        "cli": "claude",
        "ui": "chat",
        "cwd": cwd,
        "claude_options": {"skip_permissions": False},
    }
    created = client.proxy_request(agent_id, token, "POST", "api/sessions", chat_body)
    assert created.status_code == 201, created.text
    assert created.json()["ui"] == "chat"
    session_id = created.json()["session_id"]

    url = f"{client.ws_base}/relay/ws/session/{agent_id}/{session_id}?token={token}"
    asyncio.run(_drive_chat(url))

    listed = client.proxy_request(agent_id, token, "GET", "api/sessions")
    assert {s["session_id"]: s["ui"] for s in listed.json()}[session_id] == "chat"

    resumed = client.proxy_request(
        agent_id, token, "POST", "api/sessions", {**chat_body, "chat_resume_id": "external-1111"}
    )
    assert resumed.status_code == 201, resumed.text
    stored_after = client.proxy_request(agent_id, token, "GET", stored_path).json()["sessions"]
    assert {r["session_id"]: r["open_session_id"] for r in stored_after} == {
        "external-1111": resumed.json()["session_id"],
        "external-2222": None,
    }


async def _fail_a_turn(client, url: str, session_id: str) -> dict:
    async with websockets.connect(client.user_events_url(), open_timeout=15) as events:
        async with websockets.connect(url, open_timeout=15, max_size=8 * 1024 * 1024) as ws:
            chat = ChatSocket(ws)
            await chat.attach()
            await chat.send("sync")
            await chat.until(lambda: chat.snapshots > 0 and chat.status == "ready")

            sent = await chat.command("prompt", text="please fail")
            assert sent["ok"] is True
            return (await client.attention_until(events, session_id))[-1]


def test_failed_chat_turn_alerts_the_owner(e2e_stack, spawn_agent, tmp_path):
    client = e2e_stack.client
    agent = spawn_agent("e2e-chat-fail-agent", extra_env=_CHAT_AGENT_ENV)
    agent_id = agent.agent_id
    _wait_until(
        lambda: any(a["agent_id"] == agent_id and a["online"] for a in client.list_agents())
    )
    token = client.mint_mobile_token(agent_id)
    chat_body = {
        "cli": "claude",
        "ui": "chat",
        "cwd": str(tmp_path),
        "claude_options": {"skip_permissions": False},
    }
    created = client.proxy_request(agent_id, token, "POST", "api/sessions", chat_body)
    assert created.status_code == 201, created.text
    session_id = created.json()["session_id"]

    url = f"{client.ws_base}/relay/ws/session/{agent_id}/{session_id}?token={token}"
    alert = asyncio.run(_fail_a_turn(client, url, session_id))

    assert alert["reason"] == "turn_failed"
    assert alert["kind"] == "claude"
    assert alert["agent_id"] == agent_id


def test_chat_is_refused_for_a_kind_without_a_chat_agent(e2e_stack, tmp_path):
    client = e2e_stack.client
    token = client.mint_mobile_token(e2e_stack.agent_id)

    refused = client.proxy_request(
        e2e_stack.agent_id, token, "POST", "api/sessions", {"ui": "chat", "cwd": str(tmp_path)}
    )

    assert refused.status_code == 400
    assert refused.json()["detail"] == "The chat view is not available for shell sessions"
