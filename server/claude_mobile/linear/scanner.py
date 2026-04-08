from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import suppress
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from claude_mobile.server.logging import bind_request_context, get_logger, log_call
except Exception:  # pragma: no cover
    import logging
    def get_logger(name): return logging.getLogger(name)
    def log_call(*args, **kwargs):
        def deco(fn): return fn
        return deco
    def bind_request_context(**kwargs): pass

from claude_mobile.core import (
    ClaudeLauncher,
    ClaudeOptions,
    SessionConfig,
    SessionManager,
)

from .client import LinearApiClient
from .config import AutoScanConfig
from .models import (
    LinearIssue,
    ScanEvent,
    ScanIssuePayload,
    ScanPhasePayload,
    ScanSessionPayload,
    ScanStatus,
    ScanWorktreePayload,
    now_iso,
)
from .rate_limiter import LinearRateLimiter
from .worktree import WorktreeManager, convert_to_worktree_slug

logger = get_logger(__name__)


class AutoScanAlreadyRunning(Exception):
    """Raised when start() is called for a project that already has an active scan."""


class AutoScanCapacityReached(Exception):
    """Raised when the maximum concurrent-scans cap has been hit."""


class AutoScanInstance:
    """One Auto Scan polling loop bound to a single project."""

    def __init__(
        self,
        *,
        project_name: str,
        config: AutoScanConfig,
        session_manager: SessionManager,
        launcher: ClaudeLauncher,
        client: LinearApiClient,
        worktree_mgr: WorktreeManager,
        event_bus=None,
    ) -> None:
        self._project_name = project_name
        self._config = config
        self._session_manager = session_manager
        self._launcher = launcher
        self._client = client
        self._worktree_mgr = worktree_mgr
        self._event_bus = event_bus

        self._processed_items: set[str] = set()
        self._processed_lock = asyncio.Lock()
        self._status = ScanStatus.IDLE
        self._poll_task: asyncio.Task[None] | None = None
        self._last_scan_time: float | None = None
        self._started_at: float = time.time()
        self._last_activity_at: float = self._started_at
        self._event_subscribers: set[asyncio.Queue[ScanEvent]] = set()
        self._recent_events: list[ScanEvent] = []
        self._max_recent_events = 500

        self._seq: int = 0
        self._cycle_number: int = 0
        self._next_scan_at: datetime | None = None
        self._run_now_event: asyncio.Event = asyncio.Event()

    # -- Properties --

    @property
    def project_name(self) -> str:
        return self._project_name

    @property
    def status(self) -> ScanStatus:
        return self._status

    @property
    def processed_count(self) -> int:
        return len(self._processed_items)

    @property
    def current_seq(self) -> int:
        return self._seq

    @property
    def recent_events(self) -> list[ScanEvent]:
        return list(self._recent_events)

    # -- Lifecycle --

    async def start(self) -> None:
        viewer = await self._client.get_viewer()
        logger.info(
            "Linear authenticated as %s for project %s",
            viewer.get("name", "unknown"),
            self._project_name,
        )

        await self._load_processed_items()

        self._status = ScanStatus.SCANNING
        self._started_at = time.time()
        self._last_activity_at = self._started_at
        self._poll_task = asyncio.create_task(
            self._poll_loop(), name=f"autoscan-poll[{self._project_name}]"
        )
        logger.info(
            "Auto Scan started for project '%s'. Interval: %ds",
            self._project_name,
            self._config.scan_interval_seconds,
        )

    async def stop(self) -> None:
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

        await self._save_processed_items()
        self._status = ScanStatus.STOPPED
        self._next_scan_at = None
        self._emit_phase("scan_stopped")
        logger.info("Auto Scan stopped for project '%s'", self._project_name)

    def request_immediate_scan(self) -> None:
        """Wake the polling loop to run a scan cycle immediately.

        Also reanchors ``next_scan_at`` to "now" and republishes status so
        the Android client's countdown can reset synchronously without
        having to wait for the next poll cycle to emit a phase event.
        """
        self._run_now_event.set()
        self._next_scan_at = datetime.now(timezone.utc)
        self._publish_status()

    # -- Polling --

    async def _poll_loop(self) -> None:
        try:
            while True:
                self._cycle_number += 1
                try:
                    await self._scan_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error("Scan cycle failed", exc_info=True)
                    self._status = ScanStatus.ERROR
                    self._emit(
                        "log",
                        {"line": f"Scan cycle failed: {exc}", "level": "error", "ansi": False},
                    )
                else:
                    self._status = ScanStatus.SCANNING

                interval = self._config.scan_interval_seconds
                self._next_scan_at = datetime.now(timezone.utc) + timedelta(seconds=interval)
                self._publish_status()

                try:
                    await asyncio.wait_for(self._run_now_event.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    pass
                finally:
                    self._run_now_event.clear()
        except asyncio.CancelledError:
            return

    async def _scan_once(self) -> None:
        self._last_scan_time = time.time()
        self._last_activity_at = self._last_scan_time
        logger.info(
            "Scanning for AutoPipeline issues (project=%s)...", self._project_name
        )
        self._emit_phase("scan_started")

        issues = await self._client.get_auto_scan_issues()
        new_issues = [i for i in issues if i.id not in self._processed_items]

        processed_count = 0
        if new_issues:
            logger.info("Found %d new issue(s) to process", len(new_issues))
            for issue in new_issues:
                await self._process_issue(issue)
                processed_count += 1
            await self._save_processed_items()

        self._emit_phase("scan_completed", issues_processed=processed_count)

    @log_call(name="autoscan.process_issue", log_args=("issue",))
    async def _process_issue(self, issue: LinearIssue) -> None:
        bind_request_context(issue_identifier=issue.identifier)
        project = self._config.projects[self._project_name]
        logger.info("Processing: %s - %s", issue.identifier, issue.title)

        # 1) State update
        target_state_name = self._config.linear_active_state
        target_state = next(
            (s for s in issue.team_states if s.name == target_state_name), None
        )
        self._emit_issue(issue, "state_update", "started")
        if target_state is None:
            self._emit_issue(
                issue,
                "state_update",
                "failed",
                error=f"workflow state '{target_state_name}' not found",
            )
        else:
            try:
                ok = await self._client.update_issue_state(issue.id, target_state.id)
                self._emit_issue(
                    issue, "state_update", "ok" if ok else "failed",
                    error=None if ok else "Linear API returned failure",
                )
            except Exception as exc:
                self._emit_issue(issue, "state_update", "failed", error=str(exc))

        # 2) Label removal
        auto_label = next(
            (l for l in issue.labels if l.name == "AutoPipeline"), None
        )
        self._emit_issue(issue, "label_removal", "started")
        if auto_label is None:
            self._emit_issue(
                issue, "label_removal", "ok", error="AutoPipeline label absent"
            )
        else:
            try:
                await self._client.remove_issue_label(issue.id, auto_label.id)
                self._emit_issue(issue, "label_removal", "ok")
            except Exception as exc:
                self._emit_issue(issue, "label_removal", "failed", error=str(exc))

        # 3) Worktree create
        slug = convert_to_worktree_slug(issue.identifier, issue.title)
        self._emit_issue(issue, "worktree_create", "started")
        try:
            wt_result = await self._worktree_mgr.create_worktree(slug, project.path)
            target_dir = wt_result.path if wt_result else project.path
            self._emit_issue(issue, "worktree_create", "ok")
            self._emit("worktree", asdict(ScanWorktreePayload(
                identifier=issue.identifier, path=str(target_dir),
            )))
        except Exception as exc:
            self._emit_issue(issue, "worktree_create", "failed", error=str(exc))
            target_dir = project.path

        # 4) Session spawn
        self._emit_issue(issue, "session_spawn", "started")
        session_id = uuid.uuid4().hex[:12]
        try:
            spec = await self._launcher.build(
                project,
                ClaudeOptions(
                    skip_permissions=True,
                    initial_prompt=self._config.auto_scan_prompt,
                    identifier=issue.identifier,
                    title=issue.title,
                ),
            )
            config = SessionConfig(
                session_id=session_id,
                command=spec.argv,
                cwd=target_dir,
                env=spec.env,
            )
            session = await self._session_manager.create_session(config)
            pid = getattr(session, "pid", None)
            self._emit_issue(issue, "session_spawn", "ok")
            self._emit("session", asdict(ScanSessionPayload(
                identifier=issue.identifier, session_id=session_id, pid=pid,
            )))
            logger.info("Session %s started for %s", session_id, issue.identifier)
        except Exception as exc:
            self._emit_issue(issue, "session_spawn", "failed", error=str(exc))
            logger.error(
                "Failed to start session for %s", issue.identifier, exc_info=True
            )

        self._processed_items.add(issue.id)
        self._last_activity_at = time.time()

    # -- Processed items persistence --

    def _processed_items_path(self) -> Path:
        return self._config.processed_items_path_for(self._project_name)

    async def _load_processed_items(self) -> None:
        path = self._processed_items_path()
        async with self._processed_lock:
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    self._processed_items = set(data)
                    logger.info(
                        "Loaded %d processed items for %s from %s",
                        len(self._processed_items),
                        self._project_name,
                        path,
                    )
                except (json.JSONDecodeError, TypeError):
                    logger.warning(
                        "Could not parse %s — starting fresh", path
                    )
                    self._processed_items = set()
            else:
                self._processed_items = set()

    async def _save_processed_items(self) -> None:
        path = self._processed_items_path()
        async with self._processed_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(sorted(self._processed_items), indent=2),
                encoding="utf-8",
            )

    # -- Event pub-sub --

    def _emit(self, type_: str, payload: dict) -> ScanEvent:
        self._seq += 1
        evt = ScanEvent(
            seq=self._seq,
            ts=now_iso(),
            type=type_,
            payload=payload,
        )
        self._recent_events.append(evt)
        if len(self._recent_events) > self._max_recent_events:
            self._recent_events = self._recent_events[-self._max_recent_events:]

        for q in list(self._event_subscribers):
            with suppress(asyncio.QueueFull):
                q.put_nowait(evt)

        if self._event_bus is not None:
            self._event_bus.publish(
                "autoscan.event",
                {
                    "project_name": self._project_name,
                    "event": evt.to_dict(),
                },
            )
            self._event_bus.publish("autoscan.status", self.to_dto())
        return evt

    def _emit_phase(self, phase: str, *, issues_processed: int = 0) -> None:
        next_at = self._next_scan_at.isoformat() if self._next_scan_at else None
        self._emit(
            "phase",
            asdict(
                ScanPhasePayload(
                    phase=phase,
                    cycle_number=self._cycle_number,
                    next_scan_at=next_at,
                    issues_processed=issues_processed,
                )
            ),
        )

    def _emit_issue(
        self,
        issue: LinearIssue,
        step: str,
        status: str,
        error: str | None = None,
    ) -> None:
        self._emit(
            "issue",
            asdict(
                ScanIssuePayload(
                    identifier=issue.identifier,
                    title=issue.title,
                    step=step,
                    status=status,
                    error=error,
                )
            ),
        )

    def _publish_status(self) -> None:
        if self._event_bus is not None:
            self._event_bus.publish("autoscan.status", self.to_dto())

    def subscribe_events(self) -> asyncio.Queue[ScanEvent]:
        q: asyncio.Queue[ScanEvent] = asyncio.Queue(maxsize=200)
        self._event_subscribers.add(q)
        return q

    def unsubscribe_events(self, q: asyncio.Queue[ScanEvent]) -> None:
        self._event_subscribers.discard(q)

    # -- DTO --

    def to_dto(self) -> dict:
        return {
            "project_name": self._project_name,
            "status": self._status.value,
            "started_at": self._started_at,
            "last_activity_at": self._last_activity_at,
            "processed_count": len(self._processed_items),
            "last_scan_time": self._last_scan_time,
            "scan_interval_seconds": self._config.scan_interval_seconds,
            "next_scan_at": self._next_scan_at.isoformat() if self._next_scan_at else None,
            "cycle_number": self._cycle_number,
            "current_seq": self._seq,
        }

    def get_status(self) -> dict:
        dto = self.to_dto()
        dto["recent_events"] = [e.to_dict() for e in self._recent_events[-50:]]
        return dto


