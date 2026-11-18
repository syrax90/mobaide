from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, SecretStr

from mobaide.core.opencode_launcher import INITIAL_PROMPT_MAX
from mobaide.linear.scanner import AUTO_SCAN_CLIS, AutoScanStart

from ..sessions.models import ClaudeOptionsModel
from ..workspaces.models import AutoScanFilterModel, GitHubScanFilterModel

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


class StartScanRequest(BaseModel):
    """``POST /api/autoscan/start``.

    ``source`` absent is an older client: Linear, Claude Code, the stored
    filter. A newer client sends every choice: ``source``, ``cli``, the
    source's filter, ``auto_approve`` (agents other than Claude; Claude takes
    ``claude_options.skip_permissions``), ``initial_prompt`` (``""`` clears
    the saved prompt) and, when the workspace has none, ``linear_api_key``.
    """

    project_name: str
    claude_options: ClaudeOptionsModel | None = None
    source: Literal["linear", "github"] | None = None
    cli: AutoScanCli | None = None
    auto_approve: bool = True
    initial_prompt: str | None = Field(default=None, max_length=INITIAL_PROMPT_MAX)
    linear_filter: AutoScanFilterModel | None = None
    github_filter: GitHubScanFilterModel | None = None
    linear_api_key: SecretStr | None = None

    def to_start(self) -> AutoScanStart:
        prompt = self.initial_prompt
        return AutoScanStart(
            source=self.source,
            cli=self.cli,
            auto_approve=self.auto_approve,
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
    scan can start. ``can_install`` says whether ``POST
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
    can_install: bool
    install_command: str | None = None


class GitHubLabelModel(BaseModel):
    name: str
    color: str = ""


class GitHubLabelsResponse(BaseModel):
    labels: list[GitHubLabelModel]


class GitHubSetupRequest(BaseModel):
    """``POST /api/autoscan/github/setup``: open a terminal session that
    installs the GitHub CLI (``install``) or signs it in (``sign_in``)."""

    project_name: str
    action: Literal["install", "sign_in"]
    rows: int = Field(default=24, ge=1, le=500)
    cols: int = Field(default=80, ge=1, le=1000)
