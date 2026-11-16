"""herdr-core backend (binary ``hc``, the runtime of the Herdr desktop app).

The API is JSON-RPC 2.0 on a local socket. After ``hello {"framed": true}``
the connection carries frames ``[channel u32][kind u8][length u32][payload]``
(little-endian): channel 0 is JSON-RPC, every other channel is a stream. One
connection per server carries the calls, the events and all pane streams.

herdr-core knows only terminals. Spaces and tabs belong to the desktop app,
which marks its terminals with the labels ``herdr.space`` and ``herdr.tab``;
the tree here is built from those labels. An agent has a state only when its
program reports one (OSC 7501).

One size per terminal: an ``observe`` stream never changes it, any other
stream sets it to the viewer's size. An observer cannot type on its stream,
so input in watch mode goes through ``terminal.keys`` and ``terminal.send``.
"""
from __future__ import annotations

import asyncio
import collections
import logging
import os
import re
import shlex
import struct
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import orjson

from . import errors
from .agents import AgentChoice, normalize_agent
from .backend import CONTROL, PaneStream, PaneStreamSink, ServerGone, ServerLink
from .models import (
    HerdrAgentKind,
    HerdrFeatures,
    HerdrPane,
    HerdrServer,
    HerdrTab,
    HerdrWorkspace,
)
from .transport import LocalStream, in_thread, open_local_stream

logger = logging.getLogger(__name__)

PRODUCT = "core"
GENERATION = 1

CONNECT_TIMEOUT_SECONDS = 3.0
CALL_TIMEOUT_SECONDS = 8.0
# The core sends a heartbeat every 15 s; this read also covers a lost event.
SAFETY_POLL_SECONDS = 20.0
EVENT_BURST_SECONDS = 0.05
MAX_FRAME_BYTES = 16 * 1024 * 1024
# Scrollback lines that a new watch stream paints above the screen.
WATCH_HISTORY_LINES = 500
NAMES_REFRESH_SECONDS = 30.0
# A message waits for the program to take it. The start of an agent comes
# over REST, where the relay and the clients give a request 30 s.
SUBMIT_TIMEOUT_SECONDS = 75.0
START_AGENT_TIMEOUT_SECONDS = 20.0
MAX_EARLY_CHANNELS = 16
MAX_EARLY_FRAMES = 64
MAX_DROPPED_CHANNELS = 256

FRAME_DATA = 0
FRAME_CREDIT = 1
FRAME_CLOSE = 2
FRAME_RESIZE = 3

SPACE_LABEL = "herdr.space"
TAB_LABEL = "herdr.tab"
APP_DOCUMENT = "herdr-app"
# The group for terminals that no desktop-app space holds.
LOOSE_GROUP_LABEL = "Terminals"

