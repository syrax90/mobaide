"""Pydantic wire models for the workspaces CRUD API.

* :class:`WorkspaceReadModel` is the shape returned to clients — it never
  echoes the ``linear_api_key``. It exposes ``has_linear_key``, ``is_git``,
  ``path_exists`` and ``auto_scan_filter`` so the UI can render the
  per-workspace Auto Scan filter form without having to issue a separate
  call.
* :class:`WorkspaceWriteModel` / :class:`WorkspaceUpdateModel` accept the key as
  a :class:`pydantic.SecretStr` so it is auto-redacted in logs and ``repr``
  while still allowing it to reach disk via ``.get_secret_value()``.
* :class:`AutoScanFilterModel` is the wire shape for the per-workspace Linear
  filter, :class:`GitHubScanFilterModel` for the GitHub one, and
  :class:`AutoScanSettingsModel` for the last Auto Scan choices.
* :class:`ReorderRequest` carries the atomic reorder payload.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    Field,
    SecretStr,
    ValidationError,
    ValidatorFunctionWrapHandler,
    field_validator,
    model_validator,
)

from mobaide.core.workspaces_config import AutoScanFilter, GitHubScanFilter

from ..sessions.models import (
    AntigravityScanOptionsModel,
    ClaudeScanOptionsModel,
    CodexScanOptionsModel,
    CopilotScanOptionsModel,
    CursorScanOptionsModel,
    JunieScanOptionsModel,
    OpenCodeScanOptionsModel,
)

_LABEL_MAX = 100

# Narrower than ``dict[str, object]`` but wider than plain strings — matches
# the types the launcher's ``default_flags`` handler already accepts.
FlagValue = str | int | float | bool


class AutoScanFilterModel(BaseModel):
    """Per-workspace Linear filter for Auto Scan Mode.

    Mirrors :class:`mobaide.core.workspaces_config.AutoScanFilter`. Validation:

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


def _github_label(value: str) -> str:
    label = value.strip()
    if not label:
        raise ValueError("label names must be non-empty")
    if len(label) > _LABEL_MAX:
        raise ValueError(f"label names must be at most {_LABEL_MAX} characters")
    # gh splits a label option's value on commas.
    if "," in label:
        raise ValueError("label names must not contain a comma")
    return label


def _issue_type(value: str) -> str:
    name = value.strip()
    if not name:
        raise ValueError("issue type names must be non-empty")
    if len(name) > _LABEL_MAX:
        raise ValueError(f"issue type names must be at most {_LABEL_MAX} characters")
    return name


class GitHubScanFilterDraftModel(BaseModel):
    """A GitHub filter as a template keeps it: like
    :class:`GitHubScanFilterModel`, but ``labels`` may still be empty."""

    labels: list[str] = Field(default_factory=list, max_length=20)
    assignee: str | None = Field(default=None, max_length=100)
    active_label: str | None = Field(default=None, max_length=_LABEL_MAX)
    issue_types: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("labels")
    @classmethod
    def _clean_labels(cls, value: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(_github_label(item) for item in value))
        return cleaned

    @field_validator("issue_types")
    @classmethod
    def _clean_issue_types(cls, value: list[str]) -> list[str]:
        seen: dict[str, str] = {}
        for item in value:
            name = _issue_type(item)
            seen.setdefault(name.casefold(), name)
        return list(seen.values())

    @field_validator("assignee", "active_label")
    @classmethod
    def _blank_to_none(cls, value: str | None, info) -> str | None:
        if value is None or not value.strip():
            return None
        if info.field_name == "active_label":
            return _github_label(value)
        return value.strip()


class GitHubScanFilterModel(GitHubScanFilterDraftModel):
    """Per-workspace GitHub filter for Auto Scan Mode.

    Mirrors :class:`mobaide.core.workspaces_config.GitHubScanFilter`.
    ``labels`` holds 1–20 distinct label names; a blank ``assignee`` or
    ``active_label`` is ``null``. ``issue_types`` holds up to 20 GitHub issue
    type names (distinct, ignoring case); empty takes every type.
    """

    labels: list[str] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _active_label_is_not_a_trigger(self) -> "GitHubScanFilterModel":
        # Adding a trigger label would make the issue match again next cycle.
        if self.active_label and self.active_label.casefold() in {
            label.casefold() for label in self.labels
        }:
            raise ValueError("The label to add must not be one of the labels to pick up.")
        return self

    @classmethod
    def from_dataclass(cls, f: GitHubScanFilter) -> "GitHubScanFilterModel":
        return cls(
            labels=list(f.labels),
            assignee=f.assignee,
            active_label=f.active_label,
            issue_types=list(f.issue_types),
        )

    def to_dataclass(self) -> GitHubScanFilter:
        return GitHubScanFilter(
            labels=list(self.labels),
            assignee=self.assignee,
            active_label=self.active_label,
            issue_types=list(self.issue_types),
        )


