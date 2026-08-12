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

from .. import __version__
from ..monitoring import record_send_queue_drop
from ..ws_origin import enforce_ws_origin
from .envelope import (
    CH_AUTOSCAN,
    CH_CONTROL,
    CH_EVENTS,
    CH_SESSION_IO,
    CH_SESSION_IO_TARGETED,
    CH_SESSIONS_LIST,
    MIN_CLIENT_PROTOCOL_VERSION,
    PROTOCOL_INCOMPATIBLE_CLOSE_CODE,
    SUPPORTED_PROTOCOL_VERSIONS,
    decode,
    encode,
    split_targeted,
)
from .routing import (
    WS_CLOSE_AGENT_TRANSIENT_OFFLINE,
    MobileClient,
    RoutingTable,
    broadcast_user_event,
    try_enqueue_control,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Close code sent to paired mobile clients when their agent's tunnel
# disconnects (restart, crash, network blip, deploy). This is a TRANSIENT
# condition — the agent is expected to reconnect, and the mobile must
# keep its exponential-backoff reconnect loop alive. 4012, NOT 4010
# (``WS_CLOSE_ADMIN_REVOKED``): collapsing the two onto 4010 used to be
# masked by Android ignoring the code, but once the client started
# treating 4010 as destructive admin-revoke, this path would have
# permanently killed every per-agent socket on the device on every
# agent restart. See `routing.WS_CLOSE_AGENT_TRANSIENT_OFFLINE`.
AGENT_DISCONNECTED_CODE = WS_CLOSE_AGENT_TRANSIENT_OFFLINE

# Close code sent to mobile clients when the agent declines an autoscan
# subscription (e.g. capacity reached, autoscan manager not configured).
AUTOSCAN_DECLINED_CODE = 1013

_unknown_control_type_total = 0


@router.websocket("/ws/agent-tunnel")
async def agent_tunnel(websocket: WebSocket) -> None:
    """Long-lived multiplexed tunnel for a single agent."""
    # Origin gate must precede accept(): this endpoint authenticates via an
    # in-band frame *after* the handshake, so it is the one WS route where
    # a browser page could otherwise complete a connection unauthenticated.
    if not await enforce_ws_origin(websocket):
        return

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

    client_protocol_version = _coerce_int(
        auth_msg.get("protocol_version"),
        default=1,
    )
    if client_protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
        try:
            await websocket.send_text(json.dumps({
                "type": "incompatible",
                "reason": (
                    f"Server speaks protocol versions "
                    f"{list(SUPPORTED_PROTOCOL_VERSIONS)}, agent sent "
                    f"{client_protocol_version}"
                ),
                "min_client_version": MIN_CLIENT_PROTOCOL_VERSION,
                "server_protocol_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
                # Relative path, resolved by the agent against the relay's
                # HTTPS origin. The agent's HTTPS-bootstrap path fetches
                # the signed manifest from this endpoint when the in-band
                # relay update channel is blocked by a protocol-version
                # mismatch (this rejection itself). The signature is the
                # trust anchor — server-controlled URL substitution can
                # at worst force a fail-closed signature_failed outcome.
                "manifest_url": "/api/v1/agent/manifest.json",
            }))
        except Exception:
            pass
        await _safe_close(
            websocket,
            PROTOCOL_INCOMPATIBLE_CLOSE_CODE,
            "Protocol version not supported",
        )
        logger.warning(
            "agent_tunnel.protocol_incompatible agent_id=%s client_version=%s",
            agent_id, client_protocol_version,
        )
        return

    conn = routing.register_agent(agent_id, websocket)
    # Stamp the negotiated version on the live connection before the first
    # await: the DB handshake write below can lose a race between two
    # overlapping tunnels, but routing always describes the current one.
    conn.protocol_version = client_protocol_version
    # Resolve owner once at registration so the offline-broadcast path
    # (which runs after the tunnel is torn down) doesn't have to hit the DB.
    conn.owner_user_id = await db.get_agent_owner(agent_id)
    reported_version = auth_msg.get("agent_version")
    if not (isinstance(reported_version, str) and reported_version):
        reported_version = None
    # update_agent_handshake_info also bumps last_seen_at, so the connect
    # path issues a single UPDATE+commit. Heartbeats after this point are
    # in-memory only (RoutingTable.last_heartbeat); they reach the DB via
    # app.py's periodic batched flush and the disconnect flush below.
    await db.update_agent_handshake_info(
        agent_id, reported_version, client_protocol_version,
    )

    manifest_record = _read_manifest(websocket.app)
    auth_ok_payload: dict[str, object] = {
        "type": "auth_ok",
        "server_protocol_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
        "min_client_protocol_version": MIN_CLIENT_PROTOCOL_VERSION,
        "server_version": __version__,
        "capabilities": {},
        "latest_agent_version": None,
        "manifest_body": None,
        "manifest_signature": None,
    }
    if manifest_record is not None:
        auth_ok_payload["latest_agent_version"] = manifest_record.body.get("version")
        auth_ok_payload["manifest_body"] = manifest_record.body
        auth_ok_payload["manifest_signature"] = manifest_record.signature_b64
    await websocket.send_text(json.dumps(auth_ok_payload))
    logger.info(
        "agent_tunnel.authenticated agent_id=%s protocol=%s",
        agent_id, client_protocol_version,
    )

    # Notify all of the owner's user-scoped WebSocket clients that the
    # agent just came online. Queue-only; if a client's queue is full we
    # drop the notification (the client's reconnect logic will call
    # `refreshAgents()` to resync).
    if conn.owner_user_id:
        _broadcast_status(routing, conn.owner_user_id, agent_id, online=True)

    # The previous agent tunnel may have died while mobiles stayed put.
    # Replay ``mobile_session_attach`` for each currently-attached mobile
    # session so the new agent re-sends the PTY handshake (state/replay/ring)
    # and those mobiles don't get stuck on a grayed-out keyboard waiting
    # for a state frame that will never arrive. A v8 agent gets one attach
    # per attached CLIENT (conn_id included) so its per-session client
    # registry rebuilds with the true count; a v7 agent would answer N
    # per-client attaches with N broadcast re-handshakes, so it keeps the
    # legacy one-per-session replay.
    if (conn.protocol_version or 0) >= 8:
        # Each replayed attach carries the session's WHOLE conn_id set: the
        # agent registers the batch before sending the first handshake, so no
        # retained client ever sees a transient count of one (which would
        # promote it to solo and let it steal the grid on every tunnel blip).
        by_session: dict[str, list[str]] = {}
        for sid, client in routing.iter_attached_session_clients(agent_id):
            by_session.setdefault(sid, []).append(client.conn_id)
        for sid, conn_ids in by_session.items():
            for cid in conn_ids:
                _enqueue_attach(conn, sid, conn_id=cid, conn_ids=conn_ids)
    else:
        for sid in routing.list_attached_sessions(agent_id):
            _enqueue_attach(conn, sid)

    reader_task = asyncio.create_task(
        _reader(websocket, agent_id, routing),
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
        # Persist the final heartbeat and notify the owner's user-scoped
        # clients that the agent is now offline — but ONLY if no newer tunnel
        # has already replaced this one. `unregister_agent` above
        # short-circuits on conn_id mismatch (stale-disconnect guard) and
        # doesn't tell us whether it removed anything, so we derive that by
        # checking whether the agent_id is still registered. If it is, a
        # newer coroutine is holding it: our stale teardown must not undo its
        # online-broadcast nor regress last_seen_at values the batched
        # flusher already wrote for it.
        if routing.get_agent(agent_id) is None:
            try:
                await db.touch_agent_last_seen(agent_id, conn.last_heartbeat)
            except Exception:
                # A failed write (e.g. shutdown race after db.close()) must
                # not suppress the offline broadcast below.
                logger.exception(
                    "agent_tunnel.last_seen_flush_failed agent_id=%s", agent_id
                )
            if conn.owner_user_id:
                _broadcast_status(
                    routing, conn.owner_user_id, agent_id, online=False
                )
        logger.info("agent_tunnel.disconnected agent_id=%s", agent_id)


async def _reader(
    websocket: WebSocket,
    agent_id: str,
    routing: RoutingTable,
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

                elif channel == CH_SESSION_IO_TARGETED:
                    # Proto-v8: deliver the inner frame to the one attached
                    # client whose conn_id matches. An unknown conn_id means
                    # the client already detached — drop silently.
                    try:
                        conn_id, inner = split_targeted(payload)
                    except ValueError:
                        continue
                    channel_key = f"session_io:{session_id}"
                    clients = routing.get_mobile_clients(agent_id, channel_key)
                    for client in clients:
                        if client.conn_id == conn_id:
                            _try_enqueue(client, inner, binary=True)
                            break

                elif channel == CH_SESSIONS_LIST:
                    clients = routing.get_mobile_clients(agent_id, "sessions_list")
                    for client in clients:
                        _try_enqueue(client, payload, binary=False)

                elif channel == CH_EVENTS:
                    clients = routing.get_mobile_clients(agent_id, "events")
                    for client in clients:
                        _try_enqueue(client, payload, binary=False)

                elif channel == CH_AUTOSCAN:
                    channel_key = f"autoscan:{session_id}"
                    clients = routing.get_mobile_clients(agent_id, channel_key)
                    for client in clients:
                        _try_enqueue(client, payload, binary=False)

                elif channel == CH_CONTROL:
                    await _handle_control(
                        websocket, agent_id, payload, routing
                    )

            # Text frames (control messages can also come as text)
            text = msg.get("text")
            if text is not None:
                try:
                    ctrl = json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    continue
                await _handle_control_json(
                    websocket, agent_id, ctrl, routing
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
    websocket, agent_id, payload, routing
) -> None:
    """Handle a control message from the envelope binary channel."""
    try:
        ctrl = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return
    await _handle_control_json(websocket, agent_id, ctrl, routing)


async def _handle_control_json(
    websocket, agent_id, ctrl, routing
) -> None:
    """Process a parsed control JSON message."""
    msg_type = ctrl.get("type")

    if msg_type == "heartbeat":
        routing.agent_heartbeat(agent_id)

    elif msg_type == "proxy_response":
        request_id = ctrl.get("request_id")
        if request_id:
            mobile_proxy = websocket.app.state.mobile_proxy
            mobile_proxy.resolve(request_id, ctrl)

    elif msg_type == "close_autoscan":
        subscription_id = ctrl.get("subscription_id")
        if not subscription_id:
            return
        reason = ctrl.get("reason", "agent_closed")
        channel_key = f"autoscan:{subscription_id}"
        # Snapshot the client set before we start closing sockets — the
        # writer tasks of closed sockets will concurrently unregister
        # themselves via their ``finally`` handlers.
        for client in list(routing.get_mobile_clients(agent_id, channel_key)):
            try:
                await client.websocket.close(
                    code=AUTOSCAN_DECLINED_CODE, reason=reason
                )
            except Exception:
                pass

    elif msg_type == "user_event":
        _handle_user_event(websocket, agent_id, ctrl, routing)

    else:
        global _unknown_control_type_total
        _unknown_control_type_total += 1
        logger.warning(
            "agent_tunnel.unknown_control_type agent_id=%s type=%s total=%d",
            agent_id, msg_type, _unknown_control_type_total,
        )


# User-event kinds an agent may relay to the owner's user-scoped clients.
# Everything else is dropped: a compromised or outdated agent must not be
# able to forge server-originated kinds (agent.status_changed, template.*).
_AGENT_USER_EVENT_KINDS = frozenset({"session.attention"})

# Optional attention payload fields relayed verbatim when present.
_ATTENTION_OPTIONAL_FIELDS = ("title", "project_name", "exit_reason")


def _handle_user_event(websocket, agent_id, ctrl, routing) -> None:
    """Relay an agent-emitted user event to the owner's user-scoped clients,
    and push it to every registered device those sockets do not already
    reach (WS delivery wins, per device)."""
    kind = ctrl.get("kind")
    raw = ctrl.get("payload")
    if kind not in _AGENT_USER_EVENT_KINDS or not isinstance(raw, dict):
        logger.warning(
            "agent_tunnel.user_event_dropped agent_id=%s kind=%s reason=%s",
            agent_id, kind, "unsupported_kind_or_payload",
        )
        return
    session_id = raw.get("session_id")
    reason = raw.get("reason")
    if (
        not isinstance(session_id, str) or not session_id
        or not isinstance(reason, str) or not reason
    ):
        logger.warning(
            "agent_tunnel.user_event_dropped agent_id=%s kind=%s reason=%s",
            agent_id, kind, "malformed_payload",
        )
        return
    conn = routing.get_agent(agent_id)
    owner_user_id = conn.owner_user_id if conn is not None else None
    if not owner_user_id:
        logger.warning(
            "agent_tunnel.user_event_dropped agent_id=%s kind=%s reason=%s",
            agent_id, kind, "no_owner",
        )
        return

    payload: dict = {"session_id": session_id, "reason": reason}
    for field in _ATTENTION_OPTIONAL_FIELDS:
        value = raw.get(field)
        if isinstance(value, str):
            payload[field] = value
    # Stamped from the authenticated tunnel — an agent-supplied agent_id is
    # never trusted.
    payload["agent_id"] = agent_id

    broadcast_user_event(routing, owner_user_id, kind, payload)
    # WebSocket delivery wins per device, not per account: one device holding a
    # socket must not mute every other device the user owns, or a phone that is
    # asleep while a second client runs never learns about the attention at all.
    dispatcher = getattr(websocket.app.state, "push_dispatcher", None)
    if dispatcher is not None:
        dispatcher.schedule_attention(
            owner_user_id,
            payload,
            exclude_device_ids=routing.get_user_device_ids(owner_user_id),
        )


def _coerce_int(value: object, *, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _read_manifest(app):
    store = getattr(app.state, "manifest_store", None)
    if store is None:
        return None
    return store.read()


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
        # No log: this is the agent→mobile fan-out hot path, and a slow
        # mobile would turn every dropped frame into a log line. The
        # counter surfaces the rate in monitoring.sample instead.
        record_send_queue_drop("agent_to_mobile")


def _enqueue_attach(
    conn,
    session_id: str,
    conn_id: str | None = None,
    conn_ids: list[str] | None = None,
) -> None:
    """Queue a ``mobile_session_attach`` CH_CONTROL frame for the agent.

    Used on agent reconnect to replay attach for every mobile that was
    already registered before the tunnel died. ``conn_id`` (proto-v8)
    names the specific mobile connection being replayed; ``conn_ids``
    carries the session's whole attached set so the agent can rebuild its
    registry in one batch before the first replayed handshake. Best-effort:
    logs on a full queue and otherwise drops silently (the caller already
    resolved ``conn`` from routing, so ``None`` cannot occur here).
    """
    ctrl: dict[str, object] = {
        "type": "mobile_session_attach", "session_id": session_id,
    }
    if conn_id:
        ctrl["conn_id"] = conn_id
    if conn_ids:
        ctrl["conn_ids"] = conn_ids
    if not try_enqueue_control(conn, ctrl):
        logger.warning(
            "agent_tunnel.attach_replay_drop agent=%s session=%s — agent queue full",
            conn.agent_id,
            session_id,
        )


def _broadcast_status(
    routing: RoutingTable,
    user_id: str,
    agent_id: str,
    *,
    online: bool,
) -> None:
    broadcast_user_event(
        routing,
        user_id,
        "agent.status_changed",
        {"agent_id": agent_id, "online": online},
    )


async def _safe_close(websocket: WebSocket, code: int, reason: str) -> None:
    try:
        if websocket.client_state != WebSocketState.DISCONNECTED:
            await websocket.close(code=code, reason=reason)
    except Exception:
        pass
