from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

try:
    from mobaide.agent.logging import audit_event, get_logger, log_call
except Exception:  # pragma: no cover - logging package optional during isolated tests
    import logging
    def get_logger(name): return logging.getLogger(name)
    def log_call(*args, **kwargs):
        def deco(fn): return fn
        return deco
    def audit_event(*args, **kwargs): pass

from .antigravity_launcher import AntigravityLauncher, AntigravityOptions
from .claude_launcher import (
    AdHocLaunch,
    ClaudeLauncher,
    ClaudeOptions,
    check_cwd_exists,
)
from .cli_launcher import CliLauncher
from .codex_launcher import CodexLauncher, CodexOptions
from .models import (
    SessionConfig,
    SessionInfo,
    SessionState,
    session_to_wire_dict,
)
from .projects_store import ProjectsStore, safe_path_exists
from .pty_backend import PtyBackend, create_pty_backend
from .session import TerminalSession

if TYPE_CHECKING:
    from mobaide.linear.worktree import WorktreeManager

logger = get_logger(__name__)


class MaxSessionsError(RuntimeError):
    """Raised when the session limit has been reached."""


class SessionManager:
    """Manages multiple TerminalSession instances with liveness monitoring."""

    def __init__(
        self,
        max_sessions: int = 16,
        projects_store: ProjectsStore | None = None,
        launcher: ClaudeLauncher | None = None,
        codex_launcher: CodexLauncher | None = None,
        antigravity_launcher: AntigravityLauncher | None = None,
        worktree_mgr: WorktreeManager | None = None,
        event_bus: Any | None = None,
        on_session_created: Callable[[TerminalSession], Awaitable[None] | None] | None = None,
        on_session_removed: Callable[[str], Awaitable[None] | None] | None = None,
        session_dto_factory: Callable[[SessionInfo], dict] | None = None,
        backend_factory: Callable[[], PtyBackend] = create_pty_backend,
    ) -> None:
        self._sessions: dict[str, TerminalSession] = {}
        self._lock = asyncio.Lock()
        self._max_sessions = max_sessions
        self._liveness_task: asyncio.Task[None] | None = None
        self._projects_store = projects_store
        # ``launcher`` keeps its historical name (the Claude launcher); the
        # Codex and Antigravity launchers are siblings. Dispatch picks between
        # them per session.
        self._launcher = launcher
        self._codex_launcher = codex_launcher
        self._antigravity_launcher = antigravity_launcher
        self._worktree_mgr = worktree_mgr
        self._event_bus = event_bus
        self._on_session_created = on_session_created
        self._on_session_removed = on_session_removed
        self._session_dto_factory = session_dto_factory or session_to_wire_dict
        # Per-session PTY backend constructor. Defaults to the real
        # platform backend; ``create_app`` swaps in ``FakePtyBackend`` when
        # ``MOBAIDE_ENV=test`` so a disposable agent spawns no real shells.
        self._backend_factory = backend_factory

    async def _publish_sessions_changed(self) -> None:
        if self._event_bus is None:
            return
        sessions_now = self.list_sessions()
        try:
            self._event_bus.publish(
                "sessions.changed",
                {"sessions": [self._session_dto_factory(s) for s in sessions_now]},
            )
        except Exception:
            logger.exception("session_manager.publish_failed")

    @property
    def max_sessions(self) -> int:
        return self._max_sessions

    @log_call(name="session_manager.create_session", log_args=("config",))
    async def create_session(self, config: SessionConfig) -> TerminalSession:
        """Create, start, and register a new terminal session.

        Dispatch matrix on (``project_name``, managed-CLI options). A session is
        "managed" when it carries ``claude_options``, ``codex_options``, or
        ``antigravity_options`` — the entry layers normalise exactly one of them via
        ``resolve_spawn_args``, so a raw shell session (none set) keeps its
        caller-supplied argv:

        * stored project + managed → ``_apply_project_launch`` builds full
          argv/cwd/env via the matching launcher against ``ProjectsStore``.
        * stored project + shell → ``_resolve_project_cwd`` only fills ``cwd`` so
          the caller's login-shell (or custom-type) argv stays intact.
        * no project + managed → ``_apply_adhoc_launch`` builds a launch against
          the bare ``config.cwd`` (custom-path mode); never touches
          ``ProjectsStore``.
        * no project + shell → raw Terminal session in ``config.cwd`` (or the
          default cwd if ``cwd`` is also ``None``); no resolution required.
        """
        managed = (
            config.claude_options is not None
            or config.codex_options is not None
            or config.antigravity_options is not None
        )
        if config.project_name is not None:
            if managed:
                await self._apply_project_launch(config)
            else:
                await self._resolve_project_cwd(config)
        elif managed:
            await self._apply_adhoc_launch(config)
        async with self._lock:
            if len(self._sessions) >= self._max_sessions:
                raise MaxSessionsError(
                    f"Maximum session limit ({self._max_sessions}) reached",
                )
            if config.session_id in self._sessions:
                raise ValueError(
                    f"Session with id '{config.session_id}' already exists",
                )
            session = TerminalSession(config, backend=self._backend_factory())
            await session.start()
            self._sessions[config.session_id] = session
            logger.info(
                "session.created",
                session_id=config.session_id,
                active=len(self._sessions),
                max_sessions=self._max_sessions,
            )
        # Eager hook (e.g. attach SessionEventFanout) so headless sessions
        # — like Auto Scan subsessions — start fanning out events without
        # requiring a /ws/session/{id} client to first attach.
        if self._on_session_created is not None:
            try:
                result = self._on_session_created(session)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("session_manager.on_created_hook_failed")
        # Single source of truth for sessions.changed.
        await self._publish_sessions_changed()
        return session

    def _launcher_and_options(
        self, config: SessionConfig,
    ) -> tuple[CliLauncher, ClaudeOptions | CodexOptions | AntigravityOptions]:
        """Pick the launcher + options for a managed session.

        ``resolve_spawn_args`` guarantees exactly one of ``claude_options`` /
        ``codex_options`` / ``antigravity_options`` is set, so presence is the
        discriminator. Raises if the requested CLI's launcher was not wired into
        this manager.
        """
        if config.antigravity_options is not None:
            if self._antigravity_launcher is None:
                raise ValueError(
                    "antigravity session requested but agent has no Antigravity launcher",
                )
            return self._antigravity_launcher, config.antigravity_options
        if config.codex_options is not None:
            if self._codex_launcher is None:
                raise ValueError(
                    "codex session requested but agent has no Codex launcher",
                )
            return self._codex_launcher, config.codex_options
        if self._launcher is None:
            raise ValueError(
                "claude session requested but agent has no Claude launcher",
            )
        return self._launcher, config.claude_options or ClaudeOptions()

    async def _apply_project_launch(self, config: SessionConfig) -> None:
        """Resolve a project via the matching launcher and overwrite command/cwd/env in place.

        The lookup goes through the live ``ProjectsStore`` so callers always
        see post-mutation state (e.g. a project added via the REST CRUD API
        is launchable without a restart).
        """
        if self._projects_store is None:
            raise ValueError(
                "project_name set but agent has no projects store",
            )
        project = self._projects_store.get(config.project_name or "")
        if project is None:
            raise KeyError(f"Project '{config.project_name}' not found")
        launcher, options = self._launcher_and_options(config)
        spec = await launcher.build(project, options, session_id=config.session_id)
        config.command = spec.argv
        config.cwd = spec.cwd
        config.env = spec.env

    async def _apply_adhoc_launch(self, config: SessionConfig) -> None:
        """Build an ad-hoc managed-CLI launch against ``config.cwd`` and overwrite command/cwd/env in place.

        Never touches ``ProjectsStore`` — a custom-path session is not a
        project. Router/relay layers are expected to pre-validate that
        ``cwd`` is non-empty, absolute, exists, and is a directory; the
        ``ValueError`` raised here is defense-in-depth.
        """
        if config.cwd is None:
            raise ValueError("cwd is required for ad-hoc launch")
        launcher, options = self._launcher_and_options(config)
        target = AdHocLaunch(cwd=config.cwd)
        spec = await launcher.build(target, options, session_id=config.session_id)
        config.command = spec.argv
        config.cwd = spec.cwd
        config.env = spec.env

    async def _resolve_project_cwd(self, config: SessionConfig) -> None:
        """Resolve the project ``cwd`` (optionally a worktree) without invoking ``ClaudeLauncher``.

        Used by the Terminal mode path (and custom agent types): caller has
        set ``config.command`` to the login shell or the resolved custom
        command and we just need the PTY to spawn in the project's
        working directory — or, when ``config.worktree`` is set, in a freshly
        created git worktree of that project. Raises the same ``ValueError`` /
        ``KeyError`` shapes ``_apply_project_launch`` uses so router/relay
        error mapping stays unchanged.
        """
        if self._projects_store is None:
            raise ValueError(
                "project_name set but agent has no projects store",
            )
        project = self._projects_store.get(config.project_name or "")
        if project is None:
            raise KeyError(f"Project '{config.project_name}' not found")
        if config.worktree:
            config.cwd = await self._resolve_worktree_cwd(
                project.path, config.branch_name,
            )
        else:
            config.cwd = project.path

    async def _resolve_worktree_cwd(
        self, project_path: str, branch_name: str | None
    ) -> str:
        """Create a git worktree for a Terminal (SHELL) session and return its path.

        Mirrors ``ClaudeLauncher._resolve_cwd``'s Standard-mode guards: a stale
        client requesting a worktree for a non-git project is downgraded to the
        project root (logged), and a missing ``WorktreeManager`` is a server
        misconfiguration. ``WorktreeCreationError`` propagates for the
        router/relay to map to a clean HTTP 400.
        """
        if not safe_path_exists(Path(project_path) / ".git"):
            logger.warning(
                "worktree=true requested for non-git project %r; ignoring",
                project_path,
            )
            return project_path
        if self._worktree_mgr is None:
            raise RuntimeError(
                "worktree=true requested but no WorktreeManager is configured; "
                "this is a server misconfiguration"
            )
        from mobaide.linear.worktree import create_standard_worktree

        result = await create_standard_worktree(
            self._worktree_mgr,
            project_path,
            branch_name,
            fallback_prefix="terminal-session",
        )
        return check_cwd_exists(result.path)

    async def get_session(self, session_id: str) -> TerminalSession | None:
        """Look up a session by ID."""
        return self._sessions.get(session_id)

    def list_sessions(self) -> list[SessionInfo]:
        """Return lightweight DTOs for all sessions."""
        return [s.to_info() for s in self._sessions.values()]

    @log_call(name="session_manager.stop_session", log_args=("session_id", "force"))
    async def stop_session(self, session_id: str, force: bool = False) -> None:
        """Stop and remove a session."""
        async with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            raise KeyError(f"Session '{session_id}' not found")
        await session.stop(force=force)
        logger.info("session.removed", session_id=session_id)
        audit_event(
            "audit.session.terminated",
            target=session_id,
            outcome="ok",
            force=force,
            via="session_manager",
        )
        if self._on_session_removed is not None:
            try:
                result = self._on_session_removed(session_id)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("session_manager.on_removed_hook_failed")
        await self._publish_sessions_changed()

    async def stop_all(self) -> None:
        """Stop all sessions concurrently."""
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        if sessions:
            await asyncio.gather(
                *(s.stop(force=True) for s in sessions),
                return_exceptions=True,
            )
            logger.info("All %d sessions stopped", len(sessions))

    async def start_liveness_monitor(self, interval: float = 5.0) -> None:
        """Start a background task that checks session liveness periodically."""
        if self._liveness_task is not None and not self._liveness_task.done():
            return
        self._liveness_task = asyncio.create_task(
            self._liveness_loop(interval),
            name="session-liveness-monitor",
        )
        logger.info("Liveness monitor started (interval=%.1fs)", interval)

    async def _liveness_loop(self, interval: float) -> None:
        """Periodically check all sessions for crashed processes."""
        while True:
            await asyncio.sleep(interval)
            try:
                await self._check_liveness()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("Liveness check failed", exc_info=True)

    async def _check_liveness(self) -> None:
        """Detect sessions whose underlying process has died unexpectedly."""
        dead_ids: list[str] = []
        # Snapshot under the lock: concurrent create_session/stop_session mutate
        # _sessions (under the same lock), so iterating the live dict here would
        # risk "dictionary changed size during iteration".
        async with self._lock:
            snapshot = list(self._sessions.items())
        for sid, session in snapshot:
            if session.state == SessionState.RUNNING and not session._backend.is_alive():
                runtime_seconds = None
                try:
                    from datetime import UTC, datetime
                    runtime_seconds = (
                        datetime.now(UTC) - session.created_at
                    ).total_seconds()
                except Exception:
                    pass
                logger.warning(
                    "session.crashed",
                    session_id=sid,
                    pid=session.pid,
                    command=getattr(session, "_config", None)
                    and session._config.command,
                    runtime_seconds=runtime_seconds,
                )
                session._state = SessionState.CRASHED
                # Record a reason for the snapshot when the process died without
                # a Claude Code SessionEnd hook (real crash / kill). Never
                # overwrite a hook-supplied reason that may already be set.
                session._exit_reason = session._exit_reason or "process_exited"
                dead_ids.append(sid)

        # Clean up STOPPED/CRASHED sessions
        async with self._lock:
            for sid in list(self._sessions):
                if self._sessions[sid].state in (
                    SessionState.STOPPED,
                    SessionState.CRASHED,
                ):
                    self._sessions.pop(sid, None)
                    logger.info("Cleaned up dead session: %s", sid)

    async def shutdown(self) -> None:
        """Stop the liveness monitor and all sessions."""
        if self._liveness_task is not None and not self._liveness_task.done():
            self._liveness_task.cancel()
            try:
                await self._liveness_task
            except asyncio.CancelledError:
                pass
            self._liveness_task = None
        await self.stop_all()
        logger.info("SessionManager shut down")
