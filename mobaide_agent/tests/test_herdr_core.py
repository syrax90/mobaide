"""Tests for the herdr-core backend (``mobaide.herdr.core``).

``FakeCore`` is a scripted herdr-core on an in-memory stream: it speaks the
real wire format (the ``hello`` line, then ``[channel u32][kind u8][length
u32]`` frames), so the connection, the link and the pane streams run their
real code. No test opens a socket or starts ``hc``.

What breaks when these fail: a pane view that loses its first frames, a
desktop terminal left at phone size, half-typed input, and a client action
that reaches a herdr-core method nobody reviewed.
"""
from __future__ import annotations

import asyncio
import json
import struct
from typing import Any

import pytest

from mobaide.herdr import core, errors
from mobaide.herdr.agents import AgentChoice
from mobaide.herdr.backend import CONTROL, WATCH, ServerGone
from mobaide.herdr.core import (
    FRAME_CLOSE,
    FRAME_CREDIT,
    FRAME_DATA,
    FRAME_RESIZE,
    CoreApiError,
    CoreConnection,
    CoreLink,
    build_server,
    keys_for_bytes,
    names_from_document,
)
from mobaide.herdr.models import HerdrFeatures

from .herdr_support import settle

SERVER_ID = "core:default"
AGENT = "t-agent"
SHELL = "t-shell"
STREAM_CHANNEL = 7

# Every herdr-core method the link may send (see ``ALLOWED_METHODS`` in
# ``test_herdr_tui.py`` for why the list is pinned).
ALLOWED_METHODS = frozenset(
    {
        "events.subscribe",
        "terminal.list",
        "doc.get",
        "terminal.read",
        "terminal.keys",
        "terminal.send",
        "terminal.run",
        "terminal.kill",
        "terminal.rm",
        "terminal.attach",
    }
)


def _terminals() -> list[dict[str, Any]]:
    return [
        {
            "address": AGENT, "state": "live", "created_ms": 2, "cols": 120, "rows": 40,
            "name": "api refactor", "reported": True, "turn": 3,
            "status": {"app": "claude-code", "state": "error"},
            "foreground": {"name": "node", "cwd": "/work/api"},
            "labels": {"herdr.space": "sp-1", "herdr.tab": "tab-1", "other": "x"},
        },
        {
            "address": SHELL, "state": "live", "created_ms": 1, "cols": 80, "rows": 24,
            "argv": ["/bin/zsh", "-l"], "cwd": "/work", "turn": 9,
            "status": {"app": "claude", "state": "working"},
        },
        {"address": "t-dead", "state": "exited", "created_ms": 0},
    ]


