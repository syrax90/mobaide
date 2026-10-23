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
    store = getattr(app.state, "projects_store", None)
    if store is None:
        return []
    return store.list_public()


@router.websocket("/ws/events")
async def events_ws(websocket: WebSocket) -> None:
    """Unified agent-push event channel.

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
    voice = getattr(websocket.app.state, "voice", None)
    if voice is not None:
        try:
            await websocket.send_json(
                {"type": "voice.changed", "payload": voice.status().model_dump(mode="json")}
            )
        except Exception:
            logger.exception("events_ws.voice_snapshot_failed")

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
