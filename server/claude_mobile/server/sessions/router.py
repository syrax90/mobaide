from __future__ import annotations

import platform
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status

from claude_mobile.core import (
    ClaudeOptions,
    MaxSessionsError,
    SessionConfig,
    SessionManager,
)

from ..auth.dependencies import get_current_user
from ..logging import audit_event, get_logger
from .models import CreateSessionRequest, ScrollbackResponse, SessionResponse

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/sessions",
    tags=["sessions"],
    dependencies=[Depends(get_current_user)],
)

_DEFAULT_SHELL = ["cmd.exe"] if platform.system() == "Windows" else ["/bin/bash"]


def _get_manager(request: Request) -> SessionManager:
    return request.app.state.session_manager


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: CreateSessionRequest,
    request: Request,
) -> SessionResponse:
    """Create a new PTY session."""
    manager = _get_manager(request)
    session_id = uuid.uuid4().hex[:12]
    claude_options = (
        ClaudeOptions(**body.claude_options.model_dump())
        if body.claude_options is not None
        else None
    )
    # Project-aware launch ignores raw command/cwd/env — they will be
    # overwritten by ClaudeLauncher inside SessionManager.
    config = SessionConfig(
        session_id=session_id,
        command=body.command or _DEFAULT_SHELL,
        cwd=body.cwd,
        env=body.env,
        rows=body.rows,
        cols=body.cols,
        project_name=body.project_name,
        claude_options=claude_options,
    )
    try:
        session = await manager.create_session(config)
    except MaxSessionsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc).strip("'"),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    # SessionManager.create_session is the single source of truth for
    # `sessions.changed` (Domain Events). It also fires the eager fanout
    # hook so headless sessions get a SessionEventFanout immediately.
    # We only need the legacy `/ws/sessions` broadcaster nudge here.
    broadcaster = getattr(request.app.state, "broadcaster", None)
    if broadcaster:
        await broadcaster.notify(manager.list_sessions())

    audit_event(
        "audit.session.created",
        actor=getattr(request.state, "username", None),
        target=session_id,
        outcome="ok",
        command=config.command,
    )
    return SessionResponse.from_info(session.to_info())


@router.get("", response_model=list[SessionResponse])
async def list_sessions(request: Request) -> list[SessionResponse]:
    """List all active sessions."""
    manager = _get_manager(request)
    return [SessionResponse.from_info(info) for info in manager.list_sessions()]


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def stop_session(
    session_id: str,
    request: Request,
    force: bool = False,
) -> None:
    """Stop a session by ID."""
    manager = _get_manager(request)
    try:
        await manager.stop_session(session_id, force=force)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found",
        )

    # Fanout cleanup is performed by the on_session_removed hook wired in
    # app.py; SessionManager.stop_session also publishes `sessions.changed`.
    broadcaster = getattr(request.app.state, "broadcaster", None)
    if broadcaster:
        await broadcaster.notify(manager.list_sessions())

    audit_event(
        "audit.session.terminated",
        actor=getattr(request.state, "username", None),
        target=session_id,
        outcome="ok",
        force=force,
    )


@router.get("/{session_id}/scrollback", response_model=ScrollbackResponse)
async def get_scrollback(
    session_id: str,
    request: Request,
    lines: int = 1000,
) -> ScrollbackResponse:
    """Return the scrollback buffer for a session (for reconnection)."""
    manager = _get_manager(request)
    session = await manager.get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found",
        )
    buf = session.get_scrollback()
    # Return last N lines
    sliced = buf[-lines:] if lines < len(buf) else buf
    return ScrollbackResponse(
        session_id=session_id,
        lines=sliced,
        total=len(buf),
    )