class FakeCore:
    def __init__(self) -> None:
        self.reader = asyncio.StreamReader()
        self.framed = True
        self.terminals = _terminals()
        self.document: object = {
            "spaces": [{"id": "sp-1", "name": "Mobaide", "tabs": [{"id": "tab-1", "title": "API"}]}]
        }
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.frames: list[tuple[int, int, bytes]] = []
        self.failures: dict[str, dict[str, Any]] = {}
        # Stream frames sent ahead of the ``terminal.attach`` reply.
        self.early: list[tuple[int, bytes]] = []
        self.closed = False
        # Alive, and it does not read: a write never returns.
        self.stalled = False
        self._said_hello = False
        self._buffer = b""

    async def open(self, _path: str) -> "FakeCore":
        return self

    async def aclose(self) -> None:
        if not self.closed:
            self.closed = True
            self.reader.feed_eof()

    def push(self, channel: int, kind: int, payload: bytes = b"") -> None:
        """Send one frame to the client."""
        self.reader.feed_data(struct.pack("<IBI", channel, kind, len(payload)) + payload)

    def sent(self, method: str) -> list[dict[str, Any]]:
        return [params for name, params in self.calls if name == method]

    @property
    def methods(self) -> set[str]:
        return {name for name, _ in self.calls}

    def stream_frames(self) -> list[tuple[int, bytes]]:
        return [(kind, payload) for channel, kind, payload in self.frames
                if channel == STREAM_CHANNEL and kind != FRAME_CREDIT]

    async def write(self, data: bytes) -> None:
        if self.stalled:
            await asyncio.Event().wait()
        if not self._said_hello:
            self._said_hello = True
            hello = json.loads(data)
            assert hello["method"] == "hello" and hello["params"]["framed"] is True
            result = {"framed": self.framed, "version": "0.1.21"}
            self.reader.feed_data(json.dumps({"jsonrpc": "2.0", "id": 0, "result": result}).encode() + b"\n")
            return
        self._buffer += data
        while len(self._buffer) >= 9:
            channel, kind, length = struct.unpack("<IBI", self._buffer[:9])
            if len(self._buffer) < 9 + length:
                return
            payload, self._buffer = self._buffer[9 : 9 + length], self._buffer[9 + length :]
            if channel == 0 and kind == FRAME_DATA:
                self._answer(json.loads(payload))
            else:
                self.frames.append((channel, kind, payload))

    def _answer(self, request: dict[str, Any]) -> None:
        method, params = request["method"], request["params"]
        self.calls.append((method, params))
        failure = self.failures.get(method)
        if failure is not None and not params.get("force"):
            reply = {"jsonrpc": "2.0", "id": request["id"], "error": failure}
        else:
            reply = {"jsonrpc": "2.0", "id": request["id"], "result": self._result(method)}
        self.push(0, FRAME_DATA, json.dumps(reply).encode())

    def _result(self, method: str) -> dict[str, Any]:
        if method == "events.subscribe":
            return {"channel": 1, "terminals": self.terminals}
        if method == "terminal.list":
            return {"terminals": self.terminals}
        if method == "doc.get":
            return {"value": self.document}
        if method == "terminal.attach":
            for kind, payload in self.early:
                self.push(STREAM_CHANNEL, kind, payload)
            return {"channel": STREAM_CHANNEL}
        if method == "terminal.run":
            return {"address": "t-new"}
        if method == "terminal.read":
            return {"text": "last line", "complete": False}
        return {}


class Sink:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def on_output(self, data: bytes, *, repaint: bool) -> None:
        self.events.append(("output", data, repaint))

    def on_grid(self, cols: int, rows: int) -> None:
        self.events.append(("grid", cols, rows))

    def on_closed(self, reason: str | None) -> None:
        self.events.append(("closed", reason))


@pytest.fixture
async def fake(monkeypatch) -> FakeCore:
    # Async: the stream reader binds to the running loop.
    server = FakeCore()
    monkeypatch.setattr(core, "open_local_stream", server.open)
    return server


@pytest.fixture
async def conn(fake):
    connection = CoreConnection("/unused/core.sock")
    await connection.open()
    yield connection
    await connection.aclose()


@pytest.fixture
async def link(fake):
    agents = [AgentChoice(id="claude", label="Claude Code", binary="/opt/my tools/claude")]
    core_link = CoreLink(SERVER_ID, "default", "/unused/core.sock", lambda: None, lambda: agents)
    task = asyncio.create_task(core_link.run())
    await settle()
    assert core_link.state() is not None
    yield core_link
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


class TestKeys:
    @pytest.mark.parametrize(
        "data,keys",
        [
            (b"ls\r", ["l", "s", "enter"]),
            (b"a b", ["a", "space", "b"]),
            (b"\x03", ["ctrl+c"]),
            (b"\t\x7f", ["tab", "backspace"]),
            (b"\x1b", ["esc"]),
            (b"\x1b[A\x1bOB", ["up", "down"]),
            (b"\x1b[Z", ["shift+tab"]),
            (b"\x1bb", ["alt+b"]),
            (b"Y", ["shift+y"]),
            (b"\\", ["backslash"]),
        ],
    )
    def test_input_herdr_core_can_name(self, data, keys) -> None:
        assert keys_for_bytes(data) == keys

    @pytest.mark.parametrize(
        "data",
        [
            "é".encode(),
            b"\xff",
            b"\x1b[200~pasted\x1b[201~",
            b"\x1b[1;5C",
            # F1 and F4. Read as alt+o and a letter, they would type "P" or "S".
            b"\x1bOP",
            b"\x1bOS",
            # One key without a name refuses the whole input: half a command
            # typed into a shell is worse than none.
            "rm é".encode(),
            b"ok!",
        ],
    )
    def test_input_without_a_key_name_is_refused_whole(self, data) -> None:
        assert keys_for_bytes(data) is None


