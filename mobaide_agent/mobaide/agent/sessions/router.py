from __future__ import annotations

import platform
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status

from mobaide.core import (
    CliAuthRequiredError,
    CliBinaryNotFoundError,
    MaxSessionsError,
    SessionConfig,
    SessionManager,
)
from mobaide.core.models import resolve_spawn_args
from mobaide.linear.worktree import WorktreeCreationError

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


def _validate_adhoc_cwd(cwd: str) -> None:
    """Pre-validate a user-supplied custom-path ``cwd``.

    Raises :class:`HTTPException` 400 for empty / non-absolute /
    non-existent / non-directory inputs so the mobile client surfaces a
    clean error instead of a 500 from a downstream ``FileNotFoundError``
    inside ``ClaudeLauncher`` or ``PtyBackend.spawn``.
    """
    if not cwd:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Custom path cannot be empty",
        )
    p = Path(cwd)
    if not p.is_absolute():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Custom path must be absolute: {cwd}",
        )
    if not p.exists():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Custom path does not exist: {cwd}",
        )
    if not p.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Custom path is not a directory: {cwd}",
        )


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
        body.claude_options.to_options()
        if body.claude_options is not None
        else None
    )
    codex_options = (
        body.codex_options.to_options()
        if body.codex_options is not None
        else None
    )
    # Pre-validate user-supplied custom paths so the error surfaces as a
    # clean 400 from this layer rather than as a FileNotFoundError-mapped
    # 400 from the launcher or PtyBackend further down. Skipped when a
    # stored project is named (server derives cwd from the project).
    if body.project_name is None and body.cwd is not None:
        _validate_adhoc_cwd(body.cwd)
    kind, spawn_command, spawn_env, claude_options, codex_options = resolve_spawn_args(
        body.cli, claude_options, codex_options, body.command, body.env, _DEFAULT_SHELL,
    )
    config = SessionConfig(
        session_id=session_id,
        command=spawn_command,
        cwd=body.cwd,
        env=spawn_env,
        rows=body.rows,
        cols=body.cols,
        project_name=body.project_name,
        claude_options=claude_options,
        codex_options=codex_options,
        worktree=body.worktree,
        branch_name=body.branch_name,
        kind=kind,
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
    except CliBinaryNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{exc.cli_name.capitalize()} CLI not found: {exc}",
        )
    except CliAuthRequiredError as exc:
        # CLI present but not signed in: client-actionable (401). The dict detail
        # carries a `<cli>_auth_required` marker so the app shows a login prompt
        # rather than spawning a session that would hang on the CLI's login TUI.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={f"{exc.cli_name}_auth_required": True, "detail": str(exc)},
        )
    except FileNotFoundError as exc:
        # Use ``exc.filename`` (plain str) rather than ``str(exc)`` which
        # embeds ``repr(filename)`` and doubles backslashes — the mobile
        # client would otherwise render ``D:\\\\ML\\\\...`` after one more
        # JSON-encoding layer.
        missing = exc.filename or "project path"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Project path not found: {missing}",
        )
    except WorktreeCreationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Could not create worktree on branch {exc.branch}: "
                f"{exc.stderr}"
            ),
        )
    except OSError as exc:
        strerror = exc.strerror or "cannot launch session"
        filename = exc.filename or ""
        detail = f"{strerror}: {filename}" if filename else strerror
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        )

    # SessionManager.create_session is the single source of truth for
    # `sessions.changed` domain events and fires the eager fanout hook so
    # headless sessions get a SessionEventFanout immediately.
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

    # on_session_removed hook (wired in app.py) handles fanout cleanup;
    # stop_session also publishes `sessions.changed`.
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
    sliced = buf[-lines:] if lines < len(buf) else buf
    return ScrollbackResponse(
        session_id=session_id,
        lines=sliced,
        total=len(buf),
    )
