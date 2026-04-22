"""Agent-facing WebSocket tunnel endpoint.

The agent connects to ``/ws/agent-tunnel`` and authenticates via a control
message. Once authenticated, all session/event traffic is multiplexed over
this single connection using the binary envelope protocol.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from .envelope import (
    CH_CONTROL,
    CH_EVENTS,
    CH_SESSION_IO,
    CH_SESSIONS_LIST,
    decode,
    encode,
)
from .routing import MobileClient, RoutingTable

logger = logging.getLogger(__name__)

router = APIRouter()

# Close code sent to mobile clients when their agent disconnects.
AGENT_DISCONNECTED_CODE = 4010


@router.websocket("/ws/agent-tunnel")
async def agent_tunnel(websocket: WebSocket) -> None:
    """Long-lived multiplexed tunnel for a single agent."""
    await websocket.accept()

    app = websocket.app
    db = app.state.db
    auth_service = app.state.auth_service
    routing: RoutingTable = app.state.routing
    config = app.state.config

    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
    except (asyncio.TimeoutError, WebSocketDisconnect):
        await _safe_close(websocket, 4001, "Auth timeout")
        return

    try:
        auth_msg = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        await _safe_close(websocket, 4001, "Invalid auth message")
        return

    if auth_msg.get("type") != "auth":
        await _safe_close(websocket, 4001, "Expected auth message")
        return

    agent_id = auth_msg.get("agent_id", "")
    agent_token = auth_msg.get("token", "")
    if not agent_id or not agent_token:
        await _safe_close(websocket, 4001, "Missing agent_id or token")
        return

    if not await auth_service.verify_agent_token(agent_id, agent_token):
        await _safe_close(websocket, 4001, "Invalid agent credentials")
        return

    conn = routing.register_agent(agent_id, websocket)
    # Resolve owner once at registration so the offline-broadcast path
    # (which runs after the tunnel is torn down) doesn't have to hit the DB.
    conn.owner_user_id = await db.get_agent_owner(agent_id)
    await db.touch_agent_last_seen(agent_id)
    await websocket.send_text(json.dumps({"type": "auth_ok"}))
    logger.info("agent_tunnel.authenticated agent_id=%s", agent_id)

    # Notify all of the owner's user-scoped WebSocket clients that the
    # agent just came online. Queue-only; if a client's queue is full we
    # drop the notification (the client's reconnect logic will call
    # `refreshAgents()` to resync).
    if conn.owner_user_id:
        _broadcast_status(routing, conn.owner_user_id, agent_id, online=True)

    reader_task = asyncio.create_task(
        _reader(websocket, agent_id, routing, db),
        name=f"agent-reader-{agent_id}",
    )
    writer_task = asyncio.create_task(
        _writer(websocket, conn),
        name=f"agent-writer-{agent_id}",
    )
    pinger_task = asyncio.create_task(
        _pinger(websocket, agent_id, routing, config),
        name=f"agent-pinger-{agent_id}",
    )

    try:
        done, pending = await asyncio.wait(
            {reader_task, writer_task, pinger_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    finally:
        disconnected_clients = routing.unregister_agent(agent_id, conn.conn_id)
        for client in disconnected_clients:
            try:
                await client.websocket.close(
                    code=AGENT_DISCONNECTED_CODE,
                    reason="Agent disconnected",
                )
            except Exception:
                pass
        # Notify the owner's user-scoped clients that the agent is now
        # offline — but ONLY if no newer tunnel has already replaced this
        # one. `unregister_agent` above short-circuits on conn_id mismatch
        # (stale-disconnect guard) and doesn't tell us whether it removed
        # anything, so we derive that by checking whether the agent_id is
        # still registered. If it is, a newer coroutine is holding it and
        # our stale teardown must not undo its online-broadcast.
        if conn.owner_user_id and routing.get_agent(agent_id) is None:
            _broadcast_status(
                routing, conn.owner_user_id, agent_id, online=False
            )
        logger.info("agent_tunnel.disconnected agent_id=%s", agent_id)


async def _reader(
    websocket: WebSocket,
    agent_id: str,
    routing: RoutingTable,
    db,
) -> None:
    """Read frames from agent tunnel and dispatch to mobile clients."""
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                return

            # Binary envelope frames
            data = msg.get("bytes")
            if data is not None:
                try:
                    channel, session_id, payload = decode(data)
                except ValueError:
                    continue

                if channel == CH_SESSION_IO:
                    channel_key = f"session_io:{session_id}"
                    clients = routing.get_mobile_clients(agent_id, channel_key)
                    for client in clients:
                        _try_enqueue(client, payload, binary=True)

                elif channel == CH_SESSIONS_LIST:
                    clients = routing.get_mobile_clients(agent_id, "sessions_list")
                    for client in clients:
                        _try_enqueue(client, payload, binary=False)

                elif channel == CH_EVENTS:
                    clients = routing.get_mobile_clients(agent_id, "events")
                    for client in clients:
                        _try_enqueue(client, payload, binary=False)

                elif channel == CH_CONTROL:
                    await _handle_control(
                        websocket, agent_id, payload, routing, db
                    )

            # Text frames (control messages can also come as text)
            text = msg.get("text")
            if text is not None:
                try:
                    ctrl = json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    continue
                await _handle_control_json(
                    websocket, agent_id, ctrl, routing, db
                )

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("agent_tunnel.reader_error agent_id=%s: %s", agent_id, exc)


async def _writer(websocket: WebSocket, conn) -> None:
    """Drain the agent's send queue and write to the tunnel."""
    try:
        while True:
            data = await conn.send_queue.get()
            await websocket.send_bytes(data)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("agent_tunnel.writer_error agent_id=%s: %s", conn.agent_id, exc)


