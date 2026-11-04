from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, field_validator

from ..shortcuts.models import KIND_PATTERN

# Five built-in modes plus the 20-type custom cap, with room for built-ins a
# later client adds before this server knows them.
MAX_MODE_ORDER_ENTRIES = 64

ModeId = Annotated[str, Field(pattern=KIND_PATTERN)]


class SessionModeOrder(BaseModel):
    """The user's mode-picker order, as REST and the push event carry it.

    ``order`` lists mode ids (``claude``, ``codex``, ``shell``,
    ``custom_<id>``, ...) top to bottom; an empty list means the default
    order. ``version`` is 0 and ``updated_at`` null until the first save."""

    order: list[str]
    version: int
    updated_at: str | None


class UpdateSessionModeOrderRequest(BaseModel):
    """Full replace of the order. Ids are stored opaquely: the server does
    not check them against the built-in set or the live custom types, and
    clients place a mode the list does not name after the ones it does."""

    order: list[ModeId] = Field(max_length=MAX_MODE_ORDER_ENTRIES)

    @field_validator("order")
    @classmethod
    def _reject_duplicates(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("must not contain duplicate mode ids")
        return value
