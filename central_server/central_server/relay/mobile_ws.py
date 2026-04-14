"""Mobile-facing WebSocket relay endpoints.

Three endpoints that mirror the agent's existing WS API surface:
- /relay/ws/session/{agent_id}/{session_id}  — binary PTY relay
- /relay/ws/sessions/{agent_id}              — session list relay
- /relay/ws/events/{agent_id}                — events relay

The relay never inspects inner frame content (opaque relay).
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from ..auth.dependencies import WebSocketAuthError, ws_authenticate_mobile
from .envelope import CH_SESSION_IO, encode
from .routing import MobileClient, RoutingTable

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/relay")


async def _validate_mobile_connection(
    websocket: WebSocket, agent_id: str
) -> str | None:
    """Authenticate and validate pairing. Returns device_id or None on failure."""
    try:
        payload = await ws_authenticate_mobile(websocket)
    except WebSocketAuthError:
        return None

    # Verify the token's agent_id matches the requested agent
    if payload.agent_id != agent_id:
        await websocket.close(code=4003, reason="Token not valid for this agent")
        return None

    # Check pairing exists
    db = websocket.app.state.db
    if not await db.is_mobile_paired_to_agent(payload.sub, agent_id):
        await websocket.close(code=4003, reason="Not paired to this agent")
        return None

    # Check agent is online
    routing: RoutingTable = websocket.app.state.routing
    if not routing.is_agent_online(agent_id):
        await websocket.close(code=4010, reason="Agent is offline")
        return None

    return payload.sub


@router.websocket("/ws/session/{agent_id}/{session_id}")
async def relay_session_io(
    websocket: WebSocket, agent_id: str, session_id: str
) -> None:
    """Binary PTY relay — bidirectional."""
    device_id = await _validate_mobile_connection(websocket, agent_id)
    if device_id is None:
        return

    routing: RoutingTable = websocket.app.state.routing
    client = MobileClient(device_id=device_id, websocket=websocket)
    channel_key = f"session_io:{session_id}"
    routing.register_mobile(agent_id, channel_key, client)

    reader_task = asyncio.create_task(
        _session_reader(websocket, agent_id, session_id, routing),
        name=f"mobile-reader-{device_id}-{session_id}",
    )
    writer_task = asyncio.create_task(
        _mobile_writer(websocket, client, binary=True),
        name=f"mobile-writer-{device_id}-{session_id}",
    )

    try:
        done, pending = await asyncio.wait(
            {reader_task, writer_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    finally:
        routing.unregister_mobile(agent_id, channel_key, client)


@router.websocket("/ws/sessions/{agent_id}")
async def relay_sessions_list(websocket: WebSocket, agent_id: str) -> None:
    """Session list relay — agent-to-mobile only."""
    device_id = await _validate_mobile_connection(websocket, agent_id)
    if device_id is None:
        return

    routing: RoutingTable = websocket.app.state.routing
    client = MobileClient(device_id=device_id, websocket=websocket)
    channel_key = "sessions_list"
    routing.register_mobile(agent_id, channel_key, client)

    try:
        await _mobile_writer(websocket, client, binary=False)
    finally:
        routing.unregister_mobile(agent_id, channel_key, client)


@router.websocket("/ws/events/{agent_id}")
async def relay_events(websocket: WebSocket, agent_id: str) -> None:
    """Events relay — agent-to-mobile only."""
    device_id = await _validate_mobile_connection(websocket, agent_id)
    if device_id is None:
        return

    routing: RoutingTable = websocket.app.state.routing
    client = MobileClient(device_id=device_id, websocket=websocket)
    channel_key = "events"
    routing.register_mobile(agent_id, channel_key, client)

    try:
        await _mobile_writer(websocket, client, binary=False)
    finally:
        routing.unregister_mobile(agent_id, channel_key, client)


# -- Internal helpers ----------------------------------------------------------


async def _session_reader(
    websocket: WebSocket,
    agent_id: str,
    session_id: str,
    routing: RoutingTable,
) -> None:
    """Read binary frames from mobile and forward to agent via envelope."""
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                return
            data = msg.get("bytes")
            if data is None:
                continue
            # Wrap in envelope and push to agent's send queue
            envelope = encode(CH_SESSION_IO, session_id, data)
            agent_conn = routing.get_agent(agent_id)
            if agent_conn is None:
                return  # Agent disconnected
            try:
                agent_conn.send_queue.put_nowait(envelope)
            except asyncio.QueueFull:
                pass  # Drop frame (slow agent)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception(
            "relay.session_reader_error agent=%s session=%s: %s",
            agent_id, session_id, exc,
        )


async def _mobile_writer(
    websocket: WebSocket, client: MobileClient, *, binary: bool
) -> None:
    """Drain the mobile client's send queue and write to WebSocket."""
    try:
        while True:
            data = await client.send_queue.get()
            if binary:
                await websocket.send_bytes(
                    data if isinstance(data, bytes) else data.encode()
                )
            else:
                await websocket.send_text(
                    data if isinstance(data, str) else data.decode()
                )
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception(
            "relay.mobile_writer_error device=%s: %s", client.device_id, exc
        )
