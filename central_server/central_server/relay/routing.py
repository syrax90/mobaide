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

from .envelope import CH_CONTROL, encode

logger = logging.getLogger(__name__)


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
