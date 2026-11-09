"""A chat session: one ACP agent process driving one ACP session.

``ChatSession`` duck-types :class:`mobaide.core.session.TerminalSession` for
the parts the session manager, the event fan-out and the relay use
(``state``, ``event_queue``, ``to_info``, ``stop`` …), so chat sessions share
the sessions list, attention notifications and the per-session WebSocket
with terminal sessions. Its traffic on that socket is JSON: commands in
``'c'`` frames, timeline events in ``'a'`` frames (see PROTOCOL.md).
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import collections
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import orjson

from mobaide import __version__
from mobaide.core.models import SessionConfig, SessionEvent, SessionInfo, SessionState

from . import normalize as nz
from .agents import ChatLaunchPlan, ConfigIntent
from .install import INSTALL_POOL, PROBE_POOL, AdapterInstallError
from .rpc import (
    ERR_AUTH_REQUIRED,
    ERR_METHOD_NOT_FOUND,
    AcpConnection,
    AcpProcessError,
    AcpRpcError,
)
from .timeline import ChatTimeline

logger = logging.getLogger(__name__)

EVENT_QUEUE_MAXSIZE = 256
INITIALIZE_TIMEOUT = 120.0
SETUP_TIMEOUT = 180.0
CONFIG_TIMEOUT = 20.0
CLOSE_TIMEOUT = 2.0
PROMPT_TEXT_MAX = 100_000
IMAGES_MAX = 4
IMAGE_B64_MAX = 1_500_000
# One 'c' frame must stay under the relay's 4 MiB frame limit.
IMAGES_B64_TOTAL_MAX = 3_500_000
IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
QUEUE_MAX = 10
CANCEL_GRACE = 15.0
# How long closing a session on a shared host waits for its own turn to stop.
SHARED_CANCEL_WAIT = 3.0
STEER_TIMEOUT = 30.0
STEER_METHOD = "_session/steering"
# Command output of a running tool is re-rendered at most this often.
CONSOLE_RENDER_INTERVAL = 0.3
CONSOLE_TOOLS_MAX = 64
ACP_PROTOCOL_VERSION = 1

CLIENT_CAPABILITIES: dict[str, Any] = {
    "session": {
        "notices": {},
        "compaction": {},
        "configOptions": {"boolean": {}},
    },
    "elicitation": {"form": {}},
}

STOP_REASON_TEXT = {
    "cancelled": "Stopped",
    "max_tokens": "Stopped: the response reached the token limit",
    "max_turn_requests": "Stopped: the turn reached its request limit",
    "refusal": "The agent refused to continue",
}


@dataclass
class _PendingRequest:
    key: str
    kind: str
    future: asyncio.Future[Any]
    public: dict[str, Any]
    tool_call_id: str | None = None
    fields: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _QueuedPrompt:
    key: str
    text: str
    images: list[dict[str, str]]
    # A steer request for it is in flight: the queue must not send it too.
    steering: bool = False


@dataclass
class _Console:
    """Command output of one tool call (``_meta.terminal_output*``)."""

    output: str = ""
    exit_code: int | None = None
    part: dict[str, Any] | None = None
    render: asyncio.TimerHandle | None = None
    # The final render ran and ``output`` was released.
    final: bool = False


class ChatCommandError(Exception):
    """A client command that cannot be applied; the text goes to the client."""


class ChatSession:
    is_chat = True

    def __init__(
        self,
        config: SessionConfig,
        plan: ChatLaunchPlan,
        *,
        agent_title: str,
        load_session_id: str | None = None,
    ) -> None:
        self._config = config
        self._plan = plan
        self._agent_title = agent_title
        self._load_session_id = load_session_id
        self._state = SessionState.STARTING
        self._created_at = datetime.now(UTC)
        self._event_queue: asyncio.Queue[SessionEvent] = asyncio.Queue(
            maxsize=EVENT_QUEUE_MAXSIZE
        )
        self._timeline = ChatTimeline(self._emit_frame)
        self._timeline.state["cwd"] = plan.cwd
        self._title: str | None = plan.title
        self._attention: str | None = None
        self._exit_reason: str | None = None
        self._conn: AcpConnection | None = None
        self._acp_session_id: str | None = None
        self._agent_caps: dict[str, Any] = {}
        self._caps_known = False
        self._uses_modes = False
        self._bootstrap_task: asyncio.Task[None] | None = None
        self._turn_task: asyncio.Task[None] | None = None
        self._requests: dict[str, _PendingRequest] = {}
        self._queue: list[_QueuedPrompt] = []
        self._counter = 0
        self._current_agent_item: str | None = None
        self._current_thought_item: str | None = None
        self._current_user_item: str | None = None
        self._last_agent_message_id: str | None = None
        self._last_thought_message_id: str | None = None
        self._last_user_message_id: str | None = None
        self._stopping = False
        self._ready = asyncio.Event()
        self._command_tasks: set[asyncio.Task[None]] = set()
        self._prompt_dispatched = False
        self._cancel_before_dispatch = False
        self._cancel_watchdog: asyncio.TimerHandle | None = None
        self._restarting = False
        self._cancel_forced = False
        self._trust_granted = False
        self._pending_config: list[ConfigIntent] = list(plan.initial_config)
        # Categories whose launch value must hold (a sandbox): a Restart keeps
        # them required, so it can never start wider than the user chose.
        self._required_categories = {
            i.category for i in plan.initial_config if i.required and i.category
        }
        self._initial_prompt: str | None = plan.initial_prompt
        self._steering = False
        # A turn another client of a shared host started (see thread_busy).
        self._external_turn = False
        # The last busy report that came while the session was opening.
        self._busy_before_ready = False
        self._consoles: collections.OrderedDict[str, _Console] = collections.OrderedDict()

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def session_id(self) -> str:
        return self._config.session_id

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def pid(self) -> int | None:
        return self._conn.pid if self._conn is not None else None

    @property
    def event_queue(self) -> asyncio.Queue[SessionEvent]:
        return self._event_queue

    @property
    def acp_session_id(self) -> str | None:
        return self._acp_session_id or self._load_session_id

    @property
    def timeline(self) -> ChatTimeline:
        return self._timeline

    def is_alive(self) -> bool:
        return self._state in (SessionState.STARTING, SessionState.RUNNING)

    def iter_ring_chunks(self) -> list[bytes]:
        return []

    def get_scrollback(self) -> list[str]:
        return []

    async def write(self, data: bytes) -> None:
        return None

    async def write_submit(self, data: bytes) -> None:
        return None

    async def write_execute(self, data: bytes) -> None:
        return None

    async def resize(self, rows: int, cols: int) -> None:
        return None

    def to_info(self) -> SessionInfo:
        return SessionInfo(
            session_id=self._config.session_id,
            state=self._state,
            created_at=self._created_at,
            pid=self.pid,
            command=list(self._plan.argv),
            cwd=self._plan.cwd,
            title=self._title,
            project_name=self._config.project_name,
            kind=self._config.kind,
            kind_label=self._config.kind_label,
            attention=self._attention,
            exit_reason=self._exit_reason,
            ui="chat",
        )

    async def start(self) -> None:
        """Spawn the agent; the ACP handshake continues in the background.

        An adapter that is not installed yet is installed and spawned in the
        background too, so its errors show in the chat with Restart.
        """
        if self._plan.ensure_argv is None:
            await self._spawn()
        self._state = SessionState.RUNNING
        self._put_event(SessionEvent("started", self.session_id))
        self._bootstrap_task = asyncio.create_task(
            self._start_agent(load_id=self._load_session_id),
            name=f"chat-bootstrap-{self.session_id}",
        )

    async def _start_agent(self, *, load_id: str | None) -> None:
        if self._conn is None or not self._conn.alive:
            if not await self._spawn_with_install():
                return
        await self._bootstrap(load_id=load_id)

    async def _spawn_with_install(self) -> bool:
        ensure = self._plan.ensure_argv
        if ensure is not None:
            self._timeline.patch_state(status="starting", error=None, urgent=True)
            self._timeline.upsert(
                {
                    "id": self._next_id("n"),
                    "type": "notice",
                    "severity": "info",
                    "title": f"Setting up the {self._agent_title} chat on your PC",
                    "text": "This happens once and can take a minute.",
                }
            )
            try:
                argv = await asyncio.get_running_loop().run_in_executor(INSTALL_POOL, ensure)
            except AdapterInstallError as exc:
                self._fail_start(str(exc))
                return False
            except Exception as exc:
                logger.exception("chat.adapter_install_failed session=%s", self.session_id)
                self._fail_start(f"Could not set up the chat: {exc}")
                return False
            self._plan.argv[:] = argv
            self._plan.ensure_argv = None
        try:
            await self._spawn()
        except OSError as exc:
            self._fail_start(str(exc) or "The agent could not start")
            return False
        if self._stopping:
            if self._conn is not None:
                await self._conn.close(grace=0.5)
            return False
        return True

    async def stop(self, force: bool = False) -> None:
        if self._state in (SessionState.STOPPED, SessionState.STOPPING):
            return
        self._stopping = True
        self._state = SessionState.STOPPING
        self._disarm_cancel_watchdog()
        if self._plan.shared_host:
            await self._stop_own_turn()
        for task in (self._bootstrap_task, self._turn_task, *self._command_tasks):
            if task is not None and not task.done():
                task.cancel()
        self._resolve_all_requests_cancelled()
        conn = self._conn
        if conn is not None:
            # On a shared host, session/close would also stop a turn that
            # another app runs: dropping the connection leaves it to them.
            if (
                not force
                and not self._plan.shared_host
                and conn.alive
                and self._acp_session_id
                and self._session_cap("close")
            ):
                try:
                    await conn.request(
                        "session/close", {"sessionId": self._acp_session_id}, CLOSE_TIMEOUT
                    )
                except Exception:
                    pass
            await conn.close(grace=0.5 if force else CLOSE_TIMEOUT)
        self._drop_consoles()
        self._timeline.patch_state(status="stopped", urgent=True)
        self._timeline.close()
        self._state = SessionState.STOPPED
        self._put_event(SessionEvent("stopped", self.session_id))

    async def _stop_own_turn(self) -> None:
        """Cancel the turn this session started and give it a moment to end."""
        turn, conn = self._turn_task, self._conn
        if turn is None or turn.done() or conn is None or not conn.alive or not self._acp_session_id:
            return
        if not self._prompt_dispatched:
            self._cancel_before_dispatch = True
        else:
            conn.notify("session/cancel", {"sessionId": self._acp_session_id})
        self._resolve_all_requests_cancelled()
        try:
            await asyncio.wait_for(asyncio.shield(turn), SHARED_CANCEL_WAIT)
        except (TimeoutError, asyncio.CancelledError, Exception):
            pass

    def sync_frame(self, epoch: Any, after_seq: Any) -> bytes:
        """The answer to a client ``sync``: missed events, or a snapshot."""
        if (
            isinstance(epoch, str)
            and epoch == self._timeline.epoch
            and isinstance(after_seq, int)
        ):
            frame = self._timeline.replay_frame(after_seq)
            if frame is not None:
                return frame
        return self._timeline.snapshot_frame()

    async def handle_command(self, raw: bytes) -> None:
        """Apply one client ``'c'`` frame payload."""
        try:
            cmd = orjson.loads(raw)
        except orjson.JSONDecodeError:
            return
        if not isinstance(cmd, dict):
            return
        op = cmd.get("op")
        cmd_id = cmd.get("id") if isinstance(cmd.get("id"), str) else None
        if op == "sync":
            self._emit_frame(self.sync_frame(cmd.get("epoch"), cmd.get("after_seq")))
            return
        if op in ("set_config", "restart", "steer"):
            # These wait on the agent; running them aside keeps a later
            # cancel or prompt from queueing behind them.
            task = asyncio.create_task(self._run_command(op, cmd, cmd_id))
            self._command_tasks.add(task)
            task.add_done_callback(self._command_tasks.discard)
            return
        await self._run_command(op, cmd, cmd_id)

    async def _run_command(self, op: Any, cmd: dict[str, Any], cmd_id: str | None) -> None:
        try:
            data = await self._dispatch_command(op, cmd)
        except ChatCommandError as exc:
            self._send_result(cmd_id, ok=False, error=str(exc))
            return
        except Exception as exc:
            logger.exception("chat.command_failed session=%s op=%s", self.session_id, op)
            self._send_result(cmd_id, ok=False, error=str(exc) or "Command failed")
            return
        self._send_result(cmd_id, ok=True, data=data)

    async def _dispatch_command(self, op: Any, cmd: dict[str, Any]) -> Any:
        if op == "prompt":
            return self._cmd_prompt(cmd)
        if op == "cancel":
            return await self._cmd_cancel()
        if op == "set_config":
            return await self._cmd_set_config(cmd)
        if op == "respond":
            return self._cmd_respond(cmd)
        if op == "dequeue":
            return self._cmd_dequeue(cmd)
        if op == "restart":
            return await self._cmd_restart()
        if op == "steer":
            return await self._cmd_steer(cmd)
        raise ChatCommandError(f"Unknown command: {op}")

    async def _spawn(self) -> None:
        # Callbacks are bound to their connection: after a restart, late
        # messages or the exit of the replaced process must be ignored.
        holder: list[AcpConnection] = []

        def current() -> bool:
            return bool(holder) and holder[0] is self._conn

        def on_notification(method: str, params: Any) -> None:
            if current():
                self._on_notification(method, params)

        async def on_request(method: str, params: Any) -> Any:
            if not current():
                raise AcpRpcError(ERR_METHOD_NOT_FOUND, "Connection replaced")
            return await self._on_request(method, params)

        def on_exit(returncode: int | None) -> None:
            if current():
                self._on_exit(returncode)

        conn = AcpConnection(
            self._plan.argv,
            cwd=self._plan.process_cwd or os.path.expanduser("~"),
            env=self._plan.env,
            on_notification=on_notification,
            on_request=on_request,
            on_exit=on_exit,
            name=f"acp-{self.session_id}",
        )
        holder.append(conn)
        await conn.start()
        self._conn = conn

    async def _bootstrap(self, *, load_id: str | None) -> None:
        conn = self._conn
        assert conn is not None
        tl = self._timeline
        tl.patch_state(status="starting", error=None, urgent=True)
        try:
            init = await conn.request(
                "initialize",
                {
                    "protocolVersion": ACP_PROTOCOL_VERSION,
                    "clientCapabilities": CLIENT_CAPABILITIES,
                    "clientInfo": {"name": "mobaide", "title": "Mobaide", "version": __version__},
                },
                INITIALIZE_TIMEOUT,
            )
            if not isinstance(init, dict):
                raise AcpProcessError("The agent sent an invalid initialize reply")
            version = init.get("protocolVersion")
            if version != ACP_PROTOCOL_VERSION:
                raise AcpProcessError(
                    f"The agent speaks ACP version {version}; Mobaide supports version 1"
                )
            caps = init.get("agentCapabilities")
            self._agent_caps = caps if isinstance(caps, dict) else {}
            self._caps_known = True
            raw_info = init.get("agentInfo")
            info: dict[str, Any] = raw_info if isinstance(raw_info, dict) else {}
            raw_prompt_caps = self._agent_caps.get("promptCapabilities")
            prompt_caps: dict[str, Any] = raw_prompt_caps if isinstance(raw_prompt_caps, dict) else {}
            raw_meta = init.get("_meta")
            steering = raw_meta.get("steering") if isinstance(raw_meta, dict) else None
            self._steering = isinstance(steering, dict) and steering.get("supported") is True
            tl.patch_state(
                agent={
                    "name": str(info.get("name") or ""),
                    "title": str(info.get("title") or self._agent_title),
                    "version": str(info.get("version") or ""),
                },
                prompt_caps={"image": bool(prompt_caps.get("image"))},
                steering=self._steering,
            )
            if not await self._ensure_trusted():
                return
            setup_params: dict[str, Any] = {"cwd": self._plan.cwd, "mcpServers": []}
            if self._plan.system_prompt_append and self._session_cap("systemPrompt"):
                setup_params["systemPrompt"] = {
                    "mode": "append",
                    "content": [{"type": "text", "text": self._plan.system_prompt_append}],
                }
            if load_id:
                if not self._agent_caps.get("loadSession"):
                    raise AcpProcessError("This agent cannot open earlier sessions")
                tl.patch_state(status="loading")
                self._acp_session_id = load_id
                result = await conn.request(
                    "session/load", {**setup_params, "sessionId": load_id}, SETUP_TIMEOUT
                )
            else:
                result = await conn.request("session/new", setup_params, SETUP_TIMEOUT)
                sid = result.get("sessionId") if isinstance(result, dict) else None
                if not isinstance(sid, str) or not sid:
                    raise AcpProcessError("The agent did not return a session id")
                self._acp_session_id = sid
            self._close_streaming_items()
            tl.patch_state(acp_session_id=self._acp_session_id)
            self._apply_setup_result(result if isinstance(result, dict) else {})
            # Applied on a resume too: the user picked these in the wizard.
            # Cleared only once applied: a Restart after a failure retries them.
            await self._apply_initial_config(self._pending_config)
            self._pending_config = []
            self._ready.set()
            tl.patch_state(status="ready", urgent=True)
            busy_before, self._busy_before_ready = self._busy_before_ready, False
            if busy_before:
                self._on_agent_busy(True)
            initial_prompt, self._initial_prompt = self._initial_prompt, None
            if initial_prompt and not load_id:
                self._queue.insert(0, _QueuedPrompt(self._next_id("q"), initial_prompt, []))
            if self._external_turn:
                self._publish_queue()
            elif self._queue:
                self._start_next_prompt()
        except asyncio.CancelledError:
            raise
        except AcpRpcError as exc:
            self._fail_start(self._describe_rpc_error(exc, during_setup=True))
        except (AcpProcessError, TimeoutError, OSError) as exc:
            self._fail_start(str(exc) or "The agent did not respond")

    async def _ensure_trusted(self) -> bool:
        """Ask the user to trust the folder unless the agent already does."""
        trust = self._plan.trust
        if trust is None or self._trust_granted:
            return True
        # Not the default executor: running terminal sessions hold its threads.
        loop = asyncio.get_running_loop()
        if await loop.run_in_executor(PROBE_POOL, trust.is_trusted):
            return True
        self._timeline.patch_state(status="starting")
        outcome = await self._await_request(
            "permission",
            {
                "kind": "permission",
                "tool_call_id": None,
                "title": trust.prompt_title,
                "tool_kind": "other",
                "subtitle": self._plan.cwd,
                "content": [{"type": "text", "text": trust.prompt_text}],
                "options": [
                    {"id": "trust", "name": "Trust this folder", "kind": "allow_once"},
                    {"id": "decline", "name": "Don't trust", "kind": "reject_once"},
                ],
            },
            None,
        )
        selected = outcome.get("outcome") if isinstance(outcome, dict) else None
        if not isinstance(selected, dict) or selected.get("optionId") != "trust":
            self._fail_start("The folder is not trusted, so the session did not start. Use Restart to choose again.")
            return False
        # Covers a Restart of this session even when the trust is not saved.
        self._trust_granted = True
        try:
            await loop.run_in_executor(PROBE_POOL, trust.grant)
        except (OSError, ValueError) as exc:
            logger.warning("chat.trust_grant_failed session=%s: %s", self.session_id, exc)
        return True

    def _fail_start(self, message: str) -> None:
        logger.warning("chat.start_failed session=%s: %s", self.session_id, message)
        if self._timeline.state.get("status") == "error":
            return
        self._timeline.upsert({"id": self._next_id("err"), "type": "error", "text": message})
        self._timeline.patch_state(status="error", error=message, urgent=True)

    def _describe_rpc_error(self, exc: AcpRpcError, *, during_setup: bool = False) -> str:
        if exc.code == ERR_AUTH_REQUIRED:
            return (
                f"{self._agent_title} is not signed in on the PC. Sign in there "
                "(run it once in a terminal), then tap Restart."
            )
        detail = exc.message
        if isinstance(exc.data, dict):
            extra = exc.data.get("details") or exc.data.get("message")
            if isinstance(extra, str) and extra and extra not in detail:
                detail = f"{detail}: {extra}"
        if during_setup:
            return f"The agent could not start the session: {detail}"
        return detail

    def _session_cap(self, name: str) -> bool:
        caps = self._agent_caps.get("sessionCapabilities")
        return isinstance(caps, dict) and caps.get(name) is not None

    def _apply_setup_result(self, result: dict[str, Any]) -> None:
        options = nz.config_options(result.get("configOptions"))
        self._uses_modes = False
        if not options:
            mode = nz.modes_as_config(result.get("modes"))
            if mode is not None:
                options = [mode]
                self._uses_modes = True
        self._timeline.patch_state(config=options)

    async def _apply_initial_config(self, intents: list[ConfigIntent]) -> None:
        """Apply the wizard's choices. A ``required`` one (a sandbox) that
        cannot be applied stops the start: the agent's default may allow more."""
        for intent in intents:
            option = self._match_config(intent)
            refused = f"Could not set the {intent.value} mode, so the session did not start"
            if option is None:
                if intent.required:
                    raise AcpProcessError(refused)
                continue
            if option["type"] == "boolean":
                if not isinstance(intent.value, bool) or option["value"] == intent.value:
                    continue
            else:
                allowed = {v["value"] for v in option["options"]}
                if intent.value not in allowed:
                    if intent.required:
                        raise AcpProcessError(refused)
                    continue
                if option["value"] == intent.value:
                    continue
            try:
                await self._set_config(option, intent.value)
            except (AcpRpcError, TimeoutError) as exc:
                if intent.required:
                    raise AcpProcessError(f"{refused}: {exc}") from None
                logger.info(
                    "chat.initial_config_skipped session=%s option=%s: %s",
                    self.session_id,
                    option["id"],
                    exc,
                )

    def _match_config(self, intent: ConfigIntent) -> dict[str, Any] | None:
        options: list[dict[str, Any]] = self._timeline.state.get("config") or []
        if intent.option_id is not None:
            for opt in options:
                if opt["id"] == intent.option_id:
                    return opt
            return None
        for opt in options:
            if opt.get("category") == intent.category:
                return opt
        return None

    async def _set_config(self, option: dict[str, Any], value: str | bool) -> None:
        conn = self._require_conn()
        sid = self._acp_session_id
        if option["id"] == nz.MODE_CONFIG_ID:
            await conn.request("session/set_mode", {"sessionId": sid, "modeId": value}, CONFIG_TIMEOUT)
            self._replace_config_value(option["id"], value)
            return
        params: dict[str, Any] = {"sessionId": sid, "configId": option["id"], "value": value}
        if isinstance(value, bool):
            params["type"] = "boolean"
        result = await conn.request("session/set_config_option", params, CONFIG_TIMEOUT)
        options = nz.config_options(result.get("configOptions")) if isinstance(result, dict) else []
        if options:
            # Urgent: the client should see the new state before the result.
            self._timeline.patch_state(config=options, urgent=True)
        else:
            self._replace_config_value(option["id"], value)

    def _replace_config_value(self, option_id: str, value: Any) -> None:
        options = [
            {**opt, "value": value} if opt["id"] == option_id else opt
            for opt in self._timeline.state.get("config") or []
        ]
        self._timeline.patch_state(config=options, urgent=True)

    def _require_conn(self) -> AcpConnection:
        conn = self._conn
        if conn is None or not conn.alive or not self._acp_session_id:
            raise ChatCommandError("The agent is not running. Use Restart to start it again.")
        return conn

    def _cmd_prompt(self, cmd: dict[str, Any]) -> dict[str, Any]:
        text = cmd.get("text")
        text = text.strip() if isinstance(text, str) else ""
        images = self._validated_images(cmd.get("images"))
        if not text and not images:
            raise ChatCommandError("The message is empty")
        if len(text) > PROMPT_TEXT_MAX:
            raise ChatCommandError("The message is too long")
        status = self._timeline.state.get("status")
        if status in ("error", "stopped"):
            raise ChatCommandError("The agent is not running. Use Restart to start it again.")
        key = self._enqueue_or_send(text, images)
        return {"queued": key is not None, "queue_id": key}

    def _validated_images(self, raw: Any) -> list[dict[str, str]]:
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise ChatCommandError("Invalid images")
        if raw and not self._timeline.state.get("prompt_caps", {}).get("image"):
            raise ChatCommandError("This agent does not accept images")
        if len(raw) > IMAGES_MAX:
            raise ChatCommandError(f"Attach at most {IMAGES_MAX} images")
        images: list[dict[str, str]] = []
        for img in raw:
            if not isinstance(img, dict):
                raise ChatCommandError("Invalid image")
            mime = img.get("mime")
            data = img.get("data")
            if mime not in IMAGE_MIMES or not isinstance(data, str) or not data:
                raise ChatCommandError("Unsupported image type")
            if len(data) > IMAGE_B64_MAX:
                raise ChatCommandError("An image is too large")
            try:
                base64.b64decode(data, validate=True)
            except (binascii.Error, ValueError):
                raise ChatCommandError("An image is not valid base64") from None
            images.append({"mime": mime, "data": data})
        if sum(len(img["data"]) for img in images) > IMAGES_B64_TOTAL_MAX:
            raise ChatCommandError("The images are too large together")
        return images

    def _enqueue_or_send(self, text: str, images: list[dict[str, str]]) -> str | None:
        busy = (
            not self._ready.is_set()
            or self._own_turn_running()
            or self._external_turn
            or bool(self._queue)
        )
        if not busy:
            self._start_prompt(text, images)
            return None
        if len(self._queue) >= QUEUE_MAX:
            raise ChatCommandError("Too many queued messages")
        key = self._next_id("q")
        self._queue.append(_QueuedPrompt(key, text, images))
        self._publish_queue()
        if self._ready.is_set() and not self._own_turn_running() and not self._external_turn:
            self._start_next_prompt()
            return None
        return key

    def _own_turn_running(self) -> bool:
        return self._turn_task is not None and not self._turn_task.done()

    def _start_queue_if_idle(self) -> None:
        alive = self._conn is not None and self._conn.alive
        if (
            alive
            and not self._stopping
            and self._ready.is_set()
            and not self._own_turn_running()
            and not self._external_turn
        ):
            self._start_next_prompt()

    def _publish_queue(self) -> None:
        self._timeline.patch_state(
            queue=[{"id": q.key, "text": q.text[:500], "images": len(q.images)} for q in self._queue],
            urgent=True,
        )

    def _start_next_prompt(self) -> None:
        nxt = next((q for q in self._queue if not q.steering), None)
        if nxt is None:
            return
        self._queue.remove(nxt)
        self._publish_queue()
        self._start_prompt(nxt.text, nxt.images)

    def _start_prompt(self, text: str, images: list[dict[str, str]]) -> None:
        self._close_streaming_items()
        user_item = {
            "id": self._next_id("u"),
            "type": "user",
            "text": text,
            "attachments": [{"kind": "image", "mime": img["mime"]} for img in images],
            "at": _now_iso(),
        }
        self._timeline.upsert(user_item)
        blocks = _prompt_blocks(text, images)
        self._clear_attention()
        self._timeline.patch_state(
            status="running", turn_started_at=_now_iso(), cancelling=False, urgent=True
        )
        self._prompt_dispatched = False
        self._cancel_before_dispatch = False
        self._turn_task = asyncio.create_task(
            self._run_turn(blocks), name=f"chat-turn-{self.session_id}"
        )

    async def _run_turn(self, blocks: list[dict[str, Any]]) -> None:
        tl = self._timeline
        stop_reason: str | None = None
        try:
            conn = self._require_conn()
            if self._cancel_before_dispatch:
                # Stopped before the prompt reached the agent: never send it.
                stop_reason = "cancelled"
            else:
                self._prompt_dispatched = True
                result = await conn.request(
                    "session/prompt", {"sessionId": self._acp_session_id, "prompt": blocks}
                )
                if isinstance(result, dict) and isinstance(result.get("stopReason"), str):
                    stop_reason = result["stopReason"]
                else:
                    stop_reason = "end_turn"
        except asyncio.CancelledError:
            raise
        except ChatCommandError as exc:
            tl.upsert({"id": self._next_id("err"), "type": "error", "text": str(exc)})
        except AcpRpcError as exc:
            tl.upsert(
                {"id": self._next_id("err"), "type": "error", "text": self._describe_rpc_error(exc)}
            )
        except AcpProcessError as exc:
            if tl.state.get("status") != "error":
                tl.upsert({"id": self._next_id("err"), "type": "error", "text": str(exc)})
        finally:
            self._disarm_cancel_watchdog()
            self._close_streaming_items()
            self._resolve_all_requests_cancelled()
        if stop_reason == "cancelled":
            self._mark_running_tools("cancelled")
        if stop_reason and stop_reason != "end_turn":
            tl.upsert(
                {
                    "id": self._next_id("end"),
                    "type": "turn_end",
                    "stop_reason": stop_reason,
                    "text": STOP_REASON_TEXT.get(stop_reason, f"Stopped: {stop_reason}"),
                }
            )
        alive = self._conn is not None and self._conn.alive
        if any(not q.steering for q in self._queue) and alive and not self._stopping:
            tl.patch_state(status="ready", turn_started_at=None, cancelling=False)
            self._start_next_prompt()
            return
        tl.patch_state(
            status="ready" if alive else "error",
            turn_started_at=None,
            cancelling=False,
            urgent=True,
        )
        if alive and stop_reason not in (None, "cancelled") and not self._stopping:
            self._set_attention("turn_finished")

    async def _cmd_cancel(self) -> dict[str, Any]:
        running = self._own_turn_running()
        if not running and self._external_turn:
            conn = self._conn
            if conn is None or not conn.alive or not self._acp_session_id:
                return {"cancelled": False}
            conn.notify("session/cancel", {"sessionId": self._acp_session_id})
            self._resolve_all_requests_cancelled()
            self._timeline.patch_state(cancelling=True, urgent=True)
            self._arm_external_watchdog()
            return {"cancelled": True}
        if not running:
            return {"cancelled": False}
        conn = self._conn
        if not self._prompt_dispatched:
            self._cancel_before_dispatch = True
        elif conn is not None and conn.alive and self._acp_session_id:
            conn.notify("session/cancel", {"sessionId": self._acp_session_id})
            self._arm_cancel_watchdog(conn)
        self._resolve_all_requests_cancelled()
        self._timeline.patch_state(cancelling=True, urgent=True)
        return {"cancelled": True}

    def _arm_cancel_watchdog(self, conn: AcpConnection) -> None:
        """An agent that ignores session/cancel is stopped, so Restart can
        reopen the conversation instead of leaving the turn hanging."""
        if self._cancel_watchdog is not None:
            return
        turn = self._turn_task

        def fire() -> None:
            self._cancel_watchdog = None
            if (
                turn is self._turn_task
                and turn is not None
                and not turn.done()
                and conn is self._conn
                and conn.alive
                and not self._stopping
            ):
                logger.warning("chat.cancel_unanswered session=%s", self.session_id)
                self._cancel_forced = True
                asyncio.ensure_future(conn.close(grace=0.5))

        self._cancel_watchdog = asyncio.get_running_loop().call_later(CANCEL_GRACE, fire)

    def _disarm_cancel_watchdog(self) -> None:
        if self._cancel_watchdog is not None:
            self._cancel_watchdog.cancel()
            self._cancel_watchdog = None

    def _arm_external_watchdog(self) -> None:
        """A turn of another app that never reports its end after Stop (its
        app-server went away) must not hold this chat in "running"."""
        if self._cancel_watchdog is not None:
            return
        conn = self._conn

        def fire() -> None:
            self._cancel_watchdog = None
            if (
                self._external_turn
                and not self._stopping
                and conn is self._conn
                and conn is not None
                and conn.alive
            ):
                logger.warning("chat.external_cancel_unanswered session=%s", self.session_id)
                self._on_agent_busy(False)

        self._cancel_watchdog = asyncio.get_running_loop().call_later(CANCEL_GRACE, fire)

    async def _cmd_set_config(self, cmd: dict[str, Any]) -> dict[str, Any]:
        config_id = cmd.get("config_id")
        value = cmd.get("value")
        option = next(
            (o for o in self._timeline.state.get("config") or [] if o["id"] == config_id), None
        )
        if option is None:
            raise ChatCommandError("Unknown setting")
        if option["type"] == "boolean":
            if not isinstance(value, bool):
                raise ChatCommandError("Invalid value")
        elif not isinstance(value, str) or value not in {v["value"] for v in option["options"]}:
            raise ChatCommandError("Invalid value")
        self._require_conn()
        try:
            await self._set_config(option, value)
        except AcpRpcError as exc:
            raise ChatCommandError(self._describe_rpc_error(exc)) from None
        except TimeoutError:
            raise ChatCommandError("The agent did not answer in time") from None
        return {}

    def _cmd_respond(self, cmd: dict[str, Any]) -> dict[str, Any]:
        key = cmd.get("request_id")
        pending = self._requests.get(key) if isinstance(key, str) else None
        if pending is None or pending.future.done():
            raise ChatCommandError("This request was already answered")
        if pending.kind == "permission":
            option_id = cmd.get("option_id")
            options = pending.public.get("options") or []
            if not isinstance(option_id, str) or option_id not in {o["id"] for o in options}:
                raise ChatCommandError("Unknown option")
            pending.future.set_result(
                {"outcome": {"outcome": "selected", "optionId": option_id}}
            )
        else:
            action = cmd.get("action")
            if action == "accept":
                content, error = nz.coerce_form_values(pending.fields, cmd.get("values"))
                if error is not None:
                    raise ChatCommandError(error)
                pending.future.set_result({"action": "accept", "content": content})
            elif action in ("decline", "cancel"):
                pending.future.set_result({"action": action})
            else:
                raise ChatCommandError("Unknown action")
        self._drop_request(pending.key)
        return {}

    def _cmd_dequeue(self, cmd: dict[str, Any]) -> dict[str, Any]:
        key = cmd.get("queue_id")
        if any(q.key == key and q.steering for q in self._queue):
            raise ChatCommandError("This message is being sent now")
        before = len(self._queue)
        self._queue = [q for q in self._queue if q.key != key]
        if len(self._queue) == before:
            raise ChatCommandError("This message was already sent")
        self._publish_queue()
        return {}

    async def _cmd_steer(self, cmd: dict[str, Any]) -> dict[str, Any]:
        """Send a queued message into the running turn now (``_session/steering``)."""
        key = cmd.get("queue_id")
        queued = next((q for q in self._queue if q.key == key), None)
        if queued is None or queued.steering:
            raise ChatCommandError("This message was already sent")
        if not self._own_turn_running() and not self._external_turn:
            # Idle: the queue sends it as the next prompt anyway.
            self._start_queue_if_idle()
            return {"steered": False}
        if not self._steering:
            raise ChatCommandError("This agent cannot take a message during a turn")
        conn = self._require_conn()
        queued.steering = True
        failure: ChatCommandError | None = None
        result: Any = None
        try:
            result = await conn.request(
                STEER_METHOD,
                {
                    "sessionId": self._acp_session_id,
                    "prompt": _prompt_blocks(queued.text, queued.images),
                    "_meta": {"steering": {"idleBehavior": "promptRequired"}},
                },
                STEER_TIMEOUT,
            )
        except AcpRpcError as exc:
            failure = ChatCommandError(self._describe_rpc_error(exc))
        except TimeoutError:
            failure = ChatCommandError("The agent did not answer in time")
        finally:
            queued.steering = False
        if failure is not None:
            # The turn may have ended meanwhile and skipped this message.
            self._start_queue_if_idle()
            raise failure
        outcome = result.get("outcome") if isinstance(result, dict) else None
        if outcome == "injected":
            if queued in self._queue:
                self._queue.remove(queued)
                self._publish_queue()
            self._close_streaming_items()
            self._timeline.upsert(
                {
                    "id": self._next_id("u"),
                    "type": "user",
                    "text": queued.text,
                    "attachments": [{"kind": "image", "mime": img["mime"]} for img in queued.images],
                    "at": _now_iso(),
                }
            )
            return {"steered": True}
        # The turn ended meanwhile: the message stays first in the queue.
        self._start_queue_if_idle()
        if outcome == "promptRequired":
            return {"steered": False}
        raise ChatCommandError("The agent could not take the message now")

    async def _cmd_restart(self) -> dict[str, Any]:
        if self._stopping:
            raise ChatCommandError("The session is closing")
        if self._restarting or (
            self._bootstrap_task is not None and not self._bootstrap_task.done()
        ):
            raise ChatCommandError("The agent is already starting")
        if self._conn is not None and self._conn.alive and self._timeline.state.get("status") != "error":
            raise ChatCommandError("The agent is running")
        # Set before the first await: a second Restart (double tap, another
        # client) must not spawn a second adapter that nothing would close.
        self._restarting = True
        try:
            await self._restart()
        finally:
            self._restarting = False
        return {}

    async def _restart(self) -> None:
        self._cancel_forced = False
        if self._turn_task is not None and not self._turn_task.done():
            self._turn_task.cancel()
        self._resolve_all_requests_cancelled()
        # The reloaded session starts with the agent's defaults: re-apply
        # the values the user had chosen. A start that failed before applying
        # the launch choices retries those instead of the agent's defaults.
        if not self._pending_config:
            restored = [
                ConfigIntent(
                    opt["value"],
                    option_id=opt["id"],
                    category=opt.get("category"),
                    required=opt.get("category") in self._required_categories,
                )
                for opt in self._timeline.state.get("config") or []
                if isinstance(opt, dict) and "value" in opt
            ]
            # A required option the agent no longer lists falls back to the
            # launch choice, which then fails closed if it cannot be applied.
            covered = {i.category for i in restored if i.required}
            restored += [
                i for i in self._plan.initial_config
                if i.required and i.category not in covered
            ]
            self._pending_config = restored
        if self._conn is not None:
            await self._conn.close(grace=0.5)
        load_id = self._acp_session_id or self._load_session_id
        # Before the first initialize the capabilities are unknown: keep the
        # resume intent (bootstrap fails clearly if the agent cannot load).
        can_load = load_id is not None and (
            bool(self._agent_caps.get("loadSession")) or not self._caps_known
        )
        self._ready.clear()
        if can_load:
            # The reload replays the conversation, plan included.
            self._timeline.reset()
            self._close_streaming_items()
            self._drop_consoles()
            self._timeline.patch_state(plan=[])
        self._timeline.patch_state(status="starting", error=None, requests=[], urgent=True)
        self._external_turn = False
        self._busy_before_ready = False
        self._disarm_cancel_watchdog()
        if not await self._spawn_with_install():
            return
        self._exit_reason = None
        self._clear_attention()
        self._bootstrap_task = asyncio.create_task(
            self._bootstrap(load_id=load_id if can_load else None),
            name=f"chat-bootstrap-{self.session_id}",
        )

    def _on_notification(self, method: str, params: Any) -> None:
        if method != "session/update" or not isinstance(params, dict):
            return
        sid = params.get("sessionId")
        if self._acp_session_id is not None and sid != self._acp_session_id:
            return
        update = params.get("update")
        if isinstance(update, dict):
            self._apply_update(update)

    def _apply_update(self, update: dict[str, Any]) -> None:
        tl = self._timeline
        kind = update.get("sessionUpdate")
        if kind == "session_info_update":
            busy = nz.thread_busy(update)
            if busy is not None:
                self._on_agent_busy(busy)
        if kind == "agent_message_chunk":
            self._append_message("agent", update)
        elif kind == "agent_thought_chunk":
            self._append_message("thought", update)
        elif kind == "user_message_chunk":
            self._append_user_chunk(update)
        elif kind in ("tool_call", "tool_call_update"):
            self._apply_tool_update(update, create=kind == "tool_call")
        elif kind == "plan":
            tl.patch_state(plan=nz.plan_entries(update.get("entries")))
        elif kind == "available_commands_update":
            tl.patch_state(commands=nz.commands(update.get("availableCommands")))
        elif kind == "current_mode_update":
            mode = update.get("currentModeId")
            if isinstance(mode, str):
                self._sync_mode(mode)
        elif kind == "config_option_update":
            options = nz.config_options(update.get("configOptions"))
            if options:
                self._uses_modes = False
                tl.patch_state(config=options)
        elif kind == "session_info_update":
            if "title" in update:
                title = update.get("title")
                self._set_title(title if isinstance(title, str) and title.strip() else None)
        elif kind == "usage_update":
            usage = nz.usage(update)
            if usage is not None:
                previous = tl.state.get("usage")
                if usage["cost"] is None and isinstance(previous, dict):
                    # Cost is optional per update; keep the last known one.
                    usage["cost"] = previous.get("cost")
                tl.patch_state(usage=usage)
        elif kind == "notice":
            title = update.get("title")
            if isinstance(title, str) and title.strip():
                severity = update.get("severity")
                tl.upsert(
                    {
                        "id": self._next_id("n"),
                        "type": "notice",
                        "severity": severity if isinstance(severity, str) else "info",
                        "title": title[:300],
                        "text": (update.get("description") or "")[:2000]
                        if isinstance(update.get("description"), str)
                        else "",
                    }
                )
        elif kind == "compaction_update":
            self._apply_compaction(update)
        elif kind == "compaction_summary_chunk":
            cid = update.get("compactionId")
            part = nz.content_block_part(update.get("content"))
            if isinstance(cid, str) and part and part.get("type") == "text":
                item = tl.get(f"cmp-{cid}")
                if item is not None:
                    tl.append_text(item["id"], part["text"])

    def _on_agent_busy(self, busy: bool) -> None:
        """A turn this session did not start (another client of a shared
        host): show it as running, and hold queued messages until it ends."""
        if not self._ready.is_set():
            # Joining a session while another app's turn runs.
            self._busy_before_ready = busy
            return
        if self._own_turn_running():
            return
        tl = self._timeline
        if busy and not self._external_turn:
            self._external_turn = True
            self._close_streaming_items()
            tl.upsert(
                {
                    "id": self._next_id("n"),
                    "type": "notice",
                    "severity": "info",
                    "title": "Message sent from another app on your PC",
                    "text": "",
                }
            )
            tl.patch_state(status="running", turn_started_at=_now_iso(), cancelling=False, urgent=True)
        elif not busy and self._external_turn:
            self._external_turn = False
            self._disarm_cancel_watchdog()
            self._close_streaming_items()
            tl.patch_state(status="ready", turn_started_at=None, cancelling=False, urgent=True)
            if self._queue:
                self._start_next_prompt()

    def _append_message(self, role: str, update: dict[str, Any]) -> None:
        part = nz.content_block_part(update.get("content"))
        if part is None or part.get("type") != "text":
            return
        text = part["text"]
        message_id = update.get("messageId") if isinstance(update.get("messageId"), str) else None
        tl = self._timeline
        if role == "agent":
            current, last_mid = self._current_agent_item, self._last_agent_message_id
        else:
            current, last_mid = self._current_thought_item, self._last_thought_message_id
        if current is not None and (message_id is None or message_id == last_mid) and tl.get(current):
            tl.append_text(current, text)
            return
        # A new item of one role ends the open item of the other, so text
        # after a thought lands below it.
        if role == "agent":
            self._current_thought_item = None
        else:
            self._current_agent_item = None
        self._current_user_item = None
        item_id = self._next_id("m" if role == "agent" else "t")
        tl.upsert({"id": item_id, "type": role, "text": text})
        if role == "agent":
            self._current_agent_item, self._last_agent_message_id = item_id, message_id
        else:
            self._current_thought_item, self._last_thought_message_id = item_id, message_id

    def _append_user_chunk(self, update: dict[str, Any]) -> None:
        if self._turn_task is not None and not self._turn_task.done():
            # An echo of the prompt this client just sent: already shown.
            return
        part = nz.content_block_part(update.get("content"))
        if part is None:
            return
        tl = self._timeline
        message_id = update.get("messageId") if isinstance(update.get("messageId"), str) else None
        current = self._current_user_item
        item = tl.get(current) if current else None
        if item is not None and message_id is not None and message_id != self._last_user_message_id:
            item = None
        self._last_user_message_id = message_id
        if item is None:
            self._current_agent_item = self._current_thought_item = None
            item = {"id": self._next_id("u"), "type": "user", "text": "", "attachments": []}
            self._current_user_item = item["id"]
            tl.upsert(item)
        if part["type"] == "text":
            tl.append_text(item["id"], part["text"])
        elif part["type"] == "image":
            item["attachments"] = [*item["attachments"], {"kind": "image", "mime": part["mime"]}]
            tl.touch(item["id"])
        elif part["type"] in ("link", "resource"):
            item["attachments"] = [*item["attachments"], {"kind": "file", "name": part.get("name")}]
            tl.touch(item["id"])

    def _apply_tool_update(self, update: dict[str, Any], *, create: bool) -> None:
        tool_id = update.get("toolCallId")
        if not isinstance(tool_id, str) or not tool_id:
            return
        tl = self._timeline
        item = tl.get(tool_id)
        is_new = item is None
        if item is None:
            item = nz.new_tool_item(tool_id)
        nz.merge_tool_update(item, update)
        deferred = self._apply_console(tool_id, item, update)
        self._current_agent_item = self._current_thought_item = self._current_user_item = None
        # A finished tool is sent at once, except in a session/load replay,
        # where one frame per tool would flood the event queue.
        terminal = item["status"] in nz.TERMINAL_TOOL_STATUSES
        urgent = terminal and tl.state.get("status") != "loading"
        if is_new or create:
            tl.upsert(item, urgent=urgent)
        elif not (deferred and update.keys() <= _OUTPUT_ONLY_KEYS):
            # An output chunk alone changes nothing until its render runs.
            tl.touch(tool_id, urgent=urgent)
        if terminal:
            for pending in list(self._requests.values()):
                if pending.tool_call_id == tool_id and not pending.future.done():
                    pending.future.set_result(self._cancel_outcome(pending))
                    self._drop_request(pending.key)

    def _apply_console(self, tool_id: str, item: dict[str, Any], update: dict[str, Any]) -> bool:
        """Turn terminal output (``_meta.terminal_output*``) into a text part.

        A ``terminal`` content part names a client-side terminal, which
        Mobaide never creates; the output it would show goes into the item.
        Returns True when the new output waits for a delayed render.
        """
        text, replaces, exit_code, exited = nz.terminal_meta(update)
        console = self._consoles.get(tool_id)
        if console is None and text is None and not exited:
            if any(p.get("type") == "terminal" for p in item["content"]):
                item["content"] = [p for p in item["content"] if p.get("type") != "terminal"]
            return False
        if console is None:
            console = self._consoles[tool_id] = _Console()
            while len(self._consoles) > CONSOLE_TOOLS_MAX:
                _, old = self._consoles.popitem(last=False)
                if old.render is not None:
                    old.render.cancel()
        else:
            self._consoles.move_to_end(tool_id)
            if console.final and text is None:
                # Finished: keep the shown output, nothing new to render.
                self._strip_terminal_parts(item, console)
                return False
            console.final = False
        if text is not None:
            output = text if replaces else console.output + text
            console.output = output[-nz.CONSOLE_KEEP_MAX:]
        if exited:
            console.exit_code = exit_code
        finished = exited or item["status"] in nz.TERMINAL_TOOL_STATUSES
        if finished or text is None:
            self._render_console(tool_id, item, console, live=not finished)
            return False
        if console.render is None:
            # Re-sending the whole item on every chunk would flood the clients.
            console.render = asyncio.get_running_loop().call_later(
                CONSOLE_RENDER_INTERVAL, self._render_console_later, tool_id
            )
        else:
            self._strip_terminal_parts(item, console)
        return True

    def _render_console_later(self, tool_id: str) -> None:
        console = self._consoles.get(tool_id)
        if console is not None:
            console.render = None
        item = self._timeline.get(tool_id)
        if console is None or item is None:
            return
        live = item["status"] not in nz.TERMINAL_TOOL_STATUSES and console.exit_code is None
        self._render_console(tool_id, item, console, live=live)
        self._timeline.touch(tool_id)

    def _render_console(self, tool_id: str, item: dict[str, Any], console: _Console, *, live: bool) -> None:
        if console.render is not None and not live:
            console.render.cancel()
            console.render = None
        part = nz.console_part(console.output, live=live, exit_code=console.exit_code)
        parts = self._strip_terminal_parts(item, console)
        item["content"] = [*parts[: nz.TOOL_PARTS_MAX - 1], part] if part is not None else parts
        console.part = part
        console.final = not live
        if console.final:
            # The part holds what is shown; the raw output is no longer needed.
            console.output = ""

    @staticmethod
    def _strip_terminal_parts(item: dict[str, Any], console: _Console) -> list[dict[str, Any]]:
        """Drop ``terminal`` parts; keep the last rendered output at the end
        until the next render. Returns the parts without that output."""
        parts = [p for p in item["content"] if p.get("type") != "terminal" and p is not console.part]
        if console.part is not None:
            item["content"] = [*parts[: nz.TOOL_PARTS_MAX - 1], console.part]
        else:
            item["content"] = parts
        return parts

    def _apply_compaction(self, update: dict[str, Any]) -> None:
        cid = update.get("compactionId")
        if not isinstance(cid, str):
            return
        tl = self._timeline
        item_id = f"cmp-{cid}"
        item = tl.get(item_id)
        if item is None:
            item = {"id": item_id, "type": "compaction", "status": "in_progress", "text": ""}
        status = update.get("status")
        if isinstance(status, str):
            item["status"] = status
        if "summary" in update:
            summary = update.get("summary")
            texts = []
            if isinstance(summary, list):
                for block in summary:
                    part = nz.content_block_part(block)
                    if part and part.get("type") == "text":
                        texts.append(part["text"])
            item["text"] = nz.cap_text("\n\n".join(texts), nz.ITEM_TEXT_MAX)[0]
        tl.upsert(item)

    def _sync_mode(self, mode: str) -> None:
        options = self._timeline.state.get("config") or []
        changed = False
        new_options = []
        for opt in options:
            if opt.get("category") == "mode" and opt["type"] == "select":
                if mode in {v["value"] for v in opt["options"]} and opt["value"] != mode:
                    opt = {**opt, "value": mode}
                    changed = True
            new_options.append(opt)
        if changed:
            self._timeline.patch_state(config=new_options)

    def _drop_consoles(self) -> None:
        for console in self._consoles.values():
            if console.render is not None:
                console.render.cancel()
        self._consoles.clear()

    def _close_streaming_items(self) -> None:
        self._current_agent_item = None
        self._current_thought_item = None
        self._current_user_item = None
        self._last_agent_message_id = None
        self._last_thought_message_id = None

    def _mark_running_tools(self, status: str) -> None:
        tl = self._timeline
        for item in tl.items():
            if item.get("type") == "tool" and item.get("status") in ("pending", "in_progress"):
                item["status"] = status
                tl.touch(item["id"])

    async def _on_request(self, method: str, params: Any) -> Any:
        if not isinstance(params, dict):
            raise AcpRpcError(-32602, "Invalid params")
        if method == "session/request_permission":
            return await self._permission_request(params)
        if method == "elicitation/create":
            return await self._elicitation_request(params)
        raise AcpRpcError(ERR_METHOD_NOT_FOUND, f"Method not found: {method}")

    async def _permission_request(self, params: dict[str, Any]) -> Any:
        options = nz.permission_options(params.get("options"))
        raw_call = params.get("toolCall")
        tool_call: dict[str, Any] = raw_call if isinstance(raw_call, dict) else {}
        raw_tool_id = tool_call.get("toolCallId")
        tool_id = raw_tool_id if isinstance(raw_tool_id, str) else None
        if tool_id:
            self._apply_tool_update(dict(tool_call), create=False)
        if not options:
            return {"outcome": {"outcome": "cancelled"}}
        item = self._timeline.get(tool_id) if tool_id else None
        if item is None:
            item = nz.new_tool_item("preview")
            nz.merge_tool_update(item, tool_call)
        public = {
            "kind": "permission",
            "tool_call_id": tool_id,
            "title": item["title"],
            "tool_kind": item["kind"],
            "subtitle": item.get("subtitle"),
            "content": item["content"][:3],
            "options": options,
        }
        return await self._await_request("permission", public, tool_id)

    async def _elicitation_request(self, params: dict[str, Any]) -> Any:
        if params.get("mode") not in (None, "form"):
            raise AcpRpcError(-32602, "Only form elicitation is supported")
        fields = nz.form_fields(params.get("requestedSchema"))
        if fields is None:
            return {"action": "decline"}
        message = params.get("message") if isinstance(params.get("message"), str) else ""
        tool_id = params.get("toolCallId") if isinstance(params.get("toolCallId"), str) else None
        public = {
            "kind": "form",
            "tool_call_id": tool_id,
            "title": message[:2000] or "The agent has a question",
            "fields": fields,
        }
        return await self._await_request("form", public, tool_id, fields=fields)

    async def _await_request(
        self,
        kind: str,
        public: dict[str, Any],
        tool_id: str | None,
        *,
        fields: list[dict[str, Any]] | None = None,
    ) -> Any:
        key = self._next_id("r")
        public = {"id": key, **public}
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        pending = _PendingRequest(key, kind, future, public, tool_id, fields or [])
        self._requests[key] = pending
        self._publish_requests()
        self._set_attention("awaiting_permission")
        try:
            return await future
        finally:
            self._drop_request(key)

    def _publish_requests(self) -> None:
        self._timeline.patch_state(
            requests=[p.public for p in self._requests.values()], urgent=True
        )

    def _drop_request(self, key: str) -> None:
        if self._requests.pop(key, None) is None:
            return
        self._publish_requests()
        if not self._requests and self._attention == "awaiting_permission":
            self._clear_attention()

    @staticmethod
    def _cancel_outcome(pending: _PendingRequest) -> Any:
        if pending.kind == "permission":
            return {"outcome": {"outcome": "cancelled"}}
        return {"action": "cancel"}

    def _resolve_all_requests_cancelled(self) -> None:
        for pending in list(self._requests.values()):
            if not pending.future.done():
                pending.future.set_result(self._cancel_outcome(pending))
        if self._requests:
            self._requests.clear()
            self._publish_requests()
            if self._attention == "awaiting_permission":
                self._clear_attention()

    def _on_exit(self, returncode: int | None) -> None:
        # A Restart closes the old process itself: that exit is not news.
        if self._stopping or self._restarting:
            return
        self._external_turn = False
        self._disarm_cancel_watchdog()
        conn = self._conn
        message = "The agent process exited"
        if self._cancel_forced:
            message = "The agent did not stop, so Mobaide closed it. Use Restart to continue."
        elif conn is not None:
            tail = conn.stderr_tail().splitlines()
            message = (
                f"The agent process exited with code {returncode}"
                if returncode is not None
                else message
            )
            if tail:
                message = f"{message}: {tail[-1][:300]}"
        logger.warning("chat.agent_exited session=%s: %s", self.session_id, message)
        self._resolve_all_requests_cancelled()
        self._mark_running_tools("failed")
        if self._timeline.state.get("status") != "error":
            self._timeline.upsert({"id": self._next_id("err"), "type": "error", "text": message})
        self._timeline.patch_state(status="error", error=message, urgent=True)
        if not self._cancel_forced:
            self._set_attention("ended", exit_reason="process_exited")

    def _set_title(self, title: str | None) -> None:
        if title is not None:
            title = " ".join(title.split())[:200]
        self._timeline.patch_state(title=title)
        if title != self._title:
            self._title = title
            self._put_event(SessionEvent("title", self.session_id))

    def _set_attention(self, attention: str, exit_reason: str | None = None) -> None:
        changed = attention != self._attention or (
            exit_reason is not None and exit_reason != self._exit_reason
        )
        self._attention = attention
        if exit_reason is not None:
            self._exit_reason = exit_reason
        if changed:
            self._put_event(SessionEvent("attention", self.session_id, attention=attention))

    def _clear_attention(self) -> None:
        if self._attention is None:
            return
        self._attention = None
        self._put_event(SessionEvent("attention", self.session_id, attention=None))

    def _emit_frame(self, payload: bytes) -> None:
        self._put_event(SessionEvent("chat", self.session_id, data=payload))

    def _send_result(
        self, cmd_id: str | None, *, ok: bool, error: str | None = None, data: Any = None
    ) -> None:
        if cmd_id is None:
            return
        body: dict[str, Any] = {"t": "result", "id": cmd_id, "ok": ok}
        if error is not None:
            body["error"] = error
        if data:
            body["data"] = data
        self._emit_frame(orjson.dumps(body))

    def _put_event(self, event: SessionEvent) -> None:
        event.timestamp = time.time()
        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                self._event_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._event_queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def _next_id(self, prefix: str) -> str:
        # The colon keeps generated ids apart from agent tool call ids.
        self._counter += 1
        return f"{prefix}:{self._counter}"


_OUTPUT_ONLY_KEYS = frozenset({"sessionUpdate", "toolCallId", "_meta"})


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _prompt_blocks(text: str, images: list[dict[str, str]]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if text:
        blocks.append({"type": "text", "text": text})
    for img in images:
        blocks.append({"type": "image", "mimeType": img["mime"], "data": img["data"]})
    return blocks
