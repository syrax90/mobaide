"""In-memory routing table for connected agents and mobile clients.

All operations are synchronous dict mutations — safe within a single
asyncio event loop without locks.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import WebSocket

logger = logging.getLogger(__name__)

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
    """Mobile client connection. Uses identity-based equality (eq=False)
    so instances can be stored in sets despite mutable Queue fields."""

    device_id: str
    websocket: WebSocket
    send_queue: asyncio.Queue[bytes | str] = field(
        default_factory=lambda: asyncio.Queue(maxsize=_MOBILE_QUEUE_SIZE)
    )


@dataclass(eq=False)
class UserClient:
    """User-scoped mobile connection for events that span all of a user's
    agents (presence, ownership changes, etc.).

    Uses identity-based equality so instances can be stored in sets despite
    the mutable Queue field. Payload is text-only (JSON envelopes).
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
        # agent_id -> AgentConnection
        self._agents: dict[str, AgentConnection] = {}
        # agent_id -> { channel_key -> set[MobileClient] }
        self._mobile_clients: dict[str, dict[str, set[MobileClient]]] = {}
        # user_id -> set[UserClient] (user-scoped event channel)
        self._user_clients: dict[str, set[UserClient]] = {}

    # -- Agent operations ------------------------------------------------------

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

    # -- Mobile client operations ----------------------------------------------

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

    # -- User-scoped client operations -----------------------------------------

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
