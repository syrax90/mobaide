from __future__ import annotations

from pydantic import BaseModel

from claude_mobile.core.models import SessionInfo


class CreateSessionRequest(BaseModel):
    command: list[str] | None = None
    cwd: str | None = None
    env: dict[str, str] | None = None
    rows: int = 24
    cols: int = 80


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
