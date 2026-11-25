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
import math
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import orjson

from mobaide import __version__
from mobaide.core.attention_dedupe import SharedTurnDedupe, real_cwd
from mobaide.core.attention_gate import AttentionGate, HookKind, is_subagent_call
from mobaide.core.hook_settings import chat_hook_settings
from mobaide.core.models import (
    SessionConfig,
    SessionEvent,
    SessionInfo,
    SessionKind,
    SessionState,
)

from . import cursor_ext as cx
from . import normalize as nz
from . import subagents as sa
from . import wake as wk
from .agents import ChatLaunchPlan, ConfigIntent
from .install import INSTALL_POOL, PROBE_POOL, AdapterInstallError
from .presence import PresenceHold
from .rpc import (
    CANCEL_REQUEST_METHOD,
    ERR_AUTH_REQUIRED,
    ERR_METHOD_NOT_FOUND,
    AcpConnection,
    AcpProcessError,
    AcpRpcError,
)
from .signin import SignInError, SignInFlow
from .timeline import ChatTimeline

logger = logging.getLogger(__name__)

EVENT_QUEUE_MAXSIZE = 256
INITIALIZE_TIMEOUT = 120.0
SETUP_TIMEOUT = 180.0
CONFIG_TIMEOUT = 20.0
TITLE_LOOKUP_TIMEOUT = 20.0
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
# claude-agent-acp pushes the login it found; ``kind: "none"`` = signed out.
AUTH_STATUS_METHOD = "_auth/status_update"
LOGIN_COMMAND = "login"
# Agents have no ``/clear`` over ACP (claude-agent-acp hides it): unless the
# agent lists its own, the chat starts a new conversation for it.
CLEAR_COMMAND = "clear"
# Command output of a running tool is re-rendered at most this often.
CONSOLE_RENDER_INTERVAL = 0.3
CONSOLE_TOOLS_MAX = 64
# Copilot follows a file tool with a message that only names the file again.
TOOL_PATH_NOTE = "Info: "
ACP_PROTOCOL_VERSION = 1
# A Claude usage limit (claude-agent-acp ≥ 0.107 sends its reset time): a
# continue waits first in the queue and goes out this long after the reset.
LIMIT_WAIT_MARGIN = 60.0
# Only a reset at most this far away is waited for (the five-hour window);
# a weekly limit only shows its reset time.
LIMIT_WAIT_MAX = 6 * 3600.0
# The wall clock is read this often: the monotonic clock behind
# asyncio.sleep stops while the PC sleeps on macOS.
LIMIT_WAIT_TICK = 30.0
# Automatic continues in a row without a prompt from the user (Claude Code's
# own terminal rule).
AUTO_CONTINUE_MAX = 3
AUTO_CONTINUE_PROMPT = "Continue from where you stopped. The usage limit has reset."
# Timeline status → the session row's ``activity``; the other statuses
# (``starting``, ``loading``, ``error``, ``stopped``) are no turn at all.
_ACTIVITY_BY_STATUS = {"running": "working", "ready": "idle"}
# The hooks a chat session reads (see ``hook_settings.chat_hook_settings``).
_TURN_HOOKS = frozenset(
    {HookKind.PROMPT_SUBMIT, HookKind.TOOL, HookKind.STOP, HookKind.STOP_FAILURE}
)
# Quiet time after a ``Stop`` hook with nothing in flight before the session
# reads idle and alerts. It absorbs a task notification that is already on its
# way, which would start the next turn a moment later. Shorter than a terminal
# session's window: the chat row shows "working" until it ends.
CHAT_ATTENTION_QUIET_SECONDS = 2.0
# An agent with no hooks (see ``ChatLaunchPlan.wakes_after_turn``): a turn it
# started by itself ends after this long with no content update. Long, as a
# woken model can think quietly for a while: a late alert beats a missed one.
WAKE_QUIET_SECONDS = 20.0
# Content updates this soon after a prompt's answer still belong to that turn.
WAKE_GRACE_SECONDS = 1.0
# A self turn with no content update for this long ends even while one of its
# tools still runs (a dev server, a watcher) or Copilot sent no end: it must
# not hold the queue for good.
WAKE_MAX_SILENT_SECONDS = 600.0

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
    # The child session that asked (see :mod:`.subagents`).
    subagent_id: str | None = None


