"""The workspace delete-job registry: progress while a batch runs, the
running-job cap, and the all-items failure a crashed batch reports. The
end-to-end routes live in ``test_git_router.py``."""
from __future__ import annotations

import asyncio

import pytest

from mobaide.agent.git import delete_jobs
from mobaide.agent.git.changes_service import GitRefusedError
from mobaide.agent.git.delete_jobs import MAX_RUNNING, delete_job_registry
from mobaide.agent.git.models import GitDeleteItem, GitDeleteRequest


class _GatedManager:
    """``WorktreeManager`` stand-in whose branch deletes wait for ``gate``
    once ``blocked`` names them."""

    def __init__(self, blocked: set[str] | None = None) -> None:
        self.gate = asyncio.Event()
        self.blocked = blocked or set()
        self.deleted: list[str] = []

    async def delete_branch(self, workspace_root: str, name: str, *, force: bool = False) -> None:
        if name in self.blocked:
            await self.gate.wait()
        self.deleted.append(name)

    async def remove_worktree(self, workspace_root: str, path: str, *, force: bool = False) -> None:
        raise AssertionError("no worktree in these batches")


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    def publish(self, topic: str, payload: dict) -> None:
        self.published.append((topic, payload))


@pytest.fixture(autouse=True)
async def _fresh_registry():
    delete_job_registry.reset()
    yield
    await delete_job_registry.shutdown()
    delete_job_registry.reset()


def _request(*branches: str) -> GitDeleteRequest:
    return GitDeleteRequest(items=[GitDeleteItem(kind="branch_only", branch=b) for b in branches])


async def _start(request: GitDeleteRequest, manager, tmp_path, bus=None):
    return await delete_job_registry.start(
        request,
        workspace_name="alpha",
        workspace_root=str(tmp_path),
        manager=manager,
        session_manager=None,
        autoscan_manager=None,
        event_bus=bus,
        actor="tester",
    )


async def _wait_for(predicate) -> None:
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached in time")


async def test_snapshot_counts_finished_items_and_holds_results_until_done(tmp_path):
    manager = _GatedManager(blocked={"b"})

    first = await _start(_request("a", "b", "c"), manager, tmp_path)
    await _wait_for(lambda: delete_job_registry.get(first.job_id).completed == 1)
    running = delete_job_registry.get(first.job_id)

    assert first.status == "running"
    assert (running.status, running.total, running.completed, running.results) == (
        "running", 3, 1, None,
    )

    manager.gate.set()
    await delete_job_registry._jobs[first.job_id].task
    done = delete_job_registry.get(first.job_id)

    assert (done.status, done.completed) == ("done", 3)
    assert [(r.target, r.ok) for r in done.results] == [("a", True), ("b", True), ("c", True)]


async def test_running_jobs_past_the_cap_are_refused_and_finished_ones_free_a_slot(tmp_path):
    manager = _GatedManager(blocked={"slow"})
    for _ in range(MAX_RUNNING):
        await _start(_request("slow"), manager, tmp_path)

    with pytest.raises(GitRefusedError) as refused:
        await _start(_request("one-more"), manager, tmp_path)

    assert refused.value.code == "too_many_jobs"
    assert "one-more" not in manager.deleted

    manager.gate.set()
    await _wait_for(lambda: all(not job.running for job in delete_job_registry._jobs.values()))
    accepted = await _start(_request("one-more"), manager, tmp_path)

    await delete_job_registry._jobs[accepted.job_id].task
    assert delete_job_registry.get(accepted.job_id).status == "done"


async def test_crashed_batch_reports_every_item_failed_and_announces_a_reload(
    tmp_path, monkeypatch,
):
    async def explode(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(delete_jobs, "run_delete_batch", explode)
    bus = _FakeBus()

    started = await _start(_request("a", "b"), _GatedManager(), tmp_path, bus)
    await delete_job_registry._jobs[started.job_id].task
    done = delete_job_registry.get(started.job_id)

    assert done.status == "done"
    assert [(r.target, r.ok, r.error, r.error_detail) for r in done.results] == [
        ("a", False, "error", "disk on fire"),
        ("b", False, "error", "disk on fire"),
    ]
    assert [topic for topic, _ in bus.published] == ["git.changed"]
