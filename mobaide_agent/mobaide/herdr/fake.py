"""A scripted herdr server for test mode (``MOBAIDE_HERDR_FAKE=1``).

It stands in for a real herdr the way ``FakePtyBackend`` stands in for a
shell: a disposable agent then shows a Herdr dashboard with no herdr
installed. One server, two workspaces, three panes. A message submitted to an
agent pane makes that agent work for a moment and then finish, so alerts and
state chips can be exercised end to end.
"""
from __future__ import annotations

import asyncio
import itertools
from collections.abc import Callable
from dataclasses import dataclass, field

from . import errors
from .agents import AGENT_LABELS, AgentChoice
from .backend import CONTROL, PaneStream, PaneStreamSink, ServerLink
from .models import (
    HerdrAgentKind,
    HerdrFeatures,
    HerdrPane,
    HerdrRect,
    HerdrServer,
    HerdrTab,
    HerdrWorkspace,
)

SERVER_ID = "tui:demo"
WATCH_GRID = (100, 30)
WORK_SECONDS = 1.5


@dataclass
class _Pane:
    pane_id: str
    title: str
    agent: str | None = None
    status: str | None = None
    turn: int | None = None
    cwd: str = "/tmp"
    lines: list[str] = field(default_factory=list)
    typed: str = ""
    streams: list["_FakeStream"] = field(default_factory=list)


@dataclass
class _Tab:
    tab_id: str
    label: str
    panes: list[_Pane]


@dataclass
class _Workspace:
    workspace_id: str
    label: str
    tabs: list[_Tab]


class FakeLink(ServerLink):
    def __init__(
        self, on_change: Callable[[], None], agents: Callable[[], list[AgentChoice]]
    ) -> None:
        super().__init__(SERVER_ID, on_change)
        self._agents = agents
        self._ids = itertools.count(10)
        self._closed = asyncio.Event()
        self.grid_revision = 0
        self._workspaces = [
            _Workspace(
                "w1",
                "mobaide",
                [
                    _Tab(
                        "w1:t1",
                        "main",
                        [
                            _Pane("w1:p1", "api refactor", "claude", "working", None,
                                  lines=["claude: refactoring the API layer..."]),
                            _Pane("w1:p2", "shell", lines=["$ git status", "nothing to commit"]),
                        ],
                    )
                ],
            ),
            _Workspace(
                "w2",
                "website",
                [
                    _Tab(
                        "w2:t1",
                        "main",
                        [
                            _Pane("w2:p1", "landing page", "codex", "blocked", None,
                                  lines=["codex: Allow running `npm install`?", "  1. Yes   2. No"]),
                        ],
                    )
                ],
            ),
        ]

    def state(self) -> HerdrServer:
        agents = self._agents()
        kinds = [HerdrAgentKind(id=c.id, label=c.label) for c in agents] or [
            HerdrAgentKind(id="claude", label=AGENT_LABELS["claude"])
        ]
        return HerdrServer(
            server_id=SERVER_ID,
            product="tui",
            name="demo",
            version="0.9.5",
            features=HerdrFeatures(
                new_workspace=True,
                new_tab=True,
                split=True,
                rename=True,
                close_group=True,
                close_pane=True,
                control=True,
                type_in_watch=True,
                agents=kinds,
            ),
            workspaces=[
                HerdrWorkspace(
                    workspace_id=ws.workspace_id,
                    label=ws.label,
                    tabs=[
                        HerdrTab(
                            tab_id=tab.tab_id,
                            label=tab.label,
                            width=WATCH_GRID[0] * max(len(tab.panes), 1),
                            height=WATCH_GRID[1],
                            panes=[
                                HerdrPane(
                                    pane_id=p.pane_id,
                                    title=p.title,
                                    agent=p.agent,
                                    status=p.status,
                                    turn=p.turn,
                                    cwd=p.cwd,
                                    rect=HerdrRect(
                                        x=index * WATCH_GRID[0],
                                        y=0,
                                        width=WATCH_GRID[0],
                                        height=WATCH_GRID[1],
                                    ),
                                )
                                for index, p in enumerate(tab.panes)
                            ],
                        )
                        for tab in ws.tabs
                    ],
                )
                for ws in self._workspaces
            ],
        )

    async def run(self) -> None:
        self._on_change()
        await self._closed.wait()

    async def aclose(self) -> None:
        self._closed.set()

    def _all(self) -> list[_Pane]:
        return [p for ws in self._workspaces for tab in ws.tabs for p in tab.panes]

    def _pane(self, pane_id: str) -> _Pane:
        for pane in self._all():
            if pane.pane_id == pane_id:
                return pane
        raise errors.pane_not_found()

    def pane_key(self, pane_id: str) -> str | None:
        return pane_id if any(p.pane_id == pane_id for p in self._all()) else None

    def pane_id_for_key(self, key: str) -> str | None:
        return self.pane_key(key)

    async def read_text(self, pane_id: str, lines: int) -> tuple[str, bool]:
        return "\n".join(self._pane(pane_id).lines[-lines:]), False

    async def send_bytes(self, pane_id: str, data: bytes) -> None:
        pane = self._pane(pane_id)
        for ch in data.decode("utf-8", errors="replace"):
            if ch in "\r\n":
                await self.submit(pane_id, pane.typed)
            elif ch in "\x7f\x08":
                pane.typed = pane.typed[:-1]
            elif ch.isprintable():
                pane.typed += ch
        self._repaint(pane)

    async def submit(self, pane_id: str, text: str) -> None:
        pane = self._pane(pane_id)
        pane.typed = ""
        pane.lines.append(f"> {text}")
        if pane.agent:
            pane.status = "working"
            asyncio.get_running_loop().call_later(WORK_SECONDS, self._finish, pane)
        self._repaint(pane)
        self._on_change()

    def _finish(self, pane: _Pane) -> None:
        if pane.status != "working":
            return
        pane.status = "done"
        pane.turn = (pane.turn or 0) + 1
        pane.lines.append(f"{pane.agent}: done.")
        self._repaint(pane)
        self._on_change()

    def _repaint(self, pane: _Pane) -> None:
        for stream in list(pane.streams):
            stream.paint()

    async def create(
        self,
        placement: str,
        *,
        workspace_id: str | None,
        pane_id: str | None,
        cwd: str | None,
        label: str | None,
        agent: str | None = None,
    ) -> str:
        number = next(self._ids)
        if placement == "workspace":
            ws_id = f"w{number}"
            pane = _Pane(f"{ws_id}:p1", label or "shell", cwd=cwd or "/tmp")
            self._workspaces.append(
                _Workspace(ws_id, label or f"workspace {number}", [_Tab(f"{ws_id}:t1", "1", [pane])])
            )
        elif placement == "tab":
            workspace = next(
                (ws for ws in self._workspaces if ws.workspace_id == workspace_id), None
            )
            if workspace is None:
                raise errors.HerdrError(404, "not_found", "This item is closed.")
            pane = _Pane(f"{workspace.workspace_id}:p{number}", "shell", cwd=cwd or "/tmp")
            workspace.tabs.append(
                _Tab(f"{workspace.workspace_id}:t{number}", label or str(number), [pane])
            )
        else:
            source = self._pane(pane_id or "")
            tab = next(
                tab for ws in self._workspaces for tab in ws.tabs if source in tab.panes
            )
            pane = _Pane(
                f"{tab.tab_id.split(':')[0]}:p{number}", label or "shell", cwd=cwd or source.cwd
            )
            tab.panes.append(pane)
        if agent:
            pane.agent, pane.status = agent, "idle"
        self._on_change()
        return pane.pane_id

    async def start_agent(self, pane_id: str, agent: str) -> None:
        pane = self._pane(pane_id)
        pane.agent, pane.status = agent, "idle"
        pane.lines.append(f"{agent}: ready.")
        self._repaint(pane)
        self._on_change()

    async def rename(self, target: str, target_id: str, label: str) -> None:
        for ws in self._workspaces:
            if target == "workspace" and ws.workspace_id == target_id:
                ws.label = label
            for tab in ws.tabs:
                if target == "tab" and tab.tab_id == target_id:
                    tab.label = label
                for pane in tab.panes:
                    if target == "pane" and pane.pane_id == target_id:
                        pane.title = label
        self._on_change()

    async def close(self, target: str, target_id: str) -> None:
        if target == "workspace":
            self._workspaces = [w for w in self._workspaces if w.workspace_id != target_id]
        for ws in self._workspaces:
            if target == "tab":
                ws.tabs = [t for t in ws.tabs if t.tab_id != target_id]
            for tab in ws.tabs:
                if target == "pane":
                    tab.panes = [p for p in tab.panes if p.pane_id != target_id]
            ws.tabs = [t for t in ws.tabs if t.panes]
        self._workspaces = [w for w in self._workspaces if w.tabs]
        self._on_change()

    async def pane_grid(self, pane_id: str) -> tuple[int, int] | None:
        self._pane(pane_id)
        return WATCH_GRID

    async def open_stream(
        self, pane_id: str, mode: str, cols: int, rows: int, sink: PaneStreamSink
    ) -> PaneStream:
        return _FakeStream(self._pane(pane_id), mode, sink)


