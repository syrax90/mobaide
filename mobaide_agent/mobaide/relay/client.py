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
import os
import random
import re
import ssl
import struct
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import certifi
import orjson
import websockets
from websockets.asyncio.client import ClientConnection

from mobaide.core.cli_launcher import (
    await_model_cache_warmup,
    build_cli_capabilities,
    CliAuthRequiredError,
    CliBinaryNotFoundError,
    prepend_path_dir,
)
from mobaide.core.codex_auth import codex_login_with_api_key
from mobaide.core.models import (
    CustomCommandUnresolvedError,
    SessionConfig,
    SessionEvent,
    SessionState,
    resolve_spawn_args,
    session_to_wire_dict,
)
from mobaide.core.projects_store import safe_path_exists
from mobaide.core.shell import resolve_default_shell

from pydantic import ValidationError

from mobaide.agent.projects.models import (
    ProjectUpdateModel,
    ProjectWriteModel,
    ReorderRequest,
)
from mobaide.agent.projects.service import (
    DuplicateProjectError,
    ProjectInUseError,
    ReorderSetMismatchError,
    create_project,
    delete_project,
    list_projects,
    reorder_projects,
    update_project,
)

from mobaide.agent.git.changes_conflicts import (
    get_session_conflict,
    resolve_session_conflict,
    restore_session_conflict,
    take_session_conflict,
)
from mobaide.agent.fs.models import FS_CAPABILITIES
from mobaide.agent.fs.service import FsListError, list_dirs
from mobaide.agent.git.changes_jobs import job_registry
from mobaide.agent.git.changes_models import (
    GIT_CAPABILITIES,
    GitCommitRequest,
    GitConflictResolveRequest,
    GitConflictRestoreRequest,
    GitConflictTakeRequest,
    GitDiscardRequest,
    GitPathsRequest,
    GitPullRequest,
    GitPushRequest,
    GitRebaseRequest,
)
from mobaide.agent.git.changes_mutations import (
    commit_session,
    discard_session_paths,
    get_session_commit_context,
    stage_session_paths,
    unstage_session_paths,
)
from mobaide.agent.git.changes_rebase import (
    abort_session_rebase,
    continue_session_rebase,
    skip_session_rebase,
    start_session_rebase,
)
from mobaide.agent.git.changes_service import (
    get_session_git_diff,
    get_session_git_status,
)
from mobaide.agent.git.changes_service import (
    http_error_for as git_changes_http_error_for,
)
from mobaide.agent.git.models import (
    BusyByResponse,
    GitBranchCreateRequest,
    GitCheckoutRequest,
    GitDeleteRequest,
    GitItemsQuery,
    GitItemsResponse,
)
from mobaide.agent.git.service import (
    GitItemBusyError,
    checkout_ref,
    create_branch,
    delete_git_items,
    list_git_items,
)
from mobaide.linear.client import LinearApiClient
from mobaide.linear.rate_limiter import LinearRateLimiter

from mobaide.agent.autoscan.models import StartScanRequest
from mobaide.agent.autoscan.stream import run_autoscan_stream
from mobaide.agent.codex.models import CodexLoginRequest
from mobaide.agent.sessions.models import (
    AntigravityOptionsModel,
    ClaudeOptionsModel,
    CodexOptionsModel,
    CreateSessionRequest,
)
from mobaide.agent.ws.session_list import (
    _serialize_sessions as _list_session_dicts,
)
from mobaide.linear.scanner import (
    AutoScanAlreadyRunning,
    AutoScanCapacityReached,
)
from mobaide.linear.worktree import WorktreeCreationError

from mobaide import __version__
from .config import RelayConfig
from .envelope import (
    CH_AUTOSCAN,
    CH_CONTROL,
    CH_EVENTS,
    CH_SESSION_IO,
    CH_SESSIONS_LIST,
    MIN_CLIENT_PROTOCOL_VERSION,
    PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    decode,
    encode,
)

logger = logging.getLogger(__name__)

# Env opt-in permitting a cleartext ws:// relay (loopback / local E2E only).
# Mirrors the cleartext-refusal precedent in ``install_paths.py`` /
# ``pairing_monitor.py``. ``MOBAIDE_ENV=test`` implies this opt-in.
ALLOW_INSECURE_WS_ENV = "MOBAIDE_AGENT_ALLOW_INSECURE_WS"


def _build_relay_ssl_context(
    server_url: str, *, allow_insecure_ws: bool,
) -> ssl.SSLContext | None:
    """Return the per-connection TLS context for the relay tunnel.

    The agent owns its TLS trust store instead of depending on the host
    interpreter's default CA bundle: a python.org macOS build whose bundled
    *Install Certificates.command* never ran has an empty store and would fail
    to verify a valid public cert. So ``wss://`` always gets an explicit
    certifi-backed verifying context.

    ``ws://`` (cleartext) returns ``None`` and is **refused** unless
    ``allow_insecure_ws`` is set (``MOBAIDE_AGENT_ALLOW_INSECURE_WS=1`` or
    ``MOBAIDE_ENV=test``) — a misconfigured production relay URL fails loudly at
    boot instead of silently downgrading to plaintext. A verifying context is
    never returned for a non-TLS URL (``websockets`` rejects that), and
    ``CERT_NONE``/insecure never reaches ``wss://``.

    The scheme is matched case-insensitively: ``websockets`` normalizes scheme
    case (``WS://`` connects plaintext, ``WSS://`` secure), so a case-sensitive
    check would let an uppercase scheme slip past this gate — connecting
    ``WS://`` in plaintext without the opt-in, or routing ``WSS://`` through
    ``websockets``' default system trust store instead of certifi.
    """
    scheme = urlsplit(server_url).scheme.lower()
    if scheme == "wss":
        return ssl.create_default_context(cafile=certifi.where())
    if scheme == "ws":
        if not allow_insecure_ws:
            raise RuntimeError(
                f"Relay server_url uses cleartext ws:// ({server_url!r}); refused "
                f"by default. Set {ALLOW_INSECURE_WS_ENV}=1 or run with "
                f"MOBAIDE_ENV=test to allow a plaintext relay (loopback / local "
                f"testing only)."
            )
        return None
    # Any other scheme: no TLS context — websockets surfaces the error itself.
    return None

# PTY frame type bytes — must stay in sync with agent/ws/session_io.py.
FRAME_OUTPUT = b"o"
FRAME_INPUT = b"i"
FRAME_RESIZE = b"r"
FRAME_EXECUTE = b"x"
FRAME_SUBMIT = b"m"
FRAME_STATE = b"s"
FRAME_REPLAY = b"b"
FRAME_REPLAY_END = b"e"

# Bound on each session's inbound PTY-frame queue. Input is human-rate;
# the bound only guards memory if a PTY write wedges. Overflow drops the
# frame with a warning (mirrors the autoscan inbox policy).
INPUT_QUEUE_MAXSIZE = 256

_EVENT_TO_STATUS = {
    "started": "running",
    "stopped": "stopped",
    "error": "crashed",
}

# Fields whose raw input must never be echoed back in a validation error —
# mirrors the handler registered in ``agent/app.py`` so direct HTTP and
# relay-forwarded 422 responses have identical redaction guarantees.
_SECRET_VALIDATION_FIELDS = frozenset({"linear_api_key", "api_key"})


