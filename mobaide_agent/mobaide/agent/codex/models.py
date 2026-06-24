"""Pydantic wire models for the Codex auth API."""
from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr


class CodexLoginRequest(BaseModel):
    """API-key login payload.

    The key is a :class:`pydantic.SecretStr` so it is auto-redacted in logs and
    ``repr``; it is piped to ``codex login --with-api-key`` via stdin and never
    persisted by the agent (Codex writes its own ``auth.json``).
    """

    api_key: SecretStr = Field(min_length=1)


class CodexLoginResponse(BaseModel):
    authenticated: bool
