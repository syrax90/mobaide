"""Pairing service — credential push registry only.

Device management (list / revoke) lives in
``central_server.devices.router``; this service is kept for the QR-pairing
flow's WS registry wiring.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..db.database import Database


class PairingError(Exception):
    """Raised when a pairing operation fails."""


class PairingService:
    def __init__(self, db: Database) -> None:
        self._db = db