def _input_carries_secret(value: object, secret_fields: frozenset[str]) -> bool:
    """True if a validation error's ``input`` payload embeds a secret field.

    Mirrors the agent ``app.py`` helper: the ``loc`` check misses body-level
    errors (a malformed body is reported at ``loc=("body",)`` with the whole raw
    body as ``input``), so walk the parsed payload to catch a secret nested under
    a key ``loc`` never names.
    """
    if isinstance(value, dict):
        return any(
            key in secret_fields or _input_carries_secret(val, secret_fields)
            for key, val in value.items()
        )
    if isinstance(value, list):
        return any(_input_carries_secret(item, secret_fields) for item in value)
    return False


def _scrub_validation_errors(exc: "ValidationError") -> list[dict[str, object]]:
    """Return Pydantic error records with raw inputs stripped from secret fields."""
    scrubbed: list[dict[str, object]] = []
    for err in exc.errors():
        loc = err.get("loc", ())
        cleaned: dict[str, object] = dict(err)
        if any(part in _SECRET_VALIDATION_FIELDS for part in loc) or _input_carries_secret(
            err.get("input"), _SECRET_VALIDATION_FIELDS
        ):
            cleaned.pop("input", None)
        ctx = cleaned.get("ctx")
        if isinstance(ctx, dict):
            # A model_validator's `raise ValueError(...)` surfaces here as the
            # raw exception instance under ctx["error"], which json.dumps on
            # the proxy-response path cannot serialize — stringify every value.
            cleaned["ctx"] = {k: str(v) for k, v in ctx.items()}
        scrubbed.append(cleaned)
    return scrubbed

_STATE_MAP = {
    SessionState.STARTING: "starting",
    SessionState.RUNNING: "running",
    SessionState.STOPPING: "stopping",
    SessionState.STOPPED: "stopped",
    SessionState.CRASHED: "crashed",
}

MAX_AUTOSCAN_STREAMS = 10

UNKNOWN_SESSION_NOTIFY_CAP = 256


class ProtocolIncompatibleError(ConnectionError):
    """Raised when the relay rejects the agent's ``protocol_version``.

    Carries the server-provided ``manifest_url`` hint (when present in
    the ``incompatible`` payload) so the connect loop's recovery
    callback can prefer it over the agent-side derived URL. The hint
    is a relative path (e.g. ``/api/v1/agent/manifest.json``) — callers
    must resolve it against the relay's HTTPS origin.
    """

    def __init__(self, reason: str, *, manifest_url_hint: str | None = None) -> None:
        super().__init__(f"Relay rejected protocol version: {reason}")
        self.reason = reason
        self.manifest_url_hint = manifest_url_hint


class AgentUninstallRequested(ConnectionError):
    """Raised when the relay answers the auth handshake with
    ``agent_uninstall``: this agent was removed from its account and asked
    to uninstall itself while it was offline."""


# Control-frame type for the relay's uninstall order; the relay sends it on
# the live tunnel and as its reply to a removed agent's auth.
AGENT_UNINSTALL_TYPE = "agent_uninstall"


def _validate_adhoc_cwd_or_error(cwd: str) -> str | None:
    """Return a human-readable error string if ``cwd`` is unfit for an
    ad-hoc launch, else ``None``.

    Mirrors :func:`mobaide.agent.sessions.router._validate_adhoc_cwd` —
    the duplication is intentional (same rationale as the
    ``FileNotFoundError`` handling at lines 752–766 below): the relay
    must remain independently importable from the FastAPI app.
    """
    if not cwd:
        return "Custom path cannot be empty"
    p = Path(cwd)
    if not p.is_absolute():
        return f"Custom path must be absolute: {cwd}"
    if not p.exists():
        return f"Custom path does not exist: {cwd}"
    if not p.is_dir():
        return f"Custom path is not a directory: {cwd}"
    return None


def _state_frame(status: str, pid: int | None = None) -> bytes:
    payload: dict[str, object] = {"status": status}
    if pid is not None:
        payload["pid"] = pid
    return FRAME_STATE + json.dumps(payload).encode("utf-8")


