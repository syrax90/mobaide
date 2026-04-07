from __future__ import annotations

from pydantic import BaseModel


class StartScanRequest(BaseModel):
    project_name: str


class StopScanRequest(BaseModel):
    project_name: str


class AutoScanInstanceDto(BaseModel):
    project_name: str
    status: str
    started_at: float
    last_activity_at: float
    processed_count: int
    last_scan_time: float | None = None
    scan_interval_seconds: int


class AutoScanListResponse(BaseModel):
    scans: list[AutoScanInstanceDto]
    capacity: int
    max_scans: int
    available_projects: list[str]


class AutoScanInstanceStatusResponse(AutoScanInstanceDto):
    recent_events: list[dict] = []
