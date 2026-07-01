from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .claude_launcher import ClaudeOptions  # noqa: F401
    from .codex_launcher import CodexOptions  # noqa: F401
    from .gemini_launcher import GeminiOptions  # noqa: F401


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
    (create with ``cli="codex"`` / ``codex_options``). ``GEMINI`` — Gemini CLI
    launched via ``GeminiLauncher`` (create with ``cli="gemini"`` /
    ``gemini_options``). ``AUTO_SCAN`` — sub-sessions spawned by the Linear
    scanner; argv/cwd/env pre-built by the scanner. ``SHELL`` — raw shell
    (``_DEFAULT_SHELL``) anchored to a project's cwd; bypasses the managed-CLI
    launchers entirely.
    """

    CLAUDE = "claude"
    CODEX = "codex"
    GEMINI = "gemini"
    AUTO_SCAN = "auto_scan"
    SHELL = "shell"


@dataclass
class SessionConfig:
    session_id: str
    command: list[str]
    cwd: str | None = None
    env: dict[str, str] | None = None
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
    # Set when the session runs the Gemini CLI. Mutually exclusive with the other
    # options blocks; ``SessionManager`` dispatches to ``GeminiLauncher``.
    gemini_options: GeminiOptions | None = None
    kind: SessionKind = SessionKind.CLAUDE
    # Terminal-mode worktree request. Consumed only on the SHELL+project path
    # (``SessionManager._resolve_project_cwd``); kept separate from
    # ``claude_options`` so a Terminal session can create a worktree while
    # staying ``SessionKind.SHELL`` (Claude sessions use claude_options.worktree).
    worktree: bool = False
    branch_name: str | None = None
    # Seconds after spawn to wait before flagging a session that produced
    # essentially no output (< STARTUP_STALL_MIN_BYTES) as stalled — almost
    # always a macOS file-access (TCC) modal blocking the child invisibly on the
    # host's desktop, which a remote client cannot see. ``None`` lets
    # ``TerminalSession.start`` pick the platform default (enabled on macOS,
    # disabled elsewhere); ``0`` disables it; tests set an explicit value.
    startup_stall_seconds: float | None = None


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
    kind: SessionKind = SessionKind.CLAUDE
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


@dataclass
class SessionEvent:
    event_type: str  # "output", "started", "stopped", "error", "title", "attention"
    session_id: str
    # Output events carry raw PTY bytes; other event types leave this None.
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
    gemini_options: "GeminiOptions | None",
) -> SessionKind:
    """Pick the managed CLI from an explicit ``cli`` plus options presence.

    Explicit ``cli`` wins; otherwise the kind is inferred from whichever options
    object the client sent (back-compat: a legacy ``claude_options``-only payload
    with no ``cli`` still resolves to ``CLAUDE``). Anything else is ``SHELL``.
    """
    if cli == SessionKind.GEMINI.value or (cli is None and gemini_options is not None):
        return SessionKind.GEMINI
    if cli == SessionKind.CODEX.value or (cli is None and codex_options is not None):
        return SessionKind.CODEX
    if cli == SessionKind.CLAUDE.value or (cli is None and claude_options is not None):
        return SessionKind.CLAUDE
    return SessionKind.SHELL


def resolve_spawn_args(
    cli: str | None,
    claude_options: "ClaudeOptions | None",
    codex_options: "CodexOptions | None",
    gemini_options: "GeminiOptions | None",
    command_hint: list[str] | None,
    env_hint: dict[str, str] | None,
    default_shell: list[str],
) -> tuple[
    SessionKind,
    list[str],
    dict[str, str] | None,
    "ClaudeOptions | None",
    "CodexOptions | None",
    "GeminiOptions | None",
]:
    """Resolve ``(kind, command, env, claude_options, codex_options, gemini_options)``.

    The single source of truth for the Terminal-mode security guard: for a
    non-managed (shell) request the spawn always uses ``default_shell`` with a
    clean env, regardless of any client-supplied ``command``/``env`` hints. This
    prevents an authenticated client from piggybacking arbitrary argv into a
    project's cwd via the Terminal path. Managed (Claude/Codex/Gemini) requests
    honour the hints (overwritten later by the launcher inside ``SessionManager``).

    Also normalises the options so exactly the chosen CLI's object is set —
    defaulted when the client gave an explicit ``cli`` but omitted the options
    block — and the others are cleared, keeping ``kind`` / options / launcher
    choice consistent for ``SessionManager`` dispatch.
    """
    kind = _resolve_session_kind(cli, claude_options, codex_options, gemini_options)
    if kind is SessionKind.GEMINI:
        from .gemini_launcher import GeminiOptions

        return (
            kind,
            command_hint or default_shell,
            env_hint,
            None,
            None,
            gemini_options or GeminiOptions(),
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
    return {
        "session_id": info.session_id,
        "state": info.state.value,
        "created_at": info.created_at.isoformat(),
        "pid": info.pid,
        "command": info.command,
        "cwd": info.cwd,
        "title": info.title,
        "project_name": info.project_name,
        "kind": info.kind.value,
        "attention": info.attention,
        "exit_reason": info.exit_reason,
    }
