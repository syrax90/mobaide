"""Custom agent type service — account-scoped registry CRUD with push."""
from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING

from ..relay.routing import broadcast_user_event

if TYPE_CHECKING:
    from ..db.database import Database
    from ..relay.routing import RoutingTable

logger = logging.getLogger(__name__)

# Per-account cap. Enforced by the DB layer in a single statement so
# concurrent creates cannot overshoot it.
MAX_TYPES_PER_ACCOUNT = 20


class CustomTypeLimitError(Exception):
    """The account already holds the maximum number of custom agent types."""


class CustomTypeNameConflictError(Exception):
    """Another live type of the same account already uses this name
    (case-insensitively)."""


def _row_to_payload(row: dict) -> dict:
    """Coerce a DB row into the wire shape used by both REST responses and
    push events. The stored ``command`` is included — the owner needs it to
    edit the type; it is only session-create requests that never carry it."""
    return {
        "id": row["id"],
        "name": row["name"],
        "command": row["command"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class CustomAgentTypeService:
    """Business logic for per-account custom AI agent type CRUD."""

    def __init__(self, db: "Database", routing: "RoutingTable") -> None:
        self._db = db
        self._routing = routing

    async def list_types(self, user_id: str) -> list[dict]:
        rows = await self._db.list_custom_agent_types_for_user(user_id)
        return [_row_to_payload(r) for r in rows]

    async def create_type(
        self, owner_user_id: str, name: str, command: str
    ) -> dict:
        try:
            row = await self._db.create_custom_agent_type(
                owner_user_id, name, command, cap=MAX_TYPES_PER_ACCOUNT
            )
        except sqlite3.IntegrityError as exc:
            raise CustomTypeNameConflictError(
                "A custom agent type with this name already exists"
            ) from exc
        if row is None:
            raise CustomTypeLimitError(
                f"Custom agent type limit reached "
                f"({MAX_TYPES_PER_ACCOUNT} per account)"
            )
        payload = _row_to_payload(row)
        broadcast_user_event(
            self._routing, owner_user_id, "custom_type.created", payload
        )
        logger.info(
            "custom_type.created id=%s owner=%s",
            row["id"], owner_user_id,
        )
        return payload

    async def update_type(
        self,
        type_id: str,
        user_id: str,
        *,
        name: str | None,
        command: str | None,
    ) -> dict:
        try:
            row = await self._db.update_custom_agent_type(
                type_id, user_id, name=name, command=command
            )
        except sqlite3.IntegrityError as exc:
            raise CustomTypeNameConflictError(
                "A custom agent type with this name already exists"
            ) from exc
        if row is None:
            raise ValueError("Custom agent type not found")
        payload = _row_to_payload(row)
        broadcast_user_event(
            self._routing, user_id, "custom_type.updated", payload
        )
        return payload

    async def delete_type(self, type_id: str, user_id: str) -> None:
        """Soft-delete the type; the DB helper cascades the kind-scoped
        saved shortcuts in the same transaction. Deliberately broadcasts a
        single ``custom_type.deleted`` event (no per-shortcut events):
        clients purge their local shortcut caches for ``custom_<id>`` on
        receipt."""
        row = await self._db.soft_delete_custom_agent_type(type_id, user_id)
        if row is None:
            raise ValueError("Custom agent type not found")
        broadcast_user_event(
            self._routing, user_id, "custom_type.deleted", {"id": type_id}
        )
        logger.info(
            "custom_type.deleted id=%s by_user=%s", type_id, user_id
        )

    async def resolve_for_launch(
        self, type_id: str, owner_user_id: str
    ) -> dict | None:
        """Owner-scoped live lookup used by the relay to inject the stored
        command into a forwarded session-create. Returns the raw row (with
        ``command`` and ``name``) or None for unknown/deleted/foreign ids."""
        return await self._db.get_live_custom_agent_type_for_owner(
            type_id, owner_user_id
        )
