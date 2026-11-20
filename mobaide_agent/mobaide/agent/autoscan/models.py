from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator

from mobaide.core.opencode_launcher import INITIAL_PROMPT_MAX
from mobaide.linear.scanner import AUTO_SCAN_CLIS, AutoScanStart

from ..sessions.models import ClaudeOptionsModel, ClaudeScanOptionsModel
from ..workspaces.models import (
    AutoScanAgentOptionsModel,
    AutoScanFilterDraftModel,
    AutoScanFilterModel,
    GitHubScanFilterDraftModel,
    GitHubScanFilterModel,
)

AutoScanCli = Literal[
    "claude", "codex", "antigravity", "opencode", "copilot", "cursor", "junie", "pi",
]

# ``capabilities.autoscan`` in ``GET /api/version``: an agent without it
# scans Linear only, runs Claude Code only and has no GitHub routes.
AUTOSCAN_CAPABILITIES: dict[str, object] = {
    "sources": ["linear", "github"],
    "clis": list(AUTO_SCAN_CLIS),
    "github_setup": True,
}


class StartScanRequest(AutoScanAgentOptionsModel):
    """``POST /api/autoscan/start``.

    ``source`` absent is an older client: Linear, Claude Code, the stored
    filter. A newer client sends every choice: ``source``, ``cli``, the
    source's filter, the chosen agent's options (``claude_options``,
    ``codex_options``, …), ``ui``, ``interval_minutes``,
    ``initial_prompt`` (``""`` clears the saved prompt) and, when the
    workspace has none, ``linear_api_key``. The options of other agents are
    ignored.
    """

    project_name: str
    claude_options: ClaudeOptionsModel | None = None  # type: ignore[assignment]
    source: Literal["linear", "github"] | None = None
    cli: AutoScanCli | None = None
    initial_prompt: str | None = Field(default=None, max_length=INITIAL_PROMPT_MAX)
    linear_filter: AutoScanFilterModel | None = None
    github_filter: GitHubScanFilterModel | None = None
    linear_api_key: SecretStr | None = None

    def agent_options(self) -> dict[str, object] | None:
        """The options the chosen agent keeps, ``None`` when the client sent none."""
        cli = self.cli or "claude"
        options = getattr(self, f"{cli}_options", None)
        if options is None:
            return None
        if cli == "claude":
            # Only the fields an Auto Scan keeps; the rest are per launch.
            return ClaudeScanOptionsModel.model_validate(
                options.model_dump(include=set(ClaudeScanOptionsModel.model_fields)),
            ).model_dump()
        return options.model_dump()

    def to_start(self) -> AutoScanStart:
        prompt = self.initial_prompt
        return AutoScanStart(
            source=self.source,
            cli=self.cli,
            agent_options=self.agent_options(),
            ui=self.ui,
            interval_minutes=self.interval_minutes,
            # A blank prompt means "none", as on the older Claude path.
            initial_prompt=prompt if prompt is None or prompt.strip() else "",
            linear_filter=(
                self.linear_filter.to_dataclass() if self.linear_filter is not None else None
            ),
            github_filter=(
                self.github_filter.to_dataclass() if self.github_filter is not None else None
            ),
            linear_api_key=(
                self.linear_api_key.get_secret_value().strip() or None
                if self.linear_api_key is not None
                else None
            ),
        )


class StopScanRequest(BaseModel):
    project_name: str


class RunNowRequest(BaseModel):
    project_name: str


class AutoScanInstanceDto(BaseModel):
    project_name: str
    status: str
    started_at: float
    last_activity_at: float
    processed_count: int
    last_scan_time: float | None = None
    scan_interval_seconds: int
    next_scan_at: str | None = None
    cycle_number: int = 0
    current_seq: int = 0
    source: str = "linear"
    cli: str = "claude"
    ui: str = "terminal"


