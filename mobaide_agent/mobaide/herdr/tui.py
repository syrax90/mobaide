"""Herdr TUI backend (binary ``herdr``, 0.9.2 or later).

Two upstream surfaces are used:

* The JSON socket: newline-delimited JSON, **one request per connection**;
  only ``events.subscribe`` stays open. It gives the tree, the agent states
  and every action. It has no output stream and no pane size.
* The terminal bridge: ``herdr terminal session observe|control``, a child
  process that prints ``terminal.frame`` records (base64 ANSI, a full redraw
  or a diff). ``observe`` never resizes the pane; ``control`` resizes the real
  PTY to the viewer and owns the input.

Events carry no sequence number and are not replayed, so an event is only a
signal to read ``session.snapshot`` again. A change of an agent's state
arrives only on a subscription scoped to its pane, so the subscription is
opened again each time the set of panes changes.
"""
from __future__ import annotations

import asyncio
import base64
import itertools
import logging
import os
import sys
import time
from collections.abc import Callable
from typing import Any

import orjson

from . import errors
from .agents import AgentChoice, normalize_agent
from .backend import (
    CONTROL,
    AgentNotStarted,
    PaneStream,
    PaneStreamSink,
    ServerGone,
    ServerLink,
)
from .models import (
    HerdrAgentKind,
    HerdrFeatures,
    HerdrPane,
    HerdrRect,
    HerdrServer,
    HerdrTab,
    HerdrWorkspace,
)
from .transport import (
    LocalStream,
    PipedProcess,
    in_thread,
    is_executable,
    open_local_stream,
    run_text,
    spawn_piped,
)

logger = logging.getLogger(__name__)

PRODUCT = "tui"
# 0.9.2 is the first version that reports ``events_lost`` instead of dropping
# events silently, and that has ``terminal.mouse``.
MIN_VERSION = (0, 9, 2)
OUTDATED_MESSAGE = "Update Herdr to version 0.9.2 or later to use it from Mobaide."

CONNECT_TIMEOUT_SECONDS = 3.0
REQUEST_TIMEOUT_SECONDS = 5.0
# Below the 30 s that the relay and the clients give a request, with room for
# the create that comes before it.
START_AGENT_TIMEOUT_SECONDS = 20.0
# Herdr rejects a request line above 1 MiB.
MAX_REQUEST_BYTES = 900 * 1024
# Events are signals only; this slow read catches a signal that was lost.
SAFETY_POLL_SECONDS = 15.0
# Herdr sends related events in a burst; one snapshot read answers them all.
EVENT_BURST_SECONDS = 0.05
OUTDATED_RECHECK_SECONDS = 10.0
MAX_REFRESH_FAILURES = 3
BRIDGE_RECHECK_SECONDS = 60.0
POLL_STREAM_SECONDS = 0.7

_LIFECYCLE_EVENTS = (
    "workspace.created",
    "workspace.updated",
    "workspace.renamed",
    "workspace.moved",
    "workspace.reordered",
    "workspace.closed",
    "tab.created",
    "tab.closed",
    "tab.renamed",
    "tab.moved",
    "pane.created",
    "pane.closed",
    "pane.updated",
    "pane.moved",
    "pane.exited",
    "pane.agent_detected",
    "layout.updated",
)

# Mobaide agent kind → the kind name ``agent.start`` takes.
_START_KINDS = {
    "claude": "claude",
    "codex": "codex",
    "antigravity": "agy",
    "opencode": "opencode",
    "copilot": "copilot",
    "cursor": "cursor",
    "pi": "pi",
}

_TARGET_METHODS = {
    "workspace": ("workspace", "workspace_id"),
    "tab": ("tab", "tab_id"),
    "pane": ("pane", "pane_id"),
}

_request_ids = itertools.count(1)


class TuiApiError(Exception):
    """An error reply from the herdr socket."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class EventsLost(Exception):
    """The subscription fell behind herdr's event buffer."""


def parse_version(text: str | None) -> tuple[int, ...] | None:
    """``"herdr 0.9.5"`` or ``"0.9.5-preview.1"`` → ``(0, 9, 5)``."""
    if not text:
        return None
    token = text.strip().split()[-1] if text.strip() else ""
    parts: list[int] = []
    for piece in token.split("-")[0].split("."):
        # ASCII digits, and few of them: ``isdigit`` alone also takes "²",
        # and ``int`` refuses that and a number of thousands of digits.
        if not (piece.isascii() and piece.isdigit() and len(piece) <= 9):
            break
        parts.append(int(piece))
    return tuple(parts) if parts else None


def config_dir(override: str | None = None) -> str:
    """Where herdr keeps its sockets (its config directory). ``override`` is
    the agent setting; the process environment is the second source, as for
    the CLI binaries."""
    override = override or os.environ.get("MOBAIDE_HERDR_CONFIG_DIR")
    if override:
        return os.path.expanduser(override)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Roaming"
        )
        return os.path.join(base, "herdr")
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = xdg if xdg else os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "herdr")