class TestTree:
    def test_the_tree_comes_from_the_desktop_apps_labels(self) -> None:
        server = build_server(
            server_id=SERVER_ID, name="default", version="0.1.21",
            terminals=_terminals(),
            names={"sp-1": "Mobaide", "tab-1": "API"},
            features=HerdrFeatures(),
        )

        assert [(ws.workspace_id, ws.label) for ws in server.workspaces] == [
            ("sp-1", "Mobaide"),
            # Terminals without labels share one group, after the spaces.
            ("", "Terminals"),
        ]
        labelled, loose = (ws.tabs[0] for ws in server.workspaces)
        assert (labelled.tab_id, labelled.label) == ("tab-1", "API")
        assert [pane.model_dump() for pane in labelled.panes] == [
            {
                "pane_id": AGENT, "title": "api refactor", "agent": "claude",
                "status": "failed", "turn": 3, "cwd": "/work/api",
                "agent_session_id": None, "rect": None,
            }
        ]
        # A program that reported no state of its own is a plain terminal,
        # whatever stale status the record holds. A finished terminal is left out.
        assert [pane.model_dump() for pane in loose.panes] == [
            {
                "pane_id": SHELL, "title": "zsh", "agent": None, "status": None,
                "turn": None, "cwd": "/work", "agent_session_id": None, "rect": None,
            }
        ]

    def test_a_space_without_a_known_name_gets_a_number(self) -> None:
        server = build_server(
            server_id=SERVER_ID, name="default", version=None,
            terminals=_terminals(), names={}, features=HerdrFeatures(),
        )

        assert server.workspaces[0].label == "Space 1"
        assert server.workspaces[0].tabs[0].label == "Tab 1"

    @pytest.mark.parametrize(
        "document,names",
        [
            ({"spaces": [{"id": "a", "name": " Site "}, {"id": "b", "title": "Docs"}]},
             {"a": "Site", "b": "Docs"}),
            ({"id": "a", "name": "", "label": "Fallback"}, {"a": "Fallback"}),
            ({"id": 7, "name": "numeric id"}, {}),
            (None, {}),
            ("a string", {}),
            ([1, [2, {"name": "no id"}]], {}),
        ],
    )
    def test_names_are_read_from_any_document_shape(self, document, names) -> None:
        assert names_from_document(document) == names


