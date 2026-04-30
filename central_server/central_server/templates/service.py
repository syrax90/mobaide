"""Prompt template service — per-user template CRUD with cross-device push."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..relay.routing import broadcast_user_event

if TYPE_CHECKING:
    from ..db.database import Database
    from ..relay.routing import RoutingTable

logger = logging.getLogger(__name__)


def _row_to_payload(row: dict) -> dict:
    """Coerce a DB row into the wire shape used by both REST responses and
    push events. Keeps a single canonical schema so clients can reuse the
    same JSON parser."""
    return {
        "template_id": row["template_id"],
        "name": row["name"],
        "body": row["body"],
        "is_favorite": bool(row["is_favorite"]),
        "last_used_at": row["last_used_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "version": row["version"],
    }


class TemplateService:
    """Business logic for per-user prompt template CRUD."""

    def __init__(self, db: "Database", routing: "RoutingTable") -> None:
        self._db = db
        self._routing = routing

    async def list_templates(self, user_id: str) -> list[dict]:
        rows = await self._db.list_templates_for_user(user_id)
        return [_row_to_payload(r) for r in rows]

    async def create_template(
        self, owner_user_id: str, name: str, body: str
    ) -> dict:
        row = await self._db.create_template(owner_user_id, name, body)
        payload = _row_to_payload(row)
        broadcast_user_event(
            self._routing, owner_user_id, "template.created", payload
        )
        logger.info(
            "template.created template_id=%s owner=%s",
            row["template_id"], owner_user_id,
        )
        return payload

    async def update_template(
        self,
        template_id: str,
        user_id: str,
        *,
        name: str | None = None,
        body: str | None = None,
        is_favorite: bool | None = None,
    ) -> dict:
        if not await self._db.is_template_owner(template_id, user_id):
            raise ValueError("Not the owner of this template")
        row = await self._db.update_template(
            template_id, name=name, body=body, is_favorite=is_favorite
        )
        if row is None:
            raise ValueError("Template not found")
        payload = _row_to_payload(row)
        broadcast_user_event(
            self._routing, user_id, "template.updated", payload
        )
        return payload

    async def delete_template(
        self, template_id: str, user_id: str
    ) -> None:
        if not await self._db.is_template_owner(template_id, user_id):
            raise ValueError("Not the owner of this template")
        row = await self._db.soft_delete_template(template_id)
        if row is None:
            return
        broadcast_user_event(
            self._routing,
            user_id,
            "template.deleted",
            {
                "template_id": row["template_id"],
                "version": row["version"],
                "updated_at": row["updated_at"],
            },
        )
        logger.info(
            "template.deleted template_id=%s by_user=%s",
            template_id, user_id,
        )

    async def touch_template(
        self, template_id: str, user_id: str
    ) -> dict:
        if not await self._db.is_template_owner(template_id, user_id):
            raise ValueError("Not the owner of this template")
        row = await self._db.touch_template(template_id)
        if row is None:
            raise ValueError("Template not found")
        payload = _row_to_payload(row)
        broadcast_user_event(
            self._routing, user_id, "template.updated", payload
        )
        return payload
