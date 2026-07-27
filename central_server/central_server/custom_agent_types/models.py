from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

# A type name is a mode-picker card label; longer cannot render meaningfully.
MAX_TYPE_NAME = 60

# The launch command is free text handed to the login shell as a single
# argv element; 1000 chars covers any realistic invocation.
MAX_TYPE_COMMAND = 1000


class CustomAgentTypeSummary(BaseModel):
    """Wire shape for a custom agent type returned to clients."""

    id: str
    name: str
    command: str
    created_at: str
    updated_at: str


class CreateCustomAgentTypeRequest(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_TYPE_NAME)
    command: str = Field(min_length=1, max_length=MAX_TYPE_COMMAND)

    @field_validator("name", "command")
    @classmethod
    def _strip_non_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class UpdateCustomAgentTypeRequest(BaseModel):
    """Partial update: omitted/null fields stay unchanged. Unlike shortcut
    names, ``name`` and ``command`` are mandatory attributes of a type, so
    there is no empty-string-clears form — a provided-but-blank value is a
    validation error, and an empty body is rejected outright."""

    name: str | None = Field(default=None, max_length=MAX_TYPE_NAME)
    command: str | None = Field(default=None, max_length=MAX_TYPE_COMMAND)

    @field_validator("name", "command")
    @classmethod
    def _strip_non_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @model_validator(mode="after")
    def _require_a_field(self) -> "UpdateCustomAgentTypeRequest":
        if self.name is None and self.command is None:
            raise ValueError("at least one of name/command must be provided")
        return self
