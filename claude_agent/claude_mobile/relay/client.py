"""Outbound WebSocket relay client connecting the agent to a Central Server.

The agent connects TO the Central Server's ``/ws/agent-tunnel`` endpoint,
authenticates, and multiplexes all session I/O, session-list updates, and
events through a single binary-envelope tunnel.  The existing direct-connection
mode (mobile connects to agent via LAN) continues to work alongside the relay.
"""
from __future__ import annotations

import asyncio
import json
import logging
import platform
import re
import struct
import uuid
from typing import Any

import orjson
import websockets
from websockets.asyncio.client import ClientConnection

from claude_mobile.core.models import SessionConfig, SessionEvent, SessionState

from pydantic import ValidationError

from claude_mobile.agent.projects.models import (
    ProjectUpdateModel,
    ProjectWriteModel,
    ReorderRequest,
)
from claude_mobile.agent.projects.service import (
    DuplicateProjectError,
    ProjectInUseError,
    ReorderSetMismatchError,
    create_project,
    delete_project,
    list_projects,
    reorder_projects,
    update_project,
)

from claude_mobile.agent.autoscan.stream import run_autoscan_stream

from .config import RelayConfig
from .envelope import (
    CH_AUTOSCAN,
    CH_CONTROL,
    CH_EVENTS,
    CH_SESSION_IO,
    CH_SESSIONS_LIST,
    decode,
    encode,
)

logger = logging.getLogger(__name__)

# PTY frame type bytes — must stay in sync with agent/ws/session_io.py.
FRAME_OUTPUT = b"o"
FRAME_INPUT = b"i"
FRAME_RESIZE = b"r"
FRAME_EXECUTE = b"x"
FRAME_STATE = b"s"
FRAME_REPLAY = b"b"

_EVENT_TO_STATUS = {
    "started": "running",
    "stopped": "stopped",
    "error": "crashed",
}

# Fields whose raw input must never be echoed back in a validation error —
# mirrors the handler registered in ``agent/app.py`` so direct HTTP and
# relay-forwarded 422 responses have identical redaction guarantees.
_SECRET_VALIDATION_FIELDS = frozenset({"linear_api_key"})


def _scrub_validation_errors(exc: "ValidationError") -> list[dict[str, object]]:
    """Return Pydantic error records with raw inputs stripped from secret fields."""
    scrubbed: list[dict[str, object]] = []
    for err in exc.errors():
        loc = err.get("loc", ())
        cleaned: dict[str, object] = dict(err)
        if any(part in _SECRET_VALIDATION_FIELDS for part in loc):
            cleaned.pop("input", None)
        scrubbed.append(cleaned)
    return scrubbed

_STATE_MAP = {
    SessionState.STARTING: "starting",
    SessionState.RUNNING: "running",
    SessionState.STOPPING: "stopping",
    SessionState.STOPPED: "stopped",
    SessionState.CRASHED: "crashed",
}

# Strip DA capability-query replies before forwarding ring buffer content.
_DA_REPLY_STRIP = re.compile(
    rb"\x1b\[\?[\d;]*c|\x1b\[>[\d;]*c|\x1bP![|][^\x1b]*\x1b\\",
)

# Platform default shell for auto-created sessions.
_DEFAULT_SHELL = (
    ["cmd.exe"] if platform.system() == "Windows" else ["/bin/bash", "-l"]
)

MAX_AUTOSCAN_STREAMS = 10


def _state_frame(status: str, pid: int | None = None) -> bytes:
    payload: dict[str, object] = {"status": status}
    if pid is not None:
        payload["pid"] = pid
    return FRAME_STATE + json.dumps(payload).encode("utf-8")


