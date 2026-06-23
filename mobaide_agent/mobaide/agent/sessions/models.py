from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from mobaide.core.claude_launcher import ClaudeOptions
from mobaide.core.codex_launcher import CodexOptions
from mobaide.core.models import SessionInfo, session_to_wire_dict


class ClaudeOptionsModel(BaseModel):
    plan_mode: bool = False
    no_memory: bool = False
    skip_permissions: bool = True
    initial_prompt: str | None = None
    worktree: bool = False
    branch_name: str | None = None
    identifier: str | None = None
    title: str | None = None
    max_effort: bool = True

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


class CreateSessionRequest(BaseModel):
    command: list[str] | None = None
    cwd: str | None = None
    env: dict[str, str] | None = None
    rows: int = 24
    cols: int = 80
    # When set, ``command``/``cwd``/``env`` are ignored and the matching
    # launcher builds them from the project + the CLI options block.
    project_name: str | None = None
    # Which managed CLI to launch: "claude" | "codex". Optional — when omitted
    # the kind is inferred from whichever options block is present (a legacy
    # claude_options-only payload still resolves to Claude). Shell/Terminal
    # sessions leave this null and send no options block.
    cli: str | None = None
    claude_options: ClaudeOptionsModel | None = None
    codex_options: CodexOptionsModel | None = None
    # Terminal-mode worktree: create a git worktree for a SHELL session
    # (no CLI options) anchored to a stored git project and spawn the shell
    # inside it. Managed Claude/Codex sessions instead carry worktree intent
    # inside their own options block (``claude_options``/``codex_options``).
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
