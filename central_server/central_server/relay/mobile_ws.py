"""Mobile-facing WebSocket relay endpoints.

Four endpoints that mirror the agent's existing WS API surface:
- /relay/ws/session/{agent_id}/{session_id}  — binary PTY relay
- /relay/ws/sessions/{agent_id}              — session list relay
- /relay/ws/events/{agent_id}                — events relay
- /relay/ws/autoscan/{agent_id}              — per-mobile auto-scan stream

The relay never inspects inner frame content (opaque relay).
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from ..auth.dependencies import WebSocketAuthError, ws_authenticate_mobile
from .envelope import CH_AUTOSCAN, CH_CONTROL, CH_SESSION_IO, encode
from .routing import MobileClient, RoutingTable, try_enqueue_control

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/relay")


def _send_session_lifecycle(
    routing: RoutingTable,
    agent_id: str,
    session_id: str,
    event: str,
) -> None:
    """Push a session-lifecycle control frame onto the agent's send queue.

    Best-effort: silently drops when the agent is offline; warns when the
    queue is saturated. A saturated queue requires ~10k pending frames,
    which implies a stuck agent — the agent's next tunnel reconnect will
    replay attach for every still-attached mobile via the loop in
    ``agent_ws.agent_tunnel``.
    """
    conn = routing.get_agent(agent_id)
    if conn is None:
        return
    if not try_enqueue_control(
        conn, {"type": event, "session_id": session_id}
    ):
        logger.warning(
            "relay.session_lifecycle.drop agent=%s session=%s event=%s — agent queue full",
            agent_id,
            session_id,
            event,
        )


def _request_events_snapshot(routing: RoutingTable, agent_id: str) -> None:
    """Ask the agent to republish the current sessions / projects snapshots.

    The agent forwards only live ``event_bus`` updates to mobiles on the
    ``events`` channel, so a mobile that connects after the last state
    change would otherwise see an empty reactive store. This request tells
    the agent to re-emit a ``sessions.changed`` (and ``projects.changed``)
    envelope now so the fresh mobile seeds its repositories.

    Silent drop on offline agent (mobiles are already being closed with
    code 4010 in that case). Warn on saturated agent queue — the next agent
    tunnel reconnect will still resend its full snapshot.
    """
    conn = routing.get_agent(agent_id)
    if conn is None:
        return
    if not try_enqueue_control(conn, {"type": "request_events_snapshot"}):
        logger.warning(
            "relay.events_snapshot.drop agent=%s — agent queue full",
            agent_id,
        )


async def _validate_mobile_connection(
    websocket: WebSocket, agent_id: str
) -> str | None:
    """Authenticate and validate pairing. Returns device_id or None on failure."""
    try:
        payload = await ws_authenticate_mobile(websocket)
    except WebSocketAuthError:
        return None

    if payload.agent_id != agent_id:
        await websocket.close(code=4003, reason="Token not valid for this agent")
        return None

    db = websocket.app.state.db
    if not await db.is_agent_owner(agent_id, payload.user_id):
        await websocket.close(code=4003, reason="User does not own this agent")
        return None

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

    _send_session_lifecycle(
        routing, agent_id, session_id, "mobile_session_attach"
    )

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
        _send_session_lifecycle(
            routing, agent_id, session_id, "mobile_session_detach"
        )


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

    _request_events_snapshot(routing, agent_id)

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

    _request_events_snapshot(routing, agent_id)

    try:
        await _mobile_writer(websocket, client, binary=False)
    finally:
        routing.unregister_mobile(agent_id, channel_key, client)


@router.websocket("/ws/autoscan/{agent_id}")
async def relay_autoscan(websocket: WebSocket, agent_id: str) -> None:
    """Per-mobile auto-scan stream relay — bidirectional JSON text.

    Allocates a fresh ``subscription_id`` for this connection so each
    mobile owns independent ``resume_from_seq`` / ``subscribe_set`` state
    on the agent side. The agent is notified by a CH_CONTROL
    ``open_autoscan`` message on connect and a ``close_autoscan`` on
    teardown so it can spawn / cancel the matching stream task.
    """
    device_id = await _validate_mobile_connection(websocket, agent_id)
    if device_id is None:
        return

    routing: RoutingTable = websocket.app.state.routing
    subscription_id = uuid.uuid4().hex
    channel_key = f"autoscan:{subscription_id}"
    client = MobileClient(device_id=device_id, websocket=websocket)
    routing.register_mobile(agent_id, channel_key, client)

    agent_conn = routing.get_agent(agent_id)
    if agent_conn is None:
        routing.unregister_mobile(agent_id, channel_key, client)
        await websocket.close(code=4010, reason="Agent is offline")
        return

    open_msg = json.dumps({
        "type": "open_autoscan",
        "subscription_id": subscription_id,
    }).encode()
    try:
        agent_conn.send_queue.put_nowait(encode(CH_CONTROL, "", open_msg))
    except asyncio.QueueFull:
        logger.warning(
            "relay.autoscan.open_drop agent=%s device=%s — agent queue full",
            agent_id, device_id,
        )
        routing.unregister_mobile(agent_id, channel_key, client)
        await websocket.close(code=1013, reason="Agent busy")
        return

    reader_task = asyncio.create_task(
        _autoscan_reader(websocket, agent_id, subscription_id, routing),
        name=f"mobile-autoscan-reader-{device_id}-{subscription_id}",
    )
    writer_task = asyncio.create_task(
        _mobile_writer(websocket, client, binary=False),
        name=f"mobile-autoscan-writer-{device_id}-{subscription_id}",
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
        current_agent = routing.get_agent(agent_id)
        if current_agent is not None:
            close_msg = json.dumps({
                "type": "close_autoscan",
                "subscription_id": subscription_id,
            }).encode()
            try:
                current_agent.send_queue.put_nowait(
                    encode(CH_CONTROL, "", close_msg)
                )
            except asyncio.QueueFull:
                pass


async def _autoscan_reader(
    websocket: WebSocket,
    agent_id: str,
    subscription_id: str,
    routing: RoutingTable,
) -> None:
    """Forward mobile text frames to the agent as CH_AUTOSCAN envelopes."""
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                return
            text = msg.get("text")
            if text is None:
                continue
            envelope = encode(CH_AUTOSCAN, subscription_id, text.encode("utf-8"))
            agent_conn = routing.get_agent(agent_id)
            if agent_conn is None:
                return
            try:
                agent_conn.send_queue.put_nowait(envelope)
            except asyncio.QueueFull:
                pass
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception(
            "relay.autoscan.reader_error agent=%s sub=%s: %s",
            agent_id, subscription_id, exc,
        )


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