class TestConnection:
    async def test_a_server_that_refuses_framing_is_gone(self, fake) -> None:
        fake.framed = False

        with pytest.raises(ServerGone):
            await CoreConnection("/unused").open()

        assert fake.closed

    async def test_a_call_gets_its_own_reply(self, fake, conn) -> None:
        listed, document = await asyncio.gather(
            conn.call("terminal.list", {}), conn.call("doc.get", {"name": "herdr-app"})
        )

        assert listed == {"terminals": fake.terminals}
        assert document == {"value": fake.document}

    async def test_an_error_reply_carries_herdr_cores_error_kind(self, fake, conn) -> None:
        fake.failures["terminal.kill"] = {"message": "no such terminal", "data": {"kind": "not_found"}}

        with pytest.raises(CoreApiError) as failed:
            await conn.call("terminal.kill", {"terminal": "t-x"})

        assert (failed.value.kind, failed.value.message) == ("not_found", "no such terminal")

    async def test_frames_ahead_of_the_reply_wait_for_their_handler(self, fake, conn) -> None:
        # The core sends the first paint right behind the reply that names
        # the channel, before the caller can listen on it.
        fake.early = [(FRAME_DATA, b"\x18first paint"), (FRAME_DATA, b"diff")]
        received: list[tuple[int, bytes]] = []

        reply = await conn.call("terminal.attach", {"terminal": AGENT})
        await settle()
        conn.on_channel(reply["channel"], lambda kind, payload: received.append((kind, payload)))
        fake.push(STREAM_CHANNEL, FRAME_DATA, b"later")
        await settle()

        assert received == [
            (FRAME_DATA, b"\x18first paint"), (FRAME_DATA, b"diff"), (FRAME_DATA, b"later"),
        ]

    async def test_every_data_frame_is_paid_back_in_credit(self, fake, conn) -> None:
        # The core stops a stream that gets no credit for the bytes it sent.
        conn.on_channel(STREAM_CHANNEL, lambda _kind, _payload: None)

        fake.push(STREAM_CHANNEL, FRAME_DATA, b"12345")
        fake.push(STREAM_CHANNEL, FRAME_RESIZE, struct.pack("<HH", 80, 24))
        await settle()

        assert fake.frames == [(STREAM_CHANNEL, FRAME_CREDIT, struct.pack("<I", 5))]

    async def test_a_lost_connection_fails_calls_and_closes_streams(self, fake, conn) -> None:
        closed: list[int] = []
        conn.on_channel(STREAM_CHANNEL, lambda kind, _payload: closed.append(kind))
        fake.failures.clear()
        pending = asyncio.ensure_future(conn.call("terminal.list", {}))
        fake._answer = lambda _request: None  # the server never answers
        await settle()

        await fake.aclose()
        await settle()

        with pytest.raises(ServerGone):
            await pending
        assert closed == [FRAME_CLOSE]
        assert conn.closed.is_set()
        with pytest.raises(ServerGone):
            await conn.call("terminal.list", {})

    async def test_a_core_that_does_not_read_ends_the_connection_in_time(
        self, fake, conn, monkeypatch
    ) -> None:
        # A write without a deadline would hold the write lock, and with it
        # every later call, for as long as that core lives.
        monkeypatch.setattr(core, "CALL_TIMEOUT_SECONDS", 0.01)
        closed: list[int] = []
        conn.on_channel(STREAM_CHANNEL, lambda kind, _payload: closed.append(kind))
        fake.stalled = True

        with pytest.raises(ServerGone):
            await asyncio.wait_for(conn.call("terminal.list", {}), 5)
        await settle()

        assert conn.closed.is_set()
        assert closed == [FRAME_CLOSE]
        assert fake.closed
        with pytest.raises(ServerGone):
            await asyncio.wait_for(conn.send(STREAM_CHANNEL, FRAME_DATA, b"y"), 5)

    async def test_an_oversized_frame_ends_the_connection(self, fake, conn) -> None:
        fake.reader.feed_data(struct.pack("<IBI", STREAM_CHANNEL, FRAME_DATA, core.MAX_FRAME_BYTES + 1))
        await settle()

        assert conn.closed.is_set()


