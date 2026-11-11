from __future__ import annotations

import enum
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .shell import build_custom_argv

if TYPE_CHECKING:
    from .antigravity_launcher import AntigravityOptions  # noqa: F401
    from .claude_launcher import ClaudeOptions  # noqa: F401
    from .codex_launcher import CodexOptions  # noqa: F401

# Wire pattern for account-scoped custom AI agent type kinds. The 12-hex id
# is minted by the central server's registry; the raw string is the wire
# kind (never a SessionKind member).
CUSTOM_KIND_PATTERN = re.compile(r"^custom_[0-9a-f]{12}$")


class CustomCommandUnresolvedError(ValueError):
    """A ``custom_<id>`` cli reached spawn resolution without a
    relay-injected ``resolved_command`` (agent-direct REST, an unknown or
    malformed id, or a relay too old to resolve the registry)."""


class SessionState(enum.Enum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    CRASHED = "crashed"


class SessionKind(enum.Enum):
    """Classifies what runs inside a session for UI/icon routing.

    ``CLAUDE`` — Claude CLI launched via ``ClaudeLauncher`` (create with
    ``claude_options``). ``CODEX`` — Codex CLI launched via ``CodexLauncher``
    (create with ``cli="codex"`` / ``codex_options``). ``ANTIGRAVITY`` —
    Antigravity CLI (``agy``) launched via ``AntigravityLauncher`` (create with
    ``cli="antigravity"`` / ``antigravity_options``). ``AUTO_SCAN`` —
    sub-sessions spawned by the Linear scanner; argv/cwd/env pre-built by the
    scanner. ``SHELL`` — the user's login shell anchored to a project's cwd;
    bypasses the managed-CLI launchers entirely.

    Account-scoped custom AI agent types are NOT members: their wire kind is
    the raw string ``custom_<12hex>`` (``CUSTOM_KIND_PATTERN``), carried
    through ``SessionConfig.kind`` / ``SessionInfo.kind`` as ``str`` and
    emitted verbatim by ``session_to_wire_dict``. They behave like ``SHELL``
    sessions running a relay-resolved stored command.
    """

    CLAUDE = "claude"
    CODEX = "codex"
    ANTIGRAVITY = "antigravity"
    AUTO_SCAN = "auto_scan"
    SHELL = "shell"


@dataclass
class SessionConfig:
    session_id: str
    command: list[str]
    cwd: str | None = None
    # Kept out of repr: create_session logs the config, and the launch env
    # carries the user's secrets and the per-session hook token.
    env: dict[str, str] | None = field(default=None, repr=False)
    rows: int = 24
    cols: int = 80
    scrollback_size: int = 10_000
    # Sliding-window cap (bytes) on the raw PTY ring buffer replayed to
    # clients on WS (re)connect. The default ~4 MB covers tens of minutes
    # of dense Ink/React TUI output; tests override this with a small cap
    # to exercise the overflow path without producing megabytes of bytes.
    ring_buffer_bytes: int = 4 * 1024 * 1024
    # When set, ``SessionManager`` resolves the project and uses
    # ``ClaudeLauncher`` to build argv/cwd/env, overwriting the raw
    # fields above before the PTY is spawned.
    project_name: str | None = None
    claude_options: ClaudeOptions | None = None
    # Set when the session runs the Codex CLI. Mutually exclusive with
    # ``claude_options`` (the entry layers normalise via ``resolve_spawn_args``);
    # ``SessionManager`` dispatches to ``CodexLauncher`` when this is set.
    codex_options: CodexOptions | None = None
    # Set when the session runs the Antigravity CLI. Mutually exclusive with the
    # other options blocks; ``SessionManager`` dispatches to ``AntigravityLauncher``.
    antigravity_options: AntigravityOptions | None = None
    # A SessionKind member, or the raw ``custom_<12hex>`` wire string for
    # account-scoped custom agent types.
    kind: SessionKind | str = SessionKind.CLAUDE
    # Snapshot of the custom agent type's display name taken at session
    # creation (relay-injected ``resolved_name``), so session lists render
    # correctly even after the type is renamed or deleted. None for
    # built-in kinds.
    kind_label: str | None = None
    # Terminal-mode worktree request. Consumed only on the SHELL+project path
    # (``SessionManager._resolve_project_cwd``); kept separate from
    # ``claude_options`` so a Terminal session can create a worktree while
    # staying ``SessionKind.SHELL`` (Claude sessions use claude_options.worktree).
    worktree: bool = False
    branch_name: str | None = None
    base_branch: str | None = None
    # Seconds after spawn to wait before flagging a session that produced
    # essentially no output (< STARTUP_STALL_MIN_BYTES) as stalled — almost
    # always a macOS file-access (TCC) modal blocking the child invisibly on the
    # host's desktop, which a remote client cannot see. ``None`` lets
    # ``TerminalSession.start`` pick the platform default (enabled on macOS,
    # disabled elsewhere); ``0`` disables it; tests set an explicit value.
    startup_stall_seconds: float | None = None
    # Trailing-edge debounce applied to Claude Code's ``Stop`` hook before it
    # becomes a "your move" attention (see ``core.attention_gate``). ``None``
    # takes ``ATTENTION_QUIET_SECONDS``; ``0`` alerts without waiting; tests set
    # an explicit value to avoid sleeping.
    attention_quiet_seconds: float | None = None
    # ``"chat"`` runs the agent over ACP (``mobaide.acp``) and renders the
    # structured chat UI; ``"terminal"`` is the classic PTY session.
    ui: str = "terminal"
    # Chat sessions only: an earlier session the agent stored (for example
    # one started by running the CLI in a terminal) to open instead of a new
    # one. The value is the agent's own session id from ``session/list``.
    chat_resume_id: str | None = None


@dataclass
class SessionInfo:
    session_id: str
    state: SessionState
    created_at: datetime
    pid: int | None
    command: list[str]
    cwd: str | None
    title: str | None = None
    project_name: str | None = None
    # A SessionKind member, or the raw ``custom_<12hex>`` wire string.
    kind: SessionKind | str = SessionKind.CLAUDE
    # Custom-type display-name snapshot from creation time; None for
    # built-in kinds. See ``SessionConfig.kind_label``.
    kind_label: str | None = None
    # Attention state: ``awaiting_permission`` | ``awaiting_os_permission`` |
    # ``turn_finished`` | ``ended`` | ``None``. ``awaiting_os_permission`` is the
    # agent's own startup-stall heuristic (a child producing no output is most
    # likely blocked on an invisible macOS TCC file-access modal); the others
    # are Claude Code hook-driven. Stamped on the wire DTO so a client that
    # missed the one-shot ``session.attention`` event still resyncs from any
    # full-state snapshot. ``exit_reason`` carries the human-readable end
    # cause (Claude Code ``SessionEnd`` reason, or ``process_exited`` on crash).
    attention: str | None = None
    exit_reason: str | None = None
    # ``"terminal"`` (PTY) or ``"chat"`` (ACP); clients pick the screen by it.
    ui: str = "terminal"
    # Whether the agent is mid-turn: ``working`` | ``idle`` | ``None``. Unlike
    # ``attention`` (a one-shot alert the user clears by answering) this is
    # always current, so a list can show which sessions are still busy. Only a
    # running session with a source for it reports a value: Claude Code hooks
    # (terminal) and the turn state (chat). ``None`` means unknown.
    activity: str | None = None


@dataclass
class SessionEvent:
    # "output", "chat", "started", "stopped", "error", "title", "attention",
    # "activity" (``SessionInfo.activity`` changed; refreshes the session row)
    event_type: str
    session_id: str
    # Output events carry raw PTY bytes and chat events one encoded chat
    # frame payload; other event types leave this None.
    data: bytes | None = None
    timestamp: float = field(default_factory=time.time)
    # For ``attention`` events: the trigger reason (``awaiting_permission`` |
    # ``awaiting_os_permission`` | ``turn_finished`` | ``ended``). ``None`` marks
    # a *clear* (user responded), which refreshes the snapshot but is not a
    # notification trigger.
    attention: str | None = None


def _resolve_session_kind(
    cli: str | None,
    claude_options: "ClaudeOptions | None",
    codex_options: "CodexOptions | None",
    antigravity_options: "AntigravityOptions | None",
) -> SessionKind:
    """Pick the managed CLI from an explicit ``cli`` plus options presence.

    Explicit ``cli`` wins; otherwise the kind is inferred from whichever options
    object the client sent (back-compat: a legacy ``claude_options``-only payload
    with no ``cli`` still resolves to ``CLAUDE``). Anything else is ``SHELL``.
    """
    if cli == SessionKind.ANTIGRAVITY.value or (
        cli is None and antigravity_options is not None
    ):
        return SessionKind.ANTIGRAVITY
    if cli == SessionKind.CODEX.value or (cli is None and codex_options is not None):
        return SessionKind.CODEX
    if cli == SessionKind.CLAUDE.value or (cli is None and claude_options is not None):
        return SessionKind.CLAUDE
    return SessionKind.SHELL


def resolve_spawn_args(
    cli: str | None,
    claude_options: "ClaudeOptions | None",
    codex_options: "CodexOptions | None",
    antigravity_options: "AntigravityOptions | None",
    command_hint: list[str] | None,
    env_hint: dict[str, str] | None,
    default_shell: list[str],
    resolved_command: str | None = None,
) -> tuple[
    SessionKind | str,
    list[str],
    dict[str, str] | None,
    "ClaudeOptions | None",
    "CodexOptions | None",
    "AntigravityOptions | None",
]:
    """Resolve ``(kind, command, env, claude_options, codex_options,
    antigravity_options)``.

    The single source of truth for the Terminal-mode security guard: for a
    non-managed (shell) request the spawn always uses ``default_shell`` with a
    clean env, regardless of any client-supplied ``command``/``env`` hints. This
    prevents an authenticated client from piggybacking arbitrary argv into a
    project's cwd via the Terminal path. Managed (Claude/Codex/Antigravity)
    requests honour the hints (overwritten later by the launcher inside
    ``SessionManager``).

    Custom agent types (``cli`` matching ``CUSTOM_KIND_PATTERN``) keep the
    same guard posture: the client hints are discarded and the spawn argv is
    built exclusively from ``resolved_command`` — the stored command the
    relay injected after its registry lookup. Only the relay can author that
    field on any path that reaches this function, so argv authority stays
    server-side. A custom ``cli`` without ``resolved_command`` (agent-direct
    REST, unknown/deleted id, malformed id) raises
    ``CustomCommandUnresolvedError`` — never a silent bare-shell downgrade.

    Also normalises the options so exactly the chosen CLI's object is set —
    defaulted when the client gave an explicit ``cli`` but omitted the options
    block — and the others are cleared, keeping ``kind`` / options / launcher
    choice consistent for ``SessionManager`` dispatch.
    """
    if cli is not None and cli.startswith("custom_"):
        if CUSTOM_KIND_PATTERN.fullmatch(cli) is None:
            raise CustomCommandUnresolvedError(
                f"Invalid custom agent type kind: {cli!r}"
            )
        if resolved_command is None:
            raise CustomCommandUnresolvedError(
                "Custom agent types can only be launched through the relay"
            )
        return cli, build_custom_argv(resolved_command), None, None, None, None
    kind = _resolve_session_kind(
        cli, claude_options, codex_options, antigravity_options
    )
    if kind is SessionKind.ANTIGRAVITY:
        from .antigravity_launcher import AntigravityOptions

        return (
            kind,
            command_hint or default_shell,
            env_hint,
            None,
            None,
            antigravity_options or AntigravityOptions(),
        )
    if kind is SessionKind.CODEX:
        from .codex_launcher import CodexOptions

        return (
            kind,
            command_hint or default_shell,
            env_hint,
            None,
            codex_options or CodexOptions(),
            None,
        )
    if kind is SessionKind.CLAUDE:
        from .claude_launcher import ClaudeOptions

        return (
            kind,
            command_hint or default_shell,
            env_hint,
            claude_options or ClaudeOptions(),
            None,
            None,
        )
    return SessionKind.SHELL, default_shell, None, None, None, None


def session_to_wire_dict(info: SessionInfo) -> dict[str, Any]:
    """Canonical wire shape for a single session, shared by every producer.

    Every websocket envelope that contains a session object (``sessions.changed``,
    ``session.updated``, the ``/ws/sessions`` snapshot, the ``/sessions`` REST
    list, the SessionManager publish path) MUST build the dict through this
    helper. Adding or renaming a field here updates every producer in one
    place, preventing the drift that previously hid behind five duplicated
    literal dict-comprehensions.
    """
    kind = info.kind
    return {
        "session_id": info.session_id,
        "state": info.state.value,
        "created_at": info.created_at.isoformat(),
        "pid": info.pid,
        "command": info.command,
        "cwd": info.cwd,
        "title": info.title,
        "project_name": info.project_name,
        "kind": kind.value if isinstance(kind, SessionKind) else kind,
        "kind_label": info.kind_label,
        "attention": info.attention,
        "exit_reason": info.exit_reason,
        "ui": info.ui,
        "activity": info.activity,
    }
