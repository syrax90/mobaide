from __future__ import annotations

import asyncio
import json
import platform
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
from .worktree import WorktreeManager, convert_to_worktree_slug

logger = get_logger(__name__)


class AutoScanService:
    """Polls Linear for AutoPipeline issues and launches Claude sessions.

    Reimplements PS1 Start-AutoScanMode (lines 523-640).
    """

    def __init__(
        self,
        config: AutoScanConfig,
        session_manager: SessionManager,
        launcher: ClaudeLauncher | None = None,
        event_bus=None,
    ) -> None:
        self._config = config
        self._session_manager = session_manager
        self._launcher = launcher or ClaudeLauncher()
        self._event_bus = event_bus
        self._processed_items: set[str] = set()
        self._status = ScanStatus.IDLE
        self._poll_task: asyncio.Task[None] | None = None
        self._last_scan_time: float | None = None
        self._active_project: str | None = None
        self._client: LinearApiClient | None = None
        self._worktree_mgr: WorktreeManager | None = None
        self._event_subscribers: set[asyncio.Queue[ScanEvent]] = set()
        self._recent_events: list[ScanEvent] = []
        self._max_recent_events = 100

    # -- Lifecycle --

    async def start(self, project_name: str) -> None:
        """Start the auto-scan polling loop for the given project."""
        if self._status == ScanStatus.SCANNING:
            raise RuntimeError("Auto scan is already running")

        if project_name not in self._config.projects:
            raise ValueError(
                f"Project '{project_name}' not found in config. "
                f"Available: {list(self._config.projects.keys())}"
            )

        project = self._config.projects[project_name]
        self._active_project = project_name
        self._client = LinearApiClient(api_key=project.linear_api_key)

        # Verify Linear connectivity
        viewer = await self._client.get_viewer()
        logger.info("Linear authenticated as: %s", viewer.get("name", "unknown"))

        # Set up worktree manager
        prefix = self._config.branch_prefix
        if not prefix:
            prefix = f"feature/claude"
        self._worktree_mgr = WorktreeManager(
            worktree_base=self._config.worktree_base,
            branch_prefix=prefix,
            main_branch=self._config.main_branch,
        )

        # Load persisted processed items
        self._load_processed_items()

        self._status = ScanStatus.SCANNING
        self._emit_event("scan_started", {"project": project_name})
        self._poll_task = asyncio.create_task(
            self._poll_loop(), name="autoscan-poll"
        )
        logger.info(
            "Auto Scan started for project '%s'. Interval: %ds",
            project_name,
            self._config.scan_interval_seconds,
        )

    async def stop(self) -> None:
        """Stop the auto-scan polling loop."""
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

        self._save_processed_items()
        self._status = ScanStatus.STOPPED
        self._emit_event("scan_stopped", None)
        logger.info("Auto Scan stopped")

    # -- Polling --

    async def _poll_loop(self) -> None:
        """Poll Linear at the configured interval."""
        try:
            while True:
                try:
                    await self._scan_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.error("Scan cycle failed", exc_info=True)
                    self._emit_event("error", {"message": "Scan cycle failed"})

                await asyncio.sleep(self._config.scan_interval_seconds)
        except asyncio.CancelledError:
            return

    async def _scan_once(self) -> None:
        """Execute a single scan cycle."""
        self._last_scan_time = time.time()
        logger.info("Scanning for AutoPipeline issues in Linear...")

        assert self._client is not None
        issues = await self._client.get_auto_scan_issues()

        if not issues:
            logger.debug("No matching issues found")
            self._emit_event("scan_completed", {"found": 0, "new": 0})
            return

        new_issues = [i for i in issues if i.id not in self._processed_items]
        if not new_issues:
            logger.debug("All matching issues already processed")
            self._emit_event(
                "scan_completed", {"found": len(issues), "new": 0}
            )
            return

        logger.info("Found %d new issue(s) to process", len(new_issues))
        for issue in new_issues:
            await self._process_issue(issue)

        self._save_processed_items()
        self._emit_event(
            "scan_completed",
            {"found": len(issues), "new": len(new_issues)},
        )

    @log_call(name="autoscan.process_issue", log_args=("issue",))
    async def _process_issue(self, issue: LinearIssue) -> None:
        """Process a single issue: transition state, remove label, create worktree, launch session."""
        assert self._client is not None
        assert self._worktree_mgr is not None
        assert self._active_project is not None

        bind_request_context(issue_identifier=issue.identifier)
        project = self._config.projects[self._active_project]
        logger.info("Processing: %s - %s", issue.identifier, issue.title)
        self._emit_event(
            "issue_found",
            {"identifier": issue.identifier, "title": issue.title},
        )

        # Find target state ID from team workflow states
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
            logger.info(
                "Updating state: %s -> %s", issue.state.name, target_state_name
            )
            success = await self._client.update_issue_state(
                issue.id, target_state.id
            )
            if success:
                logger.info("State updated to '%s'", target_state_name)
            else:
                logger.error("Failed to update state for %s", issue.identifier)

        # Remove AutoPipeline label
        auto_label = next(
            (l for l in issue.labels if l.name == "AutoPipeline"), None
        )
        if auto_label:
            logger.info("Removing 'AutoPipeline' label...")
            await self._client.remove_issue_label(issue.id, auto_label.id)
            logger.info("Label removed")

        # Create worktree
        slug = convert_to_worktree_slug(issue.identifier, issue.title)
        logger.info("Creating worktree: %s", slug)
        wt_result = await self._worktree_mgr.create_worktree(slug, project.path)
        target_dir = wt_result.path if wt_result else project.path

        # Launch Claude session via the shared launcher (single source of
        # truth — same argv/env mobile sessions get).
        logger.info("Launching Claude in: %s", target_dir)
        spec = await self._launcher.build(
            project,
            ClaudeOptions(
                skip_permissions=True,
                initial_prompt=self._config.auto_scan_prompt,
                identifier=issue.identifier,
                title=issue.title,
            ),
        )
        # Worktree was already created above for this flow; override cwd.
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
        self._emit_event(
            "issue_processed",
            {
                "identifier": issue.identifier,
                "title": issue.title,
                "session_id": session_id,
                "worktree": target_dir,
            },
        )
        logger.info(
            "Done. Processed items total: %d", len(self._processed_items)
        )

    # -- Processed items persistence --

    def _load_processed_items(self) -> None:
        path = Path(self._config.processed_items_path)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._processed_items = set(data)
                logger.info(
                    "Loaded %d processed items from %s",
                    len(self._processed_items),
                    path,
                )
            except (json.JSONDecodeError, TypeError):
                logger.warning("Could not parse %s — starting fresh", path)
                self._processed_items = set()
        else:
            self._processed_items = set()

    def _save_processed_items(self) -> None:
        path = Path(self._config.processed_items_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(sorted(self._processed_items), indent=2),
            encoding="utf-8",
        )
        logger.debug("Saved %d processed items to %s", len(self._processed_items), path)

    # -- Event pub-sub --

    def _emit_event(self, event_type: str, data: dict | None) -> None:
        event = ScanEvent(event_type=event_type, data=data)
        self._recent_events.append(event)
        if len(self._recent_events) > self._max_recent_events:
            self._recent_events = self._recent_events[-self._max_recent_events :]

        for q in list(self._event_subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # drop for slow consumers

        # Publish to the unified server event bus.
        if self._event_bus is not None:
            self._event_bus.publish(
                "autoscan.event",
                {
                    "event_type": event.event_type,
                    "timestamp": event.timestamp,
                    "data": event.data,
                },
            )
            status = self.get_status()
            status["available_projects"] = list(self._config.projects.keys())
            self._event_bus.publish("autoscan.status", status)

    def subscribe_events(self) -> asyncio.Queue[ScanEvent]:
        """Subscribe to scan events (for WebSocket relay)."""
        q: asyncio.Queue[ScanEvent] = asyncio.Queue(maxsize=200)
        self._event_subscribers.add(q)
        return q

    def unsubscribe_events(self, q: asyncio.Queue[ScanEvent]) -> None:
        """Unsubscribe from scan events."""
        self._event_subscribers.discard(q)

    # -- Status --

    def get_status(self) -> dict:
        """Return current scan status for REST/WebSocket consumers."""
        return {
            "status": self._status.value,
            "active_project": self._active_project,
            "processed_count": len(self._processed_items),
            "last_scan_time": self._last_scan_time,
            "scan_interval_seconds": self._config.scan_interval_seconds,
            "recent_events": [
                {
                    "event_type": e.event_type,
                    "timestamp": e.timestamp,
                    "data": e.data,
                }
                for e in self._recent_events[-20:]
            ],
        }
