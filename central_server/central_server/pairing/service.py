"""Pairing service — QR token generation, consumption, and device binding."""
from __future__ import annotations

import base64
import io
import json
from typing import TYPE_CHECKING

import qrcode  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from ..config import CentralServerConfig
    from ..db.database import Database


class PairingError(Exception):
    """Raised when a pairing operation fails."""


class PairingService:
    def __init__(self, db: Database, config: CentralServerConfig) -> None:
        self._db = db
        self._config = config

    async def create_pairing_token(self, agent_id: str) -> tuple[str, str]:
        """Create a single-use pairing token.

        Returns (raw_token, expires_at ISO string).
        """
        return await self._db.create_pairing_token(
            agent_id, self._config.pairing_token_ttl_seconds
        )

    async def consume_pairing_token(
        self,
        raw_token: str,
        mobile_device_id: str,
        device_name: str | None = None,
    ) -> tuple[str, str]:
        """Consume a pairing token and create a pairing.

        Returns (agent_id, pairing_id).
        Raises PairingError if the token is invalid/expired/already consumed.
        """
        agent_id = await self._db.consume_pairing_token(raw_token, mobile_device_id)
        if agent_id is None:
            raise PairingError("Invalid, expired, or already consumed pairing token")
        pairing_id = await self._db.create_pairing(
            agent_id, mobile_device_id, device_name
        )
        return agent_id, pairing_id

    def generate_qr_data(
        self, raw_token: str, agent_id: str, server_url: str
    ) -> str:
        """Generate the QR payload string."""
        return json.dumps(
            {
                "type": "claude-pair",
                "token": raw_token,
                "server": server_url,
                "agent_id": agent_id,
            },
            separators=(",", ":"),
        )

    def generate_qr_image_base64(self, data: str) -> str:
        """Generate a QR code PNG and return as base64 string."""
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=2,
        )
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()

    async def list_pairings(self, agent_id: str) -> list[dict]:
        return await self._db.list_pairings_for_agent(agent_id)

    async def revoke_pairing(self, pairing_id: str) -> bool:
        return await self._db.revoke_pairing(pairing_id)

    async def list_pending_tokens(self, agent_id: str) -> list[dict]:
        return await self._db.get_pending_pairing_tokens(agent_id)
