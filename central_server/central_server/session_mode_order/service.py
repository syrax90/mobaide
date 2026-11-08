"""Per-user create-session mode picker order, with live push."""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from ..relay.routing import broadcast_user_event

if TYPE_CHECKING:
    from ..db.database import Database
    from ..relay.routing import RoutingTable

logger = logging.getLogger(__name__)

EVENT_UPDATED = "session_mode_order.updated"


def _decode_order(raw: str) -> list[str]:
    try:
        value = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _row_to_payload(row: dict) -> dict:
    return {
        "order": _decode_order(row["mode_order"]),
        "version": row["version"],
        "updated_at": row["updated_at"],
    }


class SessionModeOrderService:
    """Read and replace the order; a change is pushed to the user's devices."""

    def __init__(self, db: "Database", routing: "RoutingTable") -> None:
        self._db = db
        self._routing = routing

    async def get_order(self, user_id: str) -> dict:
        row = await self._db.get_session_mode_order(user_id)
        if row is None:
            return {"order": [], "version": 0, "updated_at": None}
        return _row_to_payload(row)

    async def set_order(self, user_id: str, order: list[str]) -> dict:
        encoded = json.dumps(order, separators=(",", ":"))
        row = await self._db.set_session_mode_order(user_id, encoded)
        if row is None:
            # Unchanged: nothing to broadcast, the stored row already holds it.
            return await self.get_order(user_id)
        payload = _row_to_payload(row)
        broadcast_user_event(self._routing, user_id, EVENT_UPDATED, payload)
        logger.info(
            "session_mode_order.updated user=%s version=%s entries=%d",
            user_id, payload["version"], len(order),
        )
        return payload