class RelayClient:
    """Manages the outbound WebSocket connection to a Central Server relay.

    Responsibilities:
    - Authenticate with the central server and maintain a persistent tunnel
    - Reconnect with exponential backoff on disconnection
    - Bridge local session I/O through the tunnel (both directions)
    - Forward session-list updates and EventBus events through the tunnel
    - Handle control messages (ping, create_session, stop_session)
    """

    def __init__(
        self,
        config: RelayConfig,
        session_manager: Any,  # SessionManager
        event_bus: Any,  # EventBus
        broadcaster: Any,  # SessionListBroadcaster
        app_state: Any,  # FastAPI app.state (for fanout access)
    ) -> None:
        self._config = config
        self._session_manager = session_manager
        self._event_bus = event_bus
        self._broadcaster = broadcaster
        self._app_state = app_state

        self._ws: ClientConnection | None = None
        self._task: asyncio.Task | None = None
        self._stopping = False

        # Per-connection concurrent tasks (reset on each connection).
        self._heartbeat_task: asyncio.Task | None = None
        self._event_fwd_task: asyncio.Task | None = None
        self._sesslist_fwd_task: asyncio.Task | None = None
        self._session_watch_task: asyncio.Task | None = None

        # Session bridge state: session_id -> bridge pump task.
        self._bridges: dict[str, asyncio.Task] = {}
        # Track whether we've sent the handshake for a session through relay.
        self._handshake_sent: set[str] = set()

        # Per-mobile auto-scan subscriptions: subscription_id -> stream task.
        # Each mobile device opens its own /relay/ws/autoscan/{agent_id}
        # connection, which the central server tags with a fresh
        # subscription_id and forwards as CH_AUTOSCAN envelopes; we run
        # one run_autoscan_stream task per subscription, owning its own
        # inbox queue for mobile-to-agent text frames.
        self._autoscan_streams: dict[str, asyncio.Task] = {}
        self._autoscan_inboxes: dict[str, asyncio.Queue[str]] = {}

    async def start(self) -> None:
        """Start the relay client background task."""
        self._stopping = False
        self._task = asyncio.create_task(
            self._connect_loop(),
            name="relay-client",
        )
        logger.info(
            "relay.started server_url=%s agent_id=%s",
            self._config.server_url,
            self._config.agent_id,
        )

    async def stop(self) -> None:
        """Gracefully shut down the relay client."""
        self._stopping = True
        self._cancel_all_subtasks()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        logger.info("relay.stopped")

    async def _connect_loop(self) -> None:
        """Reconnect loop with exponential backoff."""
        delay = self._config.reconnect_base_delay
        while not self._stopping:
            try:
                await self._run_connection()
                # Successful connection that ended normally — reset backoff.
                delay = self._config.reconnect_base_delay
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("relay.connection_failed: %s", exc)

            if self._stopping:
                break

            logger.info("relay.reconnecting delay=%.1fs", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._config.reconnect_max_delay)

    async def _run_connection(self) -> None:
        """Single connection attempt: connect, authenticate, run tunnel."""
        tunnel_url = self._config.server_url.rstrip("/") + "/ws/agent-tunnel"
        async with websockets.connect(
            tunnel_url,
            open_timeout=10.0,
            ping_interval=None,  # Application-level pings via CH_CONTROL
            max_size=4 * 1024 * 1024,  # 4 MB max frame
        ) as ws:
            self._ws = ws

            await self._authenticate(ws)
            logger.info(
                "relay.authenticated agent_id=%s",
                self._config.agent_id,
            )

            self._handshake_sent.clear()

            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(ws), name="relay-heartbeat"
            )
            self._event_fwd_task = asyncio.create_task(
                self._forward_events(ws), name="relay-event-fwd"
            )
            self._sesslist_fwd_task = asyncio.create_task(
                self._forward_session_list(ws), name="relay-sesslist-fwd"
            )
            self._session_watch_task = asyncio.create_task(
                self._watch_session_changes(ws), name="relay-session-watch"
            )

            await self._bridge_existing_sessions(ws)

            try:
                await self._reader(ws)
            finally:
                self._cancel_all_subtasks()
                self._ws = None

    async def _authenticate(self, ws: ClientConnection) -> None:
        """Send auth message and wait for auth_ok."""
        auth_msg = json.dumps({
            "type": "auth",
            "agent_id": self._config.agent_id,
            "token": self._config.agent_token,
        })
        await ws.send(auth_msg)
        raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        resp = json.loads(raw)
        if resp.get("type") != "auth_ok":
            raise ConnectionError(f"Relay auth failed: {resp}")

    async def _reader(self, ws: ClientConnection) -> None:
        """Read messages from the relay tunnel and dispatch."""
        async for message in ws:
            if isinstance(message, bytes):
                await self._handle_binary(message, ws)
            elif isinstance(message, str):
                await self._handle_text(message, ws)

    async def _handle_binary(self, data: bytes, ws: ClientConnection) -> None:
        """Dispatch a binary envelope from the central server."""
        try:
            channel, session_id, payload = decode(data)
        except ValueError as exc:
            logger.debug("relay.decode_error: %s", exc)
            return

        if channel == CH_SESSION_IO:
            await self._handle_session_input(session_id, payload, ws)
        elif channel == CH_AUTOSCAN:
            self._deliver_autoscan_inbound(session_id, payload)
        elif channel == CH_CONTROL:
            await self._handle_control(payload, ws)

    def _deliver_autoscan_inbound(self, subscription_id: str, payload: bytes) -> None:
        """Drop a mobile-origin CH_AUTOSCAN payload into the matching inbox.

        Silently ignores frames for unknown or closed subscriptions — those
        are stale frames from a mobile that disconnected before its
        ``close_autoscan`` reached us.
        """
        inbox = self._autoscan_inboxes.get(subscription_id)
        if inbox is None:
            return
        try:
            inbox.put_nowait(payload.decode("utf-8"))
        except asyncio.QueueFull:
            logger.warning(
                "relay.autoscan.inbox_full subscription_id=%s — dropping mobile frame",
                subscription_id,
            )

    async def _handle_text(self, text: str, ws: ClientConnection) -> None:
        """Handle text-frame control messages (e.g. pairing_token response)."""
        try:
            msg = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return
        msg_type = msg.get("type")
        if msg_type == "ping":
            await self._send_heartbeat(ws)
        elif msg_type == "pairing_token":
            logger.info(
                "relay.pairing_token expires_at=%s",
                msg.get("expires_at"),
            )

    async def _handle_control(
        self, payload: bytes, ws: ClientConnection
    ) -> None:
        """Handle a CH_CONTROL envelope payload."""
        try:
            ctrl = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return

        msg_type = ctrl.get("type")

        if msg_type == "ping":
            await self._send_heartbeat(ws)

        elif msg_type == "create_session":
            await self._handle_create_session(ctrl, ws)

        elif msg_type == "stop_session":
            await self._handle_stop_session(ctrl, ws)

        elif msg_type == "proxy_request":
            await self._handle_proxy_request(ctrl, ws)

        elif msg_type == "open_autoscan":
            await self._handle_open_autoscan(ctrl, ws)

        elif msg_type == "close_autoscan":
            self._handle_close_autoscan(ctrl)

    async def _handle_open_autoscan(
        self, ctrl: dict, ws: ClientConnection
    ) -> None:
        """Spawn a per-subscription auto-scan stream task.

        Uses the same ``run_autoscan_stream`` coroutine as the direct-mode
        ``/ws/autoscan`` handler, wrapped in adapter callables that encode
        outbound frames as CH_AUTOSCAN envelopes keyed on
        ``subscription_id`` and dequeue inbound frames from the
        per-subscription inbox.
        """
        subscription_id = ctrl.get("subscription_id")
        if not subscription_id:
            logger.warning("relay.autoscan.open_missing_subscription_id")
            return
        if subscription_id in self._autoscan_streams:
            logger.warning(
                "relay.autoscan.open_duplicate subscription_id=%s",
                subscription_id,
            )
            return

        if len(self._autoscan_streams) >= MAX_AUTOSCAN_STREAMS:
            logger.warning(
                "relay.autoscan.open_capacity_reached active=%d max=%d subscription_id=%s",
                len(self._autoscan_streams),
                MAX_AUTOSCAN_STREAMS,
                subscription_id,
            )
            await self._send_autoscan_close(
                ws, subscription_id, reason="capacity_reached"
            )
            return

        mgr = getattr(self._app_state, "autoscan_manager", None)
        if mgr is None:
            await self._send_autoscan_close(
                ws, subscription_id, reason="autoscan_not_configured"
            )
            return

        inbox: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
        self._autoscan_inboxes[subscription_id] = inbox

        # Pre-compute the 34-byte CH_AUTOSCAN header (channel + sid_len + sid
        # bytes) once per task rather than re-encoding the constant
        # subscription_id on every live event frame. At verbose-scan rates
        # (dozens of log events per second) this is a measurable allocation
        # reduction on the hot send path.
        sid_bytes = subscription_id.encode("utf-8")
        header = bytes([CH_AUTOSCAN, len(sid_bytes)]) + sid_bytes

        async def send_text(text: str) -> None:
            await ws.send(header + text.encode("utf-8"))

        async def receive_text() -> str:
            return await inbox.get()

        task = asyncio.create_task(
            self._run_autoscan_subscription(
                subscription_id, mgr, send_text, receive_text
            ),
            name=f"relay-autoscan-{subscription_id}",
        )
        self._autoscan_streams[subscription_id] = task

    async def _send_autoscan_close(
        self, ws: ClientConnection, subscription_id: str, *, reason: str
    ) -> None:
        """Send a ``close_autoscan`` control frame back to the central server.

        Used when the agent declines to open a stream (cap reached,
        autoscan not configured). The central server's control handler
        closes the matching mobile WebSocket so the client sees a clean
        disconnect rather than a silent hang.
        """
        close_msg = {
            "type": "close_autoscan",
            "subscription_id": subscription_id,
            "reason": reason,
        }
        await ws.send(encode(CH_CONTROL, "", json.dumps(close_msg).encode()))

    async def _run_autoscan_subscription(
        self,
        subscription_id: str,
        mgr: Any,
        send_text: Any,
        receive_text: Any,
    ) -> None:
        """Run a single auto-scan stream until cancelled or the peer goes away."""
        try:
            await run_autoscan_stream(
                mgr,
                send_text,
                receive_text,
                grace_timeout_s=1.0,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "relay.autoscan.stream_error subscription_id=%s: %s",
                subscription_id, exc,
            )
        finally:
            self._autoscan_inboxes.pop(subscription_id, None)
            self._autoscan_streams.pop(subscription_id, None)

    def _handle_close_autoscan(self, ctrl: dict) -> None:
        """Cancel the stream task for a subscription id; best-effort cleanup."""
        subscription_id = ctrl.get("subscription_id")
        if not subscription_id:
            return
        task = self._autoscan_streams.get(subscription_id)
        if task is not None and not task.done():
            task.cancel()

    async def _handle_create_session(
        self, ctrl: dict, ws: ClientConnection
    ) -> None:
        """Handle a create_session control message from the relay."""
        session_id = ctrl.get("session_id") or uuid.uuid4().hex[:12]
        command = ctrl.get("command") or _DEFAULT_SHELL

        config = SessionConfig(
            session_id=session_id,
            command=command,
            cwd=ctrl.get("cwd"),
            env=ctrl.get("env"),
            rows=ctrl.get("rows", 24),
            cols=ctrl.get("cols", 80),
            project_name=ctrl.get("project_name"),
        )
        try:
            session = await self._session_manager.create_session(config)
            # Bridge is auto-created via _watch_session_changes, but ensure
            # it's bridged immediately.
            self._bridge_session(session, ws)
            resp = {
                "type": "session_created",
                "session_id": session.session_id,
                "pid": session.pid,
            }
        except FileNotFoundError as exc:
            # Mirror the HTTP router: interpolate ``exc.filename`` as a plain
            # string rather than ``str(exc)``, which would embed
            # ``repr(filename)`` and produce ``D:\\\\ML\\\\...`` on the wire
            # once the relay frame is JSON-encoded.
            missing = exc.filename or "project path"
            logger.warning(
                "relay.create_session_path_missing session_id=%s path=%s",
                session_id, missing,
            )
            resp = {
                "type": "session_create_failed",
                "session_id": session_id,
                "error": f"Project path not found: {missing}",
            }
        except OSError as exc:
            strerror = exc.strerror or "cannot launch session"
            filename = exc.filename or ""
            error = f"{strerror}: {filename}" if filename else strerror
            logger.warning(
                "relay.create_session_oserror session_id=%s error=%s",
                session_id, error,
            )
            resp = {
                "type": "session_create_failed",
                "session_id": session_id,
                "error": error,
            }
        except Exception as exc:
            logger.warning(
                "relay.create_session_failed session_id=%s: %s",
                session_id,
                exc,
            )
            resp = {
                "type": "session_create_failed",
                "session_id": session_id,
                "error": str(exc),
            }
        await ws.send(encode(CH_CONTROL, "", json.dumps(resp).encode()))

    async def _handle_stop_session(
        self, ctrl: dict, ws: ClientConnection
    ) -> None:
        """Handle a stop_session control message from the relay."""
        session_id = ctrl.get("session_id", "")
        force = ctrl.get("force", False)
        try:
            await self._session_manager.stop_session(session_id, force=force)
            resp = {"type": "session_stopped", "session_id": session_id}
        except KeyError:
            resp = {
                "type": "session_stop_failed",
                "session_id": session_id,
                "error": "Session not found",
            }
        except Exception as exc:
            resp = {
                "type": "session_stop_failed",
                "session_id": session_id,
                "error": str(exc),
            }
        await ws.send(encode(CH_CONTROL, "", json.dumps(resp).encode()))

    async def _handle_proxy_request(
        self, ctrl: dict, ws: ClientConnection
    ) -> None:
        """Handle a proxy_request forwarded from a mobile client via the
        central server.  Dispatches on (method, path) and returns a
        proxy_response control message with the result."""
        request_id = ctrl.get("request_id", "")
        method = ctrl.get("method", "GET").upper()
        path: str = ctrl.get("path", "")
        body: dict | None = ctrl.get("body")
        query: dict | None = ctrl.get("query")

        try:
            result = await self._dispatch_proxy(method, path, body, query, ws)
            resp = {
                "type": "proxy_response",
                "request_id": request_id,
                "status": result.get("status", 200),
                "body": result.get("body"),
            }
        except FileNotFoundError as exc:
            # Same rationale as ``_handle_create_session``: mobile clients
            # proxying ``POST /api/sessions`` through the central server
            # otherwise re-hit the 4-backslash symptom via ``str(exc)``.
            missing = exc.filename or "project path"
            logger.warning(
                "relay.proxy_request_path_missing path=%s missing=%s",
                path, missing,
            )
            resp = {
                "type": "proxy_response",
                "request_id": request_id,
                "status": 400,
                "body": {"detail": f"Project path not found: {missing}"},
            }
        except OSError as exc:
            strerror = exc.strerror or "cannot launch session"
            filename = exc.filename or ""
            detail = f"{strerror}: {filename}" if filename else strerror
            logger.warning(
                "relay.proxy_request_oserror path=%s detail=%s",
                path, detail,
            )
            resp = {
                "type": "proxy_response",
                "request_id": request_id,
                "status": 400,
                "body": {"detail": detail},
            }
        except Exception as exc:
            logger.warning("relay.proxy_request_failed path=%s: %s", path, exc)
            resp = {
                "type": "proxy_response",
                "request_id": request_id,
                "status": 500,
                "body": {"detail": str(exc)},
            }

        await ws.send(encode(CH_CONTROL, "", json.dumps(resp).encode()))

    async def _dispatch_proxy(
        self,
        method: str,
        path: str,
        body: dict | None,
        query: dict | None,
        ws: ClientConnection,
    ) -> dict:
        """Route a proxy request to the appropriate internal handler."""
        import re as _re

        if method == "GET" and path == "/api/sessions":
            sessions = self._session_manager.list_sessions()
            return {
                "status": 200,
                "body": [
                    {
                        "session_id": s.session_id,
                        "state": s.state.value,
                        "created_at": s.created_at.isoformat(),
                        "pid": s.pid,
                        "command": s.command,
                        "cwd": s.cwd,
                    }
                    for s in sessions
                ],
            }

        if method == "POST" and path == "/api/sessions":
            body = body or {}
            session_id = uuid.uuid4().hex[:12]
            config = SessionConfig(
                session_id=session_id,
                command=body.get("command") or _DEFAULT_SHELL,
                cwd=body.get("cwd"),
                env=body.get("env"),
                rows=body.get("rows", 24),
                cols=body.get("cols", 80),
                project_name=body.get("project_name"),
            )
            session = await self._session_manager.create_session(config)
            self._bridge_session(session, ws)
            info = session.to_info()
            return {
                "status": 201,
                "body": {
                    "session_id": info.session_id,
                    "state": info.state.value,
                    "created_at": info.created_at.isoformat(),
                    "pid": info.pid,
                    "command": info.command,
                    "cwd": info.cwd,
                },
            }

        m = _re.match(r"^/api/sessions/([^/]+)$", path)
        if m and method == "DELETE":
            session_id = m.group(1)
            force = (query or {}).get("force", "false").lower() == "true"
            try:
                await self._session_manager.stop_session(
                    session_id, force=force
                )
                return {"status": 204, "body": None}
            except KeyError:
                return {
                    "status": 404,
                    "body": {"detail": "Session not found"},
                }

        m = _re.match(r"^/api/sessions/([^/]+)/scrollback$", path)
        if m and method == "GET":
            session_id = m.group(1)
            lines_param = int((query or {}).get("lines", 1000))
            session = await self._session_manager.get_session(session_id)
            if session is None:
                return {
                    "status": 404,
                    "body": {"detail": "Session not found"},
                }
            scrollback = session.get_scrollback()
            if len(scrollback) > lines_param:
                scrollback = scrollback[-lines_param:]
            return {
                "status": 200,
                "body": {
                    "session_id": session_id,
                    "lines": scrollback,
                    "total": len(scrollback),
                },
            }

        # Projects CRUD — delegate to the shared service layer so direct HTTP
        # and relay-forwarded requests produce identical semantics, including
        # audit events and bus broadcasts.
        projects_resp = await self._dispatch_projects(method, path, body)
        if projects_resp is not None:
            return projects_resp

        if method == "GET" and path == "/api/autoscan/list":
            mgr = getattr(self._app_state, "autoscan_manager", None)
            if mgr is None:
                return {
                    "status": 200,
                    "body": {
                        "scans": [],
                        "capacity": 0,
                        "max_scans": 0,
                        "available_projects": [],
                    },
                }
            return {"status": 200, "body": mgr.list_response()}

        if method == "POST" and path == "/api/autoscan/start":
            mgr = getattr(self._app_state, "autoscan_manager", None)
            if mgr is None:
                return {
                    "status": 503,
                    "body": {"detail": "Auto-scan not available"},
                }
            body = body or {}
            project_name = body.get("project_name", "")
            claude_options = body.get("claude_options")
            try:
                instance = await mgr.start(
                    project_name, client_options=claude_options
                )
                return {"status": 200, "body": instance.to_dto()}
            except ValueError as exc:
                return {"status": 400, "body": {"detail": str(exc)}}
            except Exception as exc:
                return {"status": 409, "body": {"detail": str(exc)}}

        if method == "POST" and path == "/api/autoscan/stop":
            mgr = getattr(self._app_state, "autoscan_manager", None)
            if mgr is None:
                return {
                    "status": 503,
                    "body": {"detail": "Auto-scan not available"},
                }
            body = body or {}
            project_name = body.get("project_name", "")
            try:
                await mgr.stop(project_name)
                return {"status": 200, "body": {"status": "stopped"}}
            except KeyError:
                return {
                    "status": 404,
                    "body": {"detail": "No active scan for project"},
                }

        if method == "POST" and path == "/api/autoscan/run-now":
            mgr = getattr(self._app_state, "autoscan_manager", None)
            if mgr is None:
                return {
                    "status": 503,
                    "body": {"detail": "Auto-scan not available"},
                }
            body = body or {}
            project_name = body.get("project_name", "")
            instance = mgr.get(project_name)
            if instance is None:
                return {
                    "status": 404,
                    "body": {"detail": "No active scan for project"},
                }
            instance.request_immediate_scan()
            dto = instance.to_dto()
            return {
                "status": 200,
                "body": {
                    "ok": True,
                    "project_name": project_name,
                    "next_scan_at": dto.get("next_scan_at"),
                },
            }

        return {
            "status": 404,
            "body": {"detail": f"Unknown proxy route: {method} {path}"},
        }

    async def _dispatch_projects(
        self,
        method: str,
        path: str,
        body: dict | None,
    ) -> dict | None:
        """Handle ``/api/projects`` requests through the shared service layer.

        Returns a proxy-response dict for a matching route, or ``None`` to
        signal that the caller should fall through to the next handler.
        """
        import re as _re

        store = getattr(self._app_state, "projects_store", None)
        if store is None:
            if path == "/api/projects" or path.startswith("/api/projects/"):
                return {
                    "status": 500,
                    "body": {"detail": "projects store not configured"},
                }
            return None

        autoscan_manager = getattr(self._app_state, "autoscan_manager", None)
        event_bus = getattr(self._app_state, "event_bus", None)
        # The relay channel has already authenticated the agent token but we
        # don't thread a per-request username through the proxy today;
        # audit events therefore carry ``actor=None`` from this code path.
        actor: str | None = None

        if path == "/api/projects":
            if method == "GET":
                return {"status": 200, "body": list_projects(store)}
            if method == "POST":
                try:
                    model = ProjectWriteModel.model_validate(body or {})
                except ValidationError as exc:
                    return {
                        "status": 422,
                        "body": {"detail": _scrub_validation_errors(exc)},
                    }
                try:
                    created = create_project(
                        store, model, event_bus=event_bus, actor=actor,
                    )
                    return {"status": 201, "body": created}
                except DuplicateProjectError as exc:
                    return {"status": 409, "body": {"detail": str(exc)}}

        if path == "/api/projects/order" and method == "PATCH":
            try:
                model = ReorderRequest.model_validate(body or {})
            except ValidationError as exc:
                return {
                    "status": 422,
                    "body": {"detail": _scrub_validation_errors(exc)},
                }
            try:
                result = reorder_projects(
                    store, model, event_bus=event_bus, actor=actor,
                )
                return {"status": 200, "body": result}
            except ReorderSetMismatchError as exc:
                return {"status": 400, "body": {"detail": str(exc)}}

        m = _re.match(r"^/api/projects/([^/]+)$", path)
        if m:
            name = m.group(1)
            if method == "PUT":
                try:
                    patch = ProjectUpdateModel.model_validate(body or {})
                except ValidationError as exc:
                    return {
                        "status": 422,
                        "body": {"detail": _scrub_validation_errors(exc)},
                    }
                try:
                    updated = update_project(
                        store, autoscan_manager, name, patch,
                        event_bus=event_bus, actor=actor,
                    )
                    return {"status": 200, "body": updated}
                except KeyError as exc:
                    return {
                        "status": 404,
                        "body": {"detail": str(exc).strip("'")},
                    }
                except (ProjectInUseError, ValueError) as exc:
                    return {"status": 409, "body": {"detail": str(exc)}}
            if method == "DELETE":
                try:
                    delete_project(
                        store, autoscan_manager, name,
                        event_bus=event_bus, actor=actor,
                    )
                    return {"status": 204, "body": None}
                except KeyError as exc:
                    return {
                        "status": 404,
                        "body": {"detail": str(exc).strip("'")},
                    }
                except ProjectInUseError as exc:
                    return {"status": 409, "body": {"detail": str(exc)}}

        return None

    async def _handle_session_input(
        self,
        session_id: str,
        payload: bytes,
        ws: ClientConnection,
    ) -> None:
        """Route incoming PTY frames from relay to a local session."""
        session = await self._session_manager.get_session(session_id)

        if session is None:
            # Auto-create session with default shell when relay traffic arrives
            # for an unknown session_id.
            session = await self._auto_create_session(session_id, ws)
            if session is None:
                return

        # Send state + replay + ring on first input so the client gets initial state.
        if session_id not in self._handshake_sent:
            await self._send_session_handshake(session_id, session, ws)
            self._handshake_sent.add(session_id)

        # Parse PTY frame: first byte = type, rest = data
        if not payload:
            return
        frame_type = payload[:1]
        frame_data = payload[1:]

        if frame_type == FRAME_INPUT:
            if frame_data:
                await session.write(frame_data)
        elif frame_type == FRAME_EXECUTE:
            if frame_data:
                await session.write(b"\x03")
                await asyncio.sleep(0.1)
                await session.write(frame_data)
        elif frame_type == FRAME_RESIZE:
            if len(frame_data) >= 4:
                rows, cols = struct.unpack(">HH", frame_data[:4])
                await session.resize(rows, cols)

    async def _auto_create_session(
        self, session_id: str, ws: ClientConnection
    ) -> Any:
        """Auto-create a session when relay traffic arrives for unknown ID."""
        config = SessionConfig(
            session_id=session_id,
            command=_DEFAULT_SHELL,
            rows=24,
            cols=80,
        )
        try:
            session = await self._session_manager.create_session(config)
            self._bridge_session(session, ws)
            logger.info("relay.session_auto_created session_id=%s", session_id)
            return session
        except Exception as exc:
            logger.warning(
                "relay.auto_create_failed session_id=%s: %s",
                session_id,
                exc,
            )
            return None

    async def _send_session_handshake(
        self,
        session_id: str,
        session: Any,
        ws: ClientConnection,
    ) -> None:
        """Send state + replay marker + ring buffer for a session."""
        try:
            status = _STATE_MAP.get(session.state, "running")
            state = _state_frame(status, session.pid)
            await ws.send(encode(CH_SESSION_IO, session_id, state))

            # Replay marker signals end-of-history to the client.
            await ws.send(encode(CH_SESSION_IO, session_id, FRAME_REPLAY))

            ring = _DA_REPLY_STRIP.sub(b"", session.get_ring())
            await ws.send(
                encode(CH_SESSION_IO, session_id, FRAME_OUTPUT + ring)
            )
        except Exception as exc:
            logger.debug(
                "relay.handshake_send_failed session_id=%s: %s",
                session_id,
                exc,
            )

    async def _bridge_existing_sessions(self, ws: ClientConnection) -> None:
        """Bridge all currently-active sessions to the relay tunnel."""
        for info in self._session_manager.list_sessions():
            session = await self._session_manager.get_session(info.session_id)
            if session:
                self._bridge_session(session, ws)

    def _bridge_session(self, session: Any, ws: ClientConnection) -> None:
        """Subscribe to a session's fanout and forward output as envelopes."""
        sid = session.session_id
        if sid in self._bridges:
            return

        from claude_mobile.agent.ws.session_io import ensure_fanout

        fanout = ensure_fanout(self._app_state, session)
        consumer_queue = fanout.subscribe()

        task = asyncio.create_task(
            self._session_output_pump(sid, consumer_queue, fanout, ws),
            name=f"relay-bridge-{sid}",
        )
        self._bridges[sid] = task

    async def _session_output_pump(
        self,
        session_id: str,
        queue: asyncio.Queue,
        fanout: Any,
        ws: ClientConnection,
    ) -> None:
        """Pump session events and send as CH_SESSION_IO envelopes."""
        try:
            while True:
                event: SessionEvent = await queue.get()
                if self._ws is None:
                    break
                try:
                    if event.event_type == "output" and event.data:
                        envelope = encode(
                            CH_SESSION_IO,
                            session_id,
                            FRAME_OUTPUT + event.data,
                        )
                        await ws.send(envelope)
                    elif event.event_type in _EVENT_TO_STATUS:
                        session = await self._session_manager.get_session(
                            session_id
                        )
                        pid = session.pid if session else None
                        state = _state_frame(
                            _EVENT_TO_STATUS[event.event_type], pid
                        )
                        envelope = encode(CH_SESSION_IO, session_id, state)
                        await ws.send(envelope)
                except Exception as exc:
                    logger.debug(
                        "relay.bridge_send_error session_id=%s: %s",
                        session_id,
                        exc,
                    )
                    break
        except asyncio.CancelledError:
            pass
        finally:
            fanout.unsubscribe(queue)
            self._bridges.pop(session_id, None)

    async def _watch_session_changes(self, ws: ClientConnection) -> None:
        """Watch EventBus for new/removed sessions and auto-bridge."""
        q = self._event_bus.subscribe()
        try:
            while True:
                event = await q.get()
                if event.type != "sessions.changed":
                    continue
                sessions = event.payload.get("sessions", [])
                current_ids = {s["session_id"] for s in sessions}

                for s in sessions:
                    sid = s["session_id"]
                    if sid not in self._bridges:
                        session = await self._session_manager.get_session(sid)
                        if session:
                            self._bridge_session(session, ws)

                for sid in list(self._bridges):
                    if sid not in current_ids:
                        task = self._bridges.pop(sid, None)
                        if task and not task.done():
                            task.cancel()
                        self._handshake_sent.discard(sid)
        except asyncio.CancelledError:
            pass
        finally:
            self._event_bus.unsubscribe(q)

    async def _forward_session_list(self, ws: ClientConnection) -> None:
        """Forward session-list updates as CH_SESSIONS_LIST envelopes."""
        sessions = self._session_manager.list_sessions()
        session_dicts = [
            {
                "session_id": s.session_id,
                "state": s.state.value,
                "created_at": s.created_at.isoformat(),
                "pid": s.pid,
                "command": s.command,
                "cwd": s.cwd,
            }
            for s in sessions
        ]
        initial = orjson.dumps(session_dicts)
        await ws.send(encode(CH_SESSIONS_LIST, "", initial))

        q = self._broadcaster.subscribe()
        try:
            while True:
                msg = await q.get()  # str, already JSON-serialized
                await ws.send(
                    encode(CH_SESSIONS_LIST, "", msg.encode("utf-8"))
                )
        except asyncio.CancelledError:
            pass
        finally:
            self._broadcaster.unsubscribe(q)

    async def _forward_events(self, ws: ClientConnection) -> None:
        """Forward EventBus events as CH_EVENTS envelopes."""
        q = self._event_bus.subscribe()
        try:
            while True:
                event = await q.get()
                payload = orjson.dumps(
                    {"type": event.type, "payload": event.payload}
                )
                await ws.send(encode(CH_EVENTS, "", payload))
        except asyncio.CancelledError:
            pass
        finally:
            self._event_bus.unsubscribe(q)

    async def _heartbeat_loop(self, ws: ClientConnection) -> None:
        """Send periodic heartbeats via CH_CONTROL."""
        try:
            while True:
                await asyncio.sleep(self._config.heartbeat_interval)
                await self._send_heartbeat(ws)
        except asyncio.CancelledError:
            pass

    async def _send_heartbeat(self, ws: ClientConnection) -> None:
        """Send a single heartbeat control message."""
        hb = encode(
            CH_CONTROL, "", json.dumps({"type": "heartbeat"}).encode()
        )
        await ws.send(hb)

    def _cancel_all_subtasks(self) -> None:
        """Cancel all per-connection tasks and bridge pumps."""
        for t in [
            self._heartbeat_task,
            self._event_fwd_task,
            self._sesslist_fwd_task,
            self._session_watch_task,
        ]:
            if t and not t.done():
                t.cancel()
        for task in list(self._bridges.values()):
            if not task.done():
                task.cancel()
        self._bridges.clear()
        self._handshake_sent.clear()
        for task in list(self._autoscan_streams.values()):
            if not task.done():
                task.cancel()
        self._autoscan_streams.clear()
        self._autoscan_inboxes.clear()
