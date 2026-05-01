from __future__ import annotations

from pydantic import BaseModel

from mobaide.core.claude_launcher import ClaudeOptions
from mobaide.core.models import SessionInfo


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


class SessionResponse(BaseModel):
    session_id: str
    state: str
    created_at: str
    pid: int | None
    command: list[str]
    cwd: str | None

    @classmethod
    def from_info(cls, info: SessionInfo) -> SessionResponse:
        return cls(
            session_id=info.session_id,
            state=info.state.value,
            created_at=info.created_at.isoformat(),
            pid=info.pid,
            command=info.command,
            cwd=info.cwd,
        )


class ScrollbackResponse(BaseModel):
    session_id: str
    lines: list[str]
    total: int
