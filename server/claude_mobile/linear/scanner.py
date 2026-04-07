from __future__ import annotations

import asyncio
import json
import time
import uuid
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
from .models import LinearIssue, ScanEvent, ScanStatus
from .rate_limiter import LinearRateLimiter
from .worktree import WorktreeManager, convert_to_worktree_slug

logger = get_logger(__name__)


class AutoScanAlreadyRunning(Exception):
    """Raised when start() is called for a project that already has an active scan."""


class AutoScanCapacityReached(Exception):
    """Raised when the maximum concurrent-scans cap has been hit."""


class AutoScanInstance:
    """One Auto Scan polling loop bound to a single project.

    Holds all per-scan state: Linear client, worktree manager, processed-items
    set, polling task, and event subscribers. Mirrors the old singleton
    AutoScanService but is instantiated once per active project.
    """

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
        self._max_recent_events = 100

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

    # -- Lifecycle --

    async def start(self) -> None:
        # Verify Linear connectivity
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
        self._emit_event("scan_started", {"project": self._project_name})
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
        self._emit_event("scan_stopped", None)
        logger.info("Auto Scan stopped for project '%s'", self._project_name)

    # -- Polling --

    async def _poll_loop(self) -> None:
        try:
            while True:
                try:
                    await self._scan_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.error("Scan cycle failed", exc_info=True)
                    self._status = ScanStatus.ERROR
                    self._emit_event("error", {"message": "Scan cycle failed"})
                else:
                    self._status = ScanStatus.SCANNING

                await asyncio.sleep(self._config.scan_interval_seconds)
        except asyncio.CancelledError:
            return

    async def _scan_once(self) -> None:
        self._last_scan_time = time.time()
        self._last_activity_at = self._last_scan_time
        logger.info(
            "Scanning for AutoPipeline issues (project=%s)...", self._project_name
        )

        issues = await self._client.get_auto_scan_issues()

        if not issues:
            self._emit_event("scan_completed", {"found": 0, "new": 0})
            return

        new_issues = [i for i in issues if i.id not in self._processed_items]
        if not new_issues:
            self._emit_event(
                "scan_completed", {"found": len(issues), "new": 0}
            )
            return

        logger.info("Found %d new issue(s) to process", len(new_issues))
        for issue in new_issues:
            await self._process_issue(issue)

        await self._save_processed_items()
        self._emit_event(
            "scan_completed",
            {"found": len(issues), "new": len(new_issues)},
        )

    @log_call(name="autoscan.process_issue", log_args=("issue",))
    async def _process_issue(self, issue: LinearIssue) -> None:
        bind_request_context(issue_identifier=issue.identifier)
        project = self._config.projects[self._project_name]
        logger.info("Processing: %s - %s", issue.identifier, issue.title)
        self._emit_event(
            "issue_found",
            {"identifier": issue.identifier, "title": issue.title},
        )

        target_state_name = self._config.linear_active_state
        target_state = next(
            (s for s in issue.team_states if s.name == target_state_name), None
        )
        if target_state is None:
            logger.warning(
                "Could not find state '%s' in team workflow — skipping state update",
                target_state_name,
            )
        else:
            success = await self._client.update_issue_state(
                issue.id, target_state.id
            )
            if not success:
                logger.error("Failed to update state for %s", issue.identifier)

        auto_label = next(
            (l for l in issue.labels if l.name == "AutoPipeline"), None
        )
        if auto_label:
            await self._client.remove_issue_label(issue.id, auto_label.id)

        slug = convert_to_worktree_slug(issue.identifier, issue.title)
        wt_result = await self._worktree_mgr.create_worktree(slug, project.path)
        target_dir = wt_result.path if wt_result else project.path

        spec = await self._launcher.build(
            project,
            ClaudeOptions(
                skip_permissions=True,
                initial_prompt=self._config.auto_scan_prompt,
                identifier=issue.identifier,
                title=issue.title,
            ),
        )
        session_id = uuid.uuid4().hex[:12]
        config = SessionConfig(
            session_id=session_id,
            command=spec.argv,
            cwd=target_dir,
            env=spec.env,
        )
        try:
            await self._session_manager.create_session(config)
            logger.info(
                "Session %s started for %s", session_id, issue.identifier
            )
        except Exception:
            logger.error(
                "Failed to start session for %s", issue.identifier, exc_info=True
            )

        self._processed_items.add(issue.id)
        self._last_activity_at = time.time()
        self._emit_event(
            "issue_processed",
            {
                "identifier": issue.identifier,
                "title": issue.title,
                "session_id": session_id,
                "worktree": target_dir,
            },
        )

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

    def _emit_event(self, event_type: str, data: dict | None) -> None:
        event = ScanEvent(event_type=event_type, data=data)
        self._recent_events.append(event)
        if len(self._recent_events) > self._max_recent_events:
            self._recent_events = self._recent_events[-self._max_recent_events:]

        for q in list(self._event_subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

        if self._event_bus is not None:
            self._event_bus.publish(
                "autoscan.event",
                {
                    "project_name": self._project_name,
                    "event_type": event.event_type,
                    "timestamp": event.timestamp,
                    "data": event.data,
                },
            )
            self._event_bus.publish(
                "autoscan.status", self.to_dto()
            )

    def subscribe_events(self) -> asyncio.Queue[ScanEvent]:
        q: asyncio.Queue[ScanEvent] = asyncio.Queue(maxsize=200)
        self._event_subscribers.add(q)
        return q

    def unsubscribe_events(self, q: asyncio.Queue[ScanEvent]) -> None:
        self._event_subscribers.discard(q)

    # -- DTO --

    def to_dto(self) -> dict:
        """Serialize this instance to the per-project status dict used by
        REST/WebSocket consumers."""
        return {
            "project_name": self._project_name,
            "status": self._status.value,
            "started_at": self._started_at,
            "last_activity_at": self._last_activity_at,
            "processed_count": len(self._processed_items),
            "last_scan_time": self._last_scan_time,
            "scan_interval_seconds": self._config.scan_interval_seconds,
        }

    def get_status(self) -> dict:
        """Full status payload (DTO + recent events) for the per-project
        status endpoint."""
        dto = self.to_dto()
        dto["recent_events"] = [
            {
                "event_type": e.event_type,
                "timestamp": e.timestamp,
                "data": e.data,
            }
            for e in self._recent_events[-20:]
        ]
        return dto


class AutoScanSessionManager:
    """Manages multiple concurrent AutoScanInstance objects.

    Mirrors the dict-keyed pattern of SessionManager: ``_scans`` maps
    project name → AutoScanInstance, protected by ``_lock``.
    """

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