def session_sockets(root: str) -> dict[str, str]:
    """``session name`` → socket path for every session herdr has on disk."""
    found: dict[str, str] = {}
    default = os.path.join(root, "herdr.sock")
    if os.path.exists(default):
        found["default"] = default
    sessions = os.path.join(root, "sessions")
    try:
        names = sorted(os.listdir(sessions))
    except OSError:
        names = []
    for name in names:
        path = os.path.join(sessions, name, "herdr.sock")
        if name != "default" and os.path.exists(path):
            found[name] = path
    return found


class TuiClient:
    """The JSON socket of one herdr server."""

    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path

    async def _connect(self) -> LocalStream:
        try:
            return await asyncio.wait_for(
                open_local_stream(self.socket_path), CONNECT_TIMEOUT_SECONDS
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise ServerGone(str(exc)) from exc

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """One request on its own connection. Raises :class:`TuiApiError` for
        an error reply and :class:`ServerGone` when nothing listens."""
        line = _request_line(method, params)
        stream = await self._connect()
        try:
            # One deadline for the write and the reply: a server that is alive
            # but does not read would hold a large write forever.
            async with asyncio.timeout(timeout):
                await stream.write(line)
                raw = await stream.reader.readline()
        except TimeoutError as exc:
            raise errors.server_unreachable() from exc
        except (OSError, ValueError) as exc:
            raise ServerGone(str(exc)) from exc
        finally:
            await stream.aclose()
        return _parse_reply(raw)

    async def subscribe(self, subscriptions: list[dict[str, Any]]) -> "TuiSubscription":
        """Open an event stream. Raises :class:`TuiApiError` when herdr
        rejects the set, which it does when one named pane is gone."""
        stream = await self._connect()
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                await stream.write(
                    _request_line("events.subscribe", {"subscriptions": subscriptions})
                )
                raw = await stream.reader.readline()
            _parse_reply(raw)
        except TimeoutError as exc:
            await stream.aclose()
            raise errors.server_unreachable() from exc
        except (OSError, ValueError) as exc:
            await stream.aclose()
            raise ServerGone(str(exc)) from exc
        except BaseException:
            await stream.aclose()
            raise
        return TuiSubscription(stream)


def _request_line(method: str, params: dict[str, Any] | None) -> bytes:
    line = orjson.dumps(
        {"id": str(next(_request_ids)), "method": method, "params": params or {}}
    ) + b"\n"
    if len(line) > MAX_REQUEST_BYTES:
        raise errors.HerdrError(413, "too_large", "This text is too long to send to Herdr.")
    return line


def _parse_reply(raw: bytes) -> dict[str, Any]:
    if not raw:
        raise ServerGone("connection closed")
    try:
        reply = orjson.loads(raw)
    except orjson.JSONDecodeError as exc:
        raise ServerGone("malformed reply") from exc
    if not isinstance(reply, dict):
        raise ServerGone("malformed reply")
    error = reply.get("error")
    if isinstance(error, dict):
        raise TuiApiError(str(error.get("code") or "error"), str(error.get("message") or ""))
    result = reply.get("result")
    return result if isinstance(result, dict) else {}


class TuiSubscription:
    def __init__(self, stream: LocalStream) -> None:
        self._stream = stream

    async def next(self) -> None:
        """Wait for one event. Raises :class:`EventsLost` or :class:`ServerGone`."""
        try:
            raw = await self._stream.reader.readline()
        except (OSError, ValueError) as exc:
            raise ServerGone(str(exc)) from exc
        if not raw:
            raise ServerGone("subscription closed")
        try:
            message = orjson.loads(raw)
        except orjson.JSONDecodeError:
            return
        error = message.get("error") if isinstance(message, dict) else None
        if isinstance(error, dict):
            if error.get("code") == "events_lost":
                raise EventsLost()
            raise ServerGone(str(error.get("message") or "subscription error"))

    async def drain(self, seconds: float) -> None:
        """Consume the events that arrive within ``seconds``."""
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                await asyncio.wait_for(self.next(), remaining)
            except asyncio.TimeoutError:
                return

    async def aclose(self) -> None:
        await self._stream.aclose()


def _subscriptions(pane_ids: list[str]) -> list[dict[str, Any]]:
    subs: list[dict[str, Any]] = [{"type": name} for name in _LIFECYCLE_EVENTS]
    subs.extend(
        {"type": "pane.agent_status_changed", "pane_id": pane_id} for pane_id in pane_ids
    )
    return subs


def _text(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _pane_title(pane: dict[str, Any], agent: dict[str, Any] | None) -> str:
    for candidate in (
        pane.get("label"),
        (agent or {}).get("name"),
        pane.get("title"),
        pane.get("terminal_title_stripped"),
    ):
        title = _text(candidate)
        if title:
            return title
    cwd = _text(pane.get("foreground_cwd")) or _text(pane.get("cwd"))
    if cwd:
        return os.path.basename(cwd.rstrip("/\\")) or cwd
    return str(pane.get("pane_id") or "")


def _pane_status(pane: dict[str, Any], agent: dict[str, Any] | None) -> tuple[str | None, int | None]:
    """``(status, turn)``. Herdr says ``done`` only while no herdr client has
    looked at the agent; ``completion_seq`` marks a finished turn whoever
    looked, so an ``idle`` agent with one is ``done`` here and each Mobaide
    client decides from ``turn`` whether its user has seen it."""
    raw = _text(pane.get("agent_status")) or "unknown"
    turn = (agent or {}).get("completion_seq")
    turn = turn if isinstance(turn, int) and not isinstance(turn, bool) else None
    if raw == "idle" and turn is not None:
        return "done", turn
    if raw in ("working", "blocked", "done", "idle"):
        return raw, turn
    return "unknown", turn


def build_server(
    *,
    server_id: str,
    name: str,
    version: str | None,
    snapshot: dict[str, Any],
    features: HerdrFeatures,
) -> HerdrServer:
    """Turn one ``session.snapshot`` into Mobaide's record of the server."""
    agents = {
        a.get("pane_id"): a for a in snapshot.get("agents") or [] if isinstance(a, dict)
    }
    layouts = {
        layout.get("tab_id"): layout
        for layout in snapshot.get("layouts") or []
        if isinstance(layout, dict)
    }
    panes_by_tab: dict[str, list[HerdrPane]] = {}
    for pane in snapshot.get("panes") or []:
        if not isinstance(pane, dict) or not _text(pane.get("pane_id")):
            continue
        pane_id = pane["pane_id"]
        agent = agents.get(pane_id)
        agent_name = normalize_agent(pane.get("agent") or pane.get("display_agent"))
        status, turn = _pane_status(pane, agent) if agent_name else (None, None)
        session = pane.get("agent_session")
        session_id = (
            _text(session.get("value"))
            if isinstance(session, dict) and session.get("kind") == "id"
            else None
        )
        panes_by_tab.setdefault(str(pane.get("tab_id")), []).append(
            HerdrPane(
                pane_id=pane_id,
                title=_pane_title(pane, agent),
                agent=agent_name,
                status=status,
                turn=turn,
                cwd=_text(pane.get("foreground_cwd")) or _text(pane.get("cwd")),
                agent_session_id=session_id,
                rect=_pane_rect(layouts.get(pane.get("tab_id")), pane_id),
            )
        )
    tabs_by_workspace: dict[str, list[HerdrTab]] = {}
    for tab in snapshot.get("tabs") or []:
        if not isinstance(tab, dict) or not _text(tab.get("tab_id")):
            continue
        area = (layouts.get(tab["tab_id"]) or {}).get("area") or {}
        tabs_by_workspace.setdefault(str(tab.get("workspace_id")), []).append(
            HerdrTab(
                tab_id=tab["tab_id"],
                label=_text(tab.get("label")) or str(tab.get("number") or tab["tab_id"]),
                width=_int(area.get("width")),
                height=_int(area.get("height")),
                panes=panes_by_tab.get(tab["tab_id"], []),
            )
        )
    workspaces = [
        HerdrWorkspace(
            workspace_id=ws["workspace_id"],
            label=_text(ws.get("label")) or str(ws.get("number") or ws["workspace_id"]),
            tabs=tabs_by_workspace.get(ws["workspace_id"], []),
        )
        for ws in snapshot.get("workspaces") or []
        if isinstance(ws, dict) and _text(ws.get("workspace_id"))
    ]
    return HerdrServer(
        server_id=server_id,
        product=PRODUCT,
        name=name,
        version=version,
        features=features,
        workspaces=workspaces,
    )


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _pane_rect(layout: dict[str, Any] | None, pane_id: str) -> HerdrRect | None:
    for entry in (layout or {}).get("panes") or []:
        if isinstance(entry, dict) and entry.get("pane_id") == pane_id:
            rect = entry.get("rect")
            if isinstance(rect, dict):
                try:
                    return HerdrRect(
                        x=rect["x"], y=rect["y"], width=rect["width"], height=rect["height"]
                    )
                except (KeyError, ValueError):
                    return None
    return None


def _tty_size(tty_name: str) -> tuple[int, int] | None:
    """``(cols, rows)`` of the terminal device ``tty_name``. POSIX only."""
    if sys.platform == "win32" or not tty_name or tty_name.startswith("?"):
        return None
    import fcntl
    import struct
    import termios

    try:
        fd = os.open(f"/dev/{tty_name}", os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        rows, cols, _, _ = struct.unpack(
            "HHHH", fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8)
        )
    except OSError:
        return None
    finally:
        os.close(fd)
    return (cols, rows) if cols > 0 and rows > 0 else None


class TuiLink(ServerLink):
    def __init__(
        self,
        server_id: str,
        name: str,
        socket_path: str,
        on_change: Callable[[], None],
        agents: Callable[[], list[AgentChoice]],
        *,
        bin_override: str | None = None,
    ) -> None:
        super().__init__(server_id, on_change)
        self._name = name
        self._client = TuiClient(socket_path)
        self._agents = agents
        self._bin_override = bin_override
        self._version: str | None = None
        self._server: HerdrServer | None = None
        self._panes: dict[str, dict[str, Any]] = {}
        self._tab_sizes: dict[str, int] = {}
        self._grid_signature: object = None
        # Goes up when a pane may have a new size; a pane view then reads its
        # grid again.
        self.grid_revision = 0
        self._bridge_bin: str | None = None
        self._bridge_checked_at: float | None = None
        self._bridge_broken_at: float | None = None

    def state(self) -> HerdrServer | None:
        return self._server

    def pane_key(self, pane_id: str) -> str | None:
        pane = self._panes.get(pane_id)
        if pane is None:
            return None
        return _text(pane.get("terminal_id")) or pane_id

    def pane_id_for_key(self, key: str) -> str | None:
        for pane_id, pane in self._panes.items():
            if pane.get("terminal_id") == key or pane_id == key:
                return pane_id
        return None

    async def run(self) -> None:
        pong = await self._client.request("ping")
        self._version = _text(pong.get("version"))
        version = parse_version(self._version)
        if version is None or version < MIN_VERSION:
            await self._run_outdated()
            return
        await self._refresh()
        refusals = 0
        while True:
            pane_ids = sorted(self._panes)
            try:
                subscription = await self._client.subscribe(_subscriptions(pane_ids))
            except TuiApiError as exc:
                # Usually a pane closed between the snapshot and the subscribe,
                # and the next try works. A refusal that stays (a herdr build
                # that does not know one of the events) must not spin.
                refusals += 1
                if refusals == 3:
                    logger.warning(
                        "herdr.tui.subscribe_refused server=%s code=%s",
                        self.server_id, exc.code,
                    )
                # The exponent has a limit of its own: the power is computed
                # before the cap, and it overflows a float after a day of refusals.
                await asyncio.sleep(
                    min(0.2 * 2 ** min(refusals - 1, 7), SAFETY_POLL_SECONDS)
                )
                await self._refresh()
                continue
            refusals = 0
            try:
                # Subscribe first, then read: a change in between is not lost.
                await self._refresh()
                while sorted(self._panes) == pane_ids:
                    try:
                        await asyncio.wait_for(subscription.next(), SAFETY_POLL_SECONDS)
                    except asyncio.TimeoutError:
                        pass
                    else:
                        await subscription.drain(EVENT_BURST_SECONDS)
                    await self._refresh()
            except EventsLost:
                logger.info("herdr.tui.events_lost server=%s", self.server_id)
            finally:
                await subscription.aclose()

    async def _run_outdated(self) -> None:
        self._server = HerdrServer(
            server_id=self.server_id,
            product=PRODUCT,
            name=self._name,
            version=self._version,
            state="outdated",
            message=OUTDATED_MESSAGE,
        )
        self._on_change()
        while True:
            # An update restarts the server. Mostly the ping then fails and
            # discovery finds the new one. A restart inside one wait answers
            # the ping from the new server: its version ends this link too.
            await asyncio.sleep(OUTDATED_RECHECK_SECONDS)
            pong = await self._client.request("ping")
            version = parse_version(_text(pong.get("version")))
            if version is not None and version >= MIN_VERSION:
                raise ServerGone("the server was updated")

    async def _refresh(self) -> None:
        failures = 0
        while True:
            try:
                reply = await self._client.request("session.snapshot")
                break
            except errors.HerdrError:
                failures += 1
                if failures >= MAX_REFRESH_FAILURES:
                    raise ServerGone("no answer") from None
                await asyncio.sleep(0.5)
        snapshot = reply.get("snapshot")
        if not isinstance(snapshot, dict):
            raise ServerGone("malformed snapshot")
        panes = {
            p["pane_id"]: p
            for p in snapshot.get("panes") or []
            if isinstance(p, dict) and _text(p.get("pane_id"))
        }
        self._panes = panes
        self._tab_sizes = {}
        for pane in panes.values():
            tab_id = str(pane.get("tab_id"))
            self._tab_sizes[tab_id] = self._tab_sizes.get(tab_id, 0) + 1
        signature = (
            orjson.dumps(snapshot.get("layouts") or [], option=orjson.OPT_SORT_KEYS),
            tuple(
                sorted(
                    (pid, (p.get("scroll") or {}).get("viewport_rows"))
                    for pid, p in panes.items()
                )
            ),
        )
        resized = signature != self._grid_signature
        if resized:
            self._grid_signature = signature
            self.grid_revision += 1
        await self._check_bridge()
        server = build_server(
            server_id=self.server_id,
            name=self._name,
            version=self._version,
            snapshot=snapshot,
            features=self._features(),
        )
        if server != self._server or resized:
            self._server = server
            self._on_change()

    def _features(self) -> HerdrFeatures:
        return HerdrFeatures(
            new_workspace=True,
            new_tab=True,
            split=True,
            rename=True,
            close_group=True,
            close_pane=True,
            control=self._bridge_bin is not None and not self._bridge_broken,
            type_in_watch=True,
            agents=[
                HerdrAgentKind(id=choice.id, label=choice.label)
                for choice in self._agents()
                if choice.id in _START_KINDS
            ],
        )

    async def _call(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """A request on behalf of a client; herdr's refusals become
        :class:`~mobaide.herdr.errors.HerdrError`."""
        try:
            return await self._client.request(method, params, timeout=timeout)
        except TuiApiError as exc:
            if exc.code in ("pane_not_found", "agent_not_found"):
                raise errors.pane_not_found() from exc
            if exc.code in ("workspace_not_found", "not_found"):
                raise errors.HerdrError(404, "not_found", "This item is closed.") from exc
            raise errors.upstream(exc.message) from exc
        except ServerGone as exc:
            raise errors.server_not_found() from exc

    def _require_pane(self, pane_id: str) -> dict[str, Any]:
        pane = self._panes.get(pane_id)
        if pane is None:
            raise errors.pane_not_found()
        return pane

    async def read_text(self, pane_id: str, lines: int) -> tuple[str, bool]:
        self._require_pane(pane_id)
        reply = await self._call(
            "pane.read",
            {"pane_id": pane_id, "source": "recent", "lines": lines, "format": "text"},
            timeout=15.0,
        )
        read = reply.get("read")
        if not isinstance(read, dict):
            return "", False
        return str(read.get("text") or ""), bool(read.get("truncated"))

    async def send_bytes(self, pane_id: str, data: bytes) -> None:
        self._require_pane(pane_id)
        await self._call(
            "pane.send_text",
            {"pane_id": pane_id, "text": data.decode("utf-8", errors="replace")},
        )

    async def submit(self, pane_id: str, text: str) -> None:
        pane = self._require_pane(pane_id)
        if pane.get("agent"):
            try:
                await self._client.request("agent.prompt", {"target": pane_id, "text": text})
                return
            except TuiApiError as exc:
                # ``agent.prompt`` refuses an agent that waits for an answer
                # or that is not ready. The user's line still belongs in the
                # pane: it may be the answer.
                if not exc.code.startswith("agent_"):
                    raise errors.upstream(exc.message) from exc
            except ServerGone as exc:
                raise errors.server_not_found() from exc
        await self._call(
            "pane.send_input", {"pane_id": pane_id, "text": text, "keys": ["enter"]}
        )

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
        new_pane = await self._create(
            placement, workspace_id=workspace_id, pane_id=pane_id, cwd=cwd, label=label
        )
        await self._after_change()
        if agent:
            try:
                await self.start_agent(new_pane, agent)
            except errors.HerdrError as exc:
                # The pane exists. An error alone would make the user try
                # again and get a second one.
                raise AgentNotStarted(new_pane, exc) from exc
        return new_pane

    async def _after_change(self) -> None:
        """Read the mirror again after an action, so the caller can use the
        result at once. Not every action sends an event (a pane rename sends
        none)."""
        try:
            await self._refresh()
        except (ServerGone, errors.HerdrError):
            pass

    async def _create(
        self,
        placement: str,
        *,
        workspace_id: str | None,
        pane_id: str | None,
        cwd: str | None,
        label: str | None,
    ) -> str:
        # ``focus: false`` everywhere: the view of a user at the desktop must
        # not move because a phone created something.
        if placement == "workspace":
            params: dict[str, Any] = {"focus": False}
            if cwd:
                params["cwd"] = cwd
            if label:
                params["label"] = label
            reply = await self._call("workspace.create", params)
            return _new_pane_id(reply.get("root_pane"))
        if placement == "tab":
            if not workspace_id:
                raise errors.HerdrError(422, "invalid_request", "A tab needs a workspace.")
            params = {"workspace_id": workspace_id, "focus": False}
            if cwd:
                params["cwd"] = cwd
            if label:
                params["label"] = label
            reply = await self._call("tab.create", params)
            return _new_pane_id(reply.get("root_pane"))
        if placement in ("split_right", "split_down"):
            if not pane_id:
                raise errors.HerdrError(422, "invalid_request", "A split needs a pane.")
            params = {
                "target_pane_id": pane_id,
                "direction": "right" if placement == "split_right" else "down",
                "focus": False,
            }
            if cwd:
                params["cwd"] = cwd
            reply = await self._call("pane.split", params)
            new_id = _new_pane_id(reply.get("pane"))
            if label:
                try:
                    await self._call("pane.rename", {"pane_id": new_id, "label": label})
                except errors.HerdrError as exc:
                    # The pane exists; an error would make the user split again.
                    logger.info("herdr.tui.split_label_refused code=%s", exc.code)
            return new_id
        raise errors.HerdrError(422, "invalid_request", "Unknown placement.")

    async def start_agent(self, pane_id: str, agent: str) -> None:
        kind = _START_KINDS.get(agent)
        if kind is None:
            raise errors.not_supported(f"start {agent}")
        await self._call(
            "agent.start",
            {
                "name": f"{kind}-{int(time.time()) % 100000}",
                "kind": kind,
                "pane_id": pane_id,
                "args": [],
            },
            timeout=START_AGENT_TIMEOUT_SECONDS,
        )
        await self._after_change()

    async def rename(self, target: str, target_id: str, label: str) -> None:
        group, key = _TARGET_METHODS[target]
        await self._call(f"{group}.rename", {key: target_id, "label": label})
        await self._after_change()

    async def close(self, target: str, target_id: str) -> None:
        group, key = _TARGET_METHODS[target]
        await self._call(f"{group}.close", {key: target_id})
        await self._after_change()

    async def pane_grid(self, pane_id: str) -> tuple[int, int] | None:
        pane = self._require_pane(pane_id)
        size = await self._tty_grid(pane_id)
        if size is not None:
            return size
        # No terminal device to ask (Windows): the tab rectangle includes the
        # pane border and the scrollbar column, which herdr draws around the
        # program's own grid.
        rows = _int((pane.get("scroll") or {}).get("viewport_rows"))
        rect = None
        server = self._server
        if server is not None:
            for workspace in server.workspaces:
                for tab in workspace.tabs:
                    for entry in tab.panes:
                        if entry.pane_id == pane_id:
                            rect = entry.rect
        if rect is None or rows is None:
            return None
        chrome = 3 if self._tab_sizes.get(str(pane.get("tab_id")), 1) > 1 else 1
        return max(rect.width - chrome, 1), rows

    async def _shell_pid(self, pane_id: str) -> int | None:
        try:
            reply = await self._client.request("pane.process_info", {"pane_id": pane_id})
        except (TuiApiError, errors.HerdrError, ServerGone):
            return None
        info = reply.get("process_info")
        pid = info.get("shell_pid") if isinstance(info, dict) else None
        return pid if isinstance(pid, int) and pid > 0 else None

    async def _tty_grid(self, pane_id: str) -> tuple[int, int] | None:
        if sys.platform == "win32":
            return None
        pid = await self._shell_pid(pane_id)
        if pid is None:
            return None
        tty = await run_text(["ps", "-o", "tty=", "-p", str(pid)])
        if not tty:
            return None
        return await in_thread(_tty_size, tty.strip())

    async def _server_executable(self) -> str | None:
        """The herdr binary that runs this server: the parent of a pane shell."""
        if sys.platform == "win32" or not self._panes:
            return None
        pid = await self._shell_pid(next(iter(self._panes)))
        if pid is None:
            return None
        parent = await run_text(["ps", "-o", "ppid=", "-p", str(pid)])
        if not parent or not parent.strip().isdigit():
            return None
        parent_pid = parent.strip()
        try:
            path = os.readlink(f"/proc/{parent_pid}/exe")
        except OSError:
            command = await run_text(["ps", "-o", "comm=", "-p", parent_pid])
            path = command.strip() if command else ""
        # Only a full path of a herdr binary: a bare name would resolve
        # against the agent's own working directory.
        if os.path.isabs(path) and os.path.basename(path) == "herdr":
            return path
        return None

    async def _check_bridge(self) -> None:
        """Find a herdr binary of the server's own version. The bridge speaks
        a private protocol that is tied to the build, so another version of
        the binary cannot open a stream."""
        now = time.monotonic()
        if self._bridge_bin is not None:
            return
        if (
            self._bridge_checked_at is not None
            and now - self._bridge_checked_at < BRIDGE_RECHECK_SECONDS
        ):
            return
        if not self._panes:
            return
        self._bridge_checked_at = now
        candidates: list[str] = []
        server_exe = await self._server_executable()
        if server_exe:
            candidates.append(server_exe)
        candidates.extend(installed_binaries(self._bin_override))
        seen: set[str] = set()
        for candidate in candidates:
            if candidate in seen or not is_executable(candidate):
                continue
            seen.add(candidate)
            if parse_version(await run_text([candidate, "--version"])) == parse_version(
                self._version
            ):
                self._bridge_bin = candidate
                return
        logger.info(
            "herdr.tui.no_bridge_binary server=%s version=%s", self.server_id, self._version
        )

    async def open_stream(
        self, pane_id: str, mode: str, cols: int, rows: int, sink: PaneStreamSink
    ) -> PaneStream:
        pane = self._require_pane(pane_id)
        target = _text(pane.get("terminal_id")) or pane_id
        if target.startswith("-"):
            # The id goes on the bridge command line. Herdr ids never start
            # so, and one that does would be read as an option.
            raise errors.pane_not_found()
        if (
            self._bridge_broken_at is not None
            and time.monotonic() - self._bridge_broken_at >= BRIDGE_RECHECK_SECONDS
        ):
            # One pane that closed under its stream must not cost every pane
            # the bridge for the life of the link.
            self._bridge_broken_at = None
        bridge = None if self._bridge_broken else self._bridge_bin
        if mode == CONTROL:
            if bridge is None:
                raise errors.not_supported("give control of a pane")
            return _BridgeStream(self, bridge, target, CONTROL, cols, rows, sink)
        if bridge is None:
            return _PollStream(self, pane_id, cols, rows, sink)
        return _WatchStream(self, bridge, target, pane_id, cols, rows, sink)

    def bridge_failed(self) -> None:
        """The bridge binary cannot open a stream on this server."""
        if not self._bridge_broken:
            self._bridge_broken_at = time.monotonic()
            logger.warning("herdr.tui.bridge_unusable server=%s", self.server_id)

    @property
    def _bridge_broken(self) -> bool:
        return self._bridge_broken_at is not None


def installed_binaries(override: str | None = None) -> list[str]:
    """Places a ``herdr`` binary can be: the override (the agent setting,
    then the process environment), ``PATH``, usual folders."""
    import shutil

    names = ("herdr.exe", "herdr") if sys.platform == "win32" else ("herdr",)
    found: list[str] = []
    override = override or os.environ.get("MOBAIDE_HERDR_BIN")
    if override:
        found.append(os.path.expanduser(override))
    for name in names:
        hit = shutil.which(name)
        if hit:
            found.append(os.path.abspath(hit))
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        found.append(os.path.join(local, "herdr", "bin", "herdr.exe"))
        found.append(os.path.join(home, ".local", "bin", "herdr.exe"))
    else:
        found.extend(
            [
                os.path.join(home, ".local", "bin", "herdr"),
                "/usr/local/bin/herdr",
                "/opt/homebrew/bin/herdr",
            ]
        )
    return found


def _new_pane_id(pane: object) -> str:
    pane_id = pane.get("pane_id") if isinstance(pane, dict) else None
    if not isinstance(pane_id, str) or not pane_id:
        raise errors.upstream("Herdr did not report the new pane.")
    return pane_id


class _BridgeStream:
    """One ``herdr terminal session observe|control`` process."""

    def __init__(
        self,
        link: TuiLink,
        binary: str,
        target: str,
        mode: str,
        cols: int,
        rows: int,
        sink: PaneStreamSink,
    ) -> None:
        self._link = link
        self._binary = binary
        self._target = target
        self._mode = mode
        self._cols = cols
        self._rows = rows
        self._sink = sink
        self._process: PipedProcess | None = None
        self._reader: asyncio.Task[None] | None = None
        self._closing = False
        self.frames = 0

    async def start(self) -> None:
        argv = [self._binary, "terminal", "session"]
        if self._mode == CONTROL:
            # One controller owns a terminal. The user asked for control, so
            # a controller that is already there gives way.
            argv += ["control", self._target, "--takeover"]
        else:
            argv += ["observe", self._target]
        argv += ["--cols", str(self._cols), "--rows", str(self._rows)]
        env = dict(os.environ)
        env.pop("HERDR_SESSION", None)
        env["HERDR_SOCKET_PATH"] = self._link._client.socket_path
        try:
            self._process = await spawn_piped(argv, env=env)
        except OSError as exc:
            raise errors.server_unreachable(str(exc)) from exc
        self._reader = asyncio.create_task(self._read(), name="herdr-bridge-reader")

    async def _read(self) -> None:
        process = self._process
        assert process is not None
        reason: str | None = None
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                try:
                    record = orjson.loads(line)
                except orjson.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                kind = record.get("type")
                if kind == "terminal.frame":
                    self._on_frame(record)
                elif kind == "terminal.closed":
                    reason = _text(record.get("reason"))
                    break
        except (asyncio.LimitOverrunError, ValueError):
            reason = "The pane stream sent a frame that is too large."
        except asyncio.CancelledError:
            raise
        if not self._closing:
            if reason is None:
                reason = process.stderr_tail() or None
            self._sink.on_closed(reason)

    def _on_frame(self, record: dict[str, Any]) -> None:
        if self._closing:
            return
        try:
            data = base64.b64decode(record.get("bytes") or "")
        except (ValueError, TypeError):
            return
        cols, rows = _int(record.get("width")), _int(record.get("height"))
        if cols and rows and (cols, rows) != (self._cols, self._rows):
            self._cols, self._rows = cols, rows
            self._sink.on_grid(cols, rows)
        self.frames += 1
        self._sink.on_output(data, repaint=bool(record.get("full")))

    async def _send(self, record: dict[str, Any]) -> None:
        process = self._process
        if process is None:
            return
        try:
            await asyncio.wait_for(
                process.write_stdin(orjson.dumps(record) + b"\n"), REQUEST_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            # The bridge does not read its stdin. Ending it lets the reader
            # see the end and report it; a wait here would hold every later
            # input and the close of this stream.
            await process.terminate()
        except (OSError, ValueError):
            pass

    async def write(self, data: bytes) -> None:
        if self._mode == CONTROL:
            await self._send(
                {"type": "terminal.input", "bytes": base64.b64encode(data).decode("ascii")}
            )

    async def resize(self, cols: int, rows: int) -> None:
        if self._mode == CONTROL and cols > 0 and rows > 0:
            self._cols, self._rows = cols, rows
            await self._send({"type": "terminal.resize", "cols": cols, "rows": rows})

    async def aclose(self) -> None:
        self._closing = True
        process = self._process
        if process is not None:
            try:
                if self._mode == CONTROL:
                    # Short: a write that is blocked already holds stdin, and
                    # the release must not keep the process from being ended.
                    async with asyncio.timeout(1.0):
                        await process.write_stdin(
                            orjson.dumps({"type": "terminal.release"}) + b"\n"
                        )
            except (TimeoutError, OSError, ValueError):
                pass
            finally:
                await process.terminate()
        reader = self._reader
        if reader is not None and reader is not asyncio.current_task():
            reader.cancel()
            try:
                await reader
            except (asyncio.CancelledError, Exception):
                pass


class _PollStream:
    """A pane view without the bridge: the visible screen, read on a timer.

    It shows colours but no cursor, and it is behind the pane by up to one
    poll. It is the fallback where no herdr binary of the server's version can
    run the bridge.
    """

    def __init__(
        self, link: TuiLink, pane_id: str, cols: int, rows: int, sink: PaneStreamSink
    ) -> None:
        self._link = link
        self._pane_id = pane_id
        self._sink = sink
        self._task: asyncio.Task[None] | None = None
        self._last: str | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._poll(), name="herdr-poll-stream")

    async def _poll(self) -> None:
        while True:
            try:
                reply = await self._link._client.request(
                    "pane.read",
                    {"pane_id": self._pane_id, "source": "visible", "format": "ansi"},
                )
            except TuiApiError as exc:
                self._sink.on_closed(exc.message)
                return
            except (ServerGone, errors.HerdrError):
                self._sink.on_closed(None)
                return
            read = reply.get("read")
            text = str(read.get("text") or "") if isinstance(read, dict) else ""
            if text != self._last:
                self._last = text
                body = text.replace("\r\n", "\n").replace("\n", "\r\n")
                self._sink.on_output(
                    b"\x1b[?25l\x1b[0m\x1b[H\x1b[2J" + body.encode("utf-8", errors="replace"),
                    repaint=True,
                )
            await asyncio.sleep(POLL_STREAM_SECONDS)

    async def write(self, data: bytes) -> None:
        return None

    async def resize(self, cols: int, rows: int) -> None:
        return None

    async def aclose(self) -> None:
        task = self._task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


class _WatchStream:
    """Watch a pane through the bridge; fall back to polling when the bridge
    ends before it drew anything (no bridge support on this host)."""

    def __init__(
        self,
        link: TuiLink,
        binary: str,
        target: str,
        pane_id: str,
        cols: int,
        rows: int,
        sink: PaneStreamSink,
    ) -> None:
        self._link = link
        self._pane_id = pane_id
        self._cols = cols
        self._rows = rows
        self._sink = sink
        self._bridge = _BridgeStream(link, binary, target, "watch", cols, rows, self)
        self._fallback: _PollStream | None = None
        self._closed = False
        # Held, so a task in flight is not collected.
        self._tasks: list[asyncio.Future[None]] = []

    async def start(self) -> None:
        await self._bridge.start()

    def on_output(self, data: bytes, *, repaint: bool) -> None:
        if not self._closed:
            self._sink.on_output(data, repaint=repaint)

    def on_grid(self, cols: int, rows: int) -> None:
        if not self._closed:
            self._sink.on_grid(cols, rows)

    def on_closed(self, reason: str | None) -> None:
        if self._closed:
            return
        if self._bridge.frames == 0 and self._link.pane_key(self._pane_id) is not None:
            self._link.bridge_failed()
            # The bridge process is over for this stream: reap it now, not
            # when the poll fallback closes.
            self._tasks.append(asyncio.ensure_future(self._bridge.aclose()))
            self._fallback = _PollStream(
                self._link, self._pane_id, self._cols, self._rows, self._sink
            )
            self._tasks.append(asyncio.ensure_future(self._fallback.start()))
            return
        self._sink.on_closed(reason)

    async def write(self, data: bytes) -> None:
        return None

    async def resize(self, cols: int, rows: int) -> None:
        return None

    async def aclose(self) -> None:
        self._closed = True
        # First the tasks of ``on_closed``: a fallback whose start has not run
        # yet would start after its own close.
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._bridge.aclose()
        if self._fallback is not None:
            await self._fallback.aclose()


class TuiBackend:
    product = PRODUCT

    def __init__(
        self,
        agents: Callable[[], list[AgentChoice]],
        *,
        bin_override: str | None = None,
        config_dir_override: str | None = None,
    ) -> None:
        self._agents = agents
        self._bin_override = bin_override
        self._config_dir_override = config_dir_override

    async def discover(
        self, known: frozenset[str]
    ) -> dict[str, Callable[[Callable[[], None]], ServerLink]]:
        root = config_dir(self._config_dir_override)
        sockets = await in_thread(session_sockets, root)
        found: dict[str, Callable[[Callable[[], None]], ServerLink]] = {}
        for name, path in sockets.items():
            server_id = f"{PRODUCT}:{name}"
            if server_id in known:
                continue
            try:
                await TuiClient(path).request("ping", timeout=2.0)
            except (ServerGone, TuiApiError, errors.HerdrError):
                continue
            found[server_id] = self._factory(server_id, name, path)
        return found

    def _factory(
        self, server_id: str, name: str, path: str
    ) -> Callable[[Callable[[], None]], ServerLink]:
        return lambda on_change: TuiLink(
            server_id, name, path, on_change, self._agents, bin_override=self._bin_override
        )
