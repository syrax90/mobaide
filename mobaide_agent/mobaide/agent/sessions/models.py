from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from mobaide.core.antigravity_launcher import AntigravityOptions
from mobaide.core.claude_launcher import ClaudeOptions
from mobaide.core.codex_launcher import CodexOptions
from mobaide.core.models import SessionInfo, session_to_wire_dict


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
    skip_permissions: bool = True
    initial_prompt: str | None = None
    worktree: bool = False
    branch_name: str | None = None
    identifier: str | None = None
    title: str | None = None
    model: str | None = None
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    fast_mode: bool = False
    ultracode: bool = False
    append_system_prompt: str | None = None

    def to_options(self) -> ClaudeOptions:
        return ClaudeOptions(**self.model_dump())


class CodexOptionsModel(BaseModel):
    """Wire DTO for Codex CLI launch options (see :class:`CodexOptions`).

    The enum-like fields are ``Literal``-typed so an invalid value is rejected
    with a 422 at the wire boundary rather than producing a broken ``codex``
    argv. ``protected_namespaces=()`` silences pydantic's ``model_*`` guard for
    the legitimately-named ``model`` field.
    """

    model_config = ConfigDict(protected_namespaces=())

    model: str | None = None
    sandbox: Literal["read-only", "workspace-write", "danger-full-access"] = (
        "workspace-write"
    )
    approval: Literal["untrusted", "on-request", "never"] = "never"
    reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = "high"
    full_auto: bool = False
    initial_prompt: str | None = None
    worktree: bool = False
    branch_name: str | None = None

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

    @model_validator(mode="after")
    def _reject_sandboxed_skip_permissions(self) -> "AntigravityOptionsModel":
        if self.skip_permissions and self.sandbox:
            raise ValueError(
                "skip_permissions and sandbox are mutually exclusive: "
                "--dangerously-skip-permissions auto-approves sandbox escapes"
            )
        if self.model is not None and self.model.startswith("-"):
            raise ValueError("model must not look like a CLI flag (leading '-')")
        if any(d.startswith("-") for d in self.add_dirs):
            raise ValueError("add_dirs entries must not look like CLI flags (leading '-')")
        return self

    def to_options(self) -> AntigravityOptions:
        data = self.model_dump()
        data["add_dirs"] = tuple(data["add_dirs"])
        return AntigravityOptions(**data)


class CreateSessionRequest(BaseModel):
    command: list[str] | None = None
    cwd: str | None = None
    env: dict[str, str] | None = None
    rows: int = 24
    cols: int = 80
    # When set, ``command``/``cwd``/``env`` are ignored and the matching
    # launcher builds them from the project + the CLI options block.
    project_name: str | None = None
    # Which managed CLI to launch: "claude" | "codex" | "antigravity". Optional —
    # when omitted the kind is inferred from whichever options block is present (a
    # legacy claude_options-only payload still resolves to Claude). Shell/Terminal
    # sessions leave this null and send no options block.
    cli: str | None = None
    claude_options: ClaudeOptionsModel | None = None
    codex_options: CodexOptionsModel | None = None
    antigravity_options: AntigravityOptionsModel | None = None
    # Terminal-mode worktree: create a git worktree for a SHELL session
    # (no CLI options) anchored to a stored git project and spawn the shell
    # inside it. Managed Claude/Codex/Antigravity sessions instead carry worktree
    # intent inside their own options block.
    worktree: bool = False
    branch_name: str | None = None


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
    attention: str | None = None
    exit_reason: str | None = None

    @classmethod
    def from_info(cls, info: SessionInfo) -> SessionResponse:
        return cls(**session_to_wire_dict(info))


class ScrollbackResponse(BaseModel):
    session_id: str
    lines: list[str]
    total: int