@dataclass
class _QueuedPrompt:
    key: str
    text: str
    images: list[dict[str, str]]
    # A steer request for it is in flight: the queue must not send it too.
    steering: bool = False
    # The agent mode to switch to before the message is sent.
    mode: str | None = None
    # Wall-clock time before which the queue must not send it: an automatic
    # continue that waits for a usage limit to reset. It holds the whole queue.
    not_before: float | None = None
    # Mobaide sent it, not the user: it does not reset the automatic-continue count.
    auto: bool = False


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
        attention_dedupe: SharedTurnDedupe | None = None,
    ) -> None:
        self._config = config
        self._plan = plan
        self._attention_dedupe = attention_dedupe
        self._real_cwd: str | None = None
        self._agent_title = agent_title
        self._load_session_id = load_session_id
        self._state = SessionState.STARTING
        self._created_at = datetime.now(UTC)
        self._event_queue: asyncio.Queue[SessionEvent] = asyncio.Queue(
            maxsize=EVENT_QUEUE_MAXSIZE
        )
        self._activity: str | None = None
        # Claude Code hooks report what the ``session/prompt`` answer cannot:
        # the work a turn leaves behind (a workflow, a sub-agent, a shell) and
        # the turns the agent starts by itself. None for an agent with no hooks.
        self._gate: AttentionGate | None = None
        if plan.hook_port is not None:
            quiet = config.attention_quiet_seconds
            self._gate = AttentionGate(
                on_alert=self._on_gate_alert,
                on_activity=self._on_gate_activity,
                is_running=lambda: self._state == SessionState.RUNNING,
                current_attention=lambda: self._attention,
                quiet_seconds=CHAT_ATTENTION_QUIET_SECONDS if quiet is None else quiet,
            )
        # False until this agent process sends its first hook: a CLI that runs
        # no hooks (an old one, a policy that blocks them) keeps the
        # prompt-answer behaviour.
        self._hooks_live = False
        # What the gate last reported, while hooks are live.
        self._gate_activity: str | None = None
        # An agent with no hooks that finished background work can wake after
        # a prompt's answer: the turns it then runs, and the work in flight.
        self._wakes = plan.wakes_after_turn and self._gate is None
        # Ends a self turn after its quiet window, or sends the alert held for
        # background work once that work ended and no woken turn followed.
        self._wake_timer: asyncio.TimerHandle | None = None
        self._wake_grace_until = 0.0
        # The last content update of the self turn that runs now.
        self._wake_last_update = 0.0
        # Copilot's ``assistant.turn_start`` opened the self turn that runs
        # now, so its ``session.idle`` will end it.
        self._wake_turn_signalled = False
        # Tool calls updated during the self turn that runs now: one still
        # running keeps the turn open.
        self._wake_tools: set[str] = set()
        # OpenCode subagents in flight: child session id → title.
        self._children: dict[str, str | None] = {}
        # A finished turn's "turn finished" alert waits for those subagents.
        self._alert_held = False
        # ACP subagent sessions of this conversation (``ChatLaunchPlan.subagents``).
        self._subagents = sa.Subagents()
        # A ``session/load`` replays the conversation: subagent updates until
        # its reply are history, the ones after it are current.
        self._replaying = False
        # Copilot sends its session events: they start and end self turns.
        self._copilot_events = False
        # Copilot: the foreground settled, but attached background work (an
        # async shell) still holds the prompt, or the self turn, open.
        self._foreground_idle = False
        # The last ``Stop`` hook of the prompt that runs now. The gate gets it
        # at the prompt's answer: only the answer ends a turn this session sent
        # (a ``Stop`` hook can force the turn to continue), so no "turn
        # finished" alert may come while that turn still runs.
        self._turn_stop: dict[str, Any] | None = None
        # The agent started its next turn (a task notification that was
        # already waiting) after that ``Stop`` and before the prompt's answer.
        self._turn_reopened = False
        self._timeline = ChatTimeline(self._emit_frame, on_status=self._on_timeline_status)
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
        self._last_tool_id: str | None = None
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
        # The same for a launch value set by option id (Junie's ``mode``,
        # which has no category).
        self._required_option_ids = {
            i.option_id for i in plan.initial_config if i.required and i.option_id
        }
        self._initial_prompt: str | None = plan.initial_prompt
        self._steering = False
        # A turn this session did not start: another client of a shared host
        # sent it (see thread_busy), or the agent started it by itself.
        self._external_turn = False
        # That turn is the agent's own (a task notification, a scheduled
        # wakeup): hooks report its start and end. Read only with _external_turn.
        self._self_turn = False
        # The last busy report that came while the session was opening.
        self._busy_before_ready = False
        self._consoles: collections.OrderedDict[str, _Console] = collections.OrderedDict()
        self._todos = cx.TodoList()
        # Replayed Cursor plans whose text is already shown: tool call id → title.
        self._stored_plans: dict[str, str] = {}
        # Code Mode scripts of tool calls still running: tool call id → code.
        # Later updates of the call do not repeat its input.
        self._scripts: dict[str, str] = {}
        # Running commands whose latest output waits for its next frame.
        self._output_touches: dict[str, asyncio.TimerHandle] = {}
        self._presence_hold: PresenceHold | None = None
        # The conversation was open in another app when this session took it.
        self._presence_conflict = False
        # That app closed it, maybe after turns this process never saw: the
        # next message reloads the conversation before it goes out.
        self._presence_stale = False
        # A reload after that app closed it: say why once the chat is back.
        self._reload_notice = False
        self._sign_in = SignInFlow(
            plan.sign_in,
            agent_title,
            request_id="sign-in",
            on_change=self._publish_requests,
            on_signed_in=self._on_signed_in,
            choose_text=plan.sign_in_prompt,
        )
        self._signed_in_notice = False
        # The last start failed for a missing login.
        self._start_auth_failed = False
        # The error item of the last failed start; the next start drops it.
        self._start_error_item: str | None = None
        # A sign-in finished while a turn or a restart ran.
        self._resume_pending = False
        # Messages the agent refused for a missing login: sent again once signed in.
        self._auth_retry: list[_QueuedPrompt] = []
        # A turn has finished, so the agent has stored the conversation.
        self._has_turns = False
        # Automatic continues sent since the user's last prompt.
        self._auto_continues = 0
        # Releases the queued continue once the usage limit has reset.
        self._limit_task: asyncio.Task[None] | None = None
        # The slash commands the agent itself lists.
        self._agent_commands: set[str] = set()
        self._timeline.state["commands"] = self._with_own_commands([])

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
            activity=self._activity if self._state == SessionState.RUNNING else None,
            worktree_branch=self._config.worktree_branch,
            worktree_workspace=self._config.worktree_workspace,
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
            notice_id = self._next_id("n")
            self._timeline.upsert(
                {
                    "id": notice_id,
                    "type": "notice",
                    "severity": "info",
                    "title": f"Setting up the {self._agent_title} chat on your PC",
                    "text": "This happens once and can take a few minutes.",
                }
            )
            try:
                argv = await asyncio.get_running_loop().run_in_executor(INSTALL_POOL, ensure)
            except AdapterInstallError as exc:
                self._timeline.remove(notice_id)
                self._fail_start(str(exc))
                return False
            except Exception as exc:
                logger.exception("chat.adapter_install_failed session=%s", self.session_id)
                self._timeline.remove(notice_id)
                self._fail_start(f"Could not set up the chat: {exc}")
                return False
            # The setup is done: the notice must not stay above the conversation.
            self._timeline.remove(notice_id, urgent=True)
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
        self._cancel_wake_timer()
        if self._plan.shared_host:
            await self._stop_own_turn()
        for task in (self._bootstrap_task, self._turn_task, *self._command_tasks):
            if task is not None and not task.done():
                task.cancel()
        self._sign_in.close()
        if self._limit_task is not None:
            self._limit_task.cancel()
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
        self._release_presence()
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
        if op == "new_conversation":
            # Claimed before the task runs: a prompt in the next frame must
            # wait for the new conversation, not start on the closing process.
            try:
                self._claim_new_conversation()
            except ChatCommandError as exc:
                self._send_result(cmd_id, ok=False, error=str(exc))
                return
        if op in ("set_config", "restart", "new_conversation", "steer", "sign_in"):
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
        if op == "new_conversation":
            return await self._cmd_new_conversation()
        if op == "steer":
            return await self._cmd_steer(cmd)
        if op == "sign_in":
            return await self._cmd_sign_in(cmd)
        if op == "cancel_subagent":
            return self._cmd_cancel_subagent(cmd)
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
        self._drop_start_error()
        tl.patch_state(status="starting", error=None, urgent=True)
        self._todos.clear()
        self._stored_plans.clear()
        self._scripts.clear()
        try:
            init = await conn.request(
                "initialize",
                {
                    "protocolVersion": ACP_PROTOCOL_VERSION,
                    "clientCapabilities": self._client_capabilities(),
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
            sdk_options = dict(self._plan.claude_sdk_options)
            if self._plan.hook_port is not None:
                # claude-agent-acp hands these settings to the CLI as its
                # ``--settings`` tier, for a new and for a loaded session.
                sdk_options["settings"] = chat_hook_settings(self.session_id, self._plan.hook_port)
            if sdk_options:
                setup_params["_meta"] = {"claudeCode": {"options": sdk_options}}
            if self._plan.additional_dirs and self._session_cap("additionalDirectories"):
                setup_params["additionalDirectories"] = list(self._plan.additional_dirs)
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
                self._replaying = True
                try:
                    result = await conn.request(
                        "session/load",
                        {**setup_params, "sessionId": load_id},
                        SETUP_TIMEOUT,
                        on_reply=self._end_replay,
                    )
                finally:
                    self._end_replay()
            else:
                result = await conn.request("session/new", setup_params, SETUP_TIMEOUT)
                sid = result.get("sessionId") if isinstance(result, dict) else None
                if not isinstance(sid, str) or not sid:
                    raise AcpProcessError("The agent did not return a session id")
                self._acp_session_id = sid
            self._close_streaming_items()
            tl.patch_state(acp_session_id=self._acp_session_id)
            setup_result = result if isinstance(result, dict) else {}
            self._apply_setup_result(setup_result)
            await self._hold_presence()
            pending = self._pending_config
            if self._plan.config_resolver is not None:
                pending = self._plan.config_resolver(setup_result, pending)
            # Applied on a resume too: the user picked these in the wizard.
            # Cleared only once applied: a Restart after a failure retries them.
            await self._apply_initial_config(pending)
            self._pending_config = []
            self._ready.set()
            tl.patch_state(status="ready", urgent=True)
            if load_id and self._title is None:
                self._run_aside(self._restore_title(conn, load_id))
            if self._start_auth_failed:
                # The start that needed the sign-in works now (signed in on the PC).
                self._start_auth_failed = False
                self._sign_in.withdraw()
            if self._reload_notice:
                self._reload_notice = False
                tl.upsert(
                    {
                        "id": self._next_id("n"),
                        "type": "notice",
                        "severity": "info",
                        "title": "Loaded the latest messages",
                        "text": (
                            "Another app on your PC had this conversation open and has closed "
                            "it. Mobaide opened it again here with the messages sent there."
                        ),
                    }
                )
            if self._signed_in_notice:
                self._signed_in_notice = False
                self._show_signed_in()
            busy_before, self._busy_before_ready = self._busy_before_ready, False
            if busy_before:
                self._on_agent_busy(True)
            initial_prompt, self._initial_prompt = self._initial_prompt, None
            if initial_prompt and not load_id:
                self._queue.insert(0, _QueuedPrompt(self._next_id("q"), initial_prompt, []))
            if self._external_turn:
                self._publish_queue()
            elif self._resume_if_pending():
                pass
            elif self._queue:
                self._start_next_prompt()
        except asyncio.CancelledError:
            raise
        except AcpRpcError as exc:
            self._fail_start(self._describe_rpc_error(exc, during_setup=True))
            if exc.code == ERR_AUTH_REQUIRED:
                self._start_auth_failed = True
                self._sign_in.offer()
        except (AcpProcessError, TimeoutError, OSError) as exc:
            self._fail_start(str(exc) or "The agent did not respond")

    def _client_capabilities(self) -> dict[str, Any]:
        caps = dict(CLIENT_CAPABILITIES)
        if self._plan.subagents:
            caps["subagents"] = {}
        if self._plan.client_meta:
            caps["_meta"] = dict(self._plan.client_meta)
        return caps

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

    async def _hold_presence(self) -> None:
        """Mark the conversation open, as the agent's own apps do, so they
        warn before a second app writes to it. When another app already has
        it open, say so once: both would overwrite each other's messages."""
        presence, sid = self._plan.presence, self._acp_session_id
        if presence is None or sid is None:
            return
        hold = self._presence_hold
        if hold is not None and hold.session_id == sid:
            return
        self._release_presence()
        loop = asyncio.get_running_loop()
        acquiring = loop.run_in_executor(PROBE_POOL, presence.acquire, sid)
        try:
            hold = await asyncio.shield(acquiring)
        except asyncio.CancelledError:
            # The thread still takes the lock: drop it once it has.
            acquiring.add_done_callback(_release_acquired)
            raise
        except Exception:
            logger.exception("chat.presence_failed session=%s", self.session_id)
            return
        if self._stopping:
            if hold is not None:
                hold.release()
            return
        self._presence_hold = hold
        if hold is not None:
            self._presence_conflict = False
            return
        if self._presence_conflict:
            return
        if await loop.run_in_executor(PROBE_POOL, presence.open_elsewhere, sid):
            self._presence_conflict = True
            self._timeline.upsert(
                {
                    "id": self._next_id("n"),
                    "type": "notice",
                    "severity": "warning",
                    "title": "This conversation is also open in another app on your PC",
                    "text": (
                        f"{self._agent_title} keeps a separate copy in each app, so messages "
                        "sent from both can overwrite each other. Close it in the other app "
                        "before you continue here."
                    ),
                }
            )

    def _release_presence(self) -> None:
        hold, self._presence_hold = self._presence_hold, None
        if hold is not None:
            hold.release()

    def _drop_start_error(self) -> None:
        """A new start answers the last one's failure: without this, the old
        error stays above a chat that works again."""
        stale, self._start_error_item = self._start_error_item, None
        if stale is not None:
            self._timeline.remove(stale)

    def _fail_start(self, message: str) -> None:
        logger.warning("chat.start_failed session=%s: %s", self.session_id, message)
        self._reload_notice = False
        self._signed_in_notice = False
        if self._timeline.state.get("status") == "error":
            return
        self._start_error_item = self._next_id("err")
        self._timeline.upsert({"id": self._start_error_item, "type": "error", "text": message})
        self._timeline.patch_state(status="error", error=message, urgent=True)

    def _describe_rpc_error(self, exc: AcpRpcError, *, during_setup: bool = False) -> str:
        if exc.code == ERR_AUTH_REQUIRED and self._sign_in.available:
            return f"{self._agent_title} is not signed in on your PC. Sign in to continue."
        if exc.code == ERR_AUTH_REQUIRED:
            return self._plan.sign_in_hint or (
                f"{self._agent_title} is not signed in on the PC. Sign in there "
                "(run it once in a terminal), then tap Restart."
            )
        if isinstance(exc.data, dict):
            hint = self._plan.error_hints.get(str(exc.data.get("reason")))
            if hint:
                return hint
        detail = exc.message
        if isinstance(exc.data, dict):
            extra = exc.data.get("details") or exc.data.get("message")
            if isinstance(extra, str) and extra and extra not in detail:
                detail = f"{detail}: {extra}"
        if during_setup:
            return f"The agent could not start the session: {detail}"
        return detail

    async def _restore_title(self, conn: AcpConnection, acp_session_id: str) -> None:
        """The title of a stored session that the agent replayed without it
        (Copilot), from the agent's own session list."""
        if not self._session_cap("list"):
            return
        try:
            result = await conn.request(
                "session/list", {"cwd": self._plan.cwd}, TITLE_LOOKUP_TIMEOUT
            )
        except (AcpRpcError, AcpProcessError, TimeoutError, OSError):
            return
        sessions = result.get("sessions") if isinstance(result, dict) else None
        for entry in sessions if isinstance(sessions, list) else []:
            if not isinstance(entry, dict) or entry.get("sessionId") != acp_session_id:
                continue
            title = entry.get("title")
            # A title that arrived meanwhile, or a restart, wins.
            if conn is self._conn and self._title is None and isinstance(title, str) and title.strip():
                self._set_title(title)
            return

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
        if not images and text.lower() == f"/{LOGIN_COMMAND}" and self._sign_in.available:
            return self._cmd_login()
        if not images and text.lower() == f"/{CLEAR_COMMAND}" and CLEAR_COMMAND not in self._agent_commands:
            return self._cmd_clear()
        status = self._timeline.state.get("status")
        if status in ("error", "stopped"):
            raise ChatCommandError("The agent is not running. Use Restart to start it again.")
        # The user is back: their message goes out now, not a continue later.
        self._cancel_limit_wait()
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
        if (
            self._ready.is_set()
            and not self._own_turn_running()
            and not self._external_turn
            and self._start_next_prompt()
        ):
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

    def _start_next_prompt(self) -> bool:
        nxt = next((q for q in self._queue if not q.steering), None)
        if nxt is None or nxt.not_before is not None:
            return False
        self._queue.remove(nxt)
        self._publish_queue()
        self._start_prompt(nxt.text, nxt.images, mode=nxt.mode, auto=nxt.auto)
        return True

    def _start_prompt(
        self,
        text: str,
        images: list[dict[str, str]],
        *,
        mode: str | None = None,
        auto: bool = False,
    ) -> None:
        if not auto:
            # A message from the user, by any path: no continue may follow it.
            self._cancel_limit_wait()
        self._auto_continues = self._auto_continues + 1 if auto else 0
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
        if self._gate is not None:
            # The quiet window of the turn before must not alert during this
            # one: its UserPromptSubmit hook comes a moment after the prompt.
            self._gate.cancel()
        # This turn alerts at its own end.
        self._cancel_wake_timer()
        self._wake_tools.clear()
        self._alert_held = False
        self._foreground_idle = False
        self._timeline.patch_state(
            status="running", turn_started_at=_now_iso(), cancelling=False, urgent=True
        )
        self._prompt_dispatched = False
        self._cancel_before_dispatch = False
        self._turn_stop = None
        self._turn_reopened = False
        self._turn_task = asyncio.create_task(
            self._run_turn(blocks, text, images, mode), name=f"chat-turn-{self.session_id}"
        )

    async def _switch_mode(self, mode: str) -> None:
        """Put the agent in ``mode`` for the message about to be sent."""
        option = self._match_config(ConfigIntent(mode, category="mode"))
        if option is None or option["type"] != "select" or option["value"] == mode:
            return
        refused = "Could not switch the mode, so the message was not sent."
        if mode not in {value["value"] for value in option["options"]}:
            raise ChatCommandError(refused)
        try:
            await self._set_config(option, mode)
        except (AcpRpcError, TimeoutError):
            raise ChatCommandError(refused) from None

    async def _run_turn(
        self,
        blocks: list[dict[str, Any]],
        text: str,
        images: list[dict[str, str]],
        mode: str | None = None,
    ) -> None:
        tl = self._timeline
        stop_reason: str | None = None
        reload = False
        # The agent answered the prompt with an error (rate limit, auth,
        # overload): the user has to act, the same as after a finished turn.
        failed = False
        auth_failed = False
        # The reset time of the usage limit that refused the prompt.
        limit: tuple[float, str | None] | None = None
        try:
            conn = self._require_conn()
            if self._presence_hold is None and self._plan.presence is not None:
                conflict = self._presence_conflict
                await self._hold_presence()
                if conflict and self._presence_hold is not None:
                    self._presence_stale = True
            # A new conversation or a restart replaced this one while the
            # lock was taken: the message must neither go out nor be queued.
            replaced = not self._ready.is_set()
            # Sending from the stale copy would overwrite the other app's turns.
            reload = self._presence_stale and not self._cancel_before_dispatch and not replaced
            if reload:
                pass
            elif self._cancel_before_dispatch or replaced:
                # Stopped before the prompt reached the agent: never send it.
                stop_reason = "cancelled"
            else:
                if mode is not None:
                    await self._switch_mode(mode)
                if self._cancel_before_dispatch or not self._ready.is_set():
                    # Stopped while the mode was switching: never send it.
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
            failed = True
            limit = _usage_limit_reset(exc)
            tl.upsert(
                {"id": self._next_id("err"), "type": "error", "text": self._describe_rpc_error(exc)}
            )
            if exc.code == ERR_AUTH_REQUIRED and self._sign_in.available:
                auth_failed = True
                if self._ready.is_set():
                    # Not while a new conversation replaces this one.
                    self._auth_retry.append(
                        _QueuedPrompt(self._next_id("q"), text, images, mode=mode)
                    )
                    del self._auth_retry[:-QUEUE_MAX]
                self._sign_in.offer()
        except AcpProcessError as exc:
            if tl.state.get("status") != "error":
                tl.upsert({"id": self._next_id("err"), "type": "error", "text": str(exc)})
        finally:
            self._disarm_cancel_watchdog()
            self._close_streaming_items()
            self._resolve_all_requests_cancelled(keep_running_subagents=True)
            self._foreground_idle = False
            self._wake_grace_until = time.monotonic() + WAKE_GRACE_SECONDS
        if reload:
            # Not ready until the reload: a prompt meanwhile must only queue,
            # never start on the process that is closing.
            self._ready.clear()
            tl.patch_state(status="starting", turn_started_at=None, cancelling=False, urgent=True)
            # Sent once the reloaded conversation is open (bootstrap drains the queue).
            self._queue.insert(0, _QueuedPrompt(self._next_id("q"), text, images, mode=mode))
            self._publish_queue()
            self._reload_notice = True
            # A task of its own: the reload cancels a running turn, this one.
            task = asyncio.create_task(self._reload_conversation())
            self._command_tasks.add(task)
            task.add_done_callback(self._command_tasks.discard)
            return
        if stop_reason is not None:
            self._has_turns = True
        if stop_reason is not None and not auth_failed:
            self._sign_in.withdraw()
        if not auth_failed and not self._resume_pending:
            # The agent took a message again: the refused ones are stale.
            self._auth_retry.clear()
        if stop_reason == "cancelled":
            self._mark_running_tools("cancelled", subagents=False)
        if stop_reason and stop_reason != "end_turn":
            tl.upsert(
                {
                    "id": self._next_id("end"),
                    "type": "turn_end",
                    "stop_reason": stop_reason,
                    "text": STOP_REASON_TEXT.get(stop_reason, f"Stopped: {stop_reason}"),
                }
            )
        if not self._ready.is_set():
            # A new conversation or a restart took over: its bootstrap sends
            # the queue, never this process.
            return
        alive = self._conn is not None and self._conn.alive
        # An error answering the user's own Stop is not news to them.
        cancelling = bool(tl.state.get("cancelling"))
        if alive and not self._stopping and self._resume_pending:
            # The turn's own alert goes out before the restart clears it.
            if failed and not cancelling:
                self._set_attention("turn_failed")
            elif stop_reason not in (None, "cancelled"):
                self._set_attention("turn_finished")
            self._resume_if_pending()
            return
        if (
            limit is not None
            and alive
            and not self._stopping
            and not cancelling
            and self._arm_limit_wait(*limit)
        ):
            # No alert: the user does not have to act. The continue reports
            # its own end.
            tl.patch_state(status="ready", turn_started_at=None, cancelling=False, urgent=True)
            self._settle_gate()
            return
        finished = not failed and stop_reason not in (None, "cancelled")
        if any(not q.steering for q in self._queue) and alive and not self._stopping:
            if finished and self._gate is not None and self._turn_stop is not None:
                # No alert between two prompts, but the gate must know what
                # this turn left behind: the next one may fire no hook.
                self._gate.note_stop(self._turn_stop)
            tl.patch_state(status="ready", turn_started_at=None, cancelling=False)
            self._start_next_prompt()
            return
        if finished and self._turn_reopened and alive and not self._stopping:
            # The agent already runs its next turn, which its own Stop hook
            # ends: the chat keeps showing a running turn, now the agent's own.
            self._external_turn = True
            self._self_turn = True
            tl.patch_state(turn_started_at=_now_iso(), cancelling=False, urgent=True)
            return
        tl.patch_state(status="ready" if alive else "error", turn_started_at=None, cancelling=False)
        # One frame: the turn's end and the work it left in flight (which the
        # gate reads from its Stop below), so the header never shows "Ready"
        # in between.
        try:
            if not alive or self._stopping:
                return
            if failed and not cancelling:
                self._settle_gate()
                self._set_attention("turn_failed")
            elif failed or stop_reason in (None, "cancelled"):
                self._settle_gate()
            elif self._gate is None:
                self._alert_turn_done()
            elif not self._hooks_live:
                self._set_attention("turn_finished")
            elif self._turn_stop is not None:
                # The Stop hook listed what the turn left behind: the gate
                # alerts once none of it is in flight.
                self._gate.handle(HookKind.STOP, self._turn_stop)
            else:
                # No Stop hook: it was lost, or the turn was a local command.
                self._gate.stop_lost()
        finally:
            tl.flush()

    async def _reload_conversation(self) -> None:
        """Open the conversation again in a new agent process (``session/load``)."""
        if self._stopping or self._restarting:
            return
        self._restarting = True
        try:
            await self._restart()
        except Exception:
            logger.exception("chat.reload_failed session=%s", self.session_id)
            self._fail_start("Could not open the conversation again. Use Restart to try again.")
        finally:
            self._restarting = False

    async def _cmd_cancel(self) -> dict[str, Any]:
        running = self._own_turn_running()
        if not running and self._external_turn:
            conn = self._conn
            if conn is None or not conn.alive or not self._acp_session_id:
                return {"cancelled": False}
            conn.notify("session/cancel", {"sessionId": self._acp_session_id})
            # ACP: a subagent that goes on keeps its requests.
            self._resolve_all_requests_cancelled(keep_running_subagents=True)
            if self._self_turn:
                # Nothing reports the end of an interrupted turn of the agent's
                # own (no Stop hook, no prompt answer), so it ends here.
                self._mark_running_tools("cancelled", subagents=False)
                self._settle_gate()
                self._drop_wake_turn()
                self._on_agent_busy(False)
            else:
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
        self._resolve_all_requests_cancelled(keep_running_subagents=True)
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
        held = any(q.key == key and q.not_before is not None for q in self._queue)
        before = len(self._queue)
        self._queue = [q for q in self._queue if q.key != key]
        if len(self._queue) == before:
            raise ChatCommandError("This message was already sent")
        self._publish_queue()
        if held:
            self._cancel_limit_wait()
            # The continue held the messages queued behind it.
            self._start_queue_if_idle()
        return {}

    def _arm_limit_wait(self, resets_at: float, limit_type: str | None) -> bool:
        """Queue a continue for when the usage limit resets. False when the
        reset is too far away or the automatic continues ran out: the user
        has to act."""
        now = time.time()
        if resets_at - now > LIMIT_WAIT_MAX or self._auto_continues >= AUTO_CONTINUE_MAX:
            return False
        self._cancel_limit_wait()
        continues_at = max(resets_at, now) + LIMIT_WAIT_MARGIN
        entry = _QueuedPrompt(
            self._next_id("q"), AUTO_CONTINUE_PROMPT, [], not_before=continues_at, auto=True
        )
        self._queue.insert(0, entry)
        self._publish_queue()
        self._timeline.patch_state(
            limit_wait={
                "queue_id": entry.key,
                "resets_at": _iso(resets_at),
                "continues_at": _iso(continues_at),
                "limit_type": limit_type,
            },
            urgent=True,
        )
        self._limit_task = asyncio.create_task(
            self._release_after_reset(entry), name=f"chat-limit-{self.session_id}"
        )
        logger.info(
            "chat.limit_wait_armed session=%s type=%s in=%.0fs count=%d",
            self.session_id,
            limit_type,
            continues_at - now,
            self._auto_continues,
        )
        return True

    async def _release_after_reset(self, entry: _QueuedPrompt) -> None:
        while entry.not_before is not None:
            remaining = entry.not_before - time.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(LIMIT_WAIT_TICK, remaining))
        if not any(q is entry for q in self._queue):
            return
        self._limit_task = None
        entry.not_before = None
        self._timeline.patch_state(limit_wait=None, urgent=True)
        self._start_queue_if_idle()

    def _cancel_limit_wait(self) -> None:
        """Drop the continue that waits for a usage limit to reset."""
        task, self._limit_task = self._limit_task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        # A released continue that has not started yet goes too.
        if any(q.not_before is not None or q.auto for q in self._queue):
            self._queue = [q for q in self._queue if q.not_before is None and not q.auto]
            self._publish_queue()
        if self._timeline.state.get("limit_wait") is not None:
            self._timeline.patch_state(limit_wait=None, urgent=True)

    async def _cmd_steer(self, cmd: dict[str, Any]) -> dict[str, Any]:
        """Send a queued message into the running turn now (``_session/steering``)."""
        key = cmd.get("queue_id")
        queued = next((q for q in self._queue if q.key == key), None)
        if queued is None or queued.steering:
            raise ChatCommandError("This message was already sent")
        if queued.not_before is not None:
            raise ChatCommandError("This message waits for the usage limit to reset")
        if not self._ready.is_set():
            # The agent is starting: the message goes out once it is ready.
            return {"steered": False}
        if not self._own_turn_running() and not self._external_turn:
            # Idle: the queue sends it as the next prompt anyway.
            self._start_queue_if_idle()
            return {"steered": False}
        if self._external_turn and self._self_turn and not self._own_turn_running():
            # The adapter steers only a turn it got as a prompt. But a prompt
            # sent during a turn the agent started by itself joins that turn,
            # so the message goes out as one: this session's turn takes over.
            self._external_turn = False
            self._queue.remove(queued)
            self._publish_queue()
            self._start_prompt(queued.text, queued.images, mode=queued.mode, auto=queued.auto)
            return {"steered": True}
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
        self._cancel_limit_wait()
        try:
            await self._restart()
        finally:
            self._restarting = False
        return {}

    def _restore_config(self) -> None:
        """Queue the values in use for the next start: a new agent process
        starts with the agent's defaults. A start that failed before applying
        the launch choices keeps those instead."""
        if self._pending_config:
            return
        restored = [
            ConfigIntent(
                opt["value"],
                option_id=opt["id"],
                category=opt.get("category"),
                required=(
                    opt.get("category") in self._required_categories
                    or opt["id"] in self._required_option_ids
                ),
            )
            for opt in self._timeline.state.get("config") or []
            if isinstance(opt, dict)
            and "value" in opt
            and opt.get("id") not in self._plan.restore_skip_option_ids
        ]
        # A required option the agent no longer lists falls back to the
        # launch choice, which then fails closed if it cannot be applied.
        covered_categories = {i.category for i in restored if i.required and i.category}
        covered_ids = {i.option_id for i in restored if i.required and i.option_id}
        restored += [
            i for i in self._plan.initial_config
            if i.required
            and not (i.category and i.category in covered_categories)
            and not (i.option_id and i.option_id in covered_ids)
        ]
        self._pending_config = restored

    async def _restart(self) -> None:
        self._cancel_forced = False
        if self._turn_task is not None and not self._turn_task.done():
            self._turn_task.cancel()
        self._resolve_all_requests_cancelled()
        self._restore_config()
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
            # The reset dropped the "open in another app" notice: re-check.
            self._presence_conflict = False
            self._close_streaming_items()
            self._drop_consoles()
            self._timeline.patch_state(plan=[])
        self._drop_start_error()
        self._timeline.patch_state(
            status="starting", error=None, requests=self._public_requests(), urgent=True
        )
        # A reload brings in what another app sent; a new session has no such past.
        self._presence_stale = False
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

    def _claim_new_conversation(self) -> None:
        if self._stopping:
            raise ChatCommandError("The session is closing")
        if self._restarting or (
            self._bootstrap_task is not None and not self._bootstrap_task.done()
        ):
            raise ChatCommandError("The agent is already starting")
        # Set before the first await: a second request (double tap, another
        # client) must not spawn a second adapter that nothing would close.
        self._restarting = True
        # Nothing may start on the process that is closing: a message sent
        # from here on waits in the queue for the new conversation.
        self._ready.clear()
        self._cancel_limit_wait()
        self._auto_continues = 0
        self._queue.clear()
        self._publish_queue()
        self._auth_retry.clear()
        self._resume_pending = False
        self._initial_prompt = None
        # A message sent from now on queues, also from the error state.
        self._timeline.patch_state(
            status="starting", error=None, turn_started_at=None, cancelling=False, urgent=True
        )

    async def _cmd_new_conversation(self) -> dict[str, Any]:
        # handle_command claimed it already.
        try:
            await self._new_conversation()
        except Exception:
            self._fail_new_conversation()
            raise
        finally:
            self._restarting = False
        return {}

    def _cmd_clear(self) -> dict[str, Any]:
        """``/clear``: the same as ``new_conversation``, answered at once like
        a prompt."""
        self._claim_new_conversation()
        self._run_aside(self._clear_aside())
        return {"queued": False, "queue_id": None}

    async def _clear_aside(self) -> None:
        try:
            await self._new_conversation()
        except Exception:
            self._fail_new_conversation()
        finally:
            self._restarting = False

    def _fail_new_conversation(self) -> None:
        # Without this a failure halfway leaves the chat not ready, with no Restart.
        logger.exception("chat.new_conversation_failed session=%s", self.session_id)
        self._fail_start("Could not start a new conversation. Use Restart to try again.")

    async def _new_conversation(self) -> None:
        """Start an empty conversation in a new agent process.

        Not a second ``session/new`` on this process: some agents keep
        per-process state (the working folder, a bridge's session).

        Runs after ``_claim_new_conversation``."""
        self._cancel_forced = False
        self._disarm_cancel_watchdog()
        conn = self._conn
        turn = self._turn_task
        turn_running = turn is not None and not turn.done()
        if turn is not None and turn_running:
            if self._plan.shared_host:
                # Closing this process does not stop the turn in the other
                # app's server: it would go on changing the folder while the
                # new conversation starts. Wait for it to stop, as stop() does.
                await self._stop_own_turn()
            elif self._prompt_dispatched and conn is not None and conn.alive and self._acp_session_id:
                conn.notify("session/cancel", {"sessionId": self._acp_session_id})
            turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)
        self._resolve_all_requests_cancelled()
        self._restore_config()
        if conn is not None:
            await conn.close(grace=CLOSE_TIMEOUT if turn_running else 0.5)
        if self._stopping:
            return
        stored = self._agent_caps.get("loadSession") and (
            self._has_turns or self._load_session_id is not None
        )
        self._release_presence()
        self._presence_conflict = False
        self._presence_stale = False
        self._reload_notice = False
        self._acp_session_id = None
        self._load_session_id = None
        self._has_turns = False
        self._external_turn = False
        self._self_turn = False
        self._busy_before_ready = False
        self._turn_stop = None
        self._turn_reopened = False
        self._last_tool_id = None
        self._last_agent_message_id = None
        self._last_thought_message_id = None
        self._last_user_message_id = None
        self._timeline.reset()
        self._start_error_item = None
        self._close_streaming_items()
        self._drop_consoles()
        self._set_title(self._plan.title)
        self._timeline.patch_state(
            status="starting",
            error=None,
            acp_session_id=None,
            usage=None,
            plan=[],
            requests=self._public_requests(),
            turn_started_at=None,
            cancelling=False,
            background=None,
            urgent=True,
        )
        # A message sent while the old process closed waits for the new conversation.
        self._publish_queue()
        # Always shown: after a ``/clear`` it is the only sign of what happened.
        self._timeline.upsert(
            {
                "id": self._next_id("n"),
                "type": "notice",
                "severity": "info",
                "title": "Started a new conversation",
                "text": (
                    f"{self._agent_title} keeps the previous one on your PC. To open it "
                    "again, use Continue a session in New session."
                    if stored
                    else ""
                ),
            }
        )
        if not await self._spawn_with_install():
            return
        self._exit_reason = None
        self._clear_attention()
        self._bootstrap_task = asyncio.create_task(
            self._bootstrap(load_id=None),
            name=f"chat-bootstrap-{self.session_id}",
        )

    async def _cmd_sign_in(self, cmd: dict[str, Any]) -> dict[str, Any]:
        if self._stopping:
            raise ChatCommandError("The session is closing")
        action = cmd.get("action")
        try:
            if action == "start":
                await self._sign_in.start(cmd.get("method"))
            elif action == "submit":
                await self._sign_in.submit(cmd.get("value"))
            elif action == "cancel":
                self._sign_in.cancel()
            else:
                raise ChatCommandError("Unknown action")
        except SignInError as exc:
            raise ChatCommandError(str(exc)) from None
        return {}

    def _cmd_login(self) -> dict[str, Any]:
        """``/login``: the agents' own command needs their terminal app, so
        the chat runs its sign-in instead."""
        if self._stopping:
            raise ChatCommandError("The session is closing")
        if self._own_turn_running() or self._external_turn:
            raise ChatCommandError("Wait for the current turn to end, or stop it, then sign in.")
        self._run_aside(self._sign_in.open())
        return {"queued": False, "queue_id": None}

    def _with_own_commands(self, commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
        own = list(commands)
        if CLEAR_COMMAND not in self._agent_commands:
            own.append(
                {
                    "name": CLEAR_COMMAND,
                    "description": "Start a new conversation with the same settings",
                    "hint": None,
                }
            )
        if not self._sign_in.available:
            return own
        return [
            *(c for c in own if c.get("name") != LOGIN_COMMAND),
            {
                "name": LOGIN_COMMAND,
                "description": f"Sign in to {self._agent_title} on your PC",
                "hint": None,
            },
        ]

    def _on_signed_in(self) -> None:
        self._run_aside(self._resume_after_sign_in())

    async def _resume_after_sign_in(self) -> None:
        """Start the agent again so it reads the new login, then send the
        messages it refused."""
        if self._stopping:
            return
        if self._restarting or self._own_turn_running() or self._external_turn:
            # Done once the session is idle again (_resume_if_pending).
            self._resume_pending = True
            return
        # Shown once the agent is back: a reload replays the conversation first.
        self._signed_in_notice = True
        self._restarting = True
        self._cancel_limit_wait()
        # A message sent meanwhile must only queue, never start on the
        # process that is closing (bootstrap sends the queue).
        self._ready.clear()
        self._timeline.patch_state(
            status="starting", turn_started_at=None, cancelling=False, urgent=True
        )
        try:
            bootstrap = self._bootstrap_task
            if bootstrap is not None and not bootstrap.done():
                bootstrap.cancel()
                await asyncio.gather(bootstrap, return_exceptions=True)
            if self._load_session_id is None and not self._has_turns:
                # The agent stored nothing yet: start a new session instead
                # of opening an empty one.
                self._acp_session_id = None
            retry, self._auth_retry = self._auth_retry, []
            # Within the queue's own limit.
            retry = retry[len(retry) - max(0, QUEUE_MAX - len(self._queue)):]
            if retry:
                self._queue[:0] = retry
                self._publish_queue()
            await self._restart()
        except Exception:
            logger.exception("chat.sign_in_restart_failed session=%s", self.session_id)
            self._fail_start("Could not start the agent again. Use Restart to try again.")
        finally:
            self._restarting = False

    def _resume_if_pending(self) -> bool:
        """Run the restart a sign-in left for later, once nothing else runs.
        True when it started: the restarted agent sends the queue."""
        if not self._resume_pending or self._stopping:
            return False
        self._resume_pending = False
        self._run_aside(self._resume_after_sign_in())
        return True

    def _show_signed_in(self) -> None:
        self._timeline.upsert(
            {
                "id": self._next_id("n"),
                "type": "notice",
                "severity": "info",
                "title": f"Signed in to {self._agent_title}",
                "text": "",
            }
        )

    def _run_aside(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._command_tasks.add(task)
        task.add_done_callback(self._command_tasks.discard)

    def _on_notification(self, method: str, params: Any) -> None:
        if method == AUTH_STATUS_METHOD and isinstance(params, dict):
            status = params.get("authStatus")
            kind = status.get("kind") if isinstance(status, dict) else None
            if kind == "none":
                self._sign_in.offer()
            elif isinstance(kind, str):
                self._sign_in.withdraw()
            return
        if method == cx.UPDATE_TODOS and isinstance(params, dict):
            # Cursor documents it as a notification; today it is a request.
            self._timeline.patch_state(plan=self._todos.apply(params))
            return
        if wk.method_is(method, wk.OPENCODE_CHILD_UPDATE):
            self._on_child_update(params)
            return
        if wk.method_is(method, wk.COPILOT_SESSION_EVENT):
            self._on_copilot_event(params)
            return
        if method != "session/update" or not isinstance(params, dict):
            return
        sid = params.get("sessionId")
        update = params.get("update")
        if self._acp_session_id is not None and sid != self._acp_session_id:
            child = self._subagents.get(sid)
            if child is not None and isinstance(update, dict):
                self._apply_subagent_update(child, update)
            return
        if isinstance(update, dict):
            self._note_wake_update(update)
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
            # The agent's own record of a decision request: its request card
            # shows it, so it never becomes a tool card of its own.
            if not self._is_decision_tool(update.get("toolCallId")):
                self._apply_tool_update(update, create=kind == "tool_call")
                if tl.state.get("status") == "loading":
                    self._show_stored_plan(update)
        elif kind == "plan":
            tl.patch_state(plan=nz.plan_entries(update.get("entries")))
        elif kind == "available_commands_update":
            commands = nz.commands(update.get("availableCommands"))
            self._agent_commands = {str(c.get("name")) for c in commands}
            tl.patch_state(commands=self._with_own_commands(commands))
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
        elif kind in sa.LIFECYCLE_UPDATES:
            self._apply_subagent_lifecycle(self._acp_session_id, update)
        elif kind == sa.SESSION_MESSAGE:
            self._apply_session_message(update)
        elif kind == "compaction_update":
            self._apply_compaction(update)
        elif kind == "compaction_summary_chunk":
            cid = update.get("compactionId")
            part = nz.content_block_part(update.get("content"))
            if isinstance(cid, str) and part and part.get("type") == "text":
                item = tl.get(f"cmp-{cid}")
                if item is not None:
                    tl.append_text(item["id"], part["text"])

    def _on_agent_busy(self, busy: bool, *, self_started: bool = False) -> None:
        """A turn this session did not start (another client of a shared
        host, or with ``self_started`` the agent itself): show it as running,
        and hold queued messages until it ends."""
        if not self._ready.is_set():
            # Joining a session while another app's turn runs.
            self._busy_before_ready = busy
            return
        if self._own_turn_running():
            return
        tl = self._timeline
        if busy and not self._external_turn:
            if not self_started:
                # The user went on from another app: no continue may follow.
                self._cancel_limit_wait()
            self._external_turn = True
            self._self_turn = self_started
            self._close_streaming_items()
            if not self_started:
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
            if self._resume_if_pending():
                return
            if self._queue:
                self._start_next_prompt()

    def _in_self_turn(self) -> bool:
        return self._external_turn and self._self_turn and not self._own_turn_running()

    def _note_wake_update(self, update: dict[str, Any]) -> None:
        """A content update while no turn runs: the agent woke by itself
        (a finished background task), so the chat shows a turn of its own,
        which the user can stop and which alerts at its end."""
        if not self._wakes or update.get("sessionUpdate") not in wk.CONTENT_UPDATES:
            return
        if not self._in_self_turn():
            if time.monotonic() < self._wake_grace_until or not self._start_self_turn():
                return
        tool_id = update.get("toolCallId")
        if isinstance(tool_id, str) and tool_id:
            self._wake_tools.add(tool_id)
        self._wake_last_update = time.monotonic()
        self._arm_wake_timer()

    def _start_self_turn(self) -> bool:
        if (
            not self._ready.is_set()
            or self._stopping
            or self._own_turn_running()
            or self._external_turn
            or self._timeline.state.get("status") != "ready"
        ):
            # Not while the session opens or replays (``loading``), and
            # never over a turn that already runs.
            return False
        if self._attention in ("turn_finished", "turn_failed"):
            # The agent works again, and this turn alerts at its own end.
            self._clear_attention()
        self._cancel_wake_timer()
        self._wake_tools.clear()
        self._wake_last_update = time.monotonic()
        self._wake_turn_signalled = False
        self._on_agent_busy(True, self_started=True)
        return True

    def _end_self_turn(self) -> None:
        self._cancel_wake_timer()
        self._wake_tools.clear()
        self._foreground_idle = False
        # The next queued message starts now and reports its own end.
        queued = any(not q.steering for q in self._queue)
        self._on_agent_busy(False)
        if not queued and self._timeline.state.get("status") == "ready":
            self._alert_turn_done()

    def _drop_wake_turn(self) -> None:
        """The user stopped the self turn: it ends with no alert, and its
        trailing updates must not start another one."""
        self._cancel_wake_timer()
        self._wake_tools.clear()
        self._alert_held = False
        self._foreground_idle = False
        self._wake_grace_until = time.monotonic() + WAKE_GRACE_SECONDS

    def _alert_turn_done(self) -> None:
        if self._children:
            # The parent wakes when its subagents end: that turn alerts.
            self._alert_held = True
            return
        self._alert_held = False
        self._set_attention("turn_finished")

    def _arm_wake_timer(self) -> None:
        delay = WAKE_QUIET_SECONDS
        if self._wake_turn_signalled and self._in_self_turn():
            # ``session.idle`` ends it: a long silent model turn stays open.
            delay = WAKE_MAX_SILENT_SECONDS
        self._cancel_wake_timer()
        self._wake_timer = asyncio.get_running_loop().call_later(delay, self._on_wake_quiet)

    def _cancel_wake_timer(self) -> None:
        if self._wake_timer is not None:
            self._wake_timer.cancel()
            self._wake_timer = None

    def _on_wake_quiet(self) -> None:
        self._wake_timer = None
        if self._stopping:
            return
        if self._in_self_turn():
            silent = time.monotonic() - self._wake_last_update >= WAKE_MAX_SILENT_SECONDS
            busy = self._wake_tool_running() or (
                self._wake_turn_signalled and self._copilot_events
            )
            if self._requests or (busy and not silent):
                self._arm_wake_timer()
            else:
                self._end_self_turn()
        elif (
            self._alert_held
            and not self._children
            and not self._external_turn
            and self._timeline.state.get("status") == "ready"
        ):
            # The subagents ended and the parent did not wake.
            self._alert_turn_done()

    def _wake_tool_running(self) -> bool:
        for tool_id in self._wake_tools:
            item = self._timeline.get(tool_id)
            if item is not None and item.get("status") not in nz.TERMINAL_TOOL_STATUSES:
                return True
        return False

    def _reset_wake(self) -> None:
        """No agent process runs the conversation now (a restart, a new
        conversation, an exit): what it had in flight is void."""
        self._cancel_wake_timer()
        self._wake_tools.clear()
        self._children.clear()
        self._alert_held = False
        self._foreground_idle = False

    def _on_child_update(self, params: Any) -> None:
        """OpenCode reports a subagent (``opencode/session/child_update``).
        While any runs, the chat reads "Working in background" and the
        finished turn's alert waits."""
        if (
            not self._wakes
            or not self._ready.is_set()
            or self._stopping
            or self._timeline.state.get("status") not in _ACTIVITY_BY_STATUS
        ):
            return
        child = wk.child_update(params, self._acp_session_id)
        if child is None:
            return
        if child.update is not None:
            self._apply_update(child.update)
            return
        if child.status in wk.CHILD_RUNNING:
            if child.child_id in self._children:
                return
            self._children[child.child_id] = child.title
        elif child.child_id in self._children:
            del self._children[child.child_id]
        else:
            return
        self._publish_activity()
        if not self._children and self._alert_held and not self._external_turn:
            # The parent wakes to read the results: that turn alerts at its
            # end, or this window does when no turn comes.
            self._arm_wake_timer()

    def _apply_subagent_update(self, child: sa.Subagent, update: dict[str, Any]) -> None:
        """An update of an announced subagent session. Only what its card
        needs is kept: its tool calls (tool items of their own), its
        checklist, its own subagents and its messages. Its text stays out of
        the timeline: the card shows its answer when it ends."""
        kind = update.get("sessionUpdate")
        if kind in sa.TOOL_UPDATES:
            if self._is_decision_tool(update.get("toolCallId")):
                return
            projected = sa.child_tool_update(child, update)
            if projected is not None:
                if kind == "tool_call":
                    self._subagents.note_tool(child)
                self._apply_tool_update(projected, create=kind == "tool_call", subagent=child)
        elif kind == "plan":
            # The child's checklist, never the chat's plan.
            child.plan = nz.plan_entries(update.get("entries"))
            self._show_subagent(child, urgent=False)
        elif kind == "agent_message_chunk":
            self._subagents.note_text(child, update)
        elif kind in sa.LIFECYCLE_UPDATES:
            self._apply_subagent_lifecycle(child.child_id, update)
        elif kind == sa.SESSION_MESSAGE:
            self._apply_session_message(update)

    def _apply_subagent_lifecycle(self, session_id: str | None, update: dict[str, Any]) -> None:
        change = self._subagents.lifecycle(
            session_id, self._acp_session_id, update, replay=self._replaying
        )
        if change is None:
            return
        child = change.child
        if change.new and child.parent_id is None:
            # The parent's text after the card goes below it.
            self._current_agent_item = self._current_thought_item = self._current_user_item = None
        if change.launch_tool_id is not None:
            item = self._timeline.get(change.launch_tool_id)
            if item is not None and item.get("subagent_id") != child.child_id:
                item["subagent_id"] = child.child_id
                self._timeline.touch(change.launch_tool_id)
        if change.ended:
            self._end_subagent_work(child)
            if child.launch_tool_id is not None:
                self._subagents.adopt_launch_result(child, self._timeline.get(child.launch_tool_id))
        # Last: an urgent upsert sends the changes above in the same frame.
        self._show_subagent(child)
        self._publish_activity()

    def _apply_session_message(self, update: dict[str, Any]) -> None:
        child = self._subagents.message(update)
        if child is not None:
            self._show_subagent(child)

    def _show_subagent(self, child: sa.Subagent, *, urgent: bool = True) -> None:
        tl = self._timeline
        tl.upsert(self._subagents.item(child), urgent=urgent and tl.state.get("status") != "loading")

    def _end_subagent_work(self, child: sa.Subagent) -> None:
        """A subagent's work ended: its open requests are void, and a stopped
        or failed one ends the tool calls it left running."""
        status = sa.ENDED_TOOL_STATUS.get(child.stop_reason or "")
        if status is not None:
            tl = self._timeline
            prefix = f"{child.child_id}:"
            for item in tl.items():
                if (
                    item.get("type") == "tool"
                    and item["id"].startswith(prefix)
                    and item.get("status") in ("pending", "in_progress")
                ):
                    item["status"] = status
                    tl.touch(item["id"])
        self._resolve_all_requests_cancelled(subagent_id=child.child_id)

    def _end_replay(self) -> None:
        """The ``session/load`` reply: a subagent the replay left running is
        unknown until the agent reports its current state, which follows."""
        if not self._replaying:
            return
        self._replaying = False
        for child in self._subagents.children():
            if child.running:
                child.state = "unknown"
                child.stop_reason = None
                child.can_cancel = False
                self._show_subagent(child, urgent=False)

    def _end_subagents(self) -> None:
        """No agent process runs the conversation now: no subagent card may
        read as running or offer Stop."""
        tl = self._timeline
        for child in self._subagents.children():
            if not (child.running or child.can_cancel):
                continue
            if child.running:
                child.state = "unknown"
                child.stop_reason = None
            child.can_cancel = False
            if tl.get(child.item_id) is not None:
                tl.upsert(self._subagents.item(child))
        self._subagents.clear()

    def _cmd_cancel_subagent(self, cmd: dict[str, Any]) -> dict[str, Any]:
        """Stop one subagent (``session/cancel`` with its session id); the
        parent's turn goes on. The child's idle update confirms it."""
        child = self._subagents.get(cmd.get("subagent_id"))
        if child is None:
            raise ChatCommandError("Unknown subagent")
        if not (child.can_cancel and child.running):
            raise ChatCommandError("This subagent cannot be stopped")
        conn = self._conn
        if conn is None or not conn.alive:
            raise ChatCommandError("The agent is not running")
        conn.notify("session/cancel", {"sessionId": child.child_id})
        return {"cancelled": True}

    def _request_subagent(self, params: dict[str, Any]) -> sa.Subagent | None:
        """The announced subagent whose session sends a request, or None for
        the conversation's own session."""
        sid = params.get("sessionId")
        if not self._plan.subagents or self._acp_session_id is None or sid in (None, self._acp_session_id):
            return None
        child = self._subagents.get(sid)
        if child is None:
            raise AcpRpcError(-32602, "Unknown session")
        return child

    def _on_copilot_event(self, params: Any) -> None:
        """A Copilot session event (``github.com/copilot/sessionEvent``)."""
        if not self._wakes:
            return
        event = wk.copilot_event(params, self._acp_session_id)
        if event is None:
            return
        self._copilot_events = True
        if (
            not self._ready.is_set()
            or self._stopping
            or self._timeline.state.get("status") not in _ACTIVITY_BY_STATUS
        ):
            return
        kind, data = event
        busy = self._own_turn_running() or self._in_self_turn()
        if kind == wk.COPILOT_TURN_START:
            if busy:
                self._set_foreground_idle(False)
                if self._in_self_turn():
                    self._wake_turn_signalled = True
                    self._wake_last_update = time.monotonic()
                    self._arm_wake_timer()
            elif self._start_self_turn():
                # A finished detached shell woke the agent.
                self._wake_turn_signalled = True
                self._arm_wake_timer()
        elif kind == wk.COPILOT_FOREGROUND_IDLE:
            if busy and data.get("aborted") is not True:
                self._set_foreground_idle(True)
        elif kind == wk.COPILOT_SESSION_IDLE and self._in_self_turn():
            # The prompt's answer ends a turn this session sent.
            self._end_self_turn()

    def _set_foreground_idle(self, idle: bool) -> None:
        if idle != self._foreground_idle:
            self._foreground_idle = idle
            self._publish_activity()

    def _append_message(self, role: str, update: dict[str, Any]) -> None:
        part = nz.content_block_part(update.get("content"))
        if part is None or part.get("type") != "text":
            return
        text = part["text"]
        message_id = update.get("messageId") if isinstance(update.get("messageId"), str) else None
        tl = self._timeline
        if role == "agent" and message_id is None and self._repeats_tool_path(text):
            return
        # Only the text straight after a tool call can be its note.
        self._last_tool_id = None
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

    def _repeats_tool_path(self, text: str) -> bool:
        """Whether ``text`` is only the note that names the file of the tool
        call before it; the tool item already shows that path."""
        if not text.startswith(TOOL_PATH_NOTE):
            return False
        item = self._timeline.get(self._last_tool_id) if self._last_tool_id else None
        if item is None:
            return False
        return text[len(TOOL_PATH_NOTE):].strip() in {loc["path"] for loc in item["locations"]}

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

    def _apply_tool_update(
        self, update: dict[str, Any], *, create: bool, subagent: sa.Subagent | None = None
    ) -> None:
        """``subagent``: the update is that child's, its id already
        namespaced (``subagents.child_tool_update``)."""
        tool_id = update.get("toolCallId")
        if not isinstance(tool_id, str) or not tool_id:
            return
        tl = self._timeline
        item = tl.get(tool_id)
        is_new = item is None
        if item is None:
            item = nz.new_tool_item(tool_id)
        script = nz.code_mode_script(update) or self._scripts.get(tool_id)
        if script is not None:
            nz.drop_script(item, script)
        nz.merge_tool_update(item, update, markdown_titles=self._plan.markdown_titles)
        if script is not None:
            nz.show_script(item, script)
            if item["status"] in nz.TERMINAL_TOOL_STATUSES:
                self._scripts.pop(tool_id, None)
            else:
                self._scripts[tool_id] = script
        launched: sa.Subagent | None = None
        if subagent is not None:
            sa.label_tool(item, subagent)
        else:
            launched = self._subagents.launch_owner(tool_id)
            if launched is not None:
                item["subagent_id"] = launched.child_id
            self._last_tool_id = tool_id
        deferred = self._apply_console(tool_id, item, update)
        if subagent is None:
            # A child's tool call does not split the parent's open message.
            self._current_agent_item = self._current_thought_item = self._current_user_item = None
        # A finished tool is sent at once, except in a session/load replay,
        # where one frame per tool would flood the event queue.
        terminal = item["status"] in nz.TERMINAL_TOOL_STATUSES
        urgent = terminal and tl.state.get("status") != "loading"
        live_output = (
            not is_new
            and not create
            and not terminal
            and item["kind"] == "execute"
            and update.keys() <= _CONTENT_ONLY_KEYS
        )
        if live_output:
            # Some agents send the whole output so far with each chunk: one
            # frame per interval, or every chunk would re-send the item.
            if tool_id not in self._output_touches:
                self._output_touches[tool_id] = asyncio.get_running_loop().call_later(
                    CONSOLE_RENDER_INTERVAL, self._touch_output, tool_id
                )
            return
        pending_touch = self._output_touches.pop(tool_id, None)
        if pending_touch is not None:
            pending_touch.cancel()
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
            if launched is not None and self._subagents.adopt_launch_result(launched, item):
                self._show_subagent(launched)

    def _show_stored_plan(self, update: dict[str, Any]) -> None:
        """In a replayed session, show a Cursor plan as it stood in the live
        chat: its tool call by the plan's name, and its text as a message."""
        tool_id = update.get("toolCallId")
        if not isinstance(tool_id, str):
            return
        plan = cx.stored_plan(update.get("rawInput"))
        title = self._stored_plans.get(tool_id)
        if plan is None and title is None:
            return
        item = self._timeline.get(tool_id)
        if item is None:
            return
        # A later update of the tool call can carry Cursor's own title again.
        # The same path as a live title: one line, cut, no code marks.
        renamed = dict(item)
        nz.merge_tool_update(
            renamed,
            {"title": plan[0] if plan is not None else title, "kind": "think"},
            markdown_titles=self._plan.markdown_titles,
        )
        if (renamed["title"], renamed["kind"]) != (item["title"], item["kind"]):
            self._timeline.upsert(renamed)
        if title is None and plan is not None:
            self._stored_plans[tool_id] = plan[0]
            self._append_message("agent", {"content": {"type": "text", "text": plan[1]}})
            self._current_agent_item = None

    def _touch_output(self, tool_id: str) -> None:
        self._output_touches.pop(tool_id, None)
        if self._timeline.get(tool_id) is not None:
            self._timeline.touch(tool_id)

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
        for handle in self._output_touches.values():
            handle.cancel()
        self._output_touches.clear()

    def _close_streaming_items(self) -> None:
        self._current_agent_item = None
        self._current_thought_item = None
        self._current_user_item = None
        self._last_agent_message_id = None
        self._last_thought_message_id = None

    def _mark_running_tools(self, status: str, *, subagents: bool = True) -> None:
        """``subagents=False`` spares the subagents' tool calls: a subagent
        reports its own end (``_end_subagent_work``)."""
        tl = self._timeline
        for item in tl.items():
            if item.get("type") != "tool" or item.get("status") not in ("pending", "in_progress"):
                continue
            owner = item.get("subagent_id")
            if not subagents and isinstance(owner, str) and item["id"].startswith(f"{owner}:"):
                continue
            item["status"] = status
            tl.touch(item["id"])

    async def _on_request(self, method: str, params: Any) -> Any:
        if not isinstance(params, dict):
            raise AcpRpcError(-32602, "Invalid params")
        if method == "session/request_permission":
            return await self._permission_request(
                params, decision=self._is_decision(params), subagent=self._request_subagent(params)
            )
        if method == "elicitation/create":
            return await self._elicitation_request(params, subagent=self._request_subagent(params))
        if method.startswith(cx.PREFIX):
            return await self._cursor_request(method, params)
        raise AcpRpcError(ERR_METHOD_NOT_FOUND, f"Method not found: {method}")

    async def _cursor_request(self, method: str, params: dict[str, Any]) -> Any:
        """Cursor's own requests, shown as the chat's generic cards (see
        :mod:`.cursor_ext`)."""
        if method == cx.ASK_QUESTION:
            return cx.question_outcome(await self._elicitation_request(cx.question_form(params)))
        if method == cx.CREATE_PLAN:
            # Cursor sends the plan's tasks itself; later updates merge by id.
            self._todos.apply(cx.plan_todos(params), merge=False)
            text = cx.plan_text(params)
            if text:
                # A message of its own: Cursor writes its progress over the
                # content of the plan's tool call.
                self._current_agent_item = None
                self._append_message("agent", {"content": {"type": "text", "text": text}})
                self._current_agent_item = None
            mode = self._match_config(ConfigIntent(cx.PLAN_MODE, category="mode"))
            # In Plan mode Cursor only stores an accepted plan: the card
            # can also start the work, in Agent mode.
            can_start = (
                mode is not None
                and mode["value"] == cx.PLAN_MODE
                and any(value["value"] == cx.AGENT_MODE for value in mode["options"])
            )
            answer = await self._permission_request(
                cx.plan_permission(params, can_start=can_start), decision=True
            )
            choice = cx.plan_choice(answer)
            if choice == cx.PLAN_START:
                # Next in the queue, where the user can see and remove it.
                self._queue.insert(
                    0,
                    _QueuedPrompt(
                        self._next_id("q"), cx.PLAN_START_PROMPT, [], mode=cx.AGENT_MODE
                    ),
                )
                self._publish_queue()
            elif choice != cx.PLAN_ACCEPT:
                # The tasks of a plan that was not accepted are not a plan.
                self._todos.clear()
                self._timeline.patch_state(plan=[])
            return cx.plan_outcome(choice)
        if method == cx.UPDATE_TODOS:
            self._timeline.patch_state(plan=self._todos.apply(params))
            return {}
        if method in cx.NOTICES:
            return {}
        # Cursor has its own fallback for a method the client does not know.
        raise AcpRpcError(ERR_METHOD_NOT_FOUND, f"Method not found: {method}")

    def _is_decision_tool(self, tool_id: Any) -> bool:
        """Whether the agent marks this tool call id as a request for the
        user's own decision (see ``ChatLaunchPlan.decision_tool_prefixes``)."""
        return isinstance(tool_id, str) and tool_id.startswith(self._plan.decision_tool_prefixes)

    def _is_decision(self, params: dict[str, Any]) -> bool:
        call = params.get("toolCall")
        return self._is_decision_tool(call.get("toolCallId") if isinstance(call, dict) else None)

    async def _permission_request(
        self,
        params: dict[str, Any],
        *,
        decision: bool = False,
        subagent: sa.Subagent | None = None,
    ) -> Any:
        """``decision`` marks a choice that is the user's own (a plan to
        accept), which "accept every request" must not make for them.
        ``subagent``: the child session that asks; its card goes with the
        child's tool call."""
        options = nz.permission_options(params.get("options"))
        raw_call = params.get("toolCall")
        tool_call: dict[str, Any] = raw_call if isinstance(raw_call, dict) else {}
        raw_tool_id = tool_call.get("toolCallId")
        tool_id = raw_tool_id if isinstance(raw_tool_id, str) else None
        decision_tool = self._is_decision_tool(tool_id)
        if tool_id and subagent is not None:
            tool_id = sa.child_tool_id(subagent, tool_id)
            tool_call = {**tool_call, "toolCallId": tool_id}
        if tool_id and not decision_tool:
            self._apply_tool_update(dict(tool_call), create=False, subagent=subagent)
        if not options:
            return {"outcome": {"outcome": "cancelled"}}
        if self._plan.auto_approve and not decision:
            allow = next((o for o in options if o["kind"] == "allow_once"), None)
            if allow is not None:
                return {"outcome": {"outcome": "selected", "optionId": allow["id"]}}
        item = self._timeline.get(tool_id) if tool_id else None
        if item is None:
            item = nz.new_tool_item("preview")
            nz.merge_tool_update(item, tool_call, markdown_titles=self._plan.markdown_titles)
            script = nz.code_mode_script(tool_call)
            if script is not None:
                nz.show_script(item, script)
            if subagent is not None:
                sa.label_tool(item, subagent)
        public = {
            "kind": "permission",
            "tool_call_id": tool_id,
            "title": item["title"],
            "tool_kind": item["kind"],
            "subtitle": item.get("subtitle"),
            # A decision has its text in the conversation; the tool call
            # holds only the agent's progress notes.
            "content": [] if decision else item["content"][:3],
            "options": options,
        }
        return await self._await_request(
            "permission", public, tool_id, subagent=subagent
        )

    async def _elicitation_request(
        self, params: dict[str, Any], *, subagent: sa.Subagent | None = None
    ) -> Any:
        if params.get("mode") not in (None, "form"):
            raise AcpRpcError(-32602, "Only form elicitation is supported")
        fields = nz.form_fields(params.get("requestedSchema"))
        if fields is None:
            return {"action": "decline"}
        message = params.get("message") if isinstance(params.get("message"), str) else ""
        tool_id = params.get("toolCallId") if isinstance(params.get("toolCallId"), str) else None
        if tool_id and subagent is not None:
            tool_id = sa.child_tool_id(subagent, tool_id)
        public = {
            "kind": "form",
            "tool_call_id": tool_id,
            "title": message[:2000] or "The agent has a question",
            "fields": fields,
        }
        return await self._await_request("form", public, tool_id, fields=fields, subagent=subagent)

    async def _await_request(
        self,
        kind: str,
        public: dict[str, Any],
        tool_id: str | None,
        *,
        fields: list[dict[str, Any]] | None = None,
        subagent: sa.Subagent | None = None,
    ) -> Any:
        key = self._next_id("r")
        public = {"id": key, **public}
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        pending = _PendingRequest(
            key,
            kind,
            future,
            public,
            tool_id,
            fields or [],
            subagent.child_id if subagent is not None else None,
        )
        self._requests[key] = pending
        self._publish_requests()
        self._set_attention("awaiting_permission")
        try:
            return await future
        except asyncio.CancelledError as exc:
            # The agent's $/cancel_request can land after the user answered
            # but before this task woke up: the answer wins. Any other cancel
            # (Stop, restart, close) still ends the task.
            task = asyncio.current_task()
            if (
                exc.args == (CANCEL_REQUEST_METHOD,)
                and future.done()
                and not future.cancelled()
                and task is not None
            ):
                task.uncancel()
                return future.result()
            raise
        finally:
            self._drop_request(key)

    def _public_requests(self) -> list[dict[str, Any]]:
        requests = [p.public for p in self._requests.values()]
        if self._sign_in.public is not None:
            requests.append(self._sign_in.public)
        return requests

    def _publish_requests(self) -> None:
        self._timeline.patch_state(requests=self._public_requests(), urgent=True)

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

    def _resolve_all_requests_cancelled(
        self, *, keep_running_subagents: bool = False, subagent_id: str | None = None
    ) -> None:
        """``keep_running_subagents``: the requests of a subagent that has not
        reported idle stay open (Claude's background subagents outlive the
        prompt and the parent's cancel); its idle ends them. ``subagent_id``:
        only the requests of that subagent."""
        dropped = False
        for key, pending in list(self._requests.items()):
            if subagent_id is not None and pending.subagent_id != subagent_id:
                continue
            child = self._subagents.get(pending.subagent_id)
            if keep_running_subagents and child is not None and child.state != "idle":
                continue
            if not pending.future.done():
                pending.future.set_result(self._cancel_outcome(pending))
            del self._requests[key]
            dropped = True
        if dropped:
            self._publish_requests()
            if not self._requests and self._attention == "awaiting_permission":
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
        # Restart drops it anyway: the wait must not read as armed meanwhile.
        self._cancel_limit_wait()
        self._release_presence()
        self._resolve_all_requests_cancelled()
        self._mark_running_tools("failed")
        if self._timeline.state.get("status") != "error":
            item_id = self._next_id("err")
            self._timeline.upsert({"id": item_id, "type": "error", "text": message})
            if not self._ready.is_set():
                # The agent died while it started: a failed start like any
                # other. A crash in a running chat stays as its history.
                self._start_error_item = item_id
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

    def _on_timeline_status(self, status: Any) -> None:
        """Mirror a turn starting or ending (own or another app's) onto the
        session row, so the sessions list can show which chats are busy."""
        if status not in _ACTIVITY_BY_STATUS:
            # No agent process to send hooks (starting, error, stopped): what
            # the last one reported is void.
            self._hooks_live = False
            self._gate_activity = None
            if self._gate is not None:
                self._gate.reset_turn()
            self._reset_wake()
            self._end_subagents()
        self._publish_activity()

    def _publish_activity(self) -> None:
        activity = _ACTIVITY_BY_STATUS.get(str(self._timeline.state.get("status")))
        background: str | None = None
        if activity == "idle" and self._gate_activity is not None:
            # No turn runs, and the hooks know what the last one left behind.
            activity = self._gate_activity
            parked = self._gate is not None and self._gate.parked_activity is not None
            if parked and activity in ("working", "waiting"):
                # Work the turn left in flight. Not the quiet window after a
                # turn that left nothing: the chat header shows "Ready" then.
                background = activity
        elif activity == "idle" and (
            self._children or (self._gate is None and self._subagents.running())
        ):
            # Subagents still run after the turn: OpenCode's, whose end wakes
            # the parent, or ACP subagent sessions of an agent with no hooks
            # (Codex). Claude's hooks report its own.
            activity = background = "working"
        elif activity == "working" and self._foreground_idle:
            # Copilot: the turn waits only on attached background work.
            activity = background = "waiting"
        if self._timeline.state.get("background") != background:
            self._timeline.patch_state(background=background)
        if activity == self._activity:
            return
        self._activity = activity
        self._put_event(SessionEvent("activity", self.session_id))

    def on_claude_hook(self, kind: HookKind, payload: Mapping[str, Any]) -> None:
        """Apply one Claude Code hook (see ``ChatLaunchPlan.hook_port``).

        The ``session/prompt`` answer ends a turn for the chat screen. The
        hooks add what it cannot say: the work a turn left behind, which keeps
        the session ``working`` or ``waiting`` and holds the "turn finished"
        alert back, and the turns the agent starts by itself, which the chat
        shows as running and which the user can stop.
        """
        gate = self._gate
        if gate is None or kind not in _TURN_HOOKS:
            return
        if (
            not self._ready.is_set()
            or self._stopping
            or self._timeline.state.get("status") not in _ACTIVITY_BY_STATUS
        ):
            # A hook of an agent process that is closing, gone or not open yet.
            return
        own_turn = self._own_turn_running()
        self_turn = self._external_turn and self._self_turn
        if kind is HookKind.STOP_FAILURE:
            if own_turn:
                # The prompt answer reports the failure of a turn the user
                # sent. A turn that began after its Stop has ended here too.
                self._turn_reopened = False
            elif self_turn:
                self._fail_self_turn()
            return
        if (
            kind is HookKind.TOOL
            and not own_turn
            and not self_turn
            and not is_subagent_call(payload)
        ):
            # A call that was on its way when its turn was interrupted. No
            # Stop hook follows an interrupt, so it must not revive the turn.
            return
        self._hooks_live = True
        if kind is HookKind.PROMPT_SUBMIT:
            if self._attention in ("turn_finished", "turn_failed"):
                # The agent works again, and this turn alerts at its own end.
                self._clear_attention()
            if own_turn and self._turn_stop is not None:
                # A turn began after the Stop of the prompt that still waits
                # for its answer. That Stop no longer ends anything.
                self._turn_stop = None
                self._turn_reopened = True
            # No-op for a prompt this session sent: its own turn runs.
            self._on_agent_busy(True, self_started=True)
        elif kind is HookKind.STOP and own_turn:
            self._turn_stop = dict(payload)
            self._turn_reopened = False
            return
        gate.handle(kind, payload)
        if kind is HookKind.STOP and self_turn:
            self._on_agent_busy(False)

    def _fail_self_turn(self) -> None:
        """A turn the agent started by itself ended on an error (a rate
        limit, a lost login): the user has to act, as after a failed prompt."""
        self._settle_gate()
        if not any(not q.steering for q in self._queue):
            # Otherwise the next queued message starts now and reports its own end.
            self._set_attention("turn_failed")
        self._on_agent_busy(False)

    def _on_gate_activity(self, activity: str) -> None:
        self._gate_activity = activity
        self._publish_activity()

    def _on_gate_alert(self, attention: str, _exit_reason: str | None) -> None:
        self._set_attention(attention)

    def _settle_gate(self) -> None:
        """Nothing is in flight any more and no "turn finished" alert is due:
        the turn was stopped or failed, which no ``Stop`` hook reports."""
        if self._gate is not None and self._hooks_live:
            self._gate.settle()

    def _set_attention(self, attention: str, exit_reason: str | None = None) -> None:
        changed = attention != self._attention or (
            exit_reason is not None and exit_reason != self._exit_reason
        )
        if changed and not self._admit_attention(attention):
            return
        self._attention = attention
        if exit_reason is not None:
            self._exit_reason = exit_reason
        if changed:
            self._put_event(SessionEvent("attention", self.session_id, attention=attention))

    def _admit_attention(self, attention: str) -> bool:
        """``False`` when a terminal session showing the same live thread
        already alerted this event (see ``core.attention_dedupe``). A chat on a
        private app server shares its thread with no one."""
        if self._attention_dedupe is None or not self._plan.shared_host:
            return True
        if self._real_cwd is None:
            self._real_cwd = real_cwd(self._plan.cwd)
        kind = self._config.kind
        admitted = self._attention_dedupe.admit(
            session_id=self.session_id,
            ui="chat",
            app=kind.value if isinstance(kind, SessionKind) else kind,
            cwd=self._real_cwd,
            reason=attention,
        )
        if not admitted:
            logger.info(
                "chat.attention_shared session=%s reason=%s: already alerted by the terminal",
                self.session_id,
                attention,
            )
        return admitted

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
_CONTENT_ONLY_KEYS = frozenset({"sessionUpdate", "toolCallId", "content"})


def _release_acquired(future: asyncio.Future[PresenceHold | None]) -> None:
    if future.cancelled() or future.exception() is not None:
        return
    hold = future.result()
    if hold is not None:
        hold.release()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="seconds")


def _usage_limit_reset(exc: AcpRpcError) -> tuple[float, str | None] | None:
    """The reset time (epoch seconds) and type of the usage limit that
    refused a prompt, from claude-agent-acp's error data
    (``{errorKind: "rate_limit", rateLimit: {rateLimitType, resetsAt}}``)."""
    data = exc.data
    if not isinstance(data, dict) or data.get("errorKind") != "rate_limit":
        return None
    info = data.get("rateLimit")
    if not isinstance(info, dict):
        return None
    resets_at = info.get("resetsAt")
    if isinstance(resets_at, bool) or not isinstance(resets_at, int | float):
        return None
    resets_at = float(resets_at)
    if not math.isfinite(resets_at) or resets_at <= 0:
        return None
    if resets_at > 1e12:
        # Milliseconds.
        resets_at /= 1000
    limit_type = info.get("rateLimitType")
    return resets_at, limit_type if isinstance(limit_type, str) and limit_type else None


def _prompt_blocks(text: str, images: list[dict[str, str]]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if text:
        blocks.append({"type": "text", "text": text})
    for img in images:
        blocks.append({"type": "image", "mimeType": img["mime"], "data": img["data"]})
    return blocks
