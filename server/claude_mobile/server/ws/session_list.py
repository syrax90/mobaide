from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from ..auth.dependencies import WebSocketAuthError, ws_authenticate
from .event_fanout import SessionListBroadcaster

logger = logging.getLogger(__name__)

router = APIRouter()


def _serialize_sessions(manager) -> list[dict]:
    return [
        {
            "session_id": s.session_id,
            "state": s.state.value,
            "created_at": s.created_at.isoformat(),
            "pid": s.pid,
            "command": s.command,
            "cwd": s.cwd,
        }
        for s in manager.list_sessions()
    ]


@router.websocket("/ws/sessions")
async def session_list(websocket: WebSocket) -> None:
    """Real-time session list updates — pushes on any session state change."""
    # Authenticate
    try:
        username = await ws_authenticate(websocket)
    except WebSocketAuthError:
        return

    manager = websocket.app.state.session_manager
    broadcaster: SessionListBroadcaster = websocket.app.state.broadcaster

    # Send initial state
    await websocket.send_json(_serialize_sessions(manager))

    # Subscribe and forward updates
    q = broadcaster.subscribe()
    try:
        while True:
            msg = await q.get()
            await websocket.send_text(msg)
    except WebSocketDisconnect:
        pass
    finally:
        broadcaster.unsubscribe(q)
        logger.info("Session-list WebSocket disconnected (user=%s)", username)
