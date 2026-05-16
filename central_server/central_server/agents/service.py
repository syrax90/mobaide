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

        Returns (agent_id, raw_token). Raises ValueError if the owner is an
        admin — admins never own agents.
        """
        owner = await self._db.get_user_by_id(owner_user_id)
        if owner is not None and owner["role"] == "admin":
            raise ValueError("Admin accounts cannot own agents")
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

        Raises ValueError if the user is not the owner.
        """
        if not await self._db.is_agent_owner(agent_id, user_id):
            raise ValueError("Not the owner of this agent")
        raw_token = await self._db.reregister_agent(agent_id)
        logger.info("agent.reregistered agent_id=%s by_user=%s", agent_id, user_id)
        return raw_token

    async def delete_agent(
        self, agent_id: str, user_id: str, *, as_admin: bool = False
    ) -> None:
        """Delete an agent with full cascade and tear down live sockets.

        Owner path (``as_admin=False``) requires ``user_id`` to own the
        agent and raises ``ValueError`` otherwise. Admin path
        (``as_admin=True``) bypasses the ownership check so the dashboard
        can revoke any agent regardless of owner; ``user_id`` is still
        recorded in the audit log as the actor.

        Live sockets are torn down via
        ``RoutingTable.disconnect_agent`` which sends the standard
        ``{"reason": "admin_revoked"}`` text frame before each
        ``close(4010)`` (mirrors ``delete_user``).
        """
        if not as_admin and not await self._db.is_agent_owner(agent_id, user_id):
            raise ValueError("Not the owner of this agent")

        from ..relay.routing import WS_CLOSE_ADMIN_REVOKED

        await self._routing.disconnect_agent(
            agent_id, WS_CLOSE_ADMIN_REVOKED, "admin_revoked"
        )
        await self._db.delete_agent(agent_id)
        logger.info(
            "agent.deleted agent_id=%s by_user=%s as_admin=%s",
            agent_id, user_id, as_admin,
        )