class AutoScanFilterDraftModel(BaseModel):
    """A Linear filter as a template keeps it: the fields of
    :class:`AutoScanFilterModel`, any of which may still be empty."""

    state_names: list[str] = Field(default_factory=list, max_length=200)
    label_names: list[str] = Field(default_factory=list, max_length=200)
    active_state: str = Field(default="", max_length=200)
    done_state: str = Field(default="", max_length=200)
    removal_label: str = Field(default="", max_length=200)

    @field_validator("state_names", "label_names")
    @classmethod
    def _drop_blank(cls, value: list[str]) -> list[str]:
        return [item for item in value if item.strip()]


class AutoScanAgentOptionsModel(BaseModel):
    """The Auto Scan launch options of each agent kind; ``null`` for a kind
    nobody chose options for. Pi has none."""

    claude_options: ClaudeScanOptionsModel | None = None
    codex_options: CodexScanOptionsModel | None = None
    antigravity_options: AntigravityScanOptionsModel | None = None
    opencode_options: OpenCodeScanOptionsModel | None = None
    copilot_options: CopilotScanOptionsModel | None = None
    cursor_options: CursorScanOptionsModel | None = None
    junie_options: JunieScanOptionsModel | None = None


class AutoScanSettingsModel(AutoScanAgentOptionsModel):
    """The last Auto Scan choices of a workspace, for the wizard to show again.

    Every field is ``null`` until a scan start (or, for ``linear_filter``, an
    older client's workspace form) sets it. A ``null`` filter means nobody
    chose one: the wizard starts with empty fields. ``<kind>_options`` are
    the options of the last start with that agent.
    """

    source: Literal["linear", "github"] | None = None
    cli: str | None = None
    linear_filter: AutoScanFilterModel | None = None
    github_filter: GitHubScanFilterModel | None = None

    @field_validator(
        "claude_options", "codex_options", "antigravity_options", "opencode_options",
        "copilot_options", "cursor_options", "junie_options",
        mode="wrap",
    )
    @classmethod
    def _drop_invalid_options(cls, value: Any, handler: ValidatorFunctionWrapHandler) -> Any:
        # Stored values a newer validator rejects must not fail the whole
        # workspace list: such options read as never chosen.
        try:
            return handler(value)
        except ValidationError:
            return None


class WorkspaceReadModel(BaseModel):
    """Client-safe view of a single workspace."""

    name: str
    path: str
    conda_env: str | None = None
    default_flags: dict[str, FlagValue] = Field(default_factory=dict)
    order: int
    has_linear_key: bool
    is_git: bool
    path_exists: bool
    auto_scan_filter: AutoScanFilterModel = Field(default_factory=AutoScanFilterModel)
    # Per-workspace Auto Scan initial prompt, saved on scan start so the wizard
    # can pre-seed it next time. ``None`` means "use the engine-wide default".
    auto_scan_prompt: str | None = None
    auto_scan: AutoScanSettingsModel = Field(default_factory=AutoScanSettingsModel)


class WorkspaceWriteModel(BaseModel):
    """Request body for ``POST /api/projects``."""

    name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    linear_api_key: SecretStr | None = None
    conda_env: str | None = None
    default_flags: dict[str, FlagValue] = Field(default_factory=dict)
    auto_scan_filter: AutoScanFilterModel | None = None


class WorkspaceUpdateModel(BaseModel):
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
    """Linear label discovered via the per-workspace API key."""

    name: str
    color: str = ""


class LinearStateModel(BaseModel):
    """Linear workflow state discovered via the per-workspace API key.

    ``type`` is one of Linear's documented state types (``backlog`` /
    ``unstarted`` / ``started`` / ``completed`` / ``canceled`` / ``triage``).
    Empty when the schema didn't include it (defensive — should not happen).
    """

    name: str
    type: str = ""