class AutoScanSessionManager:
    """Manages multiple concurrent AutoScanInstance objects."""

    def __init__(
        self,
        config: AutoScanConfig,
        session_manager: SessionManager,
        launcher: ClaudeLauncher | None = None,
        event_bus=None,
        rate_limiter: LinearRateLimiter | None = None,
        max_scans: int = 5,
    ) -> None:
        self._config = config
        self._session_manager = session_manager
        self._launcher = launcher or ClaudeLauncher()
        self._event_bus = event_bus
        self._rate_limiter = rate_limiter or LinearRateLimiter()
        self._max_scans = max_scans
        self._scans: dict[str, AutoScanInstance] = {}
        self._lock = asyncio.Lock()

    @property
    def max_scans(self) -> int:
        return self._max_scans

    @property
    def available_projects(self) -> list[str]:
        return list(self._config.projects.keys())

    def get(self, project_name: str) -> AutoScanInstance | None:
        return self._scans.get(project_name)

    def all_instances(self) -> list[AutoScanInstance]:
        return list(self._scans.values())

    def list(self) -> list[dict]:
        return [inst.to_dto() for inst in self._scans.values()]

    def list_response(self) -> dict:
        return {
            "scans": self.list(),
            "capacity": len(self._scans),
            "max_scans": self._max_scans,
            "available_projects": self.available_projects,
        }

    async def start(self, project_name: str) -> AutoScanInstance:
        if project_name not in self._config.projects:
            raise ValueError(
                f"Project '{project_name}' not found in config. "
                f"Available: {list(self._config.projects.keys())}"
            )

        async with self._lock:
            if project_name in self._scans:
                raise AutoScanAlreadyRunning(
                    f"Auto scan for project '{project_name}' is already running"
                )
            if len(self._scans) >= self._max_scans:
                raise AutoScanCapacityReached(
                    f"Maximum {self._max_scans} concurrent auto scans reached"
                )

            project = self._config.projects[project_name]
            assert project.linear_api_key is not None
            client = LinearApiClient(
                api_key=project.linear_api_key,
                rate_limiter=self._rate_limiter,
            )
            prefix = self._config.branch_prefix or "feature/claude"
            worktree_mgr = WorktreeManager(
                worktree_base=self._config.worktree_base,
                branch_prefix=prefix,
                main_branch=self._config.main_branch,
            )
            instance = AutoScanInstance(
                project_name=project_name,
                config=self._config,
                session_manager=self._session_manager,
                launcher=self._launcher,
                client=client,
                worktree_mgr=worktree_mgr,
                event_bus=self._event_bus,
            )
            await instance.start()
            self._scans[project_name] = instance

        self._publish_list_changed()
        return instance

    async def stop(self, project_name: str) -> None:
        async with self._lock:
            instance = self._scans.pop(project_name, None)
        if instance is None:
            raise KeyError(f"No active auto scan for project '{project_name}'")
        await instance.stop()
        self._publish_list_changed()

    async def shutdown(self) -> None:
        async with self._lock:
            instances = list(self._scans.values())
            self._scans.clear()
        if instances:
            await asyncio.gather(
                *(i.stop() for i in instances), return_exceptions=True
            )
        self._publish_list_changed()

    def _publish_list_changed(self) -> None:
        if self._event_bus is not None:
            self._event_bus.publish(
                "autoscan.list.changed", self.list_response()
            )
