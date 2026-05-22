"""Pydantic models for the user-scoped device-management API."""
from __future__ import annotations

from pydantic import BaseModel


class DeviceResponse(BaseModel):
    """One entry in the user's ``GET /api/devices`` listing."""

    device_id: str
    name: str | None
    created_at: str
    last_seen_at: str | None
