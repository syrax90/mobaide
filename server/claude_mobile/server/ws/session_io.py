from __future__ import annotations

import asyncio
import base64
import logging

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from claude_mobile.core.models import SessionEvent, SessionState

from ..auth.dependencies import WebSocketAuthError, ws_authenticate
from .event_fanout import SessionEventFanout

logger = logging.getLogger(__name__)

router = APIRouter()

_STATE_MAP = {
    SessionState.STARTING: "starting",
    SessionState.RUNNING: "running",
    SessionState.STOPPING: "stopping",
    SessionState.STOPPED: "stopped",
    SessionState.CRASHED: "crashed",
}


@router.websocket("/ws/session/{session_id}")
async def session_io(websocket: WebSocket, session_id: str) -> None:
    """Bidirectional terminal I/O channel for a single session."""
    # Authenticate
    try:
        username = await ws_authenticate(websocket)
    except WebSocketAuthError:
        return

    # Look up session
    manager = websocket.app.state.session_manager
    session = await manager.get_session(session_id)
    if session is None:
        await websocket.close(code=4004, reason="Session not found")
        return

    # Send scrollback burst
    for chunk in session.get_scrollback():
        encoded = base64.b64encode(chunk.encode("utf-8", errors="replace")).decode()
        await websocket.send_json({"type": "output", "data": encoded})

    # Send current state
    await websocket.send_json({"type": "state", "status": _STATE_MAP[session.state]})

    # Get or create event fanout for this session
    fanouts: dict[str, SessionEventFanout] = websocket.app.state.fanouts
    if session_id not in fanouts:
        fanout = SessionEventFanout(
            session=session,
            broadcaster=websocket.app.state.broadcaster,
            get_sessions=manager.list_sessions,
        )
        fanout.start()
        fanouts[session_id] = fanout
    else:
        fanout = fanouts[session_id]

    # Subscribe to events
    consumer_queue = fanout.subscribe()

    async def _reader() -> None:
        """Read messages from WebSocket and forward to PTY."""
        try:
            while True:
                raw = await websocket.receive_json()
                msg_type = raw.get("type")
                if msg_type == "input":
                    data = base64.b64decode(raw["data"]).decode("utf-8")
                    await session.write(data)
                elif msg_type == "resize":
                    await session.resize(raw["rows"], raw["cols"])
        except WebSocketDisconnect:
            pass

    async def _writer() -> None:
        """Read events from fanout queue and send to WebSocket."""
        try:
            while True:
                event: SessionEvent = await consumer_queue.get()
                if event.event_type == "output" and event.data:
                    encoded = base64.b64encode(
                        event.data.encode("utf-8", errors="replace"),
                    ).decode()
                    await websocket.send_json({"type": "output", "data": encoded})
                elif event.event_type in ("started", "stopped", "error"):
                    status_str = {
                        "started": "running",
                        "stopped": "stopped",
                        "error": "crashed",
                    }.get(event.event_type, event.event_type)
                    await websocket.send_json({"type": "state", "status": status_str})
        except WebSocketDisconnect:
            pass

    # Run both concurrently; cancel the other when one finishes
    reader_task = asyncio.create_task(_reader(), name=f"ws-reader-{session_id}")
    writer_task = asyncio.create_task(_writer(), name=f"ws-writer-{session_id}")
    try:
        done, pending = await asyncio.wait(
            {reader_task, writer_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    finally:
        # Unsubscribe but do NOT kill the session — it stays alive for reconnect
        fanout.unsubscribe(consumer_queue)
        logger.info(
            "WebSocket disconnected from session %s (user=%s)",
            session_id,
            username,
        )
