"""Tests for AutoScanInstance — focused on the structured-event model
and the run-now wakeup mechanism introduced for CLA-22.

Mocks the Linear client, worktree manager, and session manager.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_mobile.core import SessionManager
from claude_mobile.linear.config import AutoScanConfig, ProjectConfig
from claude_mobile.linear.models import (
    IssueLabel,
    IssueState,
    LinearIssue,
    ScanStatus,
)
from claude_mobile.linear.scanner import AutoScanInstance


def _make_config(tmp_path) -> AutoScanConfig:
    return AutoScanConfig(
        projects={
            "test-project": ProjectConfig(
                name="test-project",
                path=str(tmp_path / "repo"),
                linear_api_key="lin_api_test",
            )
        },
        worktree_base="claude_worktree",
        scan_interval_seconds=1,
        branch_prefix="feature/claude",
        main_branch="main",
        linear_active_state="In Progress",
        auto_scan_prompt=None,
    )


def _make_issue(
    issue_id: str = "uuid-1",
    identifier: str = "CLA-8",
    title: str = "Test issue",
) -> LinearIssue:
    return LinearIssue(
        id=issue_id,
        identifier=identifier,
        title=title,
        description=None,
        state=IssueState(id="state-proposals", name="Proposals"),
        labels=[IssueLabel(id="label-auto", name="AutoPipeline")],
        team_states=[
            IssueState(id="state-proposals", name="Proposals"),
            IssueState(id="state-inprogress", name="In Progress"),
        ],
    )


def _make_instance(tmp_path, *, client=None, worktree_mgr=None, session_manager=None,
                   launcher=None) -> AutoScanInstance:
    config = _make_config(tmp_path)
    return AutoScanInstance(
        project_name="test-project",
        config=config,
        session_manager=session_manager or AsyncMock(spec=SessionManager),
        launcher=launcher or AsyncMock(),
        client=client or AsyncMock(),
        worktree_mgr=worktree_mgr or AsyncMock(),
    )


class TestEmitAndRingBuffer:
    def test_emit_assigns_monotonic_seq(self, tmp_path):
        inst = _make_instance(tmp_path)
        a = inst._emit("log", {"line": "a"})
        b = inst._emit("log", {"line": "b"})
        c = inst._emit("log", {"line": "c"})
        assert (a.seq, b.seq, c.seq) == (1, 2, 3)
        assert a.ts and b.ts and c.ts

    def test_ring_buffer_caps_at_max(self, tmp_path):
        inst = _make_instance(tmp_path)
        inst._max_recent_events = 500
        for i in range(600):
            inst._emit("log", {"line": str(i)})
        assert len(inst._recent_events) == 500
        assert inst._recent_events[-1].seq == 600
        assert inst._recent_events[0].seq == 101

    def test_to_dto_includes_new_fields(self, tmp_path):
        inst = _make_instance(tmp_path)
        inst._emit("log", {"line": "x"})
        dto = inst.to_dto()
        assert "next_scan_at" in dto
        assert "cycle_number" in dto
        assert dto["current_seq"] == 1

    def test_subscribe_delivers_event(self, tmp_path):
        inst = _make_instance(tmp_path)
        q = inst.subscribe_events()
        inst._emit("log", {"line": "hi"})
        evt = q.get_nowait()
        assert evt.type == "log"
        assert evt.payload == {"line": "hi"}


class TestRunNow:
    @pytest.mark.asyncio
    async def test_request_immediate_scan_wakes_loop(self, tmp_path):
        """A long sleep should be cut short when request_immediate_scan() is called."""
        from dataclasses import replace
        inst = _make_instance(tmp_path)
        # Build a fresh config with a long interval (AutoScanConfig is frozen).
        inst._config = replace(inst._config, scan_interval_seconds=30)

        scan_calls = 0

        async def fake_scan_once():
            nonlocal scan_calls
            scan_calls += 1
            if scan_calls >= 2:
                # stop the loop after we've observed the wake
                raise asyncio.CancelledError()

        inst._scan_once = fake_scan_once  # type: ignore[assignment]

        loop_task = asyncio.create_task(inst._poll_loop())
        # Give the loop time to run scan_once #1 and enter wait_for sleep
        await asyncio.sleep(0.05)
        assert scan_calls == 1
        inst.request_immediate_scan()
        # Wake should arrive nearly immediately
        try:
            await asyncio.wait_for(loop_task, timeout=1.0)
        except asyncio.CancelledError:
            pass
        assert scan_calls == 2


    def test_request_immediate_scan_clears_processed_items(self, tmp_path):
        """request_immediate_scan() must clear _processed_items so that
        manually-reset issues are picked up again."""
        inst = _make_instance(tmp_path)
        inst._processed_items.add("uuid-1")
        inst._processed_items.add("uuid-2")
        assert len(inst._processed_items) == 2

        inst.request_immediate_scan()

        assert len(inst._processed_items) == 0

    @pytest.mark.asyncio
    async def test_run_now_reprocesses_previously_seen_issue(self, tmp_path):
        """After Run-Now, an issue that was already processed should be
        picked up again if it still matches the Linear query filters."""
        client = AsyncMock()
        issue = _make_issue(issue_id="uuid-reprocess", identifier="CLA-99")
        client.get_auto_scan_issues.return_value = [issue]
        client.update_issue_state.return_value = True
        client.remove_issue_label.return_value = True

        wt_mgr = AsyncMock()
        wt_result = MagicMock()
        wt_result.path = str(tmp_path / "wt")
        wt_mgr.create_worktree.return_value = wt_result

        sess_mgr = AsyncMock()
        session_obj = MagicMock()
        session_obj.pid = 999
        sess_mgr.create_session.return_value = session_obj

        launcher = AsyncMock()
        spec = MagicMock()
        spec.argv = ["claude"]
        spec.env = {}
        launcher.build.return_value = spec

        inst = _make_instance(
            tmp_path,
            client=client,
            worktree_mgr=wt_mgr,
            session_manager=sess_mgr,
            launcher=launcher,
        )

        # First scan: issue gets processed and added to dedup set
        await inst._scan_once()
        assert "uuid-reprocess" in inst._processed_items

        # Second scan WITHOUT Run-Now: issue is filtered out (dedup)
        await inst._scan_once()
        assert client.update_issue_state.call_count == 1

        # Simulate Run-Now: clears processed set
        inst.request_immediate_scan()
        assert "uuid-reprocess" not in inst._processed_items

        # Third scan AFTER Run-Now: issue is reprocessed
        await inst._scan_once()
        assert client.update_issue_state.call_count == 2


class TestProcessIssueEmitsPerStepEvents:
    @pytest.mark.asyncio
    async def test_emits_state_label_worktree_session_in_order(self, tmp_path):
        client = AsyncMock()
        client.update_issue_state.return_value = True
        client.remove_issue_label.return_value = True

        wt_mgr = AsyncMock()
        wt_result = MagicMock()
        wt_result.path = str(tmp_path / "worktree")
        wt_mgr.create_worktree.return_value = wt_result

        sess_mgr = AsyncMock()
        session_obj = MagicMock()
        session_obj.pid = 12345
        sess_mgr.create_session.return_value = session_obj

        launcher = AsyncMock()
        spec = MagicMock()
        spec.argv = ["claude"]
        spec.env = {}
        launcher.build.return_value = spec

        inst = _make_instance(
            tmp_path,
            client=client,
            worktree_mgr=wt_mgr,
            session_manager=sess_mgr,
            launcher=launcher,
        )

        await inst._process_issue(_make_issue())

        # Collect step ordering from issue events.
        steps = [
            (e.payload["step"], e.payload["status"])
            for e in inst._recent_events
            if e.type == "issue"
        ]
        # Expected: each step has a started + ok pair, in this order
        expected_order = [
            ("state_update", "started"),
            ("state_update", "ok"),
            ("label_removal", "started"),
            ("label_removal", "ok"),
            ("worktree_create", "started"),
            ("worktree_create", "ok"),
            ("session_spawn", "started"),
            ("session_spawn", "ok"),
        ]
        assert steps == expected_order

        # And worktree + session payloads were emitted
        types = [e.type for e in inst._recent_events]
        assert "worktree" in types
        assert "session" in types


class TestStopEmitsPhase:
    @pytest.mark.asyncio
    async def test_stop_emits_scan_stopped_phase(self, tmp_path):
        inst = _make_instance(tmp_path)
        await inst.stop()
        phase_events = [e for e in inst._recent_events if e.type == "phase"]
        assert any(e.payload["phase"] == "scan_stopped" for e in phase_events)


class TestPollLoopContextIsolation:
    @pytest.mark.asyncio
    async def test_poll_loop_runs_in_fresh_context(self, tmp_path):
        """The poll task must NOT inherit the caller's contextvars.

        Regression: request_id from /api/autoscan/start was leaking into
        every autoscan log entry because the background task snapshotted
        the HTTP handler's contextvars at create_task time. Fix: pass
        ``context=contextvars.Context()`` at task creation so the poll
        loop starts with a clean context.
        """
        import structlog

        from claude_mobile.agent.logging import (
            bind_request_context,
            clear_request_context,
        )

        # Simulate an HTTP request binding its context (mirrors what the
        # middleware at claude_agent/claude_mobile/agent/app.py:98 does on every request).
        bind_request_context(
            request_id="test-req-abc",
            method="POST",
            path="/api/autoscan/start",
        )
        try:
            client = AsyncMock()
            client.get_viewer.return_value = {"name": "tester"}

            inst = _make_instance(tmp_path, client=client)

            observed_ctx: dict = {}
            probe_done = asyncio.Event()

            async def fake_poll_loop():
                # Snapshot contextvars from INSIDE the poll task.
                observed_ctx.update(structlog.contextvars.get_contextvars())
                probe_done.set()

            # Replace before start() schedules the task via create_task.
            inst._poll_loop = fake_poll_loop  # type: ignore[assignment]

            await inst.start()
            assert inst._poll_task is not None
            await asyncio.wait_for(probe_done.wait(), timeout=1.0)

            # None of the HTTP-request fields may have leaked through.
            assert "request_id" not in observed_ctx, (
                f"request_id leaked into poll loop context: {observed_ctx}"
            )
            assert "method" not in observed_ctx
            assert "path" not in observed_ctx
        finally:
            clear_request_context()
