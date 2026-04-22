"""Pairing service — device binding and management."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..db.database import Database


class PairingError(Exception):
    """Raised when a pairing operation fails."""


class PairingService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list_pairings(self, agent_id: str) -> list[dict]:
        return await self._db.list_pairings_for_agent(agent_id)

    async def revoke_pairing(self, pairing_id: str) -> bool:
        return await self._db.revoke_pairing(pairing_id)
