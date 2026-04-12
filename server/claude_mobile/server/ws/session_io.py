from __future__ import annotations

import asyncio
import json
import re
import struct

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from claude_mobile.core.models import SessionEvent, SessionState

from ..auth.dependencies import WebSocketAuthError, ws_authenticate
from ..logging import bind_request_context, get_logger
from .event_fanout import SessionEventFanout
from .logging import (
    log_ws_authenticated,
    log_ws_connect,
    log_ws_disconnect,
    log_ws_error,
)

logger = get_logger(__name__)

router = APIRouter()

# --- Binary frame protocol (ttyd / gotty style) ----------------------------
# First byte = type, remainder = payload. All frames are sent as binary
# WebSocket messages.
FRAME_OUTPUT = b"o"  # 0x6f  S->C  raw PTY bytes
FRAME_INPUT = b"i"   # 0x69  C->S  raw input bytes
FRAME_RESIZE = b"r"  # 0x72  C->S  payload: u16 rows, u16 cols (big-endian)
FRAME_EXECUTE = b"x" # 0x78  C->S  clear-then-write: Ctrl+C + pause + payload
FRAME_STATE = b"s"   # 0x73  S->C  payload: UTF-8 JSON {status,pid?,exit_code?}
FRAME_REPLAY = b"b"  # 0x62  S->C  marker: ring-buffer replay starts next frame

_STATE_MAP = {
    SessionState.STARTING: "starting",
    SessionState.RUNNING: "running",
    SessionState.STOPPING: "stopping",
    SessionState.STOPPED: "stopped",
    SessionState.CRASHED: "crashed",
}

# Belt-and-braces strip for any DA reply bytes that may already be sitting
# in a session's ring buffer (e.g. from sessions started before the
# server-side DaResponder was deployed). Matches DA1/DA2 (CSI ? ... c /
# CSI > ... c) and DA3 (DCS ! | ... ST). The PTY-side responder in
# core/da_responder.py is the primary fix; this is a safety net on replay.
_DA_REPLY_STRIP = re.compile(
    rb"\x1b\[\?[\d;]*c|\x1b\[>[\d;]*c|\x1bP![|][^\x1b]*\x1b\\",
)

_EVENT_TO_STATUS = {
    "started": "running",
    "stopped": "stopped",
    "error": "crashed",
}


def _state_frame(status: str, pid: int | None = None) -> bytes:
    payload: dict[str, object] = {"status": status}
    if pid is not None:
        payload["pid"] = pid
    return FRAME_STATE + json.dumps(payload).encode("utf-8")


def ensure_fanout(app_state, session) -> SessionEventFanout:
    """Get or eagerly create a SessionEventFanout for ``session``.

    Used both by the ``/ws/session/{id}`` handler and by the
    ``SessionManager.on_session_created`` hook (registered in ``app.py``)
    so headless sessions — including those spawned by Auto Scan — start
    fanning out events the moment they are created.
    """
    fanouts: dict[str, SessionEventFanout] = app_state.fanouts
    fanout = fanouts.get(session.session_id)
    if fanout is None:
        manager = app_state.session_manager
        fanout = SessionEventFanout(
            session=session,
            broadcaster=getattr(app_state, "broadcaster", None),
            get_sessions=manager.list_sessions,
            event_bus=getattr(app_state, "event_bus", None),
        )
        fanout.start()
        fanouts[session.session_id] = fanout
    return fanout


def stop_fanout(app_state, session_id: str) -> None:
    fanouts: dict[str, SessionEventFanout] = getattr(app_state, "fanouts", {})
    fanout = fanouts.pop(session_id, None)
    if fanout:
        fanout.stop()