class TestLink:
    async def test_the_mirror_names_spaces_from_the_app_document(self, fake, link) -> None:
        server = link.state()

        assert (server.server_id, server.product, server.version) == (SERVER_ID, "core", "0.1.21")
        assert [ws.label for ws in server.workspaces] == ["Mobaide", "Terminals"]
        # No tabs or workspaces of its own, and only named keys in watch mode.
        assert server.features.model_dump(exclude={"agents"}) == {
            "new_workspace": True, "new_tab": False, "split": True, "rename": False,
            "close_group": False, "close_pane": True, "control": True,
            "type_in_watch": False,
        }

    async def test_the_link_sends_only_allowed_methods(self, fake, link) -> None:
        await link.read_text(AGENT, 50)
        await link.send_bytes(AGENT, b"y\r")
        await link.submit(AGENT, "go on")
        await link.create("workspace", workspace_id=None, pane_id=None, cwd="/work", label="New", agent="claude")
        await link.create("split_right", workspace_id=None, pane_id=AGENT, cwd=None, label=None)
        await link.start_agent(SHELL, "claude")
        stream = await link.open_stream(AGENT, WATCH, 120, 40, Sink())
        await stream.start()
        await stream.aclose()
        await link.close("pane", SHELL)

        assert fake.methods == ALLOWED_METHODS

    async def test_input_without_key_names_types_nothing(self, fake, link) -> None:
        await link.send_bytes(AGENT, "rm é\r".encode())
        await link.send_bytes(AGENT, b"ls\r")

        assert fake.sent("terminal.keys") == [
            {"terminal": AGENT, "keys": ["l", "s", "enter"]}
        ]

    async def test_a_message_to_a_blocked_program_is_sent_as_its_answer(self, fake, link) -> None:
        fake.failures["terminal.send"] = {"message": "blocked", "data": {"kind": "blocked"}}

        await link.submit(AGENT, "yes")

        assert fake.sent("terminal.send") == [
            {"terminal": AGENT, "text": "yes"},
            {"terminal": AGENT, "text": "yes", "force": True},
        ]

    async def test_another_refusal_of_a_message_is_reported(self, fake, link) -> None:
        fake.failures["terminal.send"] = {"message": "terminal is finished", "data": {"kind": "busy"}}

        with pytest.raises(errors.HerdrError) as refused:
            await link.submit(AGENT, "yes")

        assert refused.value.detail() == {"code": "herdr_refused", "message": "terminal is finished"}
        assert len(fake.sent("terminal.send")) == 1

    async def test_start_agent_quotes_the_binary_for_the_shell(self, fake, link) -> None:
        # The path is typed into the user's shell.
        await link.start_agent(SHELL, "claude")

        assert fake.sent("terminal.send") == [
            {"terminal": SHELL, "text": "'/opt/my tools/claude'"}
        ]

    async def test_a_split_joins_the_tab_of_its_neighbour(self, fake, link) -> None:
        new_pane = await link.create(
            "split_down", workspace_id=None, pane_id=AGENT, cwd=None, label="  My Logs! ",
        )

        assert new_pane == "t-new"
        assert fake.sent("terminal.run") == [
            {
                "keep": True, "no_parent": True,
                "labels": {"herdr.space": "sp-1", "herdr.tab": "tab-1"},
                "cwd": "/work/api", "name": "my-logs",
            }
        ]

    async def test_a_new_terminal_runs_the_agent_binary_without_a_shell(self, fake, link) -> None:
        await link.create("workspace", workspace_id=None, pane_id=None, cwd="/work", label=None, agent="claude")

        assert fake.sent("terminal.run") == [
            {"keep": True, "no_parent": True, "cwd": "/work", "argv": ["/opt/my tools/claude"]}
        ]

    @pytest.mark.parametrize(
        "action",
        [
            lambda link: link.create("tab", workspace_id="sp-1", pane_id=None, cwd=None, label=None),
            lambda link: link.create("workspace", workspace_id=None, pane_id=None, cwd=None, label=None, agent="codex"),
            lambda link: link.close("workspace", "sp-1"),
            lambda link: link.close("tab", "tab-1"),
            lambda link: link.start_agent(SHELL, "codex"),
            lambda link: link.rename("pane", AGENT, "x"),
        ],
        ids=["new-tab", "agent-not-installed", "close-space", "close-tab",
             "start-unknown-agent", "rename"],
    )
    async def test_what_herdr_core_cannot_do_is_refused_before_any_call(
        self, fake, link, action
    ) -> None:
        before = len(fake.calls)

        with pytest.raises(errors.HerdrError) as refused:
            await action(link)

        assert (refused.value.status, refused.value.code) == (409, "not_supported")
        assert len(fake.calls) == before

    async def test_close_ends_and_removes_one_terminal(self, fake, link) -> None:
        fake.failures["terminal.rm"] = {"message": "gone", "data": {"kind": "not_found"}}

        await link.close("pane", SHELL)

        assert fake.sent("terminal.kill") == [{"terminal": SHELL}]
        assert fake.sent("terminal.rm") == [{"terminal": SHELL}]

    async def test_an_unknown_terminal_is_refused_before_any_call(self, fake, link) -> None:
        before = len(fake.calls)

        for call in (
            link.read_text("t-dead", 10),
            link.send_bytes("t-dead", b"y"),
            link.submit("t-dead", "go"),
            link.close("pane", "t-dead"),
            link.open_stream("t-dead", WATCH, 80, 24, Sink()),
        ):
            with pytest.raises(errors.HerdrError) as refused:
                await call
            assert refused.value.code == "pane_not_found"

        assert len(fake.calls) == before

    @pytest.mark.parametrize(
        "kind,status,code",
        [("not_found", 404, "pane_not_found"), ("finished", 404, "pane_not_found"),
         ("invalid", 409, "herdr_refused")],
    )
    async def test_a_herdr_core_failure_becomes_a_coded_refusal(
        self, fake, link, kind, status, code
    ) -> None:
        fake.failures["terminal.read"] = {"message": "refused", "data": {"kind": kind}}

        with pytest.raises(errors.HerdrError) as refused:
            await link.read_text(AGENT, 10)

        assert (refused.value.status, refused.value.code) == (status, code)

    async def test_read_text_reports_older_lines(self, fake, link) -> None:
        assert await link.read_text(AGENT, 50) == ("last line", True)


