"""Pydantic models for the user-scoped device-management API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DeviceResponse(BaseModel):
    """One entry in the user's ``GET /api/devices`` listing.

    ``platform`` is the free-form label the device sent when it last minted
    agent tokens (``"ios"``, ``"android"``, ``"web"``); it is null for rows
    registered by a client that omitted the field.
    """

    device_id: str
    name: str | None
    created_at: str
    last_seen_at: str | None
    platform: str | None = None


class RegisterPushTokenRequest(BaseModel):
    """Body of ``POST /api/devices/{device_id}/push-token``."""

    platform: Literal["ios"]
    token: str = Field(min_length=1, max_length=512)
    environment: Literal["sandbox", "production"]
