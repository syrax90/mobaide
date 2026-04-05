from __future__ import annotations

import asyncio
import logging

from .models import SessionConfig, SessionInfo, SessionState
from .session import TerminalSession

logger = logging.getLogger(__name__)


class MaxSessionsError(RuntimeError):
    """Raised when the session limit has been reached."""


class SessionManager:
    """Manages multiple TerminalSession instances with liveness monitoring."""

    def __init__(self, max_sessions: int = 16) -> None:
        self._sessions: dict[str, TerminalSession] = {}
        self._lock = asyncio.Lock()
        self._max_sessions = max_sessions
        self._liveness_task: asyncio.Task[None] | None = None

    @property
    def max_sessions(self) -> int:
        return self._max_sessions

    # -- CRUD --

    async def create_session(self, config: SessionConfig) -> TerminalSession:
        """Create, start, and register a new terminal session."""
        async with self._lock:
            if len(self._sessions) >= self._max_sessions:
                raise MaxSessionsError(
                    f"Maximum session limit ({self._max_sessions}) reached",
                )
            if config.session_id in self._sessions:
                raise ValueError(
                    f"Session with id '{config.session_id}' already exists",
                )
            session = TerminalSession(config)
            await session.start()
            self._sessions[config.session_id] = session
            logger.info(
                "Session created: %s (%d/%d)",
                config.session_id,
                len(self._sessions),
                self._max_sessions,
            )
            return session

    async def get_session(self, session_id: str) -> TerminalSession | None:
        """Look up a session by ID."""
        return self._sessions.get(session_id)

    def list_sessions(self) -> list[SessionInfo]:
        """Return lightweight DTOs for all sessions."""
        return [s.to_info() for s in self._sessions.values()]

    async def stop_session(self, session_id: str, force: bool = False) -> None:
        """Stop and remove a session."""
        async with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            raise KeyError(f"Session '{session_id}' not found")
        await session.stop(force=force)
        logger.info("Session removed: %s", session_id)

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

    # -- Liveness monitoring --

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
        for sid, session in self._sessions.items():
            if session.state == SessionState.RUNNING and not session._backend.is_alive():
                logger.warning("Session %s process died — marking as CRASHED", sid)
                session._state = SessionState.CRASHED
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

    # -- Shutdown --

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
