"""Agent management service — user-owned agent lifecycle."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..db.database import Database
    from ..relay.routing import RoutingTable

logger = logging.getLogger(__name__)


class AgentService:
    """Business logic for user-owned agent CRUD operations."""

    def __init__(self, db: Database, routing: RoutingTable) -> None:
        self._db = db
        self._routing = routing

    async def create_agent(self, name: str, owner_user_id: str) -> tuple[str, str]:
        """Create a new agent owned by the given user.

        Returns (agent_id, raw_token).
        """
        agent_id, raw_token = await self._db.register_agent(name, owner_user_id)
        logger.info(
            "agent.created agent_id=%s owner=%s name=%s",
            agent_id, owner_user_id, name,
        )
        return agent_id, raw_token

    async def list_user_agents(self, user_id: str) -> list[dict]:
        """List agents owned by a user, enriched with online status."""
        agents = await self._db.list_agents_for_user(user_id)
        for agent in agents:
            agent["online"] = self._routing.is_agent_online(agent["agent_id"])
        return agents

    async def reregister_agent(self, agent_id: str, user_id: str) -> str:
        """Regenerate agent credentials. Only the owner may call this.

        Returns the new raw_token.
        Raises ValueError if the user is not the owner.
        """
        if not await self._db.is_agent_owner(agent_id, user_id):
            raise ValueError("Not the owner of this agent")
        raw_token = await self._db.reregister_agent(agent_id)
        logger.info("agent.reregistered agent_id=%s by_user=%s", agent_id, user_id)
        return raw_token

    async def delete_agent(self, agent_id: str, user_id: str) -> None:
        """Delete an agent with full cascade. Only the owner may call this.

        Disconnects the agent from the routing table if online and
        closes all connected mobile clients.
        Raises ValueError if the user is not the owner.
        """
        if not await self._db.is_agent_owner(agent_id, user_id):
            raise ValueError("Not the owner of this agent")

        # Disconnect from routing table if online
        conn = self._routing.get_agent(agent_id)
        if conn is not None:
            disconnected = self._routing.unregister_agent(agent_id, conn.conn_id)
            for client in disconnected:
                try:
                    await client.websocket.close(code=4010, reason="Agent deleted")
                except Exception:
                    pass

        await self._db.delete_agent(agent_id)
        logger.info("agent.deleted agent_id=%s by_user=%s", agent_id, user_id)
