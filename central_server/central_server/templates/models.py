from __future__ import annotations

from pydantic import BaseModel, Field

# Caps a single template body at 20k chars. A typical "prompt"-style template
# is well under 1k; the cap exists to bound storage exhaustion on a multi-tenant
# server without constraining real usage.
MAX_TEMPLATE_BODY = 20_000


class TemplateSummary(BaseModel):
    """Wire shape for a prompt template returned to clients."""

    template_id: str
    name: str
    body: str
    is_favorite: bool
    last_used_at: str | None
    created_at: str
    updated_at: str
    version: int


class CreateTemplateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=MAX_TEMPLATE_BODY)


class UpdateTemplateRequest(BaseModel):
    """Partial update — every field is optional. The router rejects an
    all-None payload at the service layer."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=MAX_TEMPLATE_BODY)
    is_favorite: bool | None = None