async def _pinger(
    websocket: WebSocket,
    agent_id: str,
    routing: RoutingTable,
    config,
) -> None:
    """Send WebSocket pings and monitor for agent liveness."""
    interval = config.ws_ping_interval
    try:
        while True:
            await asyncio.sleep(interval)
            try:
                await websocket.send_bytes(
                    encode(CH_CONTROL, "", json.dumps({"type": "ping"}).encode())
                )
            except Exception:
                return
            routing.agent_heartbeat(agent_id)
    except asyncio.CancelledError:
        pass


async def _handle_control(
    websocket, agent_id, payload, routing, db
) -> None:
    """Handle a control message from the envelope binary channel."""
    try:
        ctrl = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return
    await _handle_control_json(websocket, agent_id, ctrl, routing, db)


async def _handle_control_json(
    websocket, agent_id, ctrl, routing, db
) -> None:
    """Process a parsed control JSON message."""
    msg_type = ctrl.get("type")

    if msg_type == "heartbeat":
        routing.agent_heartbeat(agent_id)
        await db.touch_agent_last_seen(agent_id)

    elif msg_type == "proxy_response":
        request_id = ctrl.get("request_id")
        if request_id:
            mobile_proxy = websocket.app.state.mobile_proxy
            mobile_proxy.resolve(request_id, ctrl)


def _try_enqueue(client: MobileClient, payload: bytes, *, binary: bool) -> None:
    """Non-blocking enqueue to a mobile client's send queue.

    Drops the frame silently if the queue is full (slow consumer protection).
    """
    try:
        if binary:
            client.send_queue.put_nowait(payload)
        else:
            client.send_queue.put_nowait(payload.decode("utf-8"))
    except asyncio.QueueFull:
        pass


def _broadcast_status(
    routing: RoutingTable,
    user_id: str,
    agent_id: str,
    *,
    online: bool,
) -> None:
    """Push an ``agent.status_changed`` JSON envelope to every user-scoped
    client of the given user. Non-blocking; drops on a full queue because
    the client will re-sync via REST on its next reconnect anyway.
    """
    envelope = json.dumps(
        {
            "type": "agent.status_changed",
            "payload": {"agent_id": agent_id, "online": online},
        }
    )
    for client in routing.get_user_clients(user_id):
        try:
            client.send_queue.put_nowait(envelope)
        except asyncio.QueueFull:
            logger.warning(
                "user_events.queue_full user_id=%s device=%s — dropping status event",
                user_id,
                client.device_id,
            )


async def _safe_close(websocket: WebSocket, code: int, reason: str) -> None:
    try:
        if websocket.client_state != WebSocketState.DISCONNECTED:
            await websocket.close(code=code, reason=reason)
    except Exception:
        pass
