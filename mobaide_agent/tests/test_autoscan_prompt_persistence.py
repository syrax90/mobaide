"""Tests for ``AutoScanSessionManager.start`` persisting the applied Auto
Scan prompt onto the project record (the single choke point that covers both
the direct-HTTP and central-relay transports).

Uses a real ``ProjectsStore`` so the prompt genuinely round-trips to disk;
``AutoScanInstance.start`` is stubbed to a no-op so no Linear ``get_viewer``
call is made and no live poll task is spawned.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from mobaide.core import ProjectRecord, ProjectsStore, SessionManager
from mobaide.core.claude_launcher import ClaudeOptions
from mobaide.linear.config import AutoScanConfig
from mobaide.linear.scanner import AutoScanInstance, AutoScanSessionManager


class _RecordingEventBus:
    """Captures ``publish(topic, payload)`` calls for assertion."""

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def publish(self, topic: str, payload: object) -> None:
        self.events.append((topic, payload))

    def topics(self) -> list[str]:
        return [t for t, _ in self.events]

    def payload_for(self, topic: str) -> object:
        for t, p in self.events:
            if t == topic:
                return p
        raise AssertionError(f"no event published for topic {topic!r}")


@pytest.fixture(autouse=True)
def _no_op_instance_start(monkeypatch):
    """Replace the per-instance ``start`` so the manager's ``start`` never
    reaches the real Linear client or schedules a background poll task."""

    async def _noop(self) -> None:  # noqa: ANN001
        return None

    monkeypatch.setattr(AutoScanInstance, "start", _noop)


def _make_store(tmp_path, *, prompt: str | None = None) -> ProjectsStore:
    store = ProjectsStore(str(tmp_path / "projects.json"))
    store.load()
    store.create(
        ProjectRecord(
            name="alpha",
            path=str(tmp_path / "repo"),
            linear_api_key="lin_api_test",
            auto_scan_prompt=prompt,
        ),
    )
    return store


def _make_manager(store: ProjectsStore, bus: _RecordingEventBus) -> AutoScanSessionManager:
    config = AutoScanConfig(
        worktree_base="mobaide_worktree",
        scan_interval_seconds=1,
        branch_prefix="feature/claude",
        main_branch="main",
        auto_remove_worktrees=False,
        state_dir="state",
    )
    return AutoScanSessionManager(
        config=config,
        session_manager=AsyncMock(spec=SessionManager),
        projects_store=store,
        event_bus=bus,
    )


class TestStartPersistsAppliedPrompt:
    @pytest.mark.asyncio
    async def test_custom_prompt_is_saved_and_publishes_projects_changed(
        self, tmp_path,
    ):
        store = _make_store(tmp_path, prompt=None)
        bus = _RecordingEventBus()
        mgr = _make_manager(store, bus)

        await mgr.start(
            "alpha",
            client_options=ClaudeOptions(initial_prompt="custom scan prompt"),
        )

        # Persisted to the record (round-trips through the public snapshot).
        snap = store.get("alpha")
        assert snap is not None
        assert snap.auto_scan_prompt == "custom scan prompt"

        # And a projects.changed event carries the refreshed list so live
        # clients reconcile their cached prompt.
        assert "projects.changed" in bus.topics()
        payload = bus.payload_for("projects.changed")
        assert isinstance(payload, dict)
        projects = payload["projects"]
        by_name = {p["name"]: p for p in projects}
        assert by_name["alpha"]["auto_scan_prompt"] == "custom scan prompt"

    @pytest.mark.asyncio
    async def test_none_client_options_leaves_saved_prompt_untouched(
        self, tmp_path,
    ):
        # A cold REST start with no claude_options expresses no prompt intent;
        # an existing saved prompt must survive, and no projects.changed fires.
        store = _make_store(tmp_path, prompt="previously saved")
        bus = _RecordingEventBus()
        mgr = _make_manager(store, bus)

        await mgr.start("alpha", client_options=None)

        snap = store.get("alpha")
        assert snap is not None
        assert snap.auto_scan_prompt == "previously saved"
        assert "projects.changed" not in bus.topics()

    @pytest.mark.asyncio
    async def test_blank_prompt_keeps_saved_prompt(self, tmp_path):
        # A blank initial_prompt expresses no prompt intent: the project's saved
        # prompt is preserved (not clobbered), and no projects.changed fires.
        store = _make_store(tmp_path, prompt="previously saved")
        bus = _RecordingEventBus()
        mgr = _make_manager(store, bus)

        await mgr.start(
            "alpha", client_options=ClaudeOptions(initial_prompt=""),
        )

        snap = store.get("alpha")
        assert snap is not None
        assert snap.auto_scan_prompt == "previously saved"
        assert "projects.changed" not in bus.topics()

    @pytest.mark.asyncio
    async def test_failed_instance_start_does_not_persist_prompt(
        self, tmp_path, monkeypatch,
    ):
        # The prompt is saved only AFTER instance.start() succeeds; if the Linear
        # auth probe inside instance.start() raises, the saved prompt is left
        # untouched and no projects.changed fires.
        store = _make_store(tmp_path, prompt="previously saved")
        bus = _RecordingEventBus()
        mgr = _make_manager(store, bus)

        async def _boom(self) -> None:
            raise RuntimeError("linear auth failed")

        monkeypatch.setattr(AutoScanInstance, "start", _boom)

        with pytest.raises(RuntimeError, match="linear auth failed"):
            await mgr.start(
                "alpha",
                client_options=ClaudeOptions(initial_prompt="new prompt"),
            )

        snap = store.get("alpha")
        assert snap is not None
        assert snap.auto_scan_prompt == "previously saved"
        assert "projects.changed" not in bus.topics()

    @pytest.mark.asyncio
    async def test_persist_failure_does_not_fail_running_scan(
        self, tmp_path, monkeypatch,
    ):
        # The prompt save is best-effort and runs after the scan is registered;
        # a transient disk/permission error on the store write must NOT surface
        # as a start failure — the scan is already running by then.
        store = _make_store(tmp_path, prompt="previously saved")
        bus = _RecordingEventBus()
        mgr = _make_manager(store, bus)

        def _boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(store, "update", _boom)

        instance = await mgr.start(
            "alpha",
            client_options=ClaudeOptions(initial_prompt="new prompt"),
        )

        # start() succeeded and the scan is registered despite the save failing.
        assert instance is not None
        assert mgr.get("alpha") is instance
        # Nothing was persisted (record untouched) or published.
        snap = store.get("alpha")
        assert snap is not None
        assert snap.auto_scan_prompt == "previously saved"
        assert "projects.changed" not in bus.topics()

    @pytest.mark.asyncio
    async def test_rejected_start_does_not_rewrite_saved_prompt(self, tmp_path):
        # The prompt save runs AFTER the already-running gate, under the lock,
        # so a second start for the same project must not touch the record.
        store = _make_store(tmp_path, prompt="first prompt")
        bus = _RecordingEventBus()
        mgr = _make_manager(store, bus)

        await mgr.start(
            "alpha", client_options=ClaudeOptions(initial_prompt="first prompt"),
        )

        from mobaide.linear.scanner import AutoScanAlreadyRunning

        with pytest.raises(AutoScanAlreadyRunning):
            await mgr.start(
                "alpha",
                client_options=ClaudeOptions(initial_prompt="second prompt"),
            )

        snap = store.get("alpha")
        assert snap is not None
        assert snap.auto_scan_prompt == "first prompt"
