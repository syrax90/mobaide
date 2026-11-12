from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mobaide.acp.wire import CHAT_RESUME_ID_MAX, CHAT_RESUME_ID_PATTERN
from mobaide.core.antigravity_launcher import AntigravityOptions, agy_model_name
from mobaide.core.claude_launcher import ClaudeOptions
from mobaide.core.codex_launcher import CodexOptions
from mobaide.core.copilot_launcher import CopilotOptions
from mobaide.core.models import SessionInfo, session_to_wire_dict
from mobaide.core.opencode_launcher import INITIAL_PROMPT_MAX, OpenCodeOptions


class ClaudeOptionsModel(BaseModel):
    """Wire DTO for Claude CLI launch options (see :class:`ClaudeOptions`).

    ``effort`` is ``Literal``-typed so an invalid level is rejected with a 422 at
    the wire boundary; ``model`` stays free-form (the client curates the alias
    list). ``protected_namespaces=()`` silences pydantic's ``model_*`` guard for
    the legitimately-named ``model`` field.
    """

    model_config = ConfigDict(protected_namespaces=())

    plan_mode: bool = False
    no_memory: bool = False
    # See the matching comment on core.claude_launcher.ClaudeOptions: this
    # wire-absence default intentionally stays True even though the mobile
    # UI's own toggle now starts off, to keep old-client/new-agent pairings
    # (and vice versa) from silently flipping an omitted field's meaning.
    skip_permissions: bool = True
    initial_prompt: str | None = None
    worktree: bool = False
    branch_name: str | None = None
    base_branch: str | None = None
    identifier: str | None = None
    title: str | None = None
    model: str | None = None
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    fast_mode: bool = False
    ultracode: bool = False
    append_system_prompt: str | None = None
    debug: bool = False
    focus_mode: bool | None = None

    def to_options(self) -> ClaudeOptions:
        return ClaudeOptions(**self.model_dump())


class CodexOptionsModel(BaseModel):
    """Wire DTO for Codex CLI launch options (see :class:`CodexOptions`).

    ``sandbox`` and ``approval`` are ``Literal``-typed so an invalid value is
    rejected with a 422 at the wire boundary rather than producing a broken
    ``codex`` argv. ``reasoning_effort`` is free-form like ``model``: the
    vocabulary is enumerated per-model at runtime and advertised via
    ``capabilities.cli.codex.model_details``, so a closed Literal would 422
    values the agent itself just advertised whenever the Codex catalog grows.
    The value is embedded after ``=`` inside a single exec'd argv element
    (``-c model_reasoning_effort=<v>``), so there is no flag-smuggling path.
    ``protected_namespaces=()`` silences pydantic's ``model_*`` guard for the
    legitimately-named ``model`` field.
    """

    model_config = ConfigDict(protected_namespaces=())

    model: str | None = None
    sandbox: Literal["read-only", "workspace-write", "danger-full-access"] = (
        "workspace-write"
    )
    approval: Literal["untrusted", "on-request", "never"] = "never"
    reasoning_effort: str | None = "high"
    full_auto: bool = False
    initial_prompt: str | None = None
    worktree: bool = False
    branch_name: str | None = None
    base_branch: str | None = None

    def to_options(self) -> CodexOptions:
        return CodexOptions(**self.model_dump())


class AntigravityOptionsModel(BaseModel):
    """Wire DTO for Antigravity CLI launch options (see :class:`AntigravityOptions`).

    ``model`` is free-form ``str`` — like the Claude/Codex DTOs — carrying the
    full display name ``agy models`` prints (e.g. ``Gemini 3.1 Pro (Low)``);
    the launcher forwards it verbatim as a single argv element.
    ``skip_permissions`` (``--dangerously-skip-permissions``) and ``sandbox``
    are mutually exclusive — skip-permissions auto-approves ``bypassSandbox``
    actions, silently defeating the sandbox (google-antigravity/antigravity-cli
    #36) — so the combination is rejected with a 422 at the wire boundary. To
    keep that guard airtight, ``model`` and each ``add_dirs`` entry (both
    forwarded verbatim as argv elements) are rejected when they look like a CLI
    flag (leading ``-``): otherwise a value such as
    ``add_dirs=["--dangerously-skip-permissions"]`` could smuggle the blocked
    flag past the pair check. No legitimate value is affected — model names
    start with a letter and directories with ``/`` or a drive letter.
    ``protected_namespaces=()`` silences pydantic's ``model_*`` guard for the
    legitimately-named ``model`` field.
    """

    model_config = ConfigDict(protected_namespaces=())

    model: str | None = None
    skip_permissions: bool = False
    sandbox: bool = False
    add_dirs: list[str] = []
    worktree: bool = False
    branch_name: str | None = None
    base_branch: str | None = None

    @model_validator(mode="after")
    def _reject_sandboxed_skip_permissions(self) -> "AntigravityOptionsModel":
        if self.skip_permissions and self.sandbox:
            raise ValueError(
                "skip_permissions and sandbox are mutually exclusive: "
                "--dangerously-skip-permissions auto-approves sandbox escapes"
            )
        # The name the launcher keeps from a saved ``agy models`` line.
        if self.model is not None and agy_model_name(self.model).startswith("-"):
            raise ValueError("model must not look like a CLI flag (leading '-')")
        if any(d.startswith("-") for d in self.add_dirs):
            raise ValueError("add_dirs entries must not look like CLI flags (leading '-')")
        return self

    def to_options(self) -> AntigravityOptions:
        data = self.model_dump()
        data["add_dirs"] = tuple(data["add_dirs"])
        return AntigravityOptions(**data)