class AutoScanListResponse(BaseModel):
    scans: list[AutoScanInstanceDto]
    capacity: int
    max_scans: int
    available_projects: list[str]
    auto_scan_prompt: str | None = None


class AutoScanInstanceStatusResponse(AutoScanInstanceDto):
    recent_events: list[dict] = []


class RunNowResponse(BaseModel):
    ok: bool
    project_name: str
    next_scan_at: str | None = None


class GitHubStatusResponse(BaseModel):
    """``GET /api/autoscan/github/status``: what a GitHub scan still needs.

    ``problem`` is the first blocking reason in plain words, ``null`` when a
    scan can start. ``github_host`` says whether ``host`` is GitHub
    (github.com or an Enterprise Server), so signing in can help; ``null``
    before a host is known. ``can_install`` says whether ``POST
    /api/autoscan/github/setup`` with ``action: "install"`` knows an
    installer for this PC; ``install_command`` is that command as text.
    """

    installed: bool
    version: str | None = None
    signed_in: bool
    account: str | None = None
    host: str | None = None
    repo: str | None = None
    repo_url: str | None = None
    problem: str | None = None
    github_host: bool | None = None
    can_install: bool
    install_command: str | None = None


class GitHubLabelModel(BaseModel):
    name: str
    color: str = ""


class GitHubIssueTypeModel(BaseModel):
    """A GitHub issue type of the repository (Bug, Task, Feature…).

    ``color`` is GitHub's color name (``RED``, ``BLUE``…), ``""`` when unknown.
    """

    name: str
    color: str = ""
    description: str = ""


class GitHubLabelsResponse(BaseModel):
    """``GET /api/autoscan/github/labels``: what a GitHub filter can pick.

    ``issue_types`` is empty when the repository has none or GitHub did not
    say (an older GitHub Enterprise Server, for example).
    """

    labels: list[GitHubLabelModel]
    issue_types: list[GitHubIssueTypeModel] = []


class GitHubSetupRequest(BaseModel):
    """``POST /api/autoscan/github/setup``: open a terminal session that
    installs the GitHub CLI (``install``) or signs it in (``sign_in``)."""

    project_name: str
    action: Literal["install", "sign_in"]
    rows: int = Field(default=24, ge=1, le=500)
    cols: int = Field(default=80, ge=1, le=1000)


TEMPLATE_NAME_MAX = 60
TEMPLATES_MAX = 50


class AutoScanTemplateSettings(AutoScanAgentOptionsModel):
    """The Auto Scan choices a template keeps; every field may be ``null``.

    The filters may be incomplete (a GitHub filter without labels, for one):
    the wizard asks for what is missing before a scan starts. A template
    never keeps a Linear API key.
    """

    source: Literal["linear", "github"] | None = None
    cli: AutoScanCli | None = None
    initial_prompt: str | None = Field(default=None, max_length=INITIAL_PROMPT_MAX)
    linear_filter: AutoScanFilterDraftModel | None = None
    github_filter: GitHubScanFilterDraftModel | None = None


def _template_name(value: str) -> str:
    name = " ".join(value.split())
    if not name:
        raise ValueError("the template name must not be blank")
    if len(name) > TEMPLATE_NAME_MAX:
        raise ValueError(f"the template name can have at most {TEMPLATE_NAME_MAX} characters")
    return name


class AutoScanTemplate(BaseModel):
    """A named set of Auto Scan choices saved on this PC."""

    id: str
    name: str
    updated_at: str
    settings: AutoScanTemplateSettings


class SaveTemplateRequest(BaseModel):
    """``POST /api/autoscan/templates``: save ``settings`` under ``name``.

    A template with the same name (ignoring case) is replaced and keeps its
    id. Runs of white space in the name become one space.
    """

    name: str = Field(max_length=200)
    settings: AutoScanTemplateSettings

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        return _template_name(value)


class AutoScanTemplatesResponse(BaseModel):
    """Every template of this PC, sorted by name."""

    templates: list[AutoScanTemplate]
