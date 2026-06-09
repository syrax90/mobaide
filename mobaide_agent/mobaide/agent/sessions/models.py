from __future__ import annotations

from pydantic import BaseModel

from mobaide.core.claude_launcher import ClaudeOptions
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


class CreateSessionRequest(BaseModel):
    command: list[str] | None = None
    cwd: str | None = None
    env: dict[str, str] | None = None
    rows: int = 24
    cols: int = 80
    # When set, ``command``/``cwd``/``env`` are ignored and
    # ``ClaudeLauncher`` builds them from the project + ``claude_options``.
    project_name: str | None = None
    claude_options: ClaudeOptionsModel | None = None
    # Terminal-mode worktree: create a git worktree for a SHELL session
    # (no ``claude_options``) anchored to a stored git project and spawn the
    # shell inside it. Claude sessions use ``claude_options.worktree`` instead.
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

    @classmethod
    def from_info(cls, info: SessionInfo) -> SessionResponse:
        return cls(**session_to_wire_dict(info))


class ScrollbackResponse(BaseModel):
    session_id: str
    lines: list[str]
    total: int