_STATES = {
    "idle": "idle",
    "working": "working",
    "done": "done",
    "blocked": "blocked",
    "error": "failed",
}
_NAME_RE = re.compile(r"[^a-z0-9._-]+")
_PLAIN_KEYS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789`\\[],=-.';/")
_ESCAPE_KEYS = {
    "[A": "up", "[B": "down", "[C": "right", "[D": "left",
    "OA": "up", "OB": "down", "OC": "right", "OD": "left",
    "[H": "home", "[F": "end", "OH": "home", "OF": "end",
    "[3~": "delete", "[5~": "pageup", "[6~": "pagedown", "[2~": "insert",
    "[Z": "shift+tab",
}


class CoreApiError(Exception):
    """An error reply from herdr-core; ``kind`` is its stable error name."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


def state_home() -> str:
    override = os.environ.get("XDG_STATE_HOME")
    if override:
        return override
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        return os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    return os.path.join(home, ".local", "state")


def session_sockets(root: str) -> dict[str, str]:
    """``session name`` → socket path for every herdr-core session on disk."""
    base = os.path.join(root, "herdr-core")
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return {}
    found: dict[str, str] = {}
    for name in names:
        session_dir = os.path.join(base, name)
        if not os.path.isfile(os.path.join(session_dir, "core.json")):
            continue
        path = _socket_path(session_dir, name)
        if path is not None:
            found[name] = path
    return found


def _socket_path(session_dir: str, name: str) -> str | None:
    if sys.platform == "win32":
        user = "".join(c for c in os.environ.get("USERNAME", "") if c.isascii() and c.isalnum())
        return f"\\\\.\\pipe\\herdr-core-{user}-{name}.sock"
    candidates = []
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        candidates.append(os.path.join(runtime, "herdr-core", f"{name}.sock"))
    candidates.append(os.path.join(f"/run/user/{os.getuid()}", "herdr-core", f"{name}.sock"))
    candidates.append(os.path.join(session_dir, f"{name}.sock"))
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None


def keys_for_bytes(data: bytes) -> list[str] | None:
    """The key names for raw terminal input, or ``None`` when a byte has no
    key name in herdr-core (it names only a small set of keys)."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    keys: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\x1b":
            for sequence, key in _ESCAPE_KEYS.items():
                if text.startswith(sequence, i + 1):
                    keys.append(key)
                    i += 1 + len(sequence)
                    break
            else:
                raw_follower = text[i + 1] if i + 1 < len(text) else ""
                if raw_follower in ("O", "["):
                    # A function-key sequence with no key name (F1 is ESC O P).
                    # Read as alt+o it would type its last letter.
                    return None
                follower = raw_follower.lower()
                if follower in _PLAIN_KEYS and follower not in "[\\":
                    keys.append(f"alt+{follower}")
                    i += 2
                elif i + 1 == len(text):
                    keys.append("esc")
                    i += 1
                else:
                    return None
            continue
        code = ord(ch)
        if ch in "\r\n":
            keys.append("enter")
        elif ch == "\t":
            keys.append("tab")
        elif ch in "\x7f\x08":
            keys.append("backspace")
        elif ch == " ":
            keys.append("space")
        elif 1 <= code <= 26:
            keys.append(f"ctrl+{chr(code + 96)}")
        elif ch in _PLAIN_KEYS:
            keys.append("backslash" if ch == "\\" else ch)
        elif ch.isascii() and ch.isupper():
            keys.append(f"shift+{ch.lower()}")
        else:
            return None
        i += 1
    return keys


class CoreConnection:
    """One framed connection to a herdr-core socket."""

    def __init__(self, socket_path: str) -> None:
        self._socket_path = socket_path
        self._stream: LocalStream | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._abort_task: asyncio.Future[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._channels: dict[int, Callable[[int, bytes], None]] = {}
        # Frames for a channel nobody listens on yet. The core sends the first
        # frames of a stream right behind the reply that names its channel,
        # before the caller can register a handler.
        self._early: dict[int, list[tuple[int, bytes]]] = {}
        # Channels whose stream is over. A frame still on its way must not be
        # kept as "early": nothing would ever take it.
        self._dropped: collections.deque[int] = collections.deque(maxlen=MAX_DROPPED_CHANNELS)
        self._next_id = 1
        self.closed = asyncio.Event()

    async def open(self) -> dict[str, Any]:
        """Connect and switch to framed mode; returns the ``hello`` result."""
        try:
            stream = await asyncio.wait_for(
                open_local_stream(self._socket_path), CONNECT_TIMEOUT_SECONDS
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise ServerGone(str(exc)) from exc
        self._stream = stream
        try:
            await stream.write(
                orjson.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 0,
                        "method": "hello",
                        "params": {"framed": True, "generation": GENERATION},
                    }
                )
                + b"\n"
            )
            raw = await asyncio.wait_for(stream.reader.readline(), CALL_TIMEOUT_SECONDS)
            reply = orjson.loads(raw) if raw else None
        except (OSError, ValueError, asyncio.TimeoutError) as exc:
            await stream.aclose()
            raise ServerGone(str(exc)) from exc
        result = reply.get("result") if isinstance(reply, dict) else None
        if not isinstance(result, dict) or not result.get("framed"):
            await stream.aclose()
            raise ServerGone("herdr-core refused the framed protocol")
        self._reader_task = asyncio.create_task(self._read(), name="herdr-core-reader")
        return result

    async def _read(self) -> None:
        stream = self._stream
        assert stream is not None
        try:
            while True:
                header = await stream.reader.readexactly(9)
                channel, kind, length = struct.unpack("<IBI", header)
                if length > MAX_FRAME_BYTES:
                    break
                payload = await stream.reader.readexactly(length) if length else b""
                if channel == 0:
                    if kind == FRAME_DATA:
                        self._on_rpc(payload)
                    continue
                if kind == FRAME_DATA and payload:
                    # Flow control: the core stops a stream that got no
                    # credit for the bytes its viewer took.
                    await self.send(channel, FRAME_CREDIT, struct.pack("<I", len(payload)))
                handler = self._channels.get(channel)
                if handler is not None:
                    handler(kind, payload)
                elif channel not in self._dropped:
                    if channel not in self._early and len(self._early) >= MAX_EARLY_CHANNELS:
                        # The newest channel is the one about to get a handler.
                        self._early.pop(next(iter(self._early)))
                    frames = self._early.setdefault(channel, [])
                    if len(frames) < MAX_EARLY_FRAMES:
                        frames.append((kind, payload))
        except (asyncio.IncompleteReadError, OSError, ValueError):
            pass
        except asyncio.CancelledError:
            raise
        finally:
            self._fail("connection closed")

    def _fail(self, reason: str) -> None:
        self.closed.set()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ServerGone(reason))
        self._pending.clear()
        for handler in list(self._channels.values()):
            handler(FRAME_CLOSE, b"")
        self._channels.clear()
        self._early.clear()

    def _on_rpc(self, payload: bytes) -> None:
        for line in payload.splitlines():
            if not line.strip():
                continue
            try:
                message = orjson.loads(line)
            except orjson.JSONDecodeError:
                continue
            if not isinstance(message, dict) or "id" not in message:
                continue
            message_id = message.get("id")
            future = self._pending.pop(message_id, None) if isinstance(message_id, int) else None
            if future is None or future.done():
                continue
            error = message.get("error")
            if isinstance(error, dict):
                data = _dict(error.get("data"))
                future.set_exception(
                    CoreApiError(str(data.get("kind") or "error"), str(error.get("message") or ""))
                )
            else:
                result = message.get("result")
                future.set_result(result if isinstance(result, dict) else {})

    async def send(self, channel: int, kind: int, payload: bytes = b"") -> None:
        stream = self._stream
        if stream is None or self.closed.is_set():
            raise ServerGone("connection closed")
        frame = struct.pack("<IBI", channel, kind, len(payload)) + payload
        async with self._write_lock:
            try:
                # With a deadline: a core that is alive but does not read would
                # hold this lock, and with it every later call, for ever.
                await asyncio.wait_for(stream.write(frame), CALL_TIMEOUT_SECONDS)
            except asyncio.TimeoutError as exc:
                self._fail("write timed out")
                # Kept on the instance: a task nobody holds can be collected.
                self._abort_task = asyncio.ensure_future(stream.aclose())
                raise ServerGone("write timed out") from exc
            except (OSError, ValueError) as exc:
                raise ServerGone(str(exc)) from exc

    async def call(
        self, method: str, params: dict[str, Any], *, timeout: float = CALL_TIMEOUT_SECONDS
    ) -> dict[str, Any]:
        """Raises :class:`CoreApiError` for an error reply."""
        request_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self.send(
                0,
                FRAME_DATA,
                orjson.dumps(
                    {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
                ),
            )
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as exc:
            raise errors.server_unreachable() from exc
        finally:
            self._pending.pop(request_id, None)

    def on_channel(self, channel: int, handler: Callable[[int, bytes], None]) -> None:
        self._channels[channel] = handler
        for kind, payload in self._early.pop(channel, ()):
            handler(kind, payload)

    def drop_channel(self, channel: int) -> None:
        self._channels.pop(channel, None)
        self._early.pop(channel, None)
        self._dropped.append(channel)

    async def aclose(self) -> None:
        task = self._reader_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if self._stream is not None:
            await self._stream.aclose()
        self._fail("connection closed")


def _text(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _labels(terminal: dict[str, Any]) -> dict[str, Any]:
    return _dict(terminal.get("labels"))


def _pane(terminal: dict[str, Any]) -> HerdrPane:
    foreground = _dict(terminal.get("foreground"))
    status = _dict(terminal.get("status"))
    agent: str | None = None
    state: str | None = None
    if status and terminal.get("reported"):
        agent = normalize_agent(status.get("app")) or normalize_agent(foreground.get("name")) or "agent"
        state = _STATES.get(str(status.get("state")), "unknown")
    argv = terminal.get("argv") if isinstance(terminal.get("argv"), list) else []
    title = (
        _text(terminal.get("name"))
        or _text(terminal.get("title"))
        or _text(foreground.get("name"))
        or (os.path.basename(str(argv[0])) if argv else None)
        or str(terminal.get("address"))
    )
    turn = terminal.get("turn")
    return HerdrPane(
        pane_id=str(terminal.get("address")),
        title=title,
        agent=agent,
        status=state,
        turn=turn if agent and isinstance(turn, int) and not isinstance(turn, bool) else None,
        cwd=_text(foreground.get("cwd")) or _text(terminal.get("cwd")),
    )


def names_from_document(value: object) -> dict[str, str]:
    """``id`` → display name for every named object in the desktop app's
    document. The document has no promised format, so this reads only the one
    pattern it needs and returns nothing for a shape it does not know."""
    names: dict[str, str] = {}

    def visit(node: object, depth: int) -> None:
        if depth > 12:
            return
        if isinstance(node, dict):
            node_id = node.get("id")
            if isinstance(node_id, str):
                for key in ("name", "title", "label"):
                    name = _text(node.get(key))
                    if name:
                        names.setdefault(node_id, name)
                        break
            for child in node.values():
                visit(child, depth + 1)
        elif isinstance(node, list):
            for child in node:
                visit(child, depth + 1)

    visit(value, 0)
    return names


def build_server(
    *,
    server_id: str,
    name: str,
    version: str | None,
    terminals: list[dict[str, Any]],
    names: dict[str, str],
    features: HerdrFeatures,
) -> HerdrServer:
    live = [
        t
        for t in terminals
        if isinstance(t, dict) and t.get("state") == "live" and _text(t.get("address"))
    ]
    live.sort(key=lambda t: (t.get("created_ms") or 0, str(t.get("address"))))
    # space id → tab id → panes, in order of first appearance.
    spaces: dict[str, dict[str, list[HerdrPane]]] = {}
    for terminal in live:
        labels = _labels(terminal)
        space = _text(labels.get(SPACE_LABEL)) or ""
        tab = _text(labels.get(TAB_LABEL)) or ""
        spaces.setdefault(space, {}).setdefault(tab, []).append(_pane(terminal))
    workspaces: list[HerdrWorkspace] = []
    number = 0
    for space, tabs in sorted(spaces.items(), key=lambda item: item[0] == ""):
        if space:
            number += 1
        workspaces.append(
            HerdrWorkspace(
                workspace_id=space,
                label=(names.get(space) or f"Space {number}") if space else LOOSE_GROUP_LABEL,
                tabs=[
                    HerdrTab(
                        tab_id=tab,
                        label=(names.get(tab) or f"Tab {index}") if tab else LOOSE_GROUP_LABEL,
                        panes=panes,
                    )
                    for index, (tab, panes) in enumerate(tabs.items(), start=1)
                ],
            )
        )
    return HerdrServer(
        server_id=server_id,
        product=PRODUCT,
        name=name,
        version=version,
        features=features,
        workspaces=workspaces,
    )


class CoreLink(ServerLink):
    def __init__(
        self,
        server_id: str,
        name: str,
        socket_path: str,
        on_change: Callable[[], None],
        agents: Callable[[], list[AgentChoice]],
    ) -> None:
        super().__init__(server_id, on_change)
        self._name = name
        self._socket_path = socket_path
        self._agents = agents
        self._conn: CoreConnection | None = None
        self._version: str | None = None
        self._server: HerdrServer | None = None
        self._terminals: dict[str, dict[str, Any]] = {}
        self._names: dict[str, str] = {}
        self._names_read_at: float | None = None
        self._sizes: dict[str, tuple[Any, Any]] = {}
        self.grid_revision = 0

    def state(self) -> HerdrServer | None:
        return self._server

    def pane_key(self, pane_id: str) -> str | None:
        return pane_id if pane_id in self._terminals else None

    def pane_id_for_key(self, key: str) -> str | None:
        return key if key in self._terminals else None

    async def run(self) -> None:
        conn = CoreConnection(self._socket_path)
        hello = await conn.open()
        self._conn = conn
        try:
            self._version = _text(hello.get("version"))
            dirty = asyncio.Event()
            subscribed = await self._rpc("events.subscribe", {})
            channel = subscribed.get("channel")
            if isinstance(channel, int):
                conn.on_channel(channel, lambda _kind, _payload: dirty.set())
            await self._apply(subscribed.get("terminals"))
            closed = asyncio.ensure_future(conn.closed.wait())
            try:
                while True:
                    changed = asyncio.ensure_future(dirty.wait())
                    await asyncio.wait(
                        {changed, closed},
                        timeout=SAFETY_POLL_SECONDS,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    changed.cancel()
                    if conn.closed.is_set():
                        raise ServerGone("connection closed")
                    await asyncio.sleep(EVENT_BURST_SECONDS)
                    dirty.clear()
                    await self._reload()
            finally:
                closed.cancel()
        finally:
            self._conn = None
            await conn.aclose()

    async def aclose(self) -> None:
        conn = self._conn
        if conn is not None:
            await conn.aclose()

    async def _reload(self) -> None:
        listed = await self._rpc("terminal.list", {})
        await self._apply(listed.get("terminals"))

    async def _after_change(self) -> None:
        """Read the mirror again after an action, so the caller can use the
        result at once."""
        try:
            await self._reload()
        except ServerGone:
            pass

    async def _rpc(
        self, method: str, params: dict[str, Any], *, timeout: float = CALL_TIMEOUT_SECONDS
    ) -> dict[str, Any]:
        """A call for the mirror itself: a refusal means the server is not usable."""
        conn = self._conn
        if conn is None:
            raise ServerGone("not connected")
        try:
            return await conn.call(method, params, timeout=timeout)
        except (CoreApiError, errors.HerdrError) as exc:
            raise ServerGone(str(exc)) from exc

    async def _call(
        self, method: str, params: dict[str, Any], *, timeout: float = CALL_TIMEOUT_SECONDS
    ) -> dict[str, Any]:
        """A call on behalf of a client; refusals become
        :class:`~mobaide.herdr.errors.HerdrError`."""
        conn = self._conn
        if conn is None:
            raise errors.server_not_found()
        try:
            return await conn.call(method, params, timeout=timeout)
        except CoreApiError as exc:
            if exc.kind in ("not_found", "finished"):
                raise errors.pane_not_found() from exc
            raise errors.upstream(exc.message) from exc
        except ServerGone as exc:
            raise errors.server_not_found() from exc

    async def _apply(self, terminals: object) -> None:
        listed = [t for t in terminals if isinstance(t, dict)] if isinstance(terminals, list) else []
        self._terminals = {
            str(t["address"]): t
            for t in listed
            if t.get("state") == "live" and _text(t.get("address"))
        }
        sizes = {address: (t.get("cols"), t.get("rows")) for address, t in self._terminals.items()}
        resized = sizes != self._sizes
        if resized:
            self._sizes = sizes
            self.grid_revision += 1
        await self._read_names()
        server = build_server(
            server_id=self.server_id,
            name=self._name,
            version=self._version,
            terminals=listed,
            names=self._names,
            features=self._features(),
        )
        if server != self._server or resized:
            self._server = server
            self._on_change()

    async def _read_names(self) -> None:
        """Names of the desktop app's spaces and tabs, read at most every
        ``NAMES_REFRESH_SECONDS`` and only when a terminal carries a label."""
        if not any(_labels(t).get(SPACE_LABEL) for t in self._terminals.values()):
            return
        now = asyncio.get_running_loop().time()
        if self._names_read_at is not None and now - self._names_read_at < NAMES_REFRESH_SECONDS:
            return
        self._names_read_at = now
        conn = self._conn
        if conn is None:
            return
        try:
            document = await conn.call("doc.get", {"name": APP_DOCUMENT})
        except (CoreApiError, errors.HerdrError, ServerGone):
            return
        self._names = names_from_document(document.get("value"))

    def _features(self) -> HerdrFeatures:
        return HerdrFeatures(
            new_workspace=True,
            split=True,
            close_pane=True,
            control=True,
            agents=[HerdrAgentKind(id=c.id, label=c.label) for c in self._agents()],
        )

    def _require(self, pane_id: str) -> dict[str, Any]:
        terminal = self._terminals.get(pane_id)
        if terminal is None:
            raise errors.pane_not_found()
        return terminal

    async def read_text(self, pane_id: str, lines: int) -> tuple[str, bool]:
        self._require(pane_id)
        reply = await self._call(
            "terminal.read", {"terminal": pane_id, "lines": lines}, timeout=20.0
        )
        return str(reply.get("text") or ""), reply.get("complete") is False

    async def send_bytes(self, pane_id: str, data: bytes) -> None:
        self._require(pane_id)
        keys = keys_for_bytes(data)
        if not keys:
            logger.debug("herdr.core.input_without_key_name pane=%s", pane_id)
            return
        await self._call("terminal.keys", {"terminal": pane_id, "keys": keys})

    async def submit(
        self, pane_id: str, text: str, *, timeout: float = SUBMIT_TIMEOUT_SECONDS
    ) -> None:
        self._require(pane_id)
        conn = self._conn
        if conn is None:
            raise errors.server_not_found()
        try:
            await conn.call("terminal.send", {"terminal": pane_id, "text": text}, timeout=timeout)
        except CoreApiError as exc:
            if exc.kind != "blocked":
                raise errors.upstream(exc.message) from exc
            # The program waits for an answer, and the user's line is one.
            await self._call(
                "terminal.send", {"terminal": pane_id, "text": text, "force": True}, timeout=timeout
            )
        except ServerGone as exc:
            raise errors.server_not_found() from exc

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
        params: dict[str, Any] = {"keep": True, "no_parent": True}
        if placement in ("split_right", "split_down"):
            # herdr-core has no splits. A terminal with the labels of its
            # neighbour appears in the same tab of the desktop app.
            source = self._require(pane_id or "")
            labels = {
                key: value
                for key, value in _labels(source).items()
                if key in (SPACE_LABEL, TAB_LABEL) and isinstance(value, str)
            }
            if labels:
                params["labels"] = labels
            cwd = cwd or _text(_dict(source.get("foreground")).get("cwd")) or _text(source.get("cwd"))
        elif placement != "workspace":
            raise errors.not_supported("add a tab")
        if cwd:
            params["cwd"] = cwd
        name = _NAME_RE.sub("-", (label or "").strip().lower()).strip("-")[:32]
        if name:
            params["name"] = name
        if agent:
            choice = next((c for c in self._agents() if c.id == agent), None)
            if choice is None:
                raise errors.not_supported(f"start {agent}")
            params["argv"] = [choice.binary]
        reply = await self._call("terminal.run", params, timeout=20.0)
        address = _text(reply.get("address"))
        if address is None:
            raise errors.upstream("herdr-core did not report the new terminal.")
        await self._after_change()
        return address

    async def start_agent(self, pane_id: str, agent: str) -> None:
        self._require(pane_id)
        choice = next((c for c in self._agents() if c.id == agent), None)
        if choice is None:
            raise errors.not_supported(f"start {agent}")
        # The line goes to the shell of the pane, in that shell's quoting.
        quoted = (
            subprocess.list2cmdline([choice.binary])
            if sys.platform == "win32"
            else shlex.quote(choice.binary)
        )
        await self.submit(pane_id, quoted, timeout=START_AGENT_TIMEOUT_SECONDS)

    async def close(self, target: str, target_id: str) -> None:
        if target != "pane":
            raise errors.not_supported("close a space or a tab")
        self._require(target_id)
        await self._call("terminal.kill", {"terminal": target_id}, timeout=15.0)
        try:
            await self._call("terminal.rm", {"terminal": target_id})
        except errors.HerdrError:
            pass  # removed on its own (``rm``) or still closing
        await self._after_change()

    async def pane_grid(self, pane_id: str) -> tuple[int, int] | None:
        terminal = self._require(pane_id)
        cols, rows = terminal.get("cols"), terminal.get("rows")
        if isinstance(cols, int) and isinstance(rows, int) and cols > 0 and rows > 0:
            return cols, rows
        return None

    async def open_stream(
        self, pane_id: str, mode: str, cols: int, rows: int, sink: PaneStreamSink
    ) -> PaneStream:
        self._require(pane_id)
        conn = self._conn
        if conn is None:
            raise errors.server_not_found()
        restore = await self.pane_grid(pane_id) if mode == CONTROL else None
        return _CoreStream(self, conn, pane_id, mode, cols, rows, sink, restore)


class _CoreStream:
    """One ``terminal.attach`` channel."""

    def __init__(
        self,
        link: CoreLink,
        conn: CoreConnection,
        pane_id: str,
        mode: str,
        cols: int,
        rows: int,
        sink: PaneStreamSink,
        restore: tuple[int, int] | None,
    ) -> None:
        self._link = link
        self._conn = conn
        self._pane_id = pane_id
        self._mode = mode
        self._cols = cols
        self._rows = rows
        self._sink = sink
        # The size the terminal had before control. herdr-core keeps the last
        # viewer's size, so the stream puts the old one back when it closes.
        self._restore = restore
        self._channel: int | None = None
        self._closing = False

    async def start(self) -> None:
        params: dict[str, Any] = {
            "terminal": self._pane_id,
            "cols": self._cols,
            "rows": self._rows,
        }
        if self._mode != CONTROL:
            params.update(observe=True, resize_notifications=True, history=WATCH_HISTORY_LINES)
        reply = await self._link._call("terminal.attach", params)
        channel = reply.get("channel")
        if not isinstance(channel, int):
            raise errors.upstream("herdr-core opened no stream.")
        self._channel = channel
        self._conn.on_channel(channel, self._on_frame)

    def _on_frame(self, kind: int, payload: bytes) -> None:
        if self._closing:
            return
        if kind == FRAME_DATA:
            # A paint that replaces the whole screen starts with CAN.
            self._sink.on_output(payload, repaint=payload.startswith(b"\x18"))
        elif kind == FRAME_RESIZE and len(payload) >= 4:
            cols, rows = struct.unpack("<HH", payload[:4])
            if cols > 0 and rows > 0:
                self._cols, self._rows = cols, rows
                self._sink.on_grid(cols, rows)
        elif kind == FRAME_CLOSE:
            reason = None
            if payload:
                try:
                    reason = _text(orjson.loads(payload).get("reason"))
                except (orjson.JSONDecodeError, AttributeError):
                    reason = None
            # The core ended the stream: its channel is over on this side too.
            channel, self._channel = self._channel, None
            if channel is not None:
                self._conn.drop_channel(channel)
            self._sink.on_closed(reason)

    async def _frame(self, kind: int, payload: bytes = b"") -> None:
        if self._channel is None:
            return
        try:
            await self._conn.send(self._channel, kind, payload)
        except ServerGone:
            pass

    async def write(self, data: bytes) -> None:
        if self._mode == CONTROL:
            await self._frame(FRAME_DATA, data)

    async def resize(self, cols: int, rows: int) -> None:
        if self._mode == CONTROL and 0 < cols <= 0xFFFF and 0 < rows <= 0xFFFF:
            self._cols, self._rows = cols, rows
            await self._frame(FRAME_RESIZE, struct.pack("<HH", cols, rows))

    async def aclose(self) -> None:
        self._closing = True
        if self._channel is None:
            return
        if self._restore is not None:
            await self._frame(FRAME_RESIZE, struct.pack("<HH", *self._restore))
        await self._frame(FRAME_CLOSE)
        self._conn.drop_channel(self._channel)
        self._channel = None


class CoreBackend:
    product = PRODUCT

    def __init__(self, agents: Callable[[], list[AgentChoice]]) -> None:
        self._agents = agents

    async def discover(
        self, known: frozenset[str]
    ) -> dict[str, Callable[[Callable[[], None]], ServerLink]]:
        sockets = await in_thread(session_sockets, state_home())
        found: dict[str, Callable[[Callable[[], None]], ServerLink]] = {}
        for name, path in sockets.items():
            server_id = f"{PRODUCT}:{name}"
            if server_id in known:
                continue
            probe = CoreConnection(path)
            try:
                await probe.open()
            except ServerGone:
                continue
            finally:
                await probe.aclose()
            found[server_id] = self._factory(server_id, name, path)
        return found

    def _factory(
        self, server_id: str, name: str, path: str
    ) -> Callable[[Callable[[], None]], ServerLink]:
        return lambda on_change: CoreLink(server_id, name, path, on_change, self._agents)