def _attention_user_event(payload: object) -> dict[str, object] | None:
    """Build the CH_CONTROL ``user_event`` message for a ``session.attention``
    bus event, or ``None`` when the bus payload is malformed.

    CH_EVENTS stays server-opaque; this slim duplicate on the control channel
    is what lets the central server broadcast the attention as a user-scoped
    event and fall back to an APNs push when no user socket is live. The
    server stamps ``agent_id`` itself, so it is deliberately absent here.
    """
    if not isinstance(payload, dict):
        return None
    session = payload.get("session")
    if not isinstance(session, dict):
        return None
    session_id = session.get("session_id")
    reason = session.get("attention")
    if not isinstance(session_id, str) or not session_id:
        return None
    if not isinstance(reason, str) or not reason:
        return None
    slim: dict[str, object] = {"session_id": session_id, "reason": reason}
    for key in ("title", "project_name", "exit_reason"):
        value = session.get(key)
        if value is not None:
            slim[key] = value
    return {
        "type": "user_event",
        "kind": "session.attention",
        "payload": slim,
    }


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
        allow_insecure_ws: bool = False,
    ) -> None:
        self._config = config
        self._session_manager = session_manager
        self._event_bus = event_bus
        self._broadcaster = broadcaster
        self._app_state = app_state
        # Per-config TLS context, built once here instead of at module import.
        # wss:// → certifi-verifying context; ws:// → None (refused unless
        # allow_insecure_ws). Raises now so a bad relay URL fails at boot.
        self._ssl_context = _build_relay_ssl_context(
            config.server_url, allow_insecure_ws=allow_insecure_ws,
        )

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

        # Inbound PTY-frame dispatch: session_id -> (queue, consumer task).
        # CH_SESSION_IO frames must not be awaited inline in the tunnel
        # reader — write_submit/write_execute sleep 0.2-0.3 s, and an inline
        # await would head-of-line block every other session and all
        # CH_CONTROL traffic. One FIFO queue + one consumer per session
        # keeps the per-session ordering invariant (raw input can never
        # overtake a queued submit) while the tunnel loop stays non-blocking.
        self._input_queues: dict[str, asyncio.Queue[bytes]] = {}
        self._input_pumps: dict[str, asyncio.Task] = {}

        # Per-mobile auto-scan subscriptions: subscription_id -> stream task.
        # Each mobile device opens its own /relay/ws/autoscan/{agent_id}
        # connection, which the central server tags with a fresh
        # subscription_id and forwards as CH_AUTOSCAN envelopes; we run
        # one run_autoscan_stream task per subscription, owning its own
        # inbox queue for mobile-to-agent text frames.
        self._autoscan_streams: dict[str, asyncio.Task] = {}
        self._autoscan_inboxes: dict[str, asyncio.Queue[str]] = {}

        # Latest manifest pointer received from central_server (auth_ok or a
        # mid-connection ``agent_manifest_update`` control frame). Consumed by
        # the staged-binary auto-updater. Never raises on absence.
        self._latest_manifest: dict[str, object] | None = None
        self._on_manifest_update: Any | None = None
        # Callback fired when the relay rejects on protocol-incompatible
        # (close code 4002). Receives the optional ``manifest_url_hint``
        # the server may have placed in the ``incompatible`` payload.
        # Wired by the agent's lifespan to ``run_https_update_check``
        # against the env-derived manifest URL; the hint, when present,
        # takes priority. Exceptions are logged and never propagated
        # back into the reconnect loop.
        self._on_protocol_incompatible: Any | None = None
        # Fired once when the relay orders this agent to uninstall itself.
        # Wiring it is also what advertises the ``uninstall`` capability.
        self._on_uninstall_requested: Any | None = None
        self._uninstall_task: asyncio.Task | None = None
        self._unknown_control_type_total = 0
        self._unknown_session_input_total = 0
        self._unknown_session_notified: set[str] = set()

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
        """Reconnect loop with stability-gated backoff and decorrelated jitter.

        The backoff state resets to ``reconnect_base_delay`` only after a
        connection survives ``reconnect_stability_threshold`` seconds —
        regardless of HOW it ends. A connection that dies young keeps its
        escalated delay even when it closed cleanly (an instant clean close
        is still a flap). Each sleep is drawn from ``uniform(base, delay*3)``
        capped at ``reconnect_max_delay`` (AWS decorrelated jitter), so when
        a relay restart kills every stable connection at once the herd's
        reconnects spread over ``[base, 3*base]`` instead of landing in the
        same instant.

        On :class:`ProtocolIncompatibleError` the loop fires
        ``self._on_protocol_incompatible`` (if wired) so the HTTPS
        bootstrap can stage an updated release, then pins the next
        delay to exactly ``reconnect_max_delay`` — deliberately skipping
        jitter; retry spread buys nothing when the agent is provably
        wire-incompatible. Hammering the relay faster only generates log
        noise and never succeeds; the recovery path is an HTTPS-driven
        binary swap, not a relay reconnect.
        """
        delay = self._config.reconnect_base_delay
        while not self._stopping:
            started = time.monotonic()
            proto_incompatible = False
            try:
                await self._run_connection()
            except asyncio.CancelledError:
                raise
            except ProtocolIncompatibleError as exc:
                logger.warning("relay.connection_failed: %s", exc)
                if self._on_protocol_incompatible is not None:
                    try:
                        await self._on_protocol_incompatible(exc.manifest_url_hint)
                    except Exception as cb_exc:
                        logger.warning(
                            "relay.protocol_incompatible_callback_failed: %s",
                            cb_exc,
                        )
                proto_incompatible = True
            except AgentUninstallRequested:
                self._request_uninstall()
            except Exception as exc:
                logger.warning("relay.connection_failed: %s", exc)

            if self._stopping:
                break

            uptime = time.monotonic() - started
            if proto_incompatible:
                # If the callback staged a release the updater has
                # already exited; otherwise back off to the max delay
                # to stop the 60s-warning churn the user sees in logs.
                delay = self._config.reconnect_max_delay
            else:
                if uptime >= self._config.reconnect_stability_threshold:
                    delay = self._config.reconnect_base_delay
                delay = min(
                    self._config.reconnect_max_delay,
                    random.uniform(self._config.reconnect_base_delay, delay * 3),
                )

            logger.info(
                "relay.reconnecting delay=%.1fs uptime=%.1fs", delay, uptime
            )
            await asyncio.sleep(delay)

    async def _run_connection(self) -> None:
        """Single connection attempt: connect, authenticate, run tunnel."""
        tunnel_url = self._config.server_url.rstrip("/") + "/ws/agent-tunnel"
        # Per-config context built in __init__: an explicit certifi-verifying
        # context for wss://, or None for an opted-in plaintext ws:// (websockets
        # raises if a context is passed to a non-TLS URL).
        ssl_ctx = self._ssl_context
        async with websockets.connect(
            tunnel_url,
            ssl=ssl_ctx,
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

    def set_manifest_update_callback(self, callback: Any) -> None:
        """Register an async callback fired with (manifest_body, signature_b64,
        latest_version) whenever the central server publishes a new agent
        manifest, either at handshake time or via a mid-connection control
        frame. The body arrives inline (proto-v2 inlined the signed manifest
        on the relay channel — no HTTP fetch on this path). The callback is
        best-effort — exceptions are logged and never propagated to the relay
        reader loop."""
        self._on_manifest_update = callback

    def set_protocol_incompatible_callback(self, callback: Any) -> None:
        """Register an async callback fired when the relay rejects with the
        ``incompatible`` frame (close code 4002).

        Receives one argument — the server-provided ``manifest_url`` hint
        from the rejection payload (or ``None`` if absent). The agent's
        lifespan wires this to the HTTPS-bootstrap update path so a
        protocol-version mismatch self-heals into a binary swap rather
        than looping at the relay layer forever.
        """
        self._on_protocol_incompatible = callback

    def set_uninstall_callback(self, callback: Any) -> None:
        """Register the async no-argument callback run when the relay orders
        this agent to uninstall itself, and advertise the ``uninstall``
        capability in the auth handshake from then on."""
        self._on_uninstall_requested = callback

    def _request_uninstall(self) -> None:
        """Stop reconnecting for good and run the uninstall callback once.

        The callback runs as its own task so the tunnel teardown that
        follows the order cannot cancel it half-way.
        """
        self._stopping = True
        if self._uninstall_task is not None:
            return
        logger.warning("relay.uninstall_requested agent_id=%s", self._config.agent_id)
        if self._on_uninstall_requested is None:
            return

        async def _run() -> None:
            try:
                await self._on_uninstall_requested()
            except Exception as exc:
                logger.warning("relay.uninstall_callback_failed: %s", exc)

        self._uninstall_task = asyncio.create_task(_run(), name="agent-uninstall")

    @property
    def latest_manifest(self) -> dict[str, object] | None:
        return self._latest_manifest

    async def _authenticate(self, ws: ClientConnection) -> None:
        """Send auth message and wait for auth_ok."""
        auth_msg = json.dumps({
            "type": "auth",
            "agent_id": self._config.agent_id,
            "token": self._config.agent_token,
            "protocol_version": PROTOCOL_VERSION,
            "agent_version": __version__,
            "capabilities": (
                {"uninstall": True} if self._on_uninstall_requested else {}
            ),
        })
        await ws.send(auth_msg)
        raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        resp = json.loads(raw)
        msg_type = resp.get("type")
        if msg_type == "incompatible":
            reason = resp.get("reason", "protocol incompatible")
            manifest_url_hint = resp.get("manifest_url")
            if not isinstance(manifest_url_hint, str) or not manifest_url_hint:
                manifest_url_hint = None
            raise ProtocolIncompatibleError(
                reason, manifest_url_hint=manifest_url_hint,
            )
        if msg_type == AGENT_UNINSTALL_TYPE:
            raise AgentUninstallRequested("Relay removed this agent")
        if msg_type != "auth_ok":
            raise ConnectionError(f"Relay auth failed: {resp}")
        self._latest_manifest = {
            "version": resp.get("latest_agent_version"),
            "manifest_body": resp.get("manifest_body"),
            "manifest_signature": resp.get("manifest_signature"),
        }
        await self._maybe_dispatch_manifest_update(self._latest_manifest)

    async def _maybe_dispatch_manifest_update(
        self, manifest: dict[str, object]
    ) -> None:
        if self._on_manifest_update is None:
            return
        if not manifest.get("version") or not manifest.get("manifest_body"):
            return
        try:
            await self._on_manifest_update(
                manifest.get("manifest_body"),
                manifest.get("manifest_signature"),
                manifest.get("version"),
            )
        except Exception as exc:
            logger.warning(
                "relay.manifest_update_callback_failed: %s", exc,
            )

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
            self._enqueue_session_input(session_id, payload, ws)
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

        elif msg_type == "mobile_session_attach":
            await self._handle_mobile_attach(ctrl, ws)

        elif msg_type == "mobile_session_detach":
            self._handle_mobile_detach(ctrl)

        elif msg_type == "request_events_snapshot":
            await self._handle_request_events_snapshot(ws)

        elif msg_type == "agent_manifest_update":
            manifest = {
                "version": ctrl.get("version"),
                "manifest_body": ctrl.get("manifest_body"),
                "manifest_signature": ctrl.get("manifest_signature"),
            }
            self._latest_manifest = manifest
            await self._maybe_dispatch_manifest_update(manifest)

        elif msg_type == AGENT_UNINSTALL_TYPE:
            self._request_uninstall()

        else:
            self._unknown_control_type_total += 1
            logger.warning(
                "relay.unknown_control_type type=%s total=%d",
                msg_type, self._unknown_control_type_total,
            )

    async def _handle_mobile_attach(
        self, ctrl: dict, ws: ClientConnection
    ) -> None:
        """Re-send the PTY handshake because a mobile just attached to a session.

        Emitted by the central server every time a mobile opens
        ``/relay/ws/session/{agent_id}/{session_id}`` and on agent tunnel
        reconnect for every already-attached mobile. Replaces the old lazy
        "first-input triggers handshake" path, which left the mobile's
        keyboard grayed out on exit-and-reopen because the in-memory
        idempotency cache kept the state frame from ever being re-sent.

        If the session is unknown (agent restarted, session crashed and was
        cleaned up, or mobile is resuming a stale id from preferences), we
        emit a synthetic ``stopped`` state frame back to the mobile so the
        UI leaves the "Connecting" banner and renders a stopped session —
        rather than hang forever waiting for a state frame that would never
        come under the old silent-noop path.
        """
        session_id = ctrl.get("session_id", "")
        if not session_id:
            return
        session = await self._session_manager.get_session(session_id)
        if session is None:
            logger.debug(
                "relay.mobile_attach.unknown_session session_id=%s",
                session_id,
            )
            await self._send_stopped_frame(session_id, ws, "mobile_attach")
            return
        await self._send_session_handshake(session_id, session, ws)

    async def _handle_request_events_snapshot(
        self, ws: ClientConnection
    ) -> None:
        """Re-publish the current sessions / projects snapshots on event_bus.

        Central server emits this control message whenever a mobile opens
        ``/relay/ws/events/{agent_id}`` or ``/relay/ws/sessions/{agent_id}``.
        ``_forward_events`` is already subscribed to ``event_bus`` and will
        turn the published envelopes into CH_EVENTS frames; the central
        server then routes those frames to the newly-connected mobile.

        Sending a direct CH_SESSIONS_LIST envelope additionally covers the
        legacy sessions-list channel, where subscribers of
        ``/relay/ws/sessions/{agent_id}`` don't go through event_bus.
        """
        session_dicts = _list_session_dicts(self._session_manager)

        try:
            self._event_bus.publish(
                "sessions.changed", {"sessions": session_dicts}
            )
        except Exception:
            logger.exception(
                "relay.events_snapshot.sessions_publish_failed"
            )

        projects_store = getattr(self._app_state, "projects_store", None)
        if projects_store is not None:
            try:
                projects = projects_store.list_public()
                self._event_bus.publish(
                    "projects.changed", {"projects": projects}
                )
            except Exception:
                logger.exception(
                    "relay.events_snapshot.projects_publish_failed"
                )

        try:
            await ws.send(
                encode(
                    CH_SESSIONS_LIST, "", orjson.dumps(session_dicts)
                )
            )
        except Exception:
            logger.debug(
                "relay.events_snapshot.sessions_list_send_failed",
                exc_info=True,
            )

    def _handle_mobile_detach(self, ctrl: dict) -> None:
        """Record that a mobile detached from a session.

        Present for protocol symmetry with ``mobile_session_attach`` so the
        central server can notify the agent when a mobile goes away; the
        agent currently has no per-mobile state to unwind, so this is a
        debug log. Left as a named method so a future per-device routing
        change has an obvious hook.
        """
        session_id = ctrl.get("session_id", "")
        logger.debug(
            "relay.mobile_detach session_id=%s", session_id,
        )

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

        claude_options = None
        raw_options = ctrl.get("claude_options")
        if raw_options is not None:
            try:
                claude_options = ClaudeOptionsModel.model_validate(
                    raw_options
                ).to_options()
            except ValidationError as exc:
                logger.warning(
                    "relay.create_session_invalid_claude_options session_id=%s: %s",
                    session_id, exc,
                )
                resp = {
                    "type": "session_create_failed",
                    "session_id": session_id,
                    "error": f"Invalid claude_options: {exc}",
                }
                await ws.send(encode(CH_CONTROL, "", json.dumps(resp).encode()))
                return

        codex_options = None
        raw_codex_options = ctrl.get("codex_options")
        if raw_codex_options is not None:
            try:
                codex_options = CodexOptionsModel.model_validate(
                    raw_codex_options
                ).to_options()
            except ValidationError as exc:
                logger.warning(
                    "relay.create_session_invalid_codex_options session_id=%s: %s",
                    session_id, exc,
                )
                resp = {
                    "type": "session_create_failed",
                    "session_id": session_id,
                    "error": f"Invalid codex_options: {exc}",
                }
                await ws.send(encode(CH_CONTROL, "", json.dumps(resp).encode()))
                return

        antigravity_options = None
        raw_antigravity_options = ctrl.get("antigravity_options")
        if raw_antigravity_options is not None:
            try:
                antigravity_options = AntigravityOptionsModel.model_validate(
                    raw_antigravity_options
                ).to_options()
            except ValidationError as exc:
                logger.warning(
                    "relay.create_session_invalid_antigravity_options session_id=%s: %s",
                    session_id, exc,
                )
                resp = {
                    "type": "session_create_failed",
                    "session_id": session_id,
                    "error": f"Invalid antigravity_options: {exc}",
                }
                await ws.send(encode(CH_CONTROL, "", json.dumps(resp).encode()))
                return

        ctrl_cwd = ctrl.get("cwd")
        ctrl_project_name = ctrl.get("project_name")
        if ctrl_project_name is None and ctrl_cwd is not None:
            err = _validate_adhoc_cwd_or_error(ctrl_cwd)
            if err is not None:
                logger.warning(
                    "relay.create_session_adhoc_path_invalid session_id=%s error=%s",
                    session_id, err,
                )
                resp = {
                    "type": "session_create_failed",
                    "session_id": session_id,
                    "error": err,
                }
                await ws.send(encode(CH_CONTROL, "", json.dumps(resp).encode()))
                return
        try:
            (
                kind,
                spawn_command,
                spawn_env,
                claude_options,
                codex_options,
                antigravity_options,
            ) = resolve_spawn_args(
                ctrl.get("cli"),
                claude_options,
                codex_options,
                antigravity_options,
                ctrl.get("command"),
                ctrl.get("env"),
                resolve_default_shell(),
                resolved_command=ctrl.get("resolved_command"),
            )
        except CustomCommandUnresolvedError as exc:
            logger.warning(
                "relay.create_session_custom_unresolved session_id=%s error=%s",
                session_id, exc,
            )
            resp = {
                "type": "session_create_failed",
                "session_id": session_id,
                "error": str(exc),
            }
            await ws.send(encode(CH_CONTROL, "", json.dumps(resp).encode()))
            return
        resolved_name = ctrl.get("resolved_name")
        config = SessionConfig(
            session_id=session_id,
            command=spawn_command,
            cwd=ctrl_cwd,
            env=spawn_env,
            rows=ctrl.get("rows", 24),
            cols=ctrl.get("cols", 80),
            project_name=ctrl_project_name,
            claude_options=claude_options,
            codex_options=codex_options,
            antigravity_options=antigravity_options,
            worktree=bool(ctrl.get("worktree", False)),
            branch_name=ctrl.get("branch_name"),
            kind=kind,
            kind_label=(
                resolved_name
                if isinstance(kind, str) and isinstance(resolved_name, str)
                else None
            ),
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
        except CliBinaryNotFoundError as exc:
            logger.warning(
                "relay.create_session_cli_missing session_id=%s cli=%s",
                session_id, exc.cli_name,
            )
            resp = {
                "type": "session_create_failed",
                "session_id": session_id,
                "error": f"{exc.cli_name.capitalize()} CLI not found: {exc}",
                # Back-compat: ``claude_cli_missing`` for Claude; ``codex_cli_missing``
                # for Codex. Lets the mobile client surface a CLI-specific hint.
                f"{exc.cli_name}_cli_missing": True,
                "searched_paths": list(exc.searched_paths),
            }
        except CliAuthRequiredError as exc:
            logger.warning(
                "relay.create_session_auth_required session_id=%s cli=%s",
                session_id, exc.cli_name,
            )
            resp = {
                "type": "session_create_failed",
                "session_id": session_id,
                "error": str(exc),
                # Parallel to ``<cli>_cli_missing``: CLI present but not signed
                # in. Lets the mobile client surface a login prompt.
                f"{exc.cli_name}_auth_required": True,
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
        except WorktreeCreationError as exc:
            logger.warning(
                "relay.create_session_worktree_failed session_id=%s: %s",
                session_id, exc.stderr,
            )
            resp = {
                "type": "session_create_failed",
                "session_id": session_id,
                "error": (
                    f"Could not create worktree on branch {exc.branch}: "
                    f"{exc.stderr}"
                ),
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
        except CliBinaryNotFoundError as exc:
            logger.warning(
                "relay.proxy_request_cli_missing path=%s cli=%s",
                path, exc.cli_name,
            )
            resp = {
                "type": "proxy_response",
                "request_id": request_id,
                "status": 503,
                "body": {
                    "detail": f"{exc.cli_name.capitalize()} CLI not found: {exc}",
                    f"{exc.cli_name}_cli_missing": True,
                    "searched_paths": list(exc.searched_paths),
                },
            }
        except CliAuthRequiredError as exc:
            logger.warning(
                "relay.proxy_request_auth_required path=%s cli=%s",
                path, exc.cli_name,
            )
            resp = {
                "type": "proxy_response",
                "request_id": request_id,
                # 409 (not 401) so the Android AuthInterceptor doesn't refresh +
                # retry on it and swallow the marker. Mirrors the REST router.
                "status": 409,
                "body": {
                    f"{exc.cli_name}_auth_required": True,
                    "detail": str(exc),
                },
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
        except WorktreeCreationError as exc:
            logger.warning(
                "relay.proxy_request_worktree_failed path=%s: %s",
                path, exc.stderr,
            )
            resp = {
                "type": "proxy_response",
                "request_id": request_id,
                "status": 400,
                "body": {
                    "detail": (
                        f"Could not create worktree on branch {exc.branch}: "
                        f"{exc.stderr}"
                    ),
                },
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

        if method == "GET" and path == "/api/version":
            manifest = self._latest_manifest
            # Same bounded mid-warmup wait as the direct handler (see
            # await_model_cache_warmup) so both transports advertise the same
            # capabilities to a probe that lands right after agent start; a
            # warm task still running after the wait is reported via
            # models_pending rather than as an authoritative cold shape.
            warm_task = getattr(self._app_state, "cli_models_warmup", None)
            await await_model_cache_warmup(warm_task)
            return {
                "status": 200,
                "body": {
                    "name": "mobaide_agent",
                    "server_version": __version__,
                    "protocol_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
                    "min_client_version": MIN_CLIENT_PROTOCOL_VERSION,
                    # Offload the binary probes off the event loop (see the direct
                    # /api/version handler) so a slow filesystem stat on a missing
                    # CLI's fallback path can't stall the relay dispatch loop.
                    "capabilities": {
                        "cli": await asyncio.to_thread(
                            build_cli_capabilities,
                            getattr(self._app_state, "cli_launchers", {}),
                            models_pending=warm_task is not None
                            and not warm_task.done(),
                        ),
                        "git": GIT_CAPABILITIES,
                        "fs": FS_CAPABILITIES,
                    },
                    "latest_agent_version": (manifest or {}).get("version"),
                },
            }

        if method == "POST" and path == "/api/agent/update/force_now":
            updater = getattr(self._app_state, "updater", None)
            if updater is None:
                return {
                    "status": 503,
                    "body": {
                        "detail": "Auto-updater is not configured on this agent.",
                    },
                }
            manifest = self._latest_manifest
            if (
                not manifest
                or not manifest.get("manifest_body")
                or not manifest.get("version")
            ):
                return {
                    "status": 409,
                    "body": {
                        "detail": (
                            "No manifest pointer available — agent has not "
                            "received a rollout yet."
                        ),
                    },
                }
            outcome = await updater.force_update_now(
                manifest["manifest_body"],
                manifest.get("manifest_signature", ""),
                manifest["version"],
                exit_after_stage=False,
            )
            logger.info("relay.force_update_now outcome=%s", outcome)
            if outcome == "staged":
                # Defer the supervisor exit so the proxy_response frame
                # has a chance to be written back to central_server before
                # the worker dies.
                asyncio.get_running_loop().call_later(1.0, sys.exit, 75)
            return {"status": 200, "body": {"outcome": outcome}}

        if method == "GET" and path == "/api/sessions":
            sessions = self._session_manager.list_sessions()
            return {
                "status": 200,
                "body": [session_to_wire_dict(s) for s in sessions],
            }

        if method == "POST" and path == "/api/sessions":
            try:
                parsed = CreateSessionRequest.model_validate(body or {})
            except ValidationError as exc:
                return {
                    "status": 422,
                    "body": {"detail": _scrub_validation_errors(exc)},
                }
            if parsed.project_name is None and parsed.cwd is not None:
                err = _validate_adhoc_cwd_or_error(parsed.cwd)
                if err is not None:
                    return {"status": 400, "body": {"detail": err}}
            session_id = uuid.uuid4().hex[:12]
            claude_options = (
                parsed.claude_options.to_options()
                if parsed.claude_options is not None
                else None
            )
            codex_options = (
                parsed.codex_options.to_options()
                if parsed.codex_options is not None
                else None
            )
            antigravity_options = (
                parsed.antigravity_options.to_options()
                if parsed.antigravity_options is not None
                else None
            )
            try:
                (
                    kind,
                    spawn_command,
                    spawn_env,
                    claude_options,
                    codex_options,
                    antigravity_options,
                ) = resolve_spawn_args(
                    parsed.cli,
                    claude_options,
                    codex_options,
                    antigravity_options,
                    parsed.command,
                    parsed.env,
                    resolve_default_shell(),
                    resolved_command=parsed.resolved_command,
                )
            except CustomCommandUnresolvedError as exc:
                # An unknown/malformed custom kind, or a relay too old to
                # inject resolved_command — a clean 400, never the generic
                # 500 from _handle_proxy_request's catch-all.
                return {"status": 400, "body": {"detail": str(exc)}}
            config = SessionConfig(
                session_id=session_id,
                command=spawn_command,
                cwd=parsed.cwd,
                env=spawn_env,
                rows=parsed.rows,
                cols=parsed.cols,
                project_name=parsed.project_name,
                claude_options=claude_options,
                codex_options=codex_options,
                antigravity_options=antigravity_options,
                worktree=parsed.worktree,
                branch_name=parsed.branch_name,
                kind=kind,
                kind_label=(
                    parsed.resolved_name if isinstance(kind, str) else None
                ),
            )
            session = await self._session_manager.create_session(config)
            self._bridge_session(session, ws)
            info = session.to_info()
            return {
                "status": 201,
                "body": session_to_wire_dict(info),
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

        git_resp = await self._dispatch_git(method, path, body, query)
        if git_resp is not None:
            return git_resp

        session_git_resp = await self._dispatch_session_git(method, path, query, body)
        if session_git_resp is not None:
            return session_git_resp

        linear_resp = await self._dispatch_linear_discovery(method, path)
        if linear_resp is not None:
            return linear_resp

        if method == "GET" and path == "/api/fs/dirs":
            q = (query or {}).get("q") or None
            try:
                listing = await list_dirs((query or {}).get("path", ""), q)
            except FsListError as exc:
                return {"status": exc.status, "body": {"detail": exc.detail}}
            return {"status": 200, "body": listing.model_dump()}

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
            try:
                parsed = StartScanRequest.model_validate(body or {})
            except ValidationError as exc:
                return {
                    "status": 422,
                    "body": {"detail": _scrub_validation_errors(exc)},
                }
            claude_options = (
                parsed.claude_options.to_options()
                if parsed.claude_options is not None
                else None
            )
            try:
                instance = await mgr.start(
                    parsed.project_name, client_options=claude_options,
                )
            except AutoScanAlreadyRunning as exc:
                return {"status": 409, "body": {"detail": str(exc)}}
            except AutoScanCapacityReached as exc:
                return {"status": 429, "body": {"detail": str(exc)}}
            except ValueError as exc:
                return {"status": 404, "body": {"detail": str(exc)}}
            return {"status": 200, "body": instance.to_dto()}

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

        if method == "POST" and path == "/api/codex/login":
            # Mirrors the direct-HTTP ``/api/codex/login`` handler so relay and
            # LAN clients get identical semantics. ``resolve_binary`` raising
            # ``CliBinaryNotFoundError`` propagates to ``_handle_proxy_request``'s
            # 503 handler (as ``POST /api/sessions`` does).
            launcher = getattr(self._app_state, "codex_launcher", None)
            if launcher is None:
                return {
                    "status": 503,
                    "body": {"detail": "Codex is not configured on this agent."},
                }
            try:
                parsed = CodexLoginRequest.model_validate(body or {})
            except ValidationError as exc:
                return {
                    "status": 422,
                    "body": {"detail": _scrub_validation_errors(exc)},
                }
            codex_bin = launcher.resolve_binary()
            env = os.environ.copy()
            prepend_path_dir(env, os.path.dirname(codex_bin))
            ok, detail = await codex_login_with_api_key(
                parsed.api_key.get_secret_value(),
                codex_bin=codex_bin,
                env=env,
            )
            if ok:
                return {"status": 200, "body": {"authenticated": True}}
            # 400 (not 401): a rejected key is bad input — a 401 would make the
            # Android AuthInterceptor refresh + retry, re-running the subprocess.
            return {
                "status": 400,
                "body": {"detail": f"Codex login failed: {detail}"},
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

    async def _dispatch_git(
        self,
        method: str,
        path: str,
        body: dict | None,
        query: dict | None = None,
    ) -> dict | None:
        """Handle ``/api/projects/{name}/git/...`` requests through the shared
        service layer so direct HTTP and relay-forwarded requests produce
        byte-identical responses (matters for the mobile busy-conflict dialog,
        which expects ``body.detail.busy_by`` on 409, not ``body.busy_by``).

        Returns a proxy-response dict for a matching route, or ``None`` to
        signal that the caller should fall through to the next handler.
        """
        match = re.match(
            r"^/api/projects/([^/]+)/git/(items|items:delete|branches|checkout)$",
            path,
        )
        if match is None:
            return None
        name, route = match.group(1), match.group(2)
        is_items = route == "items"

        store = getattr(self._app_state, "projects_store", None)
        if store is None:
            return {
                "status": 500,
                "body": {"detail": "projects store not configured"},
            }

        manager = getattr(self._app_state, "worktree_manager", None)
        if manager is None:
            return {
                "status": 500,
                "body": {"detail": "worktree manager not configured"},
            }

        project = store.get(name)
        if project is None:
            return {
                "status": 404,
                "body": {"detail": f"Project '{name}' not found"},
            }
        project_root = project.path
        if not safe_path_exists(Path(project_root) / ".git"):
            return {
                "status": 400,
                "body": {"detail": f"Project '{name}' is not a git repository"},
            }

        if is_items and method == "GET":
            try:
                q = GitItemsQuery.model_validate(query or {})
            except ValidationError as exc:
                return {
                    "status": 422,
                    "body": {"detail": _scrub_validation_errors(exc)},
                }
            try:
                items = await list_git_items(project_root, manager, remote=q.remote)
            except Exception as exc:
                mapped = git_changes_http_error_for(exc)
                if mapped is None:
                    raise
                return {"status": mapped[0], "body": mapped[1]}
            return {
                "status": 200,
                "body": GitItemsResponse(items=items).model_dump(),
            }

        post_routes = {
            "items:delete": (GitDeleteRequest, delete_git_items),
            "branches": (GitBranchCreateRequest, create_branch),
            "checkout": (GitCheckoutRequest, checkout_ref),
        }
        if route in post_routes and method == "POST":
            model, handler = post_routes[route]
            try:
                req = model.model_validate(body or {})
            except ValidationError as exc:
                return {
                    "status": 422,
                    "body": {"detail": _scrub_validation_errors(exc)},
                }
            try:
                resp = await handler(
                    req,
                    project_name=project.name,
                    project_root=project_root,
                    manager=manager,
                    session_manager=getattr(
                        self._app_state, "session_manager", None,
                    ),
                    autoscan_manager=getattr(
                        self._app_state, "autoscan_manager", None,
                    ),
                    event_bus=getattr(self._app_state, "event_bus", None),
                    actor=None,
                )
            except GitItemBusyError as exc:
                return {
                    "status": 409,
                    "body": {
                        "detail": BusyByResponse(
                            busy_by=exc.busy_by,
                        ).model_dump(),
                    },
                }
            except Exception as exc:
                mapped = git_changes_http_error_for(exc)
                if mapped is None:
                    raise
                return {"status": mapped[0], "body": mapped[1]}
            return {"status": 200, "body": resp.model_dump()}

        return None

    async def _dispatch_session_git(
        self,
        method: str,
        path: str,
        query: dict | None,
        body: dict | None = None,
    ) -> dict | None:
        """Handle ``/api/sessions/{id}/git/*`` through the shared service
        layer — same 200/400/404/409/422/503/504 bodies as the FastAPI
        router."""
        m = re.match(
            r"^/api/sessions/([^/]+)/git/"
            r"(status|diff|stage|unstage|discard|commit|commit/context"
            r"|fetch|pull|push|jobs/([^/]+)|jobs/([^/]+)/cancel"
            r"|rebase|rebase/continue|rebase/skip|rebase/abort"
            r"|conflict|conflict/resolve|conflict/take|conflict/restore)$",
            path,
        )
        if m is None:
            return None
        session_id, route = m.group(1), m.group(2)
        job_id = m.group(3) or m.group(4)
        if job_id is not None:
            route = "jobs/cancel" if route.endswith("/cancel") else "jobs"
        expected = (
            "GET"
            if route in ("status", "diff", "commit/context", "jobs", "conflict")
            else "POST"
        )
        if method != expected:
            return None
        session = await self._session_manager.get_session(session_id)
        if session is None:
            return {
                "status": 404,
                "body": {"detail": f"Session '{session_id}' not found"},
            }
        info = session.to_info()
        cwd = info.cwd
        project_name = getattr(info, "project_name", None)
        event_bus = getattr(self._app_state, "event_bus", None)
        q = query or {}
        request_models = {
            "stage": GitPathsRequest,
            "unstage": GitPathsRequest,
            "discard": GitDiscardRequest,
            "commit": GitCommitRequest,
            "pull": GitPullRequest,
            "push": GitPushRequest,
            "rebase": GitRebaseRequest,
            "conflict/resolve": GitConflictResolveRequest,
            "conflict/take": GitConflictTakeRequest,
            "conflict/restore": GitConflictRestoreRequest,
        }
        req = None
        if route in request_models:
            try:
                req = request_models[route].model_validate(body or {})
            except ValidationError as exc:
                return {"status": 422, "body": {"detail": _scrub_validation_errors(exc)}}
        try:
            if route == "fetch":
                resp = await job_registry.start(
                    "fetch", cwd=cwd, session_id=session_id,
                    project_name=project_name, event_bus=event_bus,
                )
            elif route == "pull":
                resp = await job_registry.start(
                    "pull", cwd=cwd, session_id=session_id,
                    project_name=project_name, event_bus=event_bus,
                    pull_mode=req.mode,
                )
            elif route == "push":
                resp = await job_registry.start(
                    "push", cwd=cwd, session_id=session_id,
                    project_name=project_name, event_bus=event_bus,
                    force=req.force,
                )
            elif route == "jobs":
                resp = job_registry.get(str(job_id))
            elif route == "jobs/cancel":
                resp = await job_registry.cancel(str(job_id))
            elif route == "status":
                resp = await get_session_git_status(
                    session_id=session_id, cwd=cwd, etag=q.get("etag"),
                )
            elif route == "diff":
                resp = await get_session_git_diff(
                    cwd=cwd,
                    path=q.get("path"),
                    scope=q.get("scope"),
                    context=q.get("context", 3),
                    orig_path=q.get("orig_path"),
                )
            elif route == "commit/context":
                resp = await get_session_commit_context(cwd=cwd)
            elif route == "rebase":
                resp = await start_session_rebase(
                    cwd=cwd, onto=req.onto, project_name=project_name,
                    event_bus=event_bus,
                )
            elif route == "rebase/continue":
                resp = await continue_session_rebase(
                    cwd=cwd, project_name=project_name, event_bus=event_bus,
                )
            elif route == "rebase/skip":
                resp = await skip_session_rebase(
                    cwd=cwd, project_name=project_name, event_bus=event_bus,
                )
            elif route == "rebase/abort":
                resp = await abort_session_rebase(
                    cwd=cwd, project_name=project_name, event_bus=event_bus,
                )
            elif route == "conflict":
                resp = await get_session_conflict(cwd=cwd, path=q.get("path"))
            elif route == "conflict/resolve":
                resp = await resolve_session_conflict(
                    cwd=cwd, request=req, project_name=project_name,
                    event_bus=event_bus,
                )
            elif route == "conflict/take":
                resp = await take_session_conflict(
                    cwd=cwd, request=req, project_name=project_name,
                    event_bus=event_bus,
                )
            elif route == "conflict/restore":
                resp = await restore_session_conflict(
                    cwd=cwd, request=req, project_name=project_name,
                    event_bus=event_bus,
                )
            elif route == "stage":
                resp = await stage_session_paths(
                    cwd=cwd, paths=req.paths, project_name=project_name,
                    event_bus=event_bus,
                )
            elif route == "unstage":
                resp = await unstage_session_paths(
                    cwd=cwd, paths=req.paths, project_name=project_name,
                    event_bus=event_bus,
                )
            elif route == "discard":
                resp = await discard_session_paths(
                    cwd=cwd, paths=req.paths, scope=req.scope,
                    project_name=project_name, event_bus=event_bus,
                )
            else:
                resp = await commit_session(
                    cwd=cwd, request=req, project_name=project_name,
                    event_bus=event_bus,
                )
        except Exception as exc:
            mapped = git_changes_http_error_for(exc)
            if mapped is None:
                raise
            return {"status": mapped[0], "body": mapped[1]}
        return {"status": 200, "body": resp.model_dump(mode="json")}

    async def _dispatch_linear_discovery(
        self,
        method: str,
        path: str,
    ) -> dict | None:
        """Handle ``GET /api/projects/{name}/linear/(labels|states)`` over relay.

        Mirrors the FastAPI endpoints in ``agent/projects/router.py`` so mobile
        clients connecting through the central server reach the same surface.
        Returns 404 if the project is unknown, 400 if the project has no
        ``linear_api_key``, 502 on upstream Linear failure, otherwise the
        deduped-by-name list returned by the agent's :class:`LinearApiClient`.
        """
        m = re.match(r"^/api/projects/([^/]+)/linear/(labels|states)$", path)
        if not m or method != "GET":
            return None

        name = m.group(1)
        kind = m.group(2)

        store = getattr(self._app_state, "projects_store", None)
        if store is None:
            return {
                "status": 500,
                "body": {"detail": "projects store not configured"},
            }
        project = store.get(name)
        if project is None:
            return {
                "status": 404,
                "body": {"detail": f"Project '{name}' not found"},
            }
        api_key = project.linear_api_key
        if not api_key:
            return {
                "status": 400,
                "body": {
                    "detail": (
                        f"Project '{name}' has no linear_api_key configured; "
                        "set one before fetching Linear metadata."
                    ),
                },
            }
        rate_limiter: LinearRateLimiter | None = getattr(
            self._app_state, "linear_rate_limiter", None,
        )
        client = LinearApiClient(api_key=api_key, rate_limiter=rate_limiter)
        try:
            if kind == "labels":
                items = await client.get_labels()
            else:
                items = await client.get_workflow_states()
        except Exception as exc:
            logger.warning(
                "linear.discovery.%s_failed project=%s error=%s",
                kind, name, exc,
            )
            return {
                "status": 502,
                "body": {
                    "detail": (
                        f"Failed to fetch {'labels' if kind == 'labels' else 'workflow states'} "
                        "from Linear"
                    ),
                },
            }
        return {"status": 200, "body": items}

    def _enqueue_session_input(
        self,
        session_id: str,
        payload: bytes,
        ws: ClientConnection,
    ) -> None:
        """Queue an inbound CH_SESSION_IO frame for the session's input pump.

        Never blocks the tunnel reader: the pump absorbs the submit/execute
        write delays so other sessions and CH_CONTROL traffic keep flowing.
        On overflow the frame is dropped with a warning.
        """
        queue = self._input_queues.get(session_id)
        if queue is None:
            queue = asyncio.Queue(maxsize=INPUT_QUEUE_MAXSIZE)
            self._input_queues[session_id] = queue
            self._input_pumps[session_id] = asyncio.create_task(
                self._session_input_pump(session_id, queue, ws),
                name=f"relay-input-{session_id}",
            )
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            logger.warning(
                "relay.input_queue_full session_id=%s — dropping frame",
                session_id,
            )

    async def _session_input_pump(
        self,
        session_id: str,
        queue: asyncio.Queue[bytes],
        ws: ClientConnection,
    ) -> None:
        """Consume queued PTY frames for one session, strictly in order.

        A failing frame (e.g. the PTY dies during the delayed-CR window) is
        logged and skipped — it must neither kill the pump nor tear down the
        relay tunnel.
        """
        try:
            while True:
                payload = await queue.get()
                try:
                    await self._handle_session_input(session_id, payload, ws)
                except Exception as exc:
                    logger.warning(
                        "relay.session_input_failed session_id=%s: %s",
                        session_id,
                        exc,
                    )
        except asyncio.CancelledError:
            pass
        finally:
            # Deregister only our own entries. A successor pump may already
            # be registered for this session (late frame arriving between the
            # removed-session sweep's cancel+pop and this unwind); popping
            # unconditionally would orphan it on an unreachable queue.
            if self._input_queues.get(session_id) is queue:
                self._input_queues.pop(session_id, None)
            if self._input_pumps.get(session_id) is asyncio.current_task():
                self._input_pumps.pop(session_id, None)

    async def _handle_session_input(
        self,
        session_id: str,
        payload: bytes,
        ws: ClientConnection,
    ) -> None:
        """Route incoming PTY frames from relay to a local session.

        A frame naming an unknown session is **dropped**, never treated as a
        request to spawn one. Both mobile clients re-assert their cached PTY
        size on every socket connect, so attaching to a stale session id
        (agent restarted, session swept, resumed id from preferences) would
        otherwise materialize a phantom session out of a resize frame.

        ``_handle_mobile_attach`` normally answers the stale attach with a
        ``stopped`` state frame; this path repeats it once per unknown id to
        cover the case where the attach was missed, so the client never waits
        on a state frame while its frames go in the bin.
        """
        if not payload:
            return

        session = await self._session_manager.get_session(session_id)
        if session is None:
            self._unknown_session_input_total += 1
            # First sight of an id warns; repeats stay at debug. A client left
            # attached to a swept session re-sends on every keystroke and every
            # reconnect, so warning per frame would bury the log.
            log = (
                logger.debug
                if session_id in self._unknown_session_notified
                else logger.warning
            )
            log(
                "relay.session_input.unknown_session session_id=%s total=%d "
                "— dropping frame",
                session_id,
                self._unknown_session_input_total,
            )
            await self._notify_session_unknown(session_id, ws)
            return

        frame_type = payload[:1]
        frame_data = payload[1:]

        if frame_type == FRAME_INPUT:
            if frame_data:
                await session.write(frame_data)
        elif frame_type == FRAME_EXECUTE:
            if frame_data:
                await session.write_execute(frame_data)
        elif frame_type == FRAME_SUBMIT:
            if frame_data:
                await session.write_submit(frame_data)
        elif frame_type == FRAME_RESIZE:
            if len(frame_data) >= 4:
                rows, cols = struct.unpack(">HH", frame_data[:4])
                await session.resize(rows, cols)

    async def _notify_session_unknown(
        self, session_id: str, ws: ClientConnection
    ) -> None:
        """Tell the mobile once that ``session_id`` does not exist here.

        The set is capped rather than grown without bound: a client that keeps
        pushing frames for ids this agent never had must not be able to inflate
        agent memory. Clearing on overflow at worst re-sends a duplicate state
        frame, which is idempotent for the client.
        """
        if session_id in self._unknown_session_notified:
            return
        if len(self._unknown_session_notified) >= UNKNOWN_SESSION_NOTIFY_CAP:
            self._unknown_session_notified.clear()
        self._unknown_session_notified.add(session_id)
        await self._send_stopped_frame(session_id, ws, "session_input")

    async def _send_stopped_frame(
        self, session_id: str, ws: ClientConnection, source: str
    ) -> None:
        """Tell the mobile this agent has no such session.

        Shared by the attach path and the dropped-input path, which must agree
        on what a client sees for an unknown id. Deliberately *not* shared with
        the once-per-id dedupe above: ``_handle_mobile_attach`` has to re-send
        unconditionally on every attach, or a re-opened terminal keeps a grayed
        out keyboard waiting on a state frame that never comes.
        """
        try:
            stopped = _state_frame("stopped", None)
            await ws.send(encode(CH_SESSION_IO, session_id, stopped))
        except Exception:
            logger.debug(
                "relay.%s.stopped_frame_failed session_id=%s",
                source,
                session_id,
                exc_info=True,
            )

    async def _send_session_handshake(
        self,
        session_id: str,
        session: Any,
        ws: ClientConnection,
    ) -> None:
        """Send state + replay marker + chunked ring + replay-end marker.

        Mirrors ``mobaide.agent.ws.session_io.session_io`` — the chunked
        sequence keeps individual envelope payloads small enough for the
        relay tunnel and for the mobile emulator to process each chunk
        within a UI frame on append.
        """
        try:
            status = _STATE_MAP.get(session.state, "running")
            state = _state_frame(status, session.pid)
            await ws.send(encode(CH_SESSION_IO, session_id, state))

            await ws.send(encode(CH_SESSION_IO, session_id, FRAME_REPLAY))

            # The ring is DA-stripped at write time inside ``_append_ring``;
            # ``iter_ring_chunks`` takes a single snapshot of the (already
            # clean) ring and yields chunks of ``REPLAY_CHUNK_BYTES`` each.
            emitted_any = False
            for chunk in session.iter_ring_chunks():
                await ws.send(
                    encode(CH_SESSION_IO, session_id, FRAME_OUTPUT + chunk),
                )
                emitted_any = True
            if not emitted_any:
                await ws.send(
                    encode(CH_SESSION_IO, session_id, FRAME_OUTPUT),
                )
            await ws.send(
                encode(CH_SESSION_IO, session_id, FRAME_REPLAY_END),
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

        from mobaide.agent.ws.session_io import ensure_fanout

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

                for sid in list(self._input_pumps):
                    if sid not in current_ids:
                        pump = self._input_pumps.get(sid)
                        if pump and not pump.done():
                            pump.cancel()
                        # The pump's finally block pops both dicts; pop here
                        # too so a not-yet-scheduled pump cannot linger.
                        self._input_pumps.pop(sid, None)
                        self._input_queues.pop(sid, None)
        except asyncio.CancelledError:
            pass
        finally:
            self._event_bus.unsubscribe(q)

    async def _forward_session_list(self, ws: ClientConnection) -> None:
        """Forward session-list updates as CH_SESSIONS_LIST envelopes."""
        initial = orjson.dumps(_list_session_dicts(self._session_manager))
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
        """Forward EventBus events as CH_EVENTS envelopes.

        ``session.attention`` additionally goes out as a CH_CONTROL
        ``user_event`` so the server can reach devices without a live
        user-events socket via push (CH_EVENTS is opaque to the server).
        """
        q = self._event_bus.subscribe()
        try:
            while True:
                event = await q.get()
                payload = orjson.dumps(
                    {"type": event.type, "payload": event.payload}
                )
                await ws.send(encode(CH_EVENTS, "", payload))
                if event.type == "session.attention":
                    ctrl = _attention_user_event(event.payload)
                    if ctrl is not None:
                        await ws.send(
                            encode(CH_CONTROL, "", orjson.dumps(ctrl))
                        )
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
        for task in list(self._input_pumps.values()):
            if not task.done():
                task.cancel()
        self._input_pumps.clear()
        self._input_queues.clear()
        for task in list(self._autoscan_streams.values()):
            if not task.done():
                task.cancel()
        self._autoscan_streams.clear()
        self._autoscan_inboxes.clear()
