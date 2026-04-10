from __future__ import annotations

from pydantic import BaseModel

from ..sessions.models import ClaudeOptionsModel


class StartScanRequest(BaseModel):
    project_name: str
    claude_options: ClaudeOptionsModel | None = None


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
