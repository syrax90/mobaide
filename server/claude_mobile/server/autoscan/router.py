from __future__ import annotations

import json
from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, status

from claude_mobile.linear.scanner import (
    AutoScanAlreadyRunning,
    AutoScanCapacityReached,
    AutoScanSessionManager,
)

from ..auth.dependencies import get_current_user, ws_authenticate
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
    StartScanRequest,
    StopScanRequest,
)

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/autoscan",
    tags=["autoscan"],
    dependencies=[Depends(get_current_user)],
)


def _get_manager(request: Request) -> AutoScanSessionManager:
    mgr = getattr(request.app.state, "autoscan_manager", None)
    if mgr is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Auto scan is not configured (PROJECTS env var not set)",
        )
    return mgr


@router.post("/start", status_code=status.HTTP_200_OK)
async def start_scan(body: StartScanRequest, request: Request) -> dict:
    """Start an auto scan for a project. 409 if already running, 429 if at cap."""
    mgr = _get_manager(request)
    try:
        instance = await mgr.start(body.project_name)
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
    """Stop the auto scan for a specific project."""
    mgr = _get_manager(request)
    try:
        await mgr.stop(body.project_name)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return {"message": f"Auto scan stopped for project '{body.project_name}'"}


@router.get("/list", response_model=AutoScanListResponse)
async def list_scans(request: Request) -> AutoScanListResponse:
    """Return all currently active auto scans."""
    mgr = _get_manager(request)
    return AutoScanListResponse(**mgr.list_response())


@router.get("/status", response_model=AutoScanInstanceStatusResponse)
async def scan_status(
    request: Request, project_name: str = Query(...)
) -> AutoScanInstanceStatusResponse:
    """Return current status for a single project's auto scan."""
    mgr = _get_manager(request)
    instance = mgr.get(project_name)
    if instance is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active auto scan for project '{project_name}'",
        )
    return AutoScanInstanceStatusResponse(**instance.get_status())


# -- WebSocket for real-time scan events --

ws_router = APIRouter(tags=["autoscan-ws"])


@ws_router.websocket("/ws/autoscan")
async def ws_autoscan(websocket: WebSocket) -> None:
    """Stream auto-scan events to the mobile client.

    Optional query param ``project_name`` filters events to a single scan.
    Without the filter, all per-project events are streamed (each tagged
    with ``project_name``).
    """
    channel = "autoscan"
    await websocket.accept()
    log_ws_connect(channel, websocket)

    try:
        username = await ws_authenticate(websocket)
    except Exception as exc:
        log_ws_error(channel, exc, stage="auth")
        await websocket.close(code=4001, reason="Authentication failed")
        return
    log_ws_authenticated(channel, username)

    mgr: AutoScanSessionManager | None = getattr(
        websocket.app.state, "autoscan_manager", None
    )
    if mgr is None:
        log_ws_disconnect(channel, reason="not_configured", username=username)
        await websocket.close(code=4002, reason="Auto scan not configured")
        return

    project_filter = websocket.query_params.get("project_name")

    # Subscribe to *every* active instance; new instances started after
    # connection are not auto-subscribed (the client will see them via the
    # initial /list snapshot and any subsequent autoscan.list.changed pushes
    # over /ws/events).
    queues_by_project: dict[str, "object"] = {}
    if project_filter is not None:
        instance = mgr.get(project_filter)
        if instance is not None:
            queues_by_project[project_filter] = instance.subscribe_events()
    else:
        for name, inst in list(mgr._scans.items()):  # noqa: SLF001
            queues_by_project[name] = inst.subscribe_events()

    msg_count = 0
    try:
        # Send initial list snapshot
        await websocket.send_text(
            json.dumps({"type": "list", "data": mgr.list_response()})
        )
        msg_count += 1

        if not queues_by_project:
            # Nothing to stream — close cleanly.
            return

        async def _drain_one(project_name: str, q):
            nonlocal msg_count
            while True:
                event = await q.get()
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "event",
                            "project_name": project_name,
                            "event_type": event.event_type,
                            "timestamp": event.timestamp,
                            "data": event.data,
                        }
                    )
                )
                msg_count += 1

        import asyncio as _asyncio
        await _asyncio.gather(
            *(
                _drain_one(name, q)
                for name, q in queues_by_project.items()
            )
        )
    except Exception as exc:
        log_ws_error(channel, exc, stage="stream")
    finally:
        for name, q in queues_by_project.items():
            inst = mgr.get(name)
            if inst is not None:
                inst.unsubscribe_events(q)  # type: ignore[arg-type]
        log_ws_disconnect(channel, username=username, msg_count=msg_count)
