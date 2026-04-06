from __future__ import annotations

from pydantic import BaseModel


class StartScanRequest(BaseModel):
    project_name: str


class ScanStatusResponse(BaseModel):
    status: str
    active_project: str | None
    processed_count: int
    last_scan_time: float | None
    scan_interval_seconds: int
    available_projects: list[str]
    recent_events: list[dict]
