from __future__ import annotations

import asyncio
import contextvars
import time
import uuid
from contextlib import suppress
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone

try:
    from mobaide.agent.logging import bind_request_context, get_logger, log_call
except Exception:  # pragma: no cover
    import logging
    def get_logger(name): return logging.getLogger(name)
    def log_call(*args, **kwargs):
        def deco(fn): return fn
        return deco
    def bind_request_context(**kwargs): pass

from mobaide.core import (
    ClaudeBinaryNotFoundError,
    ClaudeLauncher,
    ClaudeOptions,
    ProjectConfig,
    ProjectsStore,
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
from .worktree import WorktreeCreationError, WorktreeManager, convert_to_worktree_slug

logger = get_logger(__name__)


class AutoScanAlreadyRunning(Exception):
    """Raised when start() is called for a project that already has an active scan."""


class AutoScanCapacityReached(Exception):
    """Raised when the maximum concurrent-scans cap has been hit."""


class AutoScanInstance:
    """One Auto Scan polling loop bound to a single project.

    The instance captures a :class:`ProjectConfig` snapshot at construction
    time — mid-scan mutations of the project (path change, key rotation,
    flags edit) do NOT affect the running loop. To pick up changes, the
    operator must ``stop`` and ``start`` the scan. This gives predictable
    behaviour and keeps the cycle loop from racing the projects store.
    """

    def __init__(
        self,
        *,
        project_name: str,
        project_config: ProjectConfig,
        config: AutoScanConfig,
        session_manager: SessionManager,
        launcher: ClaudeLauncher,
        client: LinearApiClient,
        worktree_mgr: WorktreeManager,
        event_bus=None,
        client_options: ClaudeOptions | None = None,
    ) -> None:
        if client_options is not None and not isinstance(
            client_options, ClaudeOptions
        ):
            raise TypeError(  # type: ignore[unreachable]
                "client_options must be a ClaudeOptions instance or None, "
                f"got {type(client_options).__name__}"
            )
        self._project_name = project_name
        self._project_config = project_config
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

    async def list_workitems(self) -> list[ActiveWorkItem]:
        """Return the persisted active work items for this scan instance.

        The git management screen consults this list (across all running
        scans) to refuse deletes of worktrees that auto-scan still owns.
        """
        return await self._workitems_store.read()

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
                try:
                    await self._process_issue(issue)
                except ClaudeBinaryNotFoundError:
                    logger.error(
                        "autoscan.aborting_cycle reason=claude_cli_missing project=%s",
                        self._project_name,
                    )
                    self._emit(
                        "log",
                        {
                            "line": (
                                "Aborting scan cycle: Claude CLI not found "
                                "on PATH (set MOBAIDE_CLAUDE_BIN or install "
                                "claude-code)."
                            ),
                            "level": "error",
                            "ansi": False,
                        },
                    )
                    break
                processed_count += 1
        else:
            logger.info("No matching AutoPipeline issues found")

        # Run every cycle — items added in prior cycles may have reached Done.
        if self._config.auto_remove_worktrees:
            try:
                await self._cleanup_completed_worktrees()
            except Exception:
                logger.exception("autoscan.cleanup.failed")

        self._emit_phase("scan_completed", issues_processed=processed_count)

    @log_call(name="autoscan.process_issue", log_args=("issue",))
    async def _process_issue(self, issue: LinearIssue) -> None:
        bind_request_context(issue_identifier=issue.identifier)
        project = self._project_config
        logger.info("Processing: %s - %s", issue.identifier, issue.title)

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

        slug = convert_to_worktree_slug(issue.identifier, issue.title)
        base = self._client_options
        opts = replace(
            base if base is not None else ClaudeOptions(),
            identifier=issue.identifier,
            title=issue.title,
            initial_prompt=(
                base.initial_prompt
                if base is not None and base.initial_prompt
                else self._config.auto_scan_prompt
            ),
            worktree=True,
        )

        self._emit_issue(issue, "worktree_create", "started")
        try:
            spec = await self._launcher.build(project, opts)
        except WorktreeCreationError as exc:
            self._emit_issue(issue, "worktree_create", "failed", error=str(exc))
            logger.error(
                "autoscan.worktree_create_failed id=%s: %s",
                issue.identifier, exc.stderr,
            )
            self._last_activity_at = time.time()
            return
        except ClaudeBinaryNotFoundError:
            # ``launcher.build`` resolves the binary BEFORE creating the
            # worktree, so no orphan directory is on disk by the time we
            # land here. Emit a terminal ``worktree_create:failed`` so the
            # event stream's state machine has no dangling ``started``,
            # then ``session_spawn:failed`` for consumers that key off
            # that step, and re-raise so the outer ``_scan_once`` aborts.
            self._emit_issue(
                issue,
                "worktree_create",
                "failed",
                error="Claude CLI not found",
            )
            self._emit_issue(
                issue,
                "session_spawn",
                "failed",
                error="Claude CLI not found",
            )
            logger.error(
                "autoscan.claude_cli_missing project=%s", self._project_name,
            )
            self._last_activity_at = time.time()
            raise
        except Exception as exc:
            self._emit_issue(issue, "worktree_create", "failed", error=str(exc))
            logger.error(
                "autoscan.launcher_build_failed id=%s", issue.identifier,
                exc_info=True,
            )
            self._last_activity_at = time.time()
            return
        self._emit_issue(issue, "worktree_create", "ok")
        self._emit("worktree", asdict(ScanWorktreePayload(
            identifier=issue.identifier, path=str(spec.cwd),
        )))

        if self._config.auto_remove_worktrees and spec.cwd != project.path:
            try:
                added = await self._workitems_store.add(
                    ActiveWorkItem(
                        id=issue.identifier,
                        uuid=issue.id,
                        slug=slug,
                        worktree_path=str(spec.cwd),
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

        self._emit_issue(issue, "session_spawn", "started")
        session_id = uuid.uuid4().hex[:12]
        try:
            config = SessionConfig(
                session_id=session_id,
                command=spec.argv,
                cwd=spec.cwd,
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
        project = self._project_config
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
    """Manages multiple concurrent AutoScanInstance objects.

    The manager reads the list of scannable projects live from the injected
    :class:`ProjectsStore` (filtered to those with a ``linear_api_key``), so
    projects added via the REST CRUD API are picked up without a restart.
    """

    def __init__(
        self,
        config: AutoScanConfig,
        session_manager: SessionManager,
        projects_store: ProjectsStore,
        launcher: ClaudeLauncher | None = None,
        event_bus=None,
        rate_limiter: LinearRateLimiter | None = None,
        max_scans: int = 5,
    ) -> None:
        self._config = config
        self._session_manager = session_manager
        self._projects_store = projects_store
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
        return self._projects_store.list_with_linear_key()

    def get(self, project_name: str) -> AutoScanInstance | None:
        return self._scans.get(project_name)

    def all_instances(self) -> list[AutoScanInstance]:
        return list(self._scans.values())

    async def list_all_workitems(self) -> dict[str, list[ActiveWorkItem]]:
        """Return ``{project_name: [work_items]}`` across every running scan."""
        result: dict[str, list[ActiveWorkItem]] = {}
        for inst in self._scans.values():
            result[inst.project_name] = await inst.list_workitems()
        return result

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
        project = self._projects_store.get(project_name)
        if project is None:
            raise ValueError(
                f"Project '{project_name}' not found in store. "
                f"Available: {self.available_projects}",
            )
        if not project.linear_api_key:
            raise ValueError(
                f"Project '{project_name}' has no Linear API key configured.",
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
                project_config=project,
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