class TestStreams:
    async def test_a_watch_stream_observes_and_never_sets_the_size(self, fake, link) -> None:
        sink = Sink()
        fake.early = [(FRAME_DATA, b"\x18full screen")]
        stream = await link.open_stream(AGENT, WATCH, 120, 40, sink)

        await stream.start()
        await settle()
        fake.push(STREAM_CHANNEL, FRAME_DATA, b"diff")
        fake.push(STREAM_CHANNEL, FRAME_RESIZE, struct.pack("<HH", 100, 30))
        await settle()
        await stream.write(b"typed")
        await stream.resize(50, 20)
        await stream.aclose()

        (attach,) = fake.sent("terminal.attach")
        assert attach["terminal"] == AGENT and attach["observe"] is True
        assert sink.events == [
            ("output", b"\x18full screen", True),
            ("output", b"diff", False),
            ("grid", 100, 30),
        ]
        # An observer sends no input and no size; closing restores nothing.
        assert fake.stream_frames() == [(FRAME_CLOSE, b"")]

    async def test_a_control_stream_gives_the_old_size_back_when_it_closes(
        self, fake, link
    ) -> None:
        # herdr-core keeps the last viewer's size: without the restore a
        # desktop terminal stays at phone size.
        stream = await link.open_stream(AGENT, CONTROL, 40, 20, Sink())
        await stream.start()

        await stream.write(b"y")
        await stream.resize(50, 25)
        await stream.aclose()

        (attach,) = fake.sent("terminal.attach")
        assert attach == {"terminal": AGENT, "cols": 40, "rows": 20}
        assert fake.stream_frames() == [
            (FRAME_DATA, b"y"),
            (FRAME_RESIZE, struct.pack("<HH", 50, 25)),
            (FRAME_RESIZE, struct.pack("<HH", 120, 40)),
            (FRAME_CLOSE, b""),
        ]

    async def test_a_stream_the_server_closes_tells_its_sink_why(self, fake, link) -> None:
        sink = Sink()
        stream = await link.open_stream(AGENT, CONTROL, 40, 20, sink)
        await stream.start()

        fake.push(STREAM_CHANNEL, FRAME_CLOSE, json.dumps({"reason": "control taken"}).encode())
        await settle()

        assert sink.events == [("closed", "control taken")]
