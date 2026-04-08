from __future__ import annotations

import asyncio
import json
from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, status
from starlette.websockets import WebSocketDisconnect

from claude_mobile.linear.scanner import (
    AutoScanAlreadyRunning,
    AutoScanCapacityReached,
    AutoScanInstance,
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


# -- WebSocket for real-time scan events --

ws_router = APIRouter(tags=["autoscan-ws"])


@ws_router.websocket("/ws/autoscan")
async def ws_autoscan(websocket: WebSocket) -> None:
    """Stream auto-scan events.

    Protocol (v1):
      Client may send (within 200ms of accept) a JSON text frame:
        { "subscribe": ["projectA", ...],
          "resume_from_seq": {"projectA": 42, ...} }
      The server then sends:
        1. { "type": "snapshot",
             "data": { "scans": [...DTOs...],
                       "events": {projectName: [event,...]} } }
        2. { "type": "caught_up" }
        3. Live { "type": "event", "project_name": ..., "event": {...} } frames.
      Clients NOT in `subscribe` get all event types except `log` filtered out.
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

    # Try to read an initial config frame (non-blocking-ish, 200 ms timeout).
    resume_from_seq: dict[str, int] = {}
    subscribe_set: set[str] = set()
    try:
        first = await asyncio.wait_for(websocket.receive_text(), timeout=0.2)
        try:
            payload = json.loads(first)
            rfs = payload.get("resume_from_seq") or {}
            if isinstance(rfs, dict):
                resume_from_seq = {str(k): int(v) for k, v in rfs.items()}
            sub = payload.get("subscribe") or []
            if isinstance(sub, list):
                subscribe_set = {str(s) for s in sub}
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    except asyncio.TimeoutError:
        pass
    except Exception:
        pass

    # Subscribe to all current instances; new instances are picked up via
    # the central event_bus topic and we re-subscribe in the pump loop.
    instances: dict[str, AutoScanInstance] = {
        i.project_name: i for i in mgr.all_instances()
    }
    queues: dict[str, asyncio.Queue] = {
        name: inst.subscribe_events() for name, inst in instances.items()
    }

    async def _send(obj: dict) -> None:
        await websocket.send_text(json.dumps(obj))

    msg_count = 0
    try:
        # Build snapshot
        events_snapshot: dict[str, list[dict]] = {}
        for name, inst in instances.items():
            ring = [e.to_dict() for e in inst.recent_events]
            resume_seq = resume_from_seq.get(name)
            if resume_seq is not None and ring:
                # If the requested seq is still in (or one before) the ring, replay forward.
                first_seq = ring[0]["seq"]
                if first_seq <= resume_seq + 1:
                    ring = [e for e in ring if e["seq"] > resume_seq]
            events_snapshot[name] = ring

        await _send({
            "type": "snapshot",
            "data": {
                "scans": mgr.list_response()["scans"],
                "events": events_snapshot,
            },
        })
        msg_count += 1

        await _send({"type": "caught_up"})
        msg_count += 1

        async def _pump_one(project_name: str, q: asyncio.Queue) -> None:
            nonlocal msg_count
            while True:
                evt = await q.get()
                # Filter heavy `log` events for clients not subscribed.
                if evt.type == "log" and project_name not in subscribe_set:
                    continue
                await _send({
                    "type": "event",
                    "project_name": project_name,
                    "event": evt.to_dict(),
                })
                msg_count += 1

        async def _reader() -> None:
            # Continue accepting subscribe updates after snapshot.
            # Returns cleanly on client disconnect so the surrounding
            # asyncio.wait() does not surface a "Task exception was never
            # retrieved" log entry on the normal close path.
            nonlocal subscribe_set
            try:
                while True:
                    text = await websocket.receive_text()
                    try:
                        obj = json.loads(text)
                        sub = obj.get("subscribe")
                        if isinstance(sub, list):
                            subscribe_set = {str(s) for s in sub}
                    except (json.JSONDecodeError, ValueError):
                        pass
            except WebSocketDisconnect:
                return

        if not queues:
            # No active scans — still keep the socket open for reader/late events.
            await _reader()
            return

        pump_tasks = [
            asyncio.create_task(_pump_one(n, q)) for n, q in queues.items()
        ]
        reader_task = asyncio.create_task(_reader())
        done, pending = await asyncio.wait(
            [*pump_tasks, reader_task], return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
    except Exception as exc:
        log_ws_error(channel, exc, stage="stream")
    finally:
        for name, q in queues.items():
            inst = mgr.get(name)
            if inst is not None:
                inst.unsubscribe_events(q)
        log_ws_disconnect(channel, username=username, msg_count=msg_count)
