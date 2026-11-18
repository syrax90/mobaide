"""Workspace delete batches as background jobs, shared by the HTTP router and
the relay dispatcher.

Removing many worktrees can take minutes (each ``git worktree remove``
deletes a whole checkout), far past the relay's 30 s proxied-request budget.
``POST /delete-jobs`` runs the busy guard inside the request, so a 409 still
refuses the batch whole, then hands the batch to a background task and
answers at once; clients poll ``GET /delete-jobs/{job_id}`` for ``completed``
and, once ``status`` is ``done``, the same ``results`` ``POST /items:delete``
returns. Finished jobs stay readable for ``changes_jobs.FINISHED_TTL``.
"""
from __future__ import annotations

import asyncio
import contextvars
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from mobaide.linear.worktree import WorktreeManager

from ..logging import get_logger
from .changes_jobs import prune_finished
from .changes_service import GitNotFoundError, GitRefusedError
from .models import (
    GitDeleteJobResponse,
    GitDeleteRequest,
    GitDeleteResultItem,
)
from .service import _publish_git_changed, check_delete_busy, run_delete_batch

logger = get_logger(__name__)

MAX_RUNNING = 8
_SHUTDOWN_TIMEOUT = 10.0


@dataclass
class DeleteJob:
    job_id: str
    request: GitDeleteRequest
    completed: int = 0
    results: list[GitDeleteResultItem] | None = None
    finished_at: float | None = None
    task: asyncio.Task | None = field(default=None, repr=False)

    @property
    def running(self) -> bool:
        return self.finished_at is None

    def snapshot(self) -> GitDeleteJobResponse:
        total = len(self.request.items)
        return GitDeleteJobResponse(
            job_id=self.job_id,
            status="running" if self.running else "done",
            total=total,
            completed=min(self.completed, total),
            results=None if self.results is None else list(self.results),
        )

    def advance(self, count: int) -> None:
        self.completed += count


class DeleteJobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, DeleteJob] = {}

    def reset(self) -> None:
        self._jobs.clear()

    async def start(
        self,
        request: GitDeleteRequest,
        *,
        workspace_name: str,
        workspace_root: str,
        manager: WorktreeManager,
        session_manager: Any | None,
        autoscan_manager: Any | None,
        event_bus: Any | None,
        actor: str | None,
    ) -> GitDeleteJobResponse:
        """Guard the batch, then start it; raises
        :class:`~.service.GitItemBusyError` like ``POST /items:delete`` and
        ``GitRefusedError("too_many_jobs")`` past ``MAX_RUNNING``."""
        await check_delete_busy(request, session_manager, autoscan_manager)
        prune_finished(self._jobs)
        if sum(1 for job in self._jobs.values() if job.running) >= MAX_RUNNING:
            raise GitRefusedError("too_many_jobs")
        job = DeleteJob(job_id=secrets.token_hex(16), request=request)
        self._jobs[job.job_id] = job
        # A fresh context keeps the request's log fields out of every line the
        # job logs after the response went out.
        job.task = asyncio.create_task(
            self._run(
                job,
                workspace_name=workspace_name,
                workspace_root=workspace_root,
                manager=manager,
                session_manager=session_manager,
                autoscan_manager=autoscan_manager,
                event_bus=event_bus,
                actor=actor,
            ),
            name=f"git-delete-job[{job.job_id[:8]}]",
            context=contextvars.Context(),
        )
        return job.snapshot()

    def get(self, job_id: str) -> GitDeleteJobResponse:
        job = self._jobs.get(job_id)
        if job is None:
            raise GitNotFoundError("job_not_found")
        return job.snapshot()

    async def shutdown(self) -> None:
        tasks = [job.task for job in self._jobs.values() if job.running and job.task]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=_SHUTDOWN_TIMEOUT)

    async def _run(self, job: DeleteJob, **kwargs: Any) -> None:
        try:
            response = await run_delete_batch(
                job.request, on_progress=job.advance, **kwargs
            )
            job.results = response.results
        except asyncio.CancelledError:
            job.results = _failed(job.request, "agent shutting down")
            raise
        except Exception as exc:
            logger.exception("git.delete_job.failed")
            job.results = _failed(job.request, str(exc) or "delete failed")
            _publish_git_changed(kwargs.get("event_bus"), kwargs["workspace_name"])
        finally:
            job.finished_at = time.monotonic()


def _failed(request: GitDeleteRequest, detail: str) -> list[GitDeleteResultItem]:
    """One ``error`` result per item for a batch that died midway: which
    items already went through is unknown, and the ``git.changed`` reload
    shows the truth."""
    return [
        GitDeleteResultItem(
            kind=item.kind,
            target=item.branch or item.worktree_path or "",
            ok=False,
            error="error",
            error_detail=detail,
        )
        for item in request.items
    ]


delete_job_registry = DeleteJobRegistry()