class _FakeStream:
    def __init__(self, pane: _Pane, mode: str, sink: PaneStreamSink) -> None:
        self._pane = pane
        self._mode = mode
        self._sink = sink

    async def start(self) -> None:
        self._pane.streams.append(self)
        self.paint()

    def paint(self) -> None:
        body = "\r\n".join([f"[{self._mode}] {self._pane.title}", *self._pane.lines[-20:]])
        self._sink.on_output(
            f"\x1b[0m\x1b[H\x1b[2J{body}\r\n$ {self._pane.typed}".encode(), repaint=True
        )

    async def write(self, data: bytes) -> None:
        if self._mode == CONTROL:
            for ch in data.decode("utf-8", errors="replace"):
                if ch in "\r\n":
                    self._pane.lines.append(f"> {self._pane.typed}")
                    self._pane.typed = ""
                elif ch.isprintable():
                    self._pane.typed += ch
            for stream in list(self._pane.streams):
                stream.paint()

    async def resize(self, cols: int, rows: int) -> None:
        return None

    async def aclose(self) -> None:
        if self in self._pane.streams:
            self._pane.streams.remove(self)


class FakeBackend:
    product = "tui"

    def __init__(self, agents: Callable[[], list[AgentChoice]]) -> None:
        self._agents = agents

    async def discover(
        self, known: frozenset[str]
    ) -> dict[str, Callable[[Callable[[], None]], ServerLink]]:
        if SERVER_ID in known:
            return {}
        return {SERVER_ID: lambda on_change: FakeLink(on_change, self._agents)}
