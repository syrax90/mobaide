from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, status
from starlette.websockets import WebSocketDisconnect

from claude_mobile.linear.scanner import (
    AutoScanAlreadyRunning,
    AutoScanCapacityReached,
    AutoScanSessionManager,
)

from ..auth.dependencies import WebSocketAuthError, get_current_user, ws_authenticate
from ..logging import get_logger
from ..ws.logging import (
    log_ws_authenticated,
    log_ws_connect,
    log_ws_disconnect,
    log_ws_error,
)
from .models import (
    AutoScanInstanceStatusResponse,
    AutoScanListResponse,
    RunNowRequest,
    RunNowResponse,
    StartScanRequest,
    StopScanRequest,
)
from .stream import run_autoscan_stream

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/autoscan",
    tags=["autoscan"],
    dependencies=[Depends(get_current_user)],
)


def _get_manager(request: Request) -> AutoScanSessionManager:
    mgr = getattr(request.app.state, "autoscan_manager", None)
    if mgr is None:
        # Manager is always wired in at startup; absence means a misconfigured app.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Auto scan manager is not initialised",
        )
    return mgr


@router.post("/start", status_code=status.HTTP_200_OK)
async def start_scan(body: StartScanRequest, request: Request) -> dict:
    """Start an auto scan for a project. 409 if already running, 429 if at cap."""
    mgr = _get_manager(request)
    claude_options = (
        body.claude_options.to_options()
        if body.claude_options is not None
        else None
    )
    try:
        instance = await mgr.start(body.project_name, client_options=claude_options)
    except AutoScanAlreadyRunning as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except AutoScanCapacityReached as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return {"message": f"Auto scan started for project '{instance.project_name}'"}


@router.post("/stop", status_code=status.HTTP_200_OK)
async def stop_scan(body: StopScanRequest, request: Request) -> dict:
    mgr = _get_manager(request)
    try:
        await mgr.stop(body.project_name)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return {"message": f"Auto scan stopped for project '{body.project_name}'"}


@router.post("/run-now", response_model=RunNowResponse, status_code=status.HTTP_200_OK)
async def run_now(body: RunNowRequest, request: Request) -> RunNowResponse:
    """Trigger an immediate scan cycle for a running auto scan.

    Returns the freshly anchored ``next_scan_at`` so the caller can
    rebind its countdown synchronously, without waiting for the bus event.
    """
    mgr = _get_manager(request)
    instance = mgr.get(body.project_name)
    if instance is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active auto scan for project '{body.project_name}'",
        )
    instance.request_immediate_scan()
    next_at = instance.to_dto().get("next_scan_at")
    return RunNowResponse(
        ok=True,
        project_name=body.project_name,
        next_scan_at=next_at,
    )


@router.get("/list", response_model=AutoScanListResponse)
async def list_scans(request: Request) -> AutoScanListResponse:
    mgr = _get_manager(request)
    return AutoScanListResponse(**mgr.list_response())


@router.get("/status", response_model=AutoScanInstanceStatusResponse)
async def scan_status(
    request: Request, project_name: str = Query(...)
) -> AutoScanInstanceStatusResponse:
    mgr = _get_manager(request)
    instance = mgr.get(project_name)
    if instance is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active auto scan for project '{project_name}'",
        )
    return AutoScanInstanceStatusResponse(**instance.get_status())


ws_router = APIRouter(tags=["autoscan-ws"])


@ws_router.websocket("/ws/autoscan")
async def ws_autoscan(websocket: WebSocket) -> None:
    """Stream auto-scan events over a direct mobile-to-agent WebSocket.

    Authenticates, resolves the ``AutoScanSessionManager``, then delegates
    the wire protocol to :func:`run_autoscan_stream` so the relay-mode
    bridge can share the same code path.
    """
    channel = "autoscan"
    log_ws_connect(channel, websocket)

    try:
        username = await ws_authenticate(websocket)
    except WebSocketAuthError as exc:
        log_ws_error(channel, exc, stage="auth")
        return
    log_ws_authenticated(channel, username)

    mgr: AutoScanSessionManager | None = getattr(
        websocket.app.state, "autoscan_manager", None
    )
    if mgr is None:
        log_ws_disconnect(channel, reason="not_configured", username=username)
        await websocket.close(code=4002, reason="Auto scan not configured")
        return

    msg_count = 0
    try:
        msg_count = await run_autoscan_stream(
            mgr,
            websocket.send_text,
            websocket.receive_text,
            grace_timeout_s=0.2,
        )
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log_ws_error(channel, exc, stage="stream")
    finally:
        log_ws_disconnect(channel, username=username, msg_count=msg_count)
