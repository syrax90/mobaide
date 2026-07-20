from __future__ import annotations

from pydantic import BaseModel, Field

# A shortcut name is a button label; anything longer than 100 chars cannot
# render meaningfully in the control-key row.
MAX_SHORTCUT_NAME = 100

# The session-kind wire vocabulary lives in the clients/agent; the server
# stores it opaquely but rejects strings that cannot be a wire name.
KIND_PATTERN = r"^[a-z0-9_]{1,32}$"


class ShortcutSummary(BaseModel):
    """Wire shape for a saved shortcut returned to clients."""

    shortcut_id: str
    kind: str
    chord_token: str
    name: str | None
    position: int
    created_at: str
    updated_at: str
    version: int


class CreateShortcutRequest(BaseModel):
    kind: str = Field(pattern=KIND_PATTERN)
    chord_token: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=MAX_SHORTCUT_NAME)


class UpdateShortcutRequest(BaseModel):
    """Rename-only update. ``""`` clears the name back to NULL — the Android
    client's JSON encoder omits null-valued defaults, so an empty string is
    the only way it can express "clear" on the wire."""

    name: str | None = Field(default=None, max_length=MAX_SHORTCUT_NAME)