@router.websocket("/ws/session/{session_id}")
async def session_io(websocket: WebSocket, session_id: str) -> None:
    """Bidirectional terminal I/O channel for a single session.

    Wire format is binary frames with a 1-byte type prefix; see the constants
    at the top of this module. The previous JSON+base64 protocol has been
    removed — clients must speak binary.
    """
    channel = "session_io"
    log_ws_connect(channel, websocket, session_id=session_id)
    bind_request_context(session_id=session_id)

    # Authenticate (also calls websocket.accept())
    try:
        username = await ws_authenticate(websocket)
    except WebSocketAuthError as exc:
        log_ws_error(channel, exc, session_id=session_id, stage="auth")
        return
    log_ws_authenticated(channel, username, session_id=session_id)

    # Look up session
    manager = websocket.app.state.session_manager
    session = await manager.get_session(session_id)
    if session is None:
        log_ws_disconnect(channel, reason="session_not_found", session_id=session_id)
        await websocket.close(code=4004, reason="Session not found")
        return

    # 1) Send current state immediately so the client knows what it's about
    #    to receive.
    await websocket.send_bytes(_state_frame(_STATE_MAP[session.state], session.pid))

    # 2) Send the ring-buffer replay marker, then the recent raw bytes as a
    #    single output frame. The marker lets a future client distinguish
    #    history from live output without changing the output frame shape.
    ring = _DA_REPLY_STRIP.sub(b"", session.get_ring())
    await websocket.send_bytes(FRAME_REPLAY)
    # Always emit a single output frame after the replay marker — even if
    # the ring is empty — so clients can rely on a fixed handshake shape
    # (state, marker, output) before live frames begin.
    await websocket.send_bytes(FRAME_OUTPUT + ring)

    # Get or eagerly create the fanout. (Headless auto-scan sessions also
    # come through here via the on_session_created hook in app.py.)
    fanout = ensure_fanout(websocket.app.state, session)

    # Subscribe to events
    consumer_queue = fanout.subscribe()

    # Aggregated counters — emitted on disconnect, never per message.
    counters = {"bytes_in": 0, "bytes_out": 0, "msg_count": 0}

    async def _reader() -> None:
        """Read binary frames from WebSocket and forward to PTY."""
        try:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    return
                data = msg.get("bytes")
                if data is None:
                    # Text frames are not part of the protocol — ignore.
                    continue
                if not data:
                    continue
                frame_type = data[:1]
                payload = data[1:]
                if frame_type == FRAME_INPUT:
                    counters["bytes_in"] += len(payload)
                    counters["msg_count"] += 1
                    if payload:
                        await session.write(payload)
                elif frame_type == FRAME_EXECUTE:
                    counters["bytes_in"] += len(payload)
                    counters["msg_count"] += 1
                    if payload:
                        # Cancel stale input with Ctrl+C, pause for the TUI
                        # to process and show a fresh prompt, then write the
                        # new command.
                        await session.write(b"\x03")
                        await asyncio.sleep(0.1)
                        await session.write(payload)
                elif frame_type == FRAME_RESIZE:
                    if len(payload) >= 4:
                        rows, cols = struct.unpack(">HH", payload[:4])
                        await session.resize(rows, cols)
                        counters["msg_count"] += 1
                else:
                    logger.debug(
                        "ws.unknown_frame",
                        session_id=session_id,
                        frame_type=frame_type.hex() if frame_type else "",
                    )
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            log_ws_error(channel, exc, session_id=session_id, stage="reader")

    async def _writer() -> None:
        """Read events from fanout queue and send to WebSocket."""
        try:
            while True:
                event: SessionEvent = await consumer_queue.get()
                if event.event_type == "output" and event.data:
                    counters["bytes_out"] += len(event.data)
                    await websocket.send_bytes(FRAME_OUTPUT + event.data)
                elif event.event_type in _EVENT_TO_STATUS:
                    await websocket.send_bytes(
                        _state_frame(_EVENT_TO_STATUS[event.event_type], session.pid),
                    )
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            log_ws_error(channel, exc, session_id=session_id, stage="writer")

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
        log_ws_disconnect(
            channel,
            session_id=session_id,
            username=username,
            bytes_in=counters["bytes_in"],
            bytes_out=counters["bytes_out"],
            msg_count=counters["msg_count"],
        )
