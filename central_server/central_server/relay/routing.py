"""In-memory routing table for connected agents and mobile clients.

All operations are synchronous dict mutations — safe within a single
asyncio event loop without locks.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import WebSocket

from ..monitoring import record_send_queue_drop
from .envelope import CH_CONTROL, encode

logger = logging.getLogger(__name__)

# Application-defined WebSocket close codes in the 4000-4999 range
# (RFC 6455 §7.4.2). Documented here so the Android client and the
# server share one source of truth.
#
# 4001/4002/4003 are reserved by the Android WebSocketConnection for
# auth-failure / protocol-incompatibility / token-not-valid-for-agent
# semantics — admin force-disconnect must NOT collide with those or the
# client will wipe its session on what should be a transient boot.
WS_CLOSE_ADMIN_FORCE_DISCONNECT = 4011  # non-destructive admin boot
WS_CLOSE_ADMIN_REVOKED = 4010  # admin unregistered the user/agent

# Transient agent-offline at relay-attach time. Distinct from
# WS_CLOSE_ADMIN_REVOKED (4010) because the agent may come back online at
# any moment — the mobile client SHOULD continue its normal exponential-
# backoff reconnect loop instead of treating the channel as permanently
# dead. Collapsing the two onto code 4010 used to be a latent bug: the
# Android side fell through unknown codes to scheduleReconnect(), so the
# protocol violation was invisible; once the client started honouring
# 4010 as "admin destructively unregistered", a transient agent restart
# would have permanently killed every per-agent socket on the device.
WS_CLOSE_AGENT_TRANSIENT_OFFLINE = 4012


async def safe_send_reason_and_close(
    websocket: WebSocket, code: int, reason_code: str
) -> None:
    """Send ``{"reason": reason_code}`` as a text frame, then close the WS.

    Both the send and the close are wrapped in try/except so an
    already-closed or never-accepted socket is a no-op rather than a
    raise (FastAPI issue #4672 — receiving a ``WebSocketDisconnect``
    on an already-closed socket is a known footgun).
    """
    try:
        await websocket.send_text(json.dumps({"reason": reason_code}))
    except Exception:
        pass
    try:
        await websocket.close(code=code, reason=reason_code)
    except Exception:
        pass


def try_enqueue_control(
    conn: "AgentConnection | None", body: dict
) -> bool:
    """Encode a JSON ``body`` as a CH_CONTROL envelope and enqueue on ``conn``.

    Returns ``True`` if the frame was accepted by the queue, ``False`` if
    ``conn`` is ``None`` (agent offline) or the queue is saturated.
    Callers log their own context-specific warning on ``False`` because
    the two places this helper is called from (mobile connect in
    ``mobile_ws`` vs. agent reconnect replay in ``agent_ws``) want
    different operator-facing log messages.
    """
    if conn is None:
        return False
    envelope = encode(CH_CONTROL, "", json.dumps(body).encode())
    try:
        conn.send_queue.put_nowait(envelope)
        return True
    except asyncio.QueueFull:
        record_send_queue_drop("control")
        return False


def broadcast_user_event(
    routing: "RoutingTable",
    user_id: str,
    event_type: str,
    payload: dict,
) -> None:
    """Push a JSON envelope ``{"type": event_type, "payload": payload}`` to
    every user-scoped client of ``user_id``. Non-blocking; drops on a full
    queue with a WARN log because the client re-syncs via REST on its next
    reconnect.

    This is the single fan-out helper for user-scoped events
    (``agent.status_changed``, ``template.*``, ...). New event types add a
    caller, never a copy of this loop.
    """
    envelope = json.dumps({"type": event_type, "payload": payload})
    for client in routing.get_user_clients(user_id):
        try:
            client.send_queue.put_nowait(envelope)
        except asyncio.QueueFull:
            record_send_queue_drop("user_event")
            logger.warning(
                "user_events.queue_full user_id=%s device=%s — "
                "dropping %s event",
                user_id,
                client.device_id,
                event_type,
            )

# Bounded queues to prevent memory exhaustion from slow clients.
_AGENT_QUEUE_SIZE = 10_000
_MOBILE_QUEUE_SIZE = 5_000


@dataclass
class AgentConnection:
    agent_id: str
    websocket: WebSocket
    conn_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    # Owner resolved at registration time so status broadcasts don't require
    # an async DB hit on the disconnect path.
    owner_user_id: str | None = None
    # Negotiated wire-protocol version of THIS tunnel, stamped synchronously
    # at registration. Gates that must match the live connection (e.g. the
    # custom-agent-type resolved_command injection) read this instead of the
    # DB row, which two overlapping tunnels can leave describing the other
    # connection.
    protocol_version: int | None = None
    send_queue: asyncio.Queue[bytes] = field(
        default_factory=lambda: asyncio.Queue(maxsize=_AGENT_QUEUE_SIZE)
    )
    connected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_heartbeat: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass(eq=False)
class MobileClient:
    """Mobile client connection with identity-based equality so instances can be stored in sets despite mutable Queue fields."""

    device_id: str
    websocket: WebSocket
    send_queue: asyncio.Queue[bytes | str] = field(
        default_factory=lambda: asyncio.Queue(maxsize=_MOBILE_QUEUE_SIZE)
    )


@dataclass(eq=False)
class UserClient:
    """User-scoped connection for events spanning all of a user's agents (presence, ownership changes).

    Payload is text-only (JSON envelopes); uses identity-based equality so instances can be stored
    in sets despite the mutable Queue field.
    """

    user_id: str
    device_id: str
    websocket: WebSocket
    # Registered device this socket speaks for, verified at connect against the
    # caller's own device rows. None when the client sent no hint or one that
    # did not verify; such a socket never suppresses a push, because there is no
    # way to tell which device it would be suppressing for.
    bound_device_id: str | None = None
    send_queue: asyncio.Queue[str] = field(
        default_factory=lambda: asyncio.Queue(maxsize=_MOBILE_QUEUE_SIZE)
    )


class RoutingTable:
    """Tracks connected agents and their paired mobile clients."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentConnection] = {}
        self._mobile_clients: dict[str, dict[str, set[MobileClient]]] = {}
        self._user_clients: dict[str, set[UserClient]] = {}

    def register_agent(self, agent_id: str, ws: WebSocket) -> AgentConnection:
        conn = AgentConnection(agent_id=agent_id, websocket=ws)
        self._agents[agent_id] = conn
        self._mobile_clients.setdefault(agent_id, {})
        logger.info("routing.agent_registered agent_id=%s", agent_id)
        return conn

    def unregister_agent(
        self, agent_id: str, conn_id: str | None = None
    ) -> list[MobileClient]:
        """Remove agent and return all its mobile clients (for cleanup).

        If *conn_id* is provided, the removal is a no-op when the current
        connection has a different ID (prevents a stale disconnect handler
        from evicting a newer re-connection).
        """
        current = self._agents.get(agent_id)
        if current is None:
            return []
        if conn_id is not None and current.conn_id != conn_id:
            logger.debug(
                "routing.unregister_skipped agent_id=%s stale_conn=%s current_conn=%s",
                agent_id,
                conn_id,
                current.conn_id,
            )
            return []
        self._agents.pop(agent_id, None)
        channels = self._mobile_clients.pop(agent_id, {})
        all_clients = []
        for clients in channels.values():
            all_clients.extend(clients)
        logger.info(
            "routing.agent_unregistered agent_id=%s mobile_clients=%d",
            agent_id,
            len(all_clients),
        )
        return all_clients

    def get_agent(self, agent_id: str) -> AgentConnection | None:
        return self._agents.get(agent_id)

    def is_agent_online(self, agent_id: str) -> bool:
        return agent_id in self._agents

    def agent_heartbeat(self, agent_id: str) -> None:
        conn = self._agents.get(agent_id)
        if conn:
            conn.last_heartbeat = datetime.now(timezone.utc)

    def list_online_agents(self) -> list[AgentConnection]:
        return list(self._agents.values())

    def gauges(self) -> dict[str, int]:
        """Connection-count snapshot for the monitoring.sample log line.

        O(connections) walk; fine at the 60 s monitoring cadence. Queue
        depths cover agent send queues only — they are the relay's shared
        fan-in points, so depth there is the earliest saturation signal,
        while per-mobile queues drain into exactly one socket each.
        """
        mobile_sockets = attached_sessions = 0
        for channels in self._mobile_clients.values():
            for key, clients in channels.items():
                mobile_sockets += len(clients)
                if key.startswith("session_io:") and clients:
                    attached_sessions += 1
        queue_total = queue_max = 0
        for conn in self._agents.values():
            depth = conn.send_queue.qsize()
            queue_total += depth
            queue_max = max(queue_max, depth)
        return {
            "agents_online": len(self._agents),
            "mobile_sockets": mobile_sockets,
            "attached_sessions": attached_sessions,
            "users_connected": len(self._user_clients),
            "user_sockets": sum(
                len(clients) for clients in self._user_clients.values()
            ),
            "agent_queue_depth_max": queue_max,
            "agent_queue_depth_total": queue_total,
        }

    def register_mobile(
        self, agent_id: str, channel_key: str, client: MobileClient
    ) -> None:
        channels = self._mobile_clients.setdefault(agent_id, {})
        clients = channels.setdefault(channel_key, set())
        clients.add(client)
        logger.debug(
            "routing.mobile_registered agent_id=%s channel=%s device=%s",
            agent_id,
            channel_key,
            client.device_id,
        )

    def unregister_mobile(
        self, agent_id: str, channel_key: str, client: MobileClient
    ) -> None:
        channels = self._mobile_clients.get(agent_id)
        if channels is None:
            return
        clients = channels.get(channel_key)
        if clients is None:
            return
        clients.discard(client)
        if not clients:
            channels.pop(channel_key, None)

    def get_mobile_clients(
        self, agent_id: str, channel_key: str
    ) -> set[MobileClient]:
        channels = self._mobile_clients.get(agent_id, {})
        return channels.get(channel_key, set())

    def get_all_mobile_for_agent(self, agent_id: str) -> list[MobileClient]:
        """All mobile clients across all channels for an agent."""
        channels = self._mobile_clients.get(agent_id, {})
        result = []
        for clients in channels.values():
            result.extend(clients)
        return result

    def list_attached_sessions(self, agent_id: str) -> list[str]:
        """Session ids with at least one mobile attached on ``session_io:*``.

        Used on agent reconnect to replay ``mobile_session_attach`` control
        frames so the agent re-sends the PTY handshake and the mobile's
        keyboard is unblocked without waiting for another first-input frame.
        """
        channels = self._mobile_clients.get(agent_id, {})
        prefix = "session_io:"
        return [
            key[len(prefix) :]
            for key, clients in channels.items()
            if key.startswith(prefix) and clients
        ]

    def register_user_client(self, user_id: str, client: UserClient) -> None:
        clients = self._user_clients.setdefault(user_id, set())
        clients.add(client)
        logger.debug(
            "routing.user_client_registered user_id=%s device=%s",
            user_id,
            client.device_id,
        )

    def unregister_user_client(self, user_id: str, client: UserClient) -> None:
        clients = self._user_clients.get(user_id)
        if clients is None:
            return
        clients.discard(client)
        if not clients:
            self._user_clients.pop(user_id, None)

    def get_user_clients(self, user_id: str) -> set[UserClient]:
        return self._user_clients.get(user_id, set())

    def get_user_device_ids(self, user_id: str) -> set[str]:
        """Registered devices of ``user_id`` that hold a live user-events
        socket, i.e. the ones WebSocket delivery already reaches.

        Only verified bindings count: an unbound socket cannot stand in for any
        particular device, so it must not keep that device from being pushed.
        """
        return {
            client.bound_device_id
            for client in self._user_clients.get(user_id, ())
            if client.bound_device_id is not None
        }

    async def disconnect_user(
        self,
        user_id: str,
        code: int = WS_CLOSE_ADMIN_REVOKED,
        reason_code: str = "admin_revoked",
    ) -> int:
        """Close every user-scoped WS for ``user_id`` after a reason frame.

        Pops the user_id from ``_user_clients`` atomically, then for each
        snapshot client sends a ``{"reason": reason_code}`` text frame and
        closes the underlying WebSocket. Returns the number of clients
        that were closed (zero when the user has no live sessions).
        """
        clients = self._user_clients.pop(user_id, set())
        for client in clients:
            await safe_send_reason_and_close(
                client.websocket, code, reason_code
            )
        if clients:
            logger.info(
                "routing.user_clients_disconnected user_id=%s count=%d "
                "code=%d reason=%s",
                user_id,
                len(clients),
                code,
                reason_code,
            )
        return len(clients)

    async def disconnect_agent(
        self,
        agent_id: str,
        code: int = WS_CLOSE_ADMIN_REVOKED,
        reason_code: str = "admin_revoked",
    ) -> int:
        """Close an agent's WS plus every paired mobile-client WS.

        No-op when the agent is not online. Returns the total number of
        sockets closed (agent + mobiles). Uses ``unregister_agent`` to
        atomically pop the agent and its mobile-client list out of the
        routing tables, then closes each WS with a reason frame.
        """
        conn = self._agents.get(agent_id)
        if conn is None:
            return 0
        mobiles = self.unregister_agent(agent_id, conn.conn_id)
        await safe_send_reason_and_close(conn.websocket, code, reason_code)
        for client in mobiles:
            await safe_send_reason_and_close(
                client.websocket, code, reason_code
            )
        return 1 + len(mobiles)
