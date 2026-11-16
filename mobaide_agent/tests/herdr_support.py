"""A scripted herdr server for the Herdr service and pane-session tests.

``ScriptedLink`` stands in for a ``ServerLink``: a test sets pane states and
reads back what reached the "server". ``Scheduler`` replaces
``loop.call_later``, so debounce and alert windows elapse only when a test
says so.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from mobaide.herdr import errors
from mobaide.herdr.backend import AgentNotStarted, PaneStreamSink, ServerGone, ServerLink
from mobaide.herdr.models import (
    HerdrFeatures,
    HerdrPane,
    HerdrServer,
    HerdrTab,
    HerdrWorkspace,
)

SERVER_ID = "tui:scripted"
AGENT_PANE = "w1:p1"
SHELL_PANE = "w1:p2"
GRID = (84, 47)


class ScriptedStream:
    def __init__(self, link: "ScriptedLink", pane_id: str, mode: str, size: tuple[int, int],
                 sink: PaneStreamSink) -> None:
        self.link = link
        self.pane_id = pane_id
        self.mode = mode
        self.size = size
        self.sink = sink
        self.started = False
        self.closed = False
        self.close_calls = 0
        self.written: list[bytes] = []
        self.resizes: list[tuple[int, int]] = []

    async def start(self) -> None:
        self.started = True

    async def aclose(self) -> None:
        self.closed = True
        self.close_calls += 1

    async def write(self, data: bytes) -> None:
        self.written.append(data)

    async def resize(self, cols: int, rows: int) -> None:
        self.resizes.append((cols, rows))


class ScriptedLink(ServerLink):
    def __init__(
        self,
        on_change: Callable[[], None] = lambda: None,
        *,
        first_read: asyncio.Event | None = None,
    ) -> None:
        super().__init__(SERVER_ID, on_change)
        # When given, the link has no mirror until ``run()`` passes this gate,
        # as a real link has none before its first read of the server.
        self._first_read = first_read
        self.panes: dict[str, HerdrPane] = {
            AGENT_PANE: HerdrPane(
                pane_id=AGENT_PANE, title="api refactor", agent="claude",
                status="working", cwd="/work/api",
            ),
            SHELL_PANE: HerdrPane(pane_id=SHELL_PANE, title="shell"),
        }
        self.server_state = "ready"
        self.message: str | None = None
        self.control = True
        self.grid: tuple[int, int] | None = GRID
        self.grid_revision = 0
        # Stream mode → the refusal an open in that mode gets.
        self.open_errors: dict[str, errors.HerdrError] = {}
        # When set, a stream open waits here: a test acts while it is open.
        self.open_gate: asyncio.Event | None = None
        # When set, a create makes its pane and the agent in it does not start.
        self.agent_start_error: errors.HerdrError | None = None
        # The class of the next streams: a test sets one that ends or fails
        # in its own way.
        self.stream_class: type[ScriptedStream] = ScriptedStream
        self.streams: list[ScriptedStream] = []
        self.typed: list[tuple[str, bytes]] = []
        self.submitted: list[tuple[str, str]] = []
        self.created: list[dict[str, Any]] = []
        self.text_reads: list[tuple[str, int]] = []
        self.closed_targets: list[tuple[str, str]] = []
        self.renamed: list[tuple[str, str, str]] = []
        self.started_agents: list[tuple[str, str]] = []
        self.running = asyncio.Event()
        self._gone = asyncio.Event()

    def state(self) -> HerdrServer | None:
        if self._first_read is not None and not self._first_read.is_set():
            return None
        if self.server_state != "ready":
            return HerdrServer(
                server_id=SERVER_ID, product="tui", name="scripted", version="0.9.1",
                state=self.server_state, message=self.message,
            )
        return HerdrServer(
            server_id=SERVER_ID,
            product="tui",
            name="scripted",
            version="0.9.5",
            features=HerdrFeatures(control=self.control, type_in_watch=True),
            workspaces=[
                HerdrWorkspace(
                    workspace_id="w1",
                    label="mobaide",
                    tabs=[HerdrTab(tab_id="w1:t1", label="main", panes=list(self.panes.values()))],
                )
            ],
        )

    def set_pane(self, pane_id: str, **changes: Any) -> None:
        """Change a pane the way a herdr event would, and signal the service."""
        self.panes[pane_id] = self.panes[pane_id].model_copy(update=changes)
        self._on_change()

    def remove_pane(self, pane_id: str) -> None:
        del self.panes[pane_id]
        self._on_change()

    def stop_server(self) -> None:
        self._gone.set()

    @property
    def live_streams(self) -> list[ScriptedStream]:
        return [s for s in self.streams if s.started and not s.closed]

    async def run(self) -> None:
        if self._first_read is not None:
            await self._first_read.wait()
        self._on_change()
        self.running.set()
        await self._gone.wait()
        raise ServerGone("scripted stop")

    def pane_key(self, pane_id: str) -> str | None:
        return pane_id if pane_id in self.panes else None

    def pane_id_for_key(self, key: str) -> str | None:
        return self.pane_key(key)

    async def read_text(self, pane_id: str, lines: int) -> tuple[str, bool]:
        self.text_reads.append((pane_id, lines))
        return "line", False

    async def send_bytes(self, pane_id: str, data: bytes) -> None:
        self.typed.append((pane_id, data))

    async def submit(self, pane_id: str, text: str) -> None:
        self.submitted.append((pane_id, text))

    async def create(self, placement: str, **params: Any) -> str:
        self.created.append({"placement": placement, **params})
        if params.get("agent") and self.agent_start_error is not None:
            raise AgentNotStarted("w1:p9", self.agent_start_error)
        return "w1:p9"

    async def start_agent(self, pane_id: str, agent: str) -> None:
        self.started_agents.append((pane_id, agent))

    async def rename(self, target: str, target_id: str, label: str) -> None:
        self.renamed.append((target, target_id, label))

    async def close(self, target: str, target_id: str) -> None:
        self.closed_targets.append((target, target_id))

    async def pane_grid(self, pane_id: str) -> tuple[int, int] | None:
        if pane_id not in self.panes:
            raise errors.pane_not_found()
        return self.grid

    async def open_stream(
        self, pane_id: str, mode: str, cols: int, rows: int, sink: PaneStreamSink
    ) -> ScriptedStream:
        if mode in self.open_errors:
            raise self.open_errors[mode]
        if self.open_gate is not None:
            await self.open_gate.wait()
        stream = self.stream_class(self, pane_id, mode, (cols, rows), sink)
        self.streams.append(stream)
        return stream


class ScriptedBackend:
    """Finds one ``ScriptedLink``, once; again after ``restart()``."""

    product = "tui"

    def __init__(self) -> None:
        self.link: ScriptedLink | None = None
        self.attached = asyncio.Event()
        self.discover_calls = 0
        self._first_read: asyncio.Event | None = None

    def restart(self, first_read: asyncio.Event | None = None) -> None:
        """The herdr server runs again: the next discovery pass finds a new
        link. With ``first_read``, that link has no mirror until the event
        is set."""
        self.link = None
        self._first_read = first_read
        self.attached.clear()

    async def discover(
        self, known: frozenset[str]
    ) -> dict[str, Callable[[Callable[[], None]], ServerLink]]:
        self.discover_calls += 1
        if SERVER_ID in known or self.link is not None:
            return {}

        def make(on_change: Callable[[], None]) -> ServerLink:
            self.link = ScriptedLink(on_change, first_read=self._first_read)
            self.attached.set()
            return self.link

        return {SERVER_ID: make}


class _Handle:
    def __init__(self, delay: float, callback: Callable[..., None], args: tuple) -> None:
        self.delay = delay
        self.callback = callback
        self.args = args
        self.cancelled = False
        self.fired = False

    def cancel(self) -> None:
        self.cancelled = True


class Scheduler:
    """Manual ``loop.call_later``: a timer runs only on ``elapse()``."""

    def __init__(self) -> None:
        self.handles: list[_Handle] = []

    def call_later(self, delay: float, callback: Callable[..., None], *args: Any) -> _Handle:
        handle = _Handle(delay, callback, args)
        self.handles.append(handle)
        return handle

    def pending(self, delay: float) -> list[_Handle]:
        return [
            h for h in self.handles
            if h.delay == delay and not (h.cancelled or h.fired)
        ]

    def elapse(self, delay: float) -> int:
        """Run the armed timers of this ``delay``; return how many ran."""
        due = self.pending(delay)
        for handle in due:
            handle.fired = True
            handle.callback(*handle.args)
        return len(due)


@pytest.fixture
async def scheduler(monkeypatch) -> Scheduler:
    sched = Scheduler()
    monkeypatch.setattr(asyncio.get_running_loop(), "call_later", sched.call_later)
    return sched


async def settle() -> None:
    """Let the tasks that are ready now run to their next wait."""
    for _ in range(25):
        await asyncio.sleep(0)
