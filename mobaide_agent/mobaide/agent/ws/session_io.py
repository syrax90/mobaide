from __future__ import annotations

import asyncio
import json
import struct

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from mobaide import __version__
from mobaide.core.models import SessionEvent, SessionState
from mobaide.relay.envelope import (
    PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    WS_CLOSE_STREAM_DESYNC,
)

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

# Binary frame protocol: first byte = type, remainder = payload; all frames sent as binary WebSocket messages.
FRAME_OUTPUT = b"o"      # 0x6f  S->C  raw PTY bytes
FRAME_INPUT = b"i"       # 0x69  C->S  raw input bytes
FRAME_RESIZE = b"r"      # 0x72  C->S  payload: u16 rows, u16 cols (big-endian)
FRAME_EXECUTE = b"x"     # 0x78  C->S  clear-then-submit: Ctrl+C + pause + payload + pause + CR
FRAME_SUBMIT = b"m"      # 0x6d  C->S  submit: payload + pause + CR (discrete Enter)
FRAME_STATE = b"s"       # 0x73  S->C  payload: UTF-8 JSON {status,pid?,exit_code?}
FRAME_REPLAY = b"b"      # 0x62  S->C  marker: ring-buffer replay starts next frame
FRAME_REPLAY_END = b"e"  # 0x65  S->C  marker: ring-buffer replay finished; live frames follow

# How long to wait for the desync close frame to reach a client that has
# already proven it is not reading.
_DESYNC_CLOSE_TIMEOUT_S = 5.0

_STATE_MAP = {
    SessionState.STARTING: "starting",
    SessionState.RUNNING: "running",
    SessionState.STOPPING: "stopping",
    SessionState.STOPPED: "stopped",
    SessionState.CRASHED: "crashed",
}

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
    """Get or create a SessionEventFanout for ``session``.

    Called both from the ``/ws/session/{id}`` handler and from the
    ``SessionManager.on_session_created`` hook so headless sessions start
    fanning out events the moment they are created, before any client connects.
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

    # ws_authenticate also calls websocket.accept()
    try:
        username = await ws_authenticate(websocket)
    except WebSocketAuthError as exc:
        log_ws_error(channel, exc, session_id=session_id, stage="auth")
        return
    log_ws_authenticated(channel, username, session_id=session_id)

    # Single text frame announcing this agent's identity and the wire-contract
    # version range it speaks. The mobile reads this before any binary handshake
    # frame so it can render an "agent outdated" banner without waiting for
    # a separate /api/version round trip.
    await websocket.send_text(json.dumps({
        "type": "server_info",
        "agent_version": __version__,
        "protocol_version": PROTOCOL_VERSION,
        "supported_protocol_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
        "capabilities": {},
    }))

    manager = websocket.app.state.session_manager
    session = await manager.get_session(session_id)
    if session is None:
        log_ws_disconnect(channel, reason="session_not_found", session_id=session_id)
        await websocket.close(code=4004, reason="Session not found")
        return

    await websocket.send_bytes(_state_frame(_STATE_MAP[session.state], session.pid))

    # Chunked replay handshake (protocol v3+):
    #   FRAME_REPLAY → N × FRAME_OUTPUT(chunk) → FRAME_REPLAY_END
    # Chunking serves two purposes — bounded per-frame size on the wire,
    # and bounded per-call cost on the client's emulator main-thread append.
    # An empty ring still emits one empty FRAME_OUTPUT so clients can rely
    # on at least one output frame between the start and end markers.
    # The ring is already DA-stripped at write time (``session._append_ring``)
    # so the snapshot inside ``iter_ring_chunks`` is the only allocation here.
    await websocket.send_bytes(FRAME_REPLAY)
    emitted_any = False
    for chunk in session.iter_ring_chunks():
        await websocket.send_bytes(FRAME_OUTPUT + chunk)
        emitted_any = True
    if not emitted_any:
        await websocket.send_bytes(FRAME_OUTPUT)
    await websocket.send_bytes(FRAME_REPLAY_END)

    fanout = ensure_fanout(websocket.app.state, session)
    subscription = fanout.subscribe(close_on_overflow=True)

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
                        await session.write_execute(payload)
                elif frame_type == FRAME_SUBMIT:
                    counters["bytes_in"] += len(payload)
                    counters["msg_count"] += 1
                    if payload:
                        await session.write_submit(payload)
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
                event: SessionEvent = await subscription.queue.get()
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

    # Run all three concurrently; cancel the others when one finishes. The
    # third arm fires when the fan-out had to discard an event for this
    # consumer: raw PTY bytes carry no sequence number, so the client's screen
    # is corrupt from that point on and only a fresh replay repairs it.
    reader_task = asyncio.create_task(_reader(), name=f"ws-reader-{session_id}")
    writer_task = asyncio.create_task(_writer(), name=f"ws-writer-{session_id}")
    desync_task = asyncio.create_task(
        subscription.overflowed.wait(), name=f"ws-desync-{session_id}"
    )
    try:
        done, pending = await asyncio.wait(
            {reader_task, writer_task, desync_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if desync_task in done:
            # Unsubscribe and empty before waiting on the close. Unsubscribing
            # alone does neither — the local binding keeps the queue alive —
            # hence the drain, mirroring the relay. The `finally` below
            # repeats the unsubscribe harmlessly (`set.discard`).
            fanout.unsubscribe(subscription)
            while True:
                try:
                    subscription.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            # The queue filled because this peer stopped reading, so the close
            # frame can stall the same way the events did. Bounded, and the
            # socket goes down when this handler returns either way.
            try:
                await asyncio.wait_for(
                    websocket.close(
                        code=WS_CLOSE_STREAM_DESYNC, reason="Stream desynchronized"
                    ),
                    timeout=_DESYNC_CLOSE_TIMEOUT_S,
                )
            except Exception as exc:
                log_ws_error(channel, exc, session_id=session_id, stage="desync_close")
    finally:
        # Unsubscribe but do NOT kill the session — it stays alive for reconnect
        fanout.unsubscribe(subscription)
        log_ws_disconnect(
            channel,
            session_id=session_id,
            username=username,
            bytes_in=counters["bytes_in"],
            bytes_out=counters["bytes_out"],
            msg_count=counters["msg_count"],
        )
