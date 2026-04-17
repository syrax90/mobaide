"""User-scoped mobile WebSocket endpoint.

Unlike the per-agent relay endpoints in ``mobile_ws.py``, this channel is
bound to the *user* (dashboard JWT) and survives independent of whether
any of the user's agents is currently online. It exists so the mobile app
can be notified when any of the user's agents transitions to/from online
without having to poll ``GET /api/agents``.

Message format mirrors the per-agent events channel: text JSON envelopes
of the shape ``{"type": "...", "payload": {...}}``. Current event types:

- ``agent.status_changed`` — payload: ``{"agent_id": str, "online": bool}``
"""
from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from ..auth.dependencies import WebSocketAuthError, ws_authenticate_user
from .routing import RoutingTable, UserClient

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/user-events")
async def user_events(websocket: WebSocket) -> None:
    """Long-lived user-scoped event stream (server → mobile).

    Inbound frames from the client are ignored; the channel is one-way.
    """
    try:
        payload = await ws_authenticate_user(websocket)
    except WebSocketAuthError:
        return

    user_id = payload.sub
    # The dashboard JWT doesn't carry a device_id, so generate a short
    # correlation id purely for logging and set-identity purposes.
    device_id = secrets.token_hex(4)

    routing: RoutingTable = websocket.app.state.routing
    client = UserClient(user_id=user_id, device_id=device_id, websocket=websocket)
    routing.register_user_client(user_id, client)
    logger.info(
        "user_events.connected user_id=%s device=%s", user_id, device_id
    )

    try:
        await _user_writer(websocket, client)
    finally:
        routing.unregister_user_client(user_id, client)
        logger.info(
            "user_events.disconnected user_id=%s device=%s", user_id, device_id
        )


async def _user_writer(websocket: WebSocket, client: UserClient) -> None:
    """Drain the client's send queue and write text frames to the WS."""
    try:
        while True:
            data = await client.send_queue.get()
            await websocket.send_text(data)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception(
            "user_events.writer_error user_id=%s device=%s: %s",
            client.user_id,
            client.device_id,
            exc,
        )
