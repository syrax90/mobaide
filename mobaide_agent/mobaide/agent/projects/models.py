"""Pydantic wire models for the projects CRUD API.

* :class:`ProjectReadModel` is the shape returned to clients — it never
  echoes the ``linear_api_key``. It exposes ``has_linear_key``, ``is_git``,
  and ``path_exists`` so the UI can warn the user about misconfigured
  projects without the server needing to reject them.
* :class:`ProjectWriteModel` / :class:`ProjectUpdateModel` accept the key as
  a :class:`pydantic.SecretStr` so it is auto-redacted in logs and ``repr``
  while still allowing it to reach disk via ``.get_secret_value()``.
* :class:`ReorderRequest` carries the atomic reorder payload.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr

# Narrower than ``dict[str, object]`` but wider than plain strings — matches
# the types the launcher's ``default_flags`` handler already accepts.
FlagValue = str | int | float | bool


class ProjectReadModel(BaseModel):
    """Client-safe view of a single project."""

    name: str
    path: str
    conda_env: str | None = None
    default_flags: dict[str, FlagValue] = Field(default_factory=dict)
    order: int
    has_linear_key: bool
    is_git: bool
    path_exists: bool


class ProjectWriteModel(BaseModel):
    """Request body for ``POST /api/projects``."""

    name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    linear_api_key: SecretStr | None = None
    conda_env: str | None = None
    default_flags: dict[str, FlagValue] = Field(default_factory=dict)


class ProjectUpdateModel(BaseModel):
    """Request body for ``PUT /api/projects/{name}``.

    Three-state semantics for ``linear_api_key`` and ``conda_env`` follow the
    GitHub/Stripe convention:

    * field omitted / ``null`` → keep the existing value,
    * empty string → clear (store ``None``),
    * non-empty string → replace.

    For ``default_flags``: ``null``/omitted keeps, a dict replaces the whole
    map. Use ``{}`` to clear all flags.
    """

    name: str | None = Field(default=None, min_length=1)
    path: str | None = Field(default=None, min_length=1)
    linear_api_key: SecretStr | None = None
    conda_env: str | None = None
    default_flags: dict[str, FlagValue] | None = None


class ReorderRequest(BaseModel):
    """Request body for ``PATCH /api/projects/order``."""

    names: list[str] = Field(min_length=0)
