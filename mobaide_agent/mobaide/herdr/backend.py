"""The seam between Mobaide and one herdr product.

A :class:`HerdrBackend` finds the running servers of its product. A
:class:`ServerLink` is the live connection to one of them: it mirrors the
server state as a :class:`~mobaide.herdr.models.HerdrServer`, runs the actions
and opens pane streams. Only typed methods exist, so a client request can never
reach a herdr method that has no method here (``server.stop``, plugins,
``layout.apply``).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from . import errors
from .models import HerdrServer

WATCH = "watch"
CONTROL = "control"


class ServerGone(Exception):
    """The herdr server stopped or its socket went away."""


class AgentNotStarted(Exception):
    """A create made its pane, and the agent that was to run in it did not
    start. The caller gets the pane and the reason, not only an error."""

    def __init__(self, pane_id: str, error: errors.HerdrError) -> None:
        super().__init__(error.message)
        self.pane_id = pane_id
        self.error = error


class PaneStreamSink(Protocol):
    """Receives the content of one pane stream."""

    def on_output(self, data: bytes, *, repaint: bool) -> None:
        """Terminal bytes. ``repaint`` marks bytes that redraw the whole
        screen, so everything received before them can be forgotten."""

    def on_grid(self, cols: int, rows: int) -> None:
        """The size of the grid that the following bytes are drawn for."""

    def on_closed(self, reason: str | None) -> None:
        """The stream ended on its own (not through ``aclose``)."""


class PaneStream(Protocol):
    async def start(self) -> None: ...

    async def aclose(self) -> None: ...

    async def write(self, data: bytes) -> None:
        """Raw input bytes. Control streams only."""

    async def resize(self, cols: int, rows: int) -> None:
        """Resize the real pane. Control streams only."""


class ServerLink:
    """Live connection to one herdr server. Subclasses fill in the product."""

    server_id: str

    def __init__(self, server_id: str, on_change: Callable[[], None]) -> None:
        self.server_id = server_id
        self._on_change = on_change

    def state(self) -> HerdrServer | None:
        """The current mirror, or ``None`` before the first read."""
        raise NotImplementedError

    async def run(self) -> None:
        """Keep the mirror current. Returns or raises :class:`ServerGone` when
        the server is gone; any other error is a fault the caller retries."""
        raise NotImplementedError

    async def aclose(self) -> None:
        return None

    def pane_key(self, pane_id: str) -> str | None:
        """An id for the pane's terminal that survives a pane move, or
        ``None`` when the pane does not exist."""
        raise NotImplementedError

    def pane_id_for_key(self, key: str) -> str | None:
        raise NotImplementedError

    async def read_text(self, pane_id: str, lines: int) -> tuple[str, bool]:
        raise errors.not_supported("read pane text")

    async def send_bytes(self, pane_id: str, data: bytes) -> None:
        """Type raw input into the pane without taking control of it."""
        raise errors.not_supported("type into a pane")

    async def submit(self, pane_id: str, text: str) -> None:
        """Send one message: the text, then Enter."""
        raise errors.not_supported("send a message")

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
        """Create a workspace, tab or split, and start ``agent`` in it when
        one is named; returns the new pane id."""
        raise errors.not_supported("create terminals")

    async def start_agent(self, pane_id: str, agent: str) -> None:
        raise errors.not_supported("start an agent")

    async def rename(self, target: str, target_id: str, label: str) -> None:
        raise errors.not_supported("rename this")

    async def close(self, target: str, target_id: str) -> None:
        raise errors.not_supported("close this")

    async def pane_grid(self, pane_id: str) -> tuple[int, int] | None:
        """The real ``(cols, rows)`` of the pane, when it can be known."""
        return None

    async def open_stream(
        self, pane_id: str, mode: str, cols: int, rows: int, sink: PaneStreamSink
    ) -> PaneStream:
        raise errors.not_supported("show a live pane")


class HerdrBackend(Protocol):
    product: str

    async def discover(
        self, known: frozenset[str]
    ) -> dict[str, Callable[[Callable[[], None]], ServerLink]]:
        """The servers that answer now and are not in ``known``:
        ``server_id`` → a link factory."""
        ...
