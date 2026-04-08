from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from ..auth.dependencies import WebSocketAuthError, ws_authenticate
from ..events import EventBus
from .session_list import _serialize_sessions

logger = logging.getLogger(__name__)

router = APIRouter()


def _serialize_projects(app) -> list[dict]:
    pc = getattr(app.state, "projects_config", None)
    if pc is None:
        return []
    return pc.list_public()


@router.websocket("/ws/events")
async def events_ws(websocket: WebSocket) -> None:
    """Unified server-push event channel.

    On connect we send one snapshot envelope per topic so the client can
    seed every reactive repository, then forward published events forever.
    """
    try:
        username = await ws_authenticate(websocket)
    except WebSocketAuthError:
        return

    bus: EventBus | None = getattr(websocket.app.state, "event_bus", None)
    if bus is None:
        await websocket.close(code=1011, reason="Event bus not configured")
        return

    manager = websocket.app.state.session_manager
    autoscan_mgr = getattr(websocket.app.state, "autoscan_manager", None)

    # Initial snapshots — one envelope per topic.
    await websocket.send_json(
        {"type": "sessions.changed", "payload": {"sessions": _serialize_sessions(manager)}}
    )
    await websocket.send_json(
        {"type": "projects.changed", "payload": {"projects": _serialize_projects(websocket.app)}}
    )
    if autoscan_mgr is not None:
        try:
            await websocket.send_json(
                {"type": "autoscan.list.changed", "payload": autoscan_mgr.list_response()}
            )
        except Exception:
            logger.exception("events_ws.autoscan_list_snapshot_failed")

    q = bus.subscribe()
    try:
        while True:
            ev = await q.get()
            await websocket.send_json({"type": ev.type, "payload": ev.payload})
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("events_ws.stream_failed")
    finally:
        bus.unsubscribe(q)
        logger.info("events_ws.disconnected user=%s", username)
