from __future__ import annotations

import asyncio
import contextvars
import time
import uuid
from contextlib import suppress
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

try:
    from claude_mobile.agent.logging import bind_request_context, get_logger, log_call
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
from .workitems import (
    ActiveWorkItem,
    ActiveWorkItemsStore,
    active_workitems_path,
    now_iso_utc,
)
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
        client_options: ClaudeOptions | None = None,
    ) -> None:
        self._project_name = project_name
        self._config = config
        self._session_manager = session_manager
        self._launcher = launcher
        self._client = client
        self._worktree_mgr = worktree_mgr
        self._event_bus = event_bus
        self._client_options = client_options

        # Per-project persistent record of issues whose worktrees have been
        # created by auto-scan. The scanner consults this file AFTER the
        # processing loop each cycle to detect issues that reached the
        # configured Done state and tear down their worktrees.
        #
        # It is intentionally NOT used as a dedup gate for the processing
        # loop — the source of truth for "needs processing" is the Linear
        # query filter (state=Proposals + label=AutoPipeline). The processing
        # step itself transitions state and removes the label, which
        # naturally prevents the issue from reappearing unless the user
        # explicitly resets it in Linear.
        self._workitems_store = ActiveWorkItemsStore(
            active_workitems_path(config.state_dir, project_name)
        )
        # Cache of ``len(workitems_store)`` kept in sync by every add/remove
        # call in this class. Primed lazily from disk on first read to cover
        # the case where the store was populated in a previous process.
        self._active_workitem_count: int = self._workitems_store.peek_count()
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
        """Number of currently tracked active work items for this project.

        Name kept for DTO compatibility with the Android client, which
        reads ``processed_count`` from the auto-scan status payload. The
        value is updated in-memory at every add/remove to avoid a
        synchronous disk read on the status-rendering hot path.
        """
        return self._active_workitem_count

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

        self._status = ScanStatus.SCANNING
        self._started_at = time.time()
        self._last_activity_at = self._started_at
        # Run the poll loop in a fresh contextvars.Context so it does NOT
        # inherit per-request context (request_id, method, path, client_ip)
        # from whichever HTTP handler happened to call start(). Without this,
        # every structlog entry from the background loop would be tagged
        # with the request_id of the /api/autoscan/start call that spawned
        # it, making debugging run-now cycles very confusing.
        self._poll_task = asyncio.create_task(
            self._poll_loop(),
            name=f"autoscan-poll[{self._project_name}]",
            context=contextvars.Context(),
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

        self._status = ScanStatus.STOPPED
        self._next_scan_at = None
        self._emit_phase("scan_stopped")
        logger.info("Auto Scan stopped for project '%s'", self._project_name)

    def request_immediate_scan(self) -> None:
        """Wake the polling loop to run a scan cycle immediately.

        Reanchors ``next_scan_at`` to "now" and republishes status so
        the Android client's countdown can reset synchronously without
        having to wait for the next poll cycle to emit a phase event.

        Re-processing of previously-seen issues is now driven purely by
        the Linear query filters (state=Proposals + label=AutoPipeline) —
        there is no in-memory dedup set to clear.
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

        processed_count = 0
        if issues:
            logger.info("Found %d matching issue(s) to process", len(issues))
            for issue in issues:
                await self._process_issue(issue)
                processed_count += 1
        else:
            logger.info("No matching AutoPipeline issues found")

        # Worktree cleanup runs every cycle, even when no new issues were
        # processed — items added in earlier cycles may now be Done.
        if self._config.auto_remove_worktrees:
            try:
                await self._cleanup_completed_worktrees()
            except Exception:
                logger.exception("autoscan.cleanup.failed")

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
        wt_result = None
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

        # 3b) Persist to active_workitems.json for later cleanup.
        # Only track when a worktree actually exists on disk and the
        # feature is enabled.
        if self._config.auto_remove_worktrees and wt_result is not None:
            try:
                added = await self._workitems_store.add(
                    ActiveWorkItem(
                        id=issue.identifier,
                        uuid=issue.id,
                        slug=slug,
                        worktree_path=str(wt_result.path),
                        project_root=project.path,
                        added_at=now_iso_utc(),
                    )
                )
                if added:
                    self._active_workitem_count += 1
            except Exception:
                logger.exception(
                    "autoscan.workitem.persist_failed id=%s", issue.identifier,
                )

        # 4) Session spawn
        self._emit_issue(issue, "session_spawn", "started")
        session_id = uuid.uuid4().hex[:12]
        try:
            base = self._client_options
            spec = await self._launcher.build(
                project,
                ClaudeOptions(
                    plan_mode=base.plan_mode if base else False,
                    no_memory=base.no_memory if base else False,
                    skip_permissions=base.skip_permissions if base else True,
                    initial_prompt=(
                        base.initial_prompt
                        if base and base.initial_prompt
                        else self._config.auto_scan_prompt
                    ),
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

        self._last_activity_at = time.time()

    async def _cleanup_completed_worktrees(self) -> None:
        """Remove worktrees for tracked issues that reached the Done state.

        Mirrors the PowerShell ``Remove-CompletedWorktrees`` function: for
        each item in ``active_workitems.json`` belonging to the current
        project, query Linear for the issue's current workflow state;
        if it matches ``config.linear_done_state``, remove the git
        worktree and drop the entry from the store. Items for other
        projects are preserved (the same file is shared per-project in
        practice, but the guard is kept for safety).
        """
        project = self._config.projects[self._project_name]
        project_root = project.path
        done_state_name = self._config.linear_done_state

        items = await self._workitems_store.read()
        tracked = [i for i in items if i.project_root == project_root]
        if not tracked:
            logger.info(
                "autoscan.cleanup.start project=%s tracked=0 — nothing to check",
                self._project_name,
            )
            return

        logger.info(
            "autoscan.cleanup.start project=%s tracked=%d",
            self._project_name, len(tracked),
        )

        removed = 0
        kept = 0
        for item in tracked:
            try:
                state = await self._client.get_issue_state(item.uuid)
            except Exception as exc:
                logger.warning(
                    "autoscan.cleanup.query_failed id=%s error=%s — keeping for retry",
                    item.id, exc,
                )
                kept += 1
                continue

            if state is None:
                logger.warning(
                    "autoscan.cleanup.query_failed id=%s — Linear returned no state; keeping",
                    item.id,
                )
                kept += 1
                continue

            if state.name != done_state_name:
                logger.info(
                    "autoscan.cleanup.keep id=%s state=%s",
                    item.id, state.name,
                )
                kept += 1
                continue

            logger.info(
                "autoscan.cleanup.remove id=%s state=%s worktree=%s",
                item.id, state.name, item.worktree_path,
            )
            try:
                ok = await self._worktree_mgr.cleanup_worktree(
                    item.worktree_path, project_root,
                )
            except Exception as exc:
                logger.warning(
                    "autoscan.cleanup.remove_failed id=%s error=%s — keeping for retry",
                    item.id, exc,
                )
                kept += 1
                continue
            if not ok:
                logger.warning(
                    "autoscan.cleanup.remove_failed id=%s — keeping for retry", item.id,
                )
                kept += 1
                continue
            if await self._workitems_store.remove(item.id):
                self._active_workitem_count = max(
                    0, self._active_workitem_count - 1,
                )
            removed += 1

        logger.info(
            "autoscan.cleanup.summary project=%s removed=%d remaining=%d",
            self._project_name, removed, kept,
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
            "processed_count": self.processed_count,
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
            "auto_scan_prompt": self._config.auto_scan_prompt,
        }

    async def start(
        self, project_name: str, *, client_options: ClaudeOptions | None = None,
    ) -> AutoScanInstance:
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
                client_options=client_options,
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
