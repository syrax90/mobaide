"""User-scoped mobile WebSocket endpoint.

Unlike the per-agent relay endpoints in ``mobile_ws.py``, this channel is
bound to the *user* (dashboard JWT) and survives independent of whether
any of the user's agents is currently online. It exists so the mobile app
can be notified when any of the user's agents transitions to/from online
without having to poll ``GET /api/agents``.

Clients may pass their registered ``device_id`` as a query param. It is a
delivery hint only — it identifies which device WebSocket delivery already
reaches, so an attention push skips that device and still goes out to the
user's other ones.

Message format mirrors the per-agent events channel: text JSON envelopes
of the shape ``{"type": "...", "payload": {...}}``. Current event types:

- ``agent.status_changed`` — payload: ``{"agent_id": str, "online": bool}``
- ``template.created`` — payload: full template row
- ``template.updated`` — payload: full template row (PATCH or /touch)
- ``template.deleted`` — payload: ``{"template_id": str, "version": int,
  "updated_at": str}``
- ``shortcut.created`` — payload: full shortcut row
- ``shortcut.updated`` — payload: full shortcut row (upsert rename or PATCH)
- ``shortcut.deleted`` — payload: ``{"shortcut_id": str, "version": int,
  "updated_at": str}``
- ``custom_type.created`` — payload: full custom agent type row
- ``custom_type.updated`` — payload: full custom agent type row
- ``custom_type.deleted`` — payload: ``{"id": str}``; the server has
  already cascaded the type's kind-scoped saved shortcuts, so clients
  purge their local shortcut caches for ``custom_<id>`` on receipt (no
  companion ``shortcut.deleted`` events are sent)
- ``session.attention`` — payload: attention push mirror (see PROTOCOL.md)
- ``account.first_agent_paired`` — payload: ``{"agent_id": str}``; sent once
  per account, when ``_complete_pairing`` stamps ``users.first_paired_at``
  (the apps log their Firebase ``agent_paired`` conversion off it)
"""
from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from ..auth.dependencies import (
    WebSocketAuthError,
    token_predates_device_revoke,
    ws_authenticate_user,
)
from .routing import (
    WS_CLOSE_AUTH_FAILED,
    RoutingTable,
    UserClient,
    safe_send_reason_and_close,
)

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
    # Not every dashboard JWT carries a device_id, so generate a short token
    # for log correlation and as a unique key within the user's connection set.
    device_id = secrets.token_hex(4)
    bound_device_id = await _bind_device(websocket, user_id)

    routing: RoutingTable = websocket.app.state.routing
    client = UserClient(
        user_id=user_id,
        device_id=device_id,
        websocket=websocket,
        bound_device_id=bound_device_id,
        token_device_id=payload.device_id,
    )
    routing.register_user_client(user_id, client)
    # A device revoke whose disconnect_device ran during the setup above found
    # nothing to close; re-check now that a later one would see this socket.
    if await token_predates_device_revoke(websocket, payload.device_id, payload.iat):
        routing.unregister_user_client(user_id, client)
        await safe_send_reason_and_close(
            websocket, WS_CLOSE_AUTH_FAILED, "device_revoked"
        )
        return
    logger.info(
        "user_events.connected user_id=%s device=%s bound=%s",
        user_id, device_id, bound_device_id,
    )

    try:
        await _user_writer(websocket, client)
    finally:
        routing.unregister_user_client(user_id, client)
        logger.info(
            "user_events.disconnected user_id=%s device=%s", user_id, device_id
        )


async def _bind_device(websocket: WebSocket, user_id: str) -> str | None:
    """Resolve the optional ``device_id`` query hint to one of the caller's own
    active devices, so attention pushes can skip the devices this socket
    already delivers to.

    A routing hint, not an authorization decision: an absent or unverifiable
    hint downgrades the socket to "cannot suppress a push" instead of refusing
    the stream, because losing presence events is a worse failure than one
    redundant notification.
    """
    claimed = websocket.query_params.get("device_id")
    if not claimed:
        return None
    try:
        devices = await websocket.app.state.db.list_user_devices(user_id)
    except Exception:
        logger.exception("user_events.device_lookup_failed user_id=%s", user_id)
        return None
    if any(row["device_id"] == claimed for row in devices):
        return claimed
    logger.warning(
        "user_events.device_unrecognised user_id=%s claimed=%s", user_id, claimed
    )
    return None


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
