from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, status

from claude_mobile.linear.scanner import AutoScanService

from ..auth.dependencies import get_current_user, ws_authenticate
from .models import ScanStatusResponse, StartScanRequest

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/autoscan",
    tags=["autoscan"],
    dependencies=[Depends(get_current_user)],
)


def _get_service(request: Request) -> AutoScanService:
    svc = getattr(request.app.state, "auto_scan_service", None)
    if svc is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Auto scan is not configured (PROJECTS env var not set)",
        )
    return svc


@router.post("/start", status_code=status.HTTP_200_OK)
async def start_scan(body: StartScanRequest, request: Request) -> dict:
    """Start the auto-scan polling loop for a configured project."""
    svc = _get_service(request)
    try:
        await svc.start(body.project_name)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return {"message": f"Auto scan started for project '{body.project_name}'"}


@router.post("/stop", status_code=status.HTTP_200_OK)
async def stop_scan(request: Request) -> dict:
    """Stop the auto-scan polling loop."""
    svc = _get_service(request)
    await svc.stop()
    return {"message": "Auto scan stopped"}


@router.get("/status", response_model=ScanStatusResponse)
async def scan_status(request: Request) -> ScanStatusResponse:
    """Return current auto-scan status."""
    svc = _get_service(request)
    config = request.app.state.config
    status_data = svc.get_status()
    available = (
        list(config.auto_scan_config.projects.keys())
        if config.auto_scan_config
        else []
    )
    return ScanStatusResponse(
        available_projects=available,
        **status_data,
    )


# -- WebSocket for real-time scan events --

ws_router = APIRouter(tags=["autoscan-ws"])


@ws_router.websocket("/ws/autoscan")
async def ws_autoscan(websocket: WebSocket) -> None:
    """Stream auto-scan events to the mobile client in real time."""
    await websocket.accept()

    try:
        await ws_authenticate(websocket)
    except Exception:
        await websocket.close(code=4001, reason="Authentication failed")
        return

    svc = getattr(websocket.app.state, "auto_scan_service", None)
    if svc is None:
        await websocket.close(
            code=4002, reason="Auto scan not configured"
        )
        return

    queue = svc.subscribe_events()
    try:
        # Send current status as first message
        await websocket.send_text(json.dumps(svc.get_status()))

        # Stream events
        while True:
            event = await queue.get()
            await websocket.send_text(
                json.dumps(
                    {
                        "event_type": event.event_type,
                        "timestamp": event.timestamp,
                        "data": event.data,
                    }
                )
            )
    except Exception:
        pass
    finally:
        svc.unsubscribe_events(queue)
