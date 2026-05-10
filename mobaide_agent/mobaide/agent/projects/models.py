"""Pydantic wire models for the projects CRUD API.

* :class:`ProjectReadModel` is the shape returned to clients — it never
  echoes the ``linear_api_key``. It exposes ``has_linear_key``, ``is_git``,
  ``path_exists`` and ``auto_scan_filter`` so the UI can render the
  per-project Auto Scan filter form without having to issue a separate
  call.
* :class:`ProjectWriteModel` / :class:`ProjectUpdateModel` accept the key as
  a :class:`pydantic.SecretStr` so it is auto-redacted in logs and ``repr``
  while still allowing it to reach disk via ``.get_secret_value()``.
* :class:`AutoScanFilterModel` is the wire shape for the per-project filter.
* :class:`ReorderRequest` carries the atomic reorder payload.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr, field_validator

from mobaide.core.projects_config import AutoScanFilter

# Narrower than ``dict[str, object]`` but wider than plain strings — matches
# the types the launcher's ``default_flags`` handler already accepts.
FlagValue = str | int | float | bool


class AutoScanFilterModel(BaseModel):
    """Per-project Linear filter for Auto Scan Mode.

    Mirrors :class:`mobaide.core.projects_config.AutoScanFilter`. Validation:

    * ``state_names`` and ``label_names`` must each contain at least one
      non-empty string. An empty list would silently match every issue,
      which is almost certainly not what the user wants — better to fail
      loud at the API boundary.
    * ``active_state``, ``done_state``, ``removal_label`` must be non-empty.
    """

    state_names: list[str] = Field(default_factory=lambda: ["Proposals"], min_length=1)
    label_names: list[str] = Field(default_factory=lambda: ["AutoPipeline"], min_length=1)
    active_state: str = Field(default="In Progress", min_length=1)
    done_state: str = Field(default="Done", min_length=1)
    removal_label: str = Field(default="AutoPipeline", min_length=1)

    @field_validator("state_names", "label_names")
    @classmethod
    def _reject_empty_elements(cls, value: list[str]) -> list[str]:
        # ``min_length=1`` only validates the list length, not element content;
        # without this guard, ``[""]`` would pass validation and produce a
        # permanently-no-match scanner state once the empty string flows into
        # the Linear filter dict.
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(
                    "list elements must be non-empty strings",
                )
        return value

    @field_validator("active_state", "done_state", "removal_label")
    @classmethod
    def _reject_blank_string(cls, value: str) -> str:
        # ``min_length=1`` rejects ``""`` but allows whitespace-only strings.
        if not value.strip():
            raise ValueError("must be a non-empty, non-whitespace string")
        return value

    @classmethod
    def from_dataclass(cls, f: AutoScanFilter) -> "AutoScanFilterModel":
        return cls(
            state_names=list(f.state_names),
            label_names=list(f.label_names),
            active_state=f.active_state,
            done_state=f.done_state,
            removal_label=f.removal_label,
        )

    def to_dataclass(self) -> AutoScanFilter:
        return AutoScanFilter(
            state_names=list(self.state_names),
            label_names=list(self.label_names),
            active_state=self.active_state,
            done_state=self.done_state,
            removal_label=self.removal_label,
        )


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
    auto_scan_filter: AutoScanFilterModel = Field(default_factory=AutoScanFilterModel)


class ProjectWriteModel(BaseModel):
    """Request body for ``POST /api/projects``."""

    name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    linear_api_key: SecretStr | None = None
    conda_env: str | None = None
    default_flags: dict[str, FlagValue] = Field(default_factory=dict)
    auto_scan_filter: AutoScanFilterModel | None = None


class ProjectUpdateModel(BaseModel):
    """Request body for ``PUT /api/projects/{name}``.

    Three-state semantics for ``linear_api_key`` and ``conda_env`` follow the
    GitHub/Stripe convention:

    * field omitted / ``null`` → keep the existing value,
    * empty string → clear (store ``None``),
    * non-empty string → replace.

    For ``default_flags``: ``null``/omitted keeps, a dict replaces the whole
    map. Use ``{}`` to clear all flags.

    For ``auto_scan_filter``: ``null``/omitted keeps; a model replaces
    wholesale (no field-level merge — the whole filter is one logical unit).
    """

    name: str | None = Field(default=None, min_length=1)
    path: str | None = Field(default=None, min_length=1)
    linear_api_key: SecretStr | None = None
    conda_env: str | None = None
    default_flags: dict[str, FlagValue] | None = None
    auto_scan_filter: AutoScanFilterModel | None = None


class ReorderRequest(BaseModel):
    """Request body for ``PATCH /api/projects/order``."""

    names: list[str] = Field(min_length=0)


class LinearLabelModel(BaseModel):
    """Linear label discovered via the per-project API key."""

    name: str
    color: str = ""


class LinearStateModel(BaseModel):
    """Linear workflow state discovered via the per-project API key.

    ``type`` is one of Linear's documented state types (``backlog`` /
    ``unstarted`` / ``started`` / ``completed`` / ``canceled`` / ``triage``).
    Empty when the schema didn't include it (defensive — should not happen).
    """

    name: str
    type: str = ""
