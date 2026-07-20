"""Saved-shortcut service — per-user pinned chord CRUD with cross-device push."""
from __future__ import annotations

import logging
import sqlite3
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
        "shortcut_id": row["shortcut_id"],
        "kind": row["kind"],
        "chord_token": row["chord_token"],
        "name": row["name"],
        "position": row["position"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "version": row["version"],
    }


class ShortcutService:
    """Business logic for per-user saved keyboard shortcut CRUD."""

    def __init__(self, db: "Database", routing: "RoutingTable") -> None:
        self._db = db
        self._routing = routing

    async def list_shortcuts(self, user_id: str) -> list[dict]:
        rows = await self._db.list_user_shortcuts_for_user(user_id)
        return [_row_to_payload(r) for r in rows]

    async def create_shortcut(
        self,
        owner_user_id: str,
        kind: str,
        chord_token: str,
        name: str | None,
    ) -> dict:
        """Upsert: saving a chord that already exists live for the same
        (owner, kind) updates its name (when one is provided) instead of
        creating a duplicate button."""
        name = name or None
        existing = await self._db.get_live_user_shortcut_by_chord(
            owner_user_id, kind, chord_token
        )
        if existing is None:
            try:
                row = await self._db.create_user_shortcut(
                    owner_user_id, kind, chord_token, name
                )
            except sqlite3.IntegrityError:
                # Lost a race with a concurrent save of the same chord —
                # the partial unique index is the backstop; fall through to
                # the upsert path against the winning row.
                existing = await self._db.get_live_user_shortcut_by_chord(
                    owner_user_id, kind, chord_token
                )
                if existing is None:
                    raise
            else:
                payload = _row_to_payload(row)
                broadcast_user_event(
                    self._routing, owner_user_id, "shortcut.created", payload
                )
                logger.info(
                    "shortcut.created shortcut_id=%s owner=%s kind=%s",
                    row["shortcut_id"], owner_user_id, kind,
                )
                return payload
        if name is not None and name != existing["name"]:
            row = await self._db.update_user_shortcut_name(
                existing["shortcut_id"], name
            )
            if row is None:
                return _row_to_payload(existing)
            payload = _row_to_payload(row)
            broadcast_user_event(
                self._routing, owner_user_id, "shortcut.updated", payload
            )
            return payload
        return _row_to_payload(existing)

    async def rename_shortcut(
        self, shortcut_id: str, user_id: str, name: str | None
    ) -> dict:
        """Set or clear (empty/None name) the display name."""
        if not await self._db.is_user_shortcut_owner(shortcut_id, user_id):
            raise ValueError("Not the owner of this shortcut")
        row = await self._db.update_user_shortcut_name(
            shortcut_id, name or None
        )
        if row is None:
            raise ValueError("Shortcut not found")
        payload = _row_to_payload(row)
        broadcast_user_event(
            self._routing, user_id, "shortcut.updated", payload
        )
        return payload

    async def delete_shortcut(
        self, shortcut_id: str, user_id: str
    ) -> None:
        if not await self._db.is_user_shortcut_owner(shortcut_id, user_id):
            raise ValueError("Not the owner of this shortcut")
        row = await self._db.soft_delete_user_shortcut(shortcut_id)
        if row is None:
            return
        broadcast_user_event(
            self._routing,
            user_id,
            "shortcut.deleted",
            {
                "shortcut_id": row["shortcut_id"],
                "version": row["version"],
                "updated_at": row["updated_at"],
            },
        )
        logger.info(
            "shortcut.deleted shortcut_id=%s by_user=%s",
            shortcut_id, user_id,
        )