class OpenCodeOptionsModel(BaseModel):
    """Wire DTO for OpenCode CLI launch options (see :class:`OpenCodeOptions`).

    ``auto_approve`` is OpenCode's ``--auto``: it accepts every permission
    request that the user's OpenCode rules do not deny. ``initial_prompt`` is
    the first message; it travels as one argv element, so its length is capped.
    ``connect`` starts a terminal session that runs ``opencode auth login``
    (sign in to a model provider) in place of an OpenCode session.
    """

    auto_approve: bool = False
    initial_prompt: str | None = Field(default=None, max_length=INITIAL_PROMPT_MAX)
    connect: bool = False
    worktree: bool = False
    branch_name: str | None = None
    base_branch: str | None = None

    def to_options(self) -> OpenCodeOptions:
        return OpenCodeOptions(**self.model_dump())


class CopilotOptionsModel(BaseModel):
    """Wire DTO for GitHub Copilot CLI launch options (see :class:`CopilotOptions`).

    ``allow_all`` is Copilot's ``--allow-all``: it runs tools and uses paths
    and URLs without asking.
    """

    allow_all: bool = False
    worktree: bool = False
    branch_name: str | None = None
    base_branch: str | None = None

    def to_options(self) -> CopilotOptions:
        return CopilotOptions(**self.model_dump())


class CreateSessionRequest(BaseModel):
    command: list[str] | None = None
    cwd: str | None = None
    env: dict[str, str] | None = None
    rows: int = 24
    cols: int = 80
    # When set, ``command``/``cwd``/``env`` are ignored and the matching
    # launcher builds them from the project + the CLI options block.
    project_name: str | None = None
    # Which managed CLI to launch: "claude" | "codex" | "antigravity" |
    # "opencode" | "copilot". Optional —
    # when omitted the kind is inferred from whichever options block is present (a
    # legacy claude_options-only payload still resolves to Claude). Shell/Terminal
    # sessions leave this null and send no options block.
    cli: str | None = None
    claude_options: ClaudeOptionsModel | None = None
    codex_options: CodexOptionsModel | None = None
    antigravity_options: AntigravityOptionsModel | None = None
    opencode_options: OpenCodeOptionsModel | None = None
    copilot_options: CopilotOptionsModel | None = None
    # Terminal-mode worktree: create a git worktree for a SHELL session
    # (no CLI options) anchored to a stored git project and spawn the shell
    # inside it. Managed Claude/Codex/Antigravity/OpenCode/Copilot sessions instead
    # carry worktree
    # intent inside their own options block.
    worktree: bool = False
    branch_name: str | None = None
    base_branch: str | None = None
    # Server-only fields for custom agent types (``cli="custom_<12hex>"``):
    # the relay strips any client-supplied values and injects the stored
    # command + display name after its registry lookup, so on the relay
    # proxy path a non-null value is always relay-authored. The agent-direct
    # REST router never reads them (custom kinds are rejected there).
    resolved_command: str | None = None
    resolved_name: str | None = None
    # ``"chat"`` starts the session in the structured chat UI (the agent
    # runs over ACP); ``"terminal"`` or absent keeps the PTY session.
    ui: Literal["terminal", "chat"] | None = None
    # Chat only: the agent's stored session to continue (an id from
    # ``GET /api/chat/{kind}/sessions``) instead of starting a new one.
    chat_resume_id: str | None = Field(
        default=None, max_length=CHAT_RESUME_ID_MAX, pattern=CHAT_RESUME_ID_PATTERN
    )


class SessionResponse(BaseModel):
    """REST response for a single session.

    Field set is the canonical wire shape — must stay in lockstep with
    :func:`mobaide.core.models.session_to_wire_dict` and the Android
    ``SessionResponse`` DTO. Add or rename a field in one place only.
    """

    session_id: str
    state: str
    created_at: str
    pid: int | None
    command: list[str]
    cwd: str | None
    title: str | None = None
    project_name: str | None = None
    kind: str = "claude"
    # Custom-type display-name snapshot from creation time; null for
    # built-in kinds.
    kind_label: str | None = None
    attention: str | None = None
    exit_reason: str | None = None
    ui: str = "terminal"
    # ``working`` | ``idle`` | null (unknown); see ``SessionInfo.activity``.
    activity: str | None = None

    @classmethod
    def from_info(cls, info: SessionInfo) -> SessionResponse:
        return cls(**session_to_wire_dict(info))


class ScrollbackResponse(BaseModel):
    session_id: str
    lines: list[str]
    total: int


class ChatStoredSession(BaseModel):
    """One session the agent stored on the host (``session/list``)."""

    session_id: str
    cwd: str
    title: str | None = None
    updated_at: str | None = None
    # The open Mobaide chat session that already shows it, if any.
    open_session_id: str | None = None
    # Another app on the PC has it open (Antigravity's presence lock): the
    # two would overwrite each other's messages.
    open_elsewhere: bool = False


class ChatStoredSessionsResponse(BaseModel):
    sessions: list[ChatStoredSession]
    next_cursor: str | None = None
