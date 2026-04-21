"""Tests for AutoScanInstance — focused on the structured-event model
and the run-now wakeup mechanism introduced for CLA-22.

Mocks the Linear client, worktree manager, and session manager.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_mobile.core import SessionManager
from claude_mobile.core.projects_config import ProjectConfig
from claude_mobile.linear.config import AutoScanConfig
from claude_mobile.linear.models import (
    IssueLabel,
    IssueState,
    LinearIssue,
    ScanStatus,
)
from claude_mobile.linear.scanner import AutoScanInstance


def _make_config(tmp_path, *, auto_remove_worktrees: bool = True) -> AutoScanConfig:
    return AutoScanConfig(
        worktree_base="claude_worktree",
        scan_interval_seconds=1,
        branch_prefix="feature/claude",
        main_branch="main",
        linear_active_state="In Progress",
        auto_scan_prompt=None,
        auto_remove_worktrees=auto_remove_worktrees,
        linear_done_state="Done",
        state_dir=str(tmp_path / "state"),
    )


def _make_project_config(tmp_path) -> ProjectConfig:
    # POSIX form matches the canonical path that flows through the real
    # ``ProjectsStore`` after project-path reconciliation — keeps the
    # synthetic fixture in sync with production, so string-equality
    # comparisons (e.g. ``scanner._cleanup_completed_worktrees`` filter
    # against ``ActiveWorkItem.project_root`` which is normalized on load)
    # match as they would at runtime.
    return ProjectConfig(
        name="test-project",
        path=Path(tmp_path / "repo").as_posix(),
        linear_api_key="lin_api_test",
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
                   launcher=None, config=None, project_config=None) -> AutoScanInstance:
    return AutoScanInstance(
        project_name="test-project",
        project_config=project_config or _make_project_config(tmp_path),
        config=config or _make_config(tmp_path),
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


    def test_request_immediate_scan_reanchors_next_scan_and_wakes_loop(self, tmp_path):
        """request_immediate_scan() now only signals the wake event and
        reanchors next_scan_at — the old in-memory dedup set was removed
        so the bug where manually-reset issues were silently skipped no
        longer exists."""
        inst = _make_instance(tmp_path)
        assert not inst._run_now_event.is_set()

        inst.request_immediate_scan()

        assert inst._run_now_event.is_set()
        assert inst._next_scan_at is not None

    @pytest.mark.asyncio
    async def test_reprocesses_same_issue_across_scans_without_run_now(self, tmp_path):
        """Bug fix: an issue that Linear still reports as matching must be
        processed on every scan cycle — there is no local dedup layer.

        Previously, ``_processed_items`` filtered the issue out forever
        until ``request_immediate_scan()`` cleared the set, so a user
        resetting the issue back to Proposals + AutoPipeline in Linear
        saw the scanner silently skip it."""
        client = AsyncMock()
        issue = _make_issue(issue_id="uuid-reprocess", identifier="CLA-99")
        client.get_auto_scan_issues.return_value = [issue]
        client.update_issue_state.return_value = True
        client.remove_issue_label.return_value = True
        client.get_issue_state.return_value = None

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

        # First scan — issue is processed.
        await inst._scan_once()
        assert client.update_issue_state.call_count == 1

        # Second scan, no Run-Now: because the Linear query still returns
        # the issue, it must be processed AGAIN (the bug would have
        # skipped it silently).
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


class TestProcessIssueForwardsClientOptions:
    """The scanner's spawn path merges a ``ClaudeOptions`` from the client's
    stored preferences with per-issue fields (identifier/title). The
    ``max_effort`` flag must round-trip so that a user who toggled Max
    effort OFF on the Auto Scan config screen actually gets a session
    launched WITHOUT ``--effort max``. Nothing else in the suite asserts
    what ``launcher.build`` receives, so this class is the regression
    boundary for that wire-up."""

    @pytest.mark.asyncio
    async def test_process_issue_propagates_max_effort_false_from_client_options(
        self, tmp_path,
    ):
        from claude_mobile.core.claude_launcher import ClaudeOptions

        client = AsyncMock()
        client.update_issue_state.return_value = True
        client.remove_issue_label.return_value = True

        wt_mgr = AsyncMock()
        wt_result = MagicMock()
        wt_result.path = str(tmp_path / "worktree")
        wt_mgr.create_worktree.return_value = wt_result

        sess_mgr = AsyncMock()
        sess_mgr.create_session.return_value = MagicMock(pid=1)

        launcher = AsyncMock()
        launcher.build.return_value = MagicMock(argv=["claude"], env={})

        inst = _make_instance(
            tmp_path, client=client, worktree_mgr=wt_mgr,
            session_manager=sess_mgr, launcher=launcher,
        )
        # Simulate the Auto Scan config-screen submit path where the user
        # toggled Max effort OFF. The HTTP router stores the client-supplied
        # options on ``_client_options``; the scanner reads them when it
        # spawns a session for a matching issue.
        inst._client_options = ClaudeOptions(max_effort=False)

        await inst._process_issue(_make_issue(issue_id="uuid-off", identifier="CLA-OFF"))

        # launcher.build was called with (project, options). Pull the
        # second positional arg regardless of whether it was passed by
        # keyword.
        call = launcher.build.await_args
        forwarded = call.args[1] if len(call.args) > 1 else call.kwargs["options"]
        assert forwarded.max_effort is False
        # And the per-issue fields must still be set correctly.
        assert forwarded.identifier == "CLA-OFF"
        assert forwarded.title == "Test issue"

    @pytest.mark.asyncio
    async def test_process_issue_defaults_max_effort_true_when_no_client_options(
        self, tmp_path,
    ):
        """Covers the ``else True`` branch: when the scanner was started
        without any stored client options (e.g. server restart before any
        mobile client submitted), the spawned session must still get the
        default-ON max-effort behavior."""
        client = AsyncMock()
        client.update_issue_state.return_value = True
        client.remove_issue_label.return_value = True

        wt_mgr = AsyncMock()
        wt_result = MagicMock()
        wt_result.path = str(tmp_path / "worktree")
        wt_mgr.create_worktree.return_value = wt_result

        sess_mgr = AsyncMock()
        sess_mgr.create_session.return_value = MagicMock(pid=1)

        launcher = AsyncMock()
        launcher.build.return_value = MagicMock(argv=["claude"], env={})

        inst = _make_instance(
            tmp_path, client=client, worktree_mgr=wt_mgr,
            session_manager=sess_mgr, launcher=launcher,
        )
        # No inst._client_options assignment — the constructor default is None.
        assert inst._client_options is None

        await inst._process_issue(_make_issue(issue_id="uuid-def", identifier="CLA-DEF"))

        call = launcher.build.await_args
        forwarded = call.args[1] if len(call.args) > 1 else call.kwargs["options"]
        assert forwarded.max_effort is True


class TestWorktreeCleanup:
    """Coverage for the active_workitems.json + cleanup-on-Done feature."""

    @pytest.mark.asyncio
    async def test_process_issue_persists_workitem_when_worktree_created(self, tmp_path):
        client = AsyncMock()
        client.update_issue_state.return_value = True
        client.remove_issue_label.return_value = True

        wt_mgr = AsyncMock()
        wt_result = MagicMock()
        wt_result.path = str(tmp_path / "worktree")
        wt_mgr.create_worktree.return_value = wt_result

        sess_mgr = AsyncMock()
        session_obj = MagicMock()
        session_obj.pid = 1234
        sess_mgr.create_session.return_value = session_obj

        launcher = AsyncMock()
        spec = MagicMock()
        spec.argv = ["claude"]
        spec.env = {}
        launcher.build.return_value = spec

        inst = _make_instance(
            tmp_path, client=client, worktree_mgr=wt_mgr,
            session_manager=sess_mgr, launcher=launcher,
        )

        await inst._process_issue(_make_issue(issue_id="uuid-1", identifier="CLA-100"))

        items = await inst._workitems_store.read()
        assert len(items) == 1
        assert items[0].id == "CLA-100"
        assert items[0].uuid == "uuid-1"
        # Loaded value is POSIX-normalized by ``ActiveWorkItem.from_dict``;
        # compare against the same canonical form the loader produces.
        assert items[0].worktree_path == Path(tmp_path / "worktree").as_posix()

    @pytest.mark.asyncio
    async def test_process_issue_dedups_on_repeat_persistence(self, tmp_path):
        """If the same issue is processed twice (the bug-fix scenario),
        the JSON must not contain duplicate entries."""
        client = AsyncMock()
        client.update_issue_state.return_value = True
        client.remove_issue_label.return_value = True

        wt_mgr = AsyncMock()
        wt_result = MagicMock()
        wt_result.path = str(tmp_path / "worktree")
        wt_mgr.create_worktree.return_value = wt_result

        sess_mgr = AsyncMock()
        session_obj = MagicMock()
        session_obj.pid = 1234
        sess_mgr.create_session.return_value = session_obj

        launcher = AsyncMock()
        spec = MagicMock()
        spec.argv = ["claude"]
        spec.env = {}
        launcher.build.return_value = spec

        inst = _make_instance(
            tmp_path, client=client, worktree_mgr=wt_mgr,
            session_manager=sess_mgr, launcher=launcher,
        )

        issue = _make_issue(issue_id="uuid-x", identifier="CLA-200")
        await inst._process_issue(issue)
        await inst._process_issue(issue)

        items = await inst._workitems_store.read()
        assert len(items) == 1

    @pytest.mark.asyncio
    async def test_process_issue_does_not_persist_when_worktree_creation_fails(self, tmp_path):
        client = AsyncMock()
        client.update_issue_state.return_value = True
        client.remove_issue_label.return_value = True

        wt_mgr = AsyncMock()
        wt_mgr.create_worktree.return_value = None  # creation failed

        sess_mgr = AsyncMock()
        sess_mgr.create_session.return_value = MagicMock(pid=1)

        launcher = AsyncMock()
        launcher.build.return_value = MagicMock(argv=["claude"], env={})

        inst = _make_instance(
            tmp_path, client=client, worktree_mgr=wt_mgr,
            session_manager=sess_mgr, launcher=launcher,
        )

        await inst._process_issue(_make_issue(issue_id="uuid-noWT"))

        assert await inst._workitems_store.count() == 0

    @pytest.mark.asyncio
    async def test_cleanup_removes_worktree_when_issue_state_is_done(self, tmp_path):
        from claude_mobile.linear.workitems import ActiveWorkItem, now_iso_utc

        client = AsyncMock()
        client.get_issue_state.return_value = IssueState(id="state-done", name="Done")

        wt_mgr = AsyncMock()
        wt_mgr.cleanup_worktree.return_value = True

        inst = _make_instance(tmp_path, client=client, worktree_mgr=wt_mgr)

        item = ActiveWorkItem(
            id="CLA-7", uuid="uuid-7", slug="cla-7-x",
            worktree_path=str(tmp_path / "wt-7"),
            project_root=str(tmp_path / "repo"),
            added_at=now_iso_utc(),
        )
        await inst._workitems_store.write([item])

        await inst._cleanup_completed_worktrees()

        wt_mgr.cleanup_worktree.assert_awaited_once_with(
            Path(tmp_path / "wt-7").as_posix(), Path(tmp_path / "repo").as_posix(),
        )
        assert await inst._workitems_store.count() == 0

    @pytest.mark.asyncio
    async def test_cleanup_keeps_item_when_issue_state_is_not_done(self, tmp_path):
        from claude_mobile.linear.workitems import ActiveWorkItem, now_iso_utc

        client = AsyncMock()
        client.get_issue_state.return_value = IssueState(
            id="state-prog", name="In Progress",
        )

        wt_mgr = AsyncMock()
        inst = _make_instance(tmp_path, client=client, worktree_mgr=wt_mgr)

        item = ActiveWorkItem(
            id="CLA-9", uuid="uuid-9", slug="cla-9",
            worktree_path=str(tmp_path / "wt-9"),
            project_root=str(tmp_path / "repo"),
            added_at=now_iso_utc(),
        )
        await inst._workitems_store.write([item])

        await inst._cleanup_completed_worktrees()

        wt_mgr.cleanup_worktree.assert_not_called()
        assert await inst._workitems_store.count() == 1

    @pytest.mark.asyncio
    async def test_cleanup_keeps_item_when_linear_query_returns_none(self, tmp_path):
        from claude_mobile.linear.workitems import ActiveWorkItem, now_iso_utc

        client = AsyncMock()
        client.get_issue_state.return_value = None

        wt_mgr = AsyncMock()
        inst = _make_instance(tmp_path, client=client, worktree_mgr=wt_mgr)

        item = ActiveWorkItem(
            id="CLA-X", uuid="uuid-x", slug="cla-x",
            worktree_path=str(tmp_path / "wt-x"),
            project_root=str(tmp_path / "repo"),
            added_at=now_iso_utc(),
        )
        await inst._workitems_store.write([item])

        await inst._cleanup_completed_worktrees()

        wt_mgr.cleanup_worktree.assert_not_called()
        assert await inst._workitems_store.count() == 1

    @pytest.mark.asyncio
    async def test_cleanup_keeps_item_when_worktree_remove_fails(self, tmp_path):
        from claude_mobile.linear.workitems import ActiveWorkItem, now_iso_utc

        client = AsyncMock()
        client.get_issue_state.return_value = IssueState(id="state-done", name="Done")

        wt_mgr = AsyncMock()
        wt_mgr.cleanup_worktree.return_value = False  # removal failed

        inst = _make_instance(tmp_path, client=client, worktree_mgr=wt_mgr)

        item = ActiveWorkItem(
            id="CLA-FAIL", uuid="uuid-fail", slug="cla-fail",
            worktree_path=str(tmp_path / "wt-fail"),
            project_root=str(tmp_path / "repo"),
            added_at=now_iso_utc(),
        )
        await inst._workitems_store.write([item])

        await inst._cleanup_completed_worktrees()

        assert await inst._workitems_store.count() == 1

    @pytest.mark.asyncio
    async def test_scan_once_skips_cleanup_when_auto_remove_disabled(self, tmp_path):
        client = AsyncMock()
        client.get_auto_scan_issues.return_value = []
        client.get_issue_state.return_value = IssueState(id="state-done", name="Done")

        wt_mgr = AsyncMock()
        config = _make_config(tmp_path, auto_remove_worktrees=False)
        inst = _make_instance(tmp_path, client=client, worktree_mgr=wt_mgr, config=config)

        await inst._scan_once()

        client.get_issue_state.assert_not_called()
        wt_mgr.cleanup_worktree.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_keeps_item_when_get_issue_state_raises(self, tmp_path):
        """Distinct branch from ``returns None``: exception path must log
        ``query_failed`` and preserve the item for retry on the next cycle."""
        from claude_mobile.linear.workitems import ActiveWorkItem, now_iso_utc

        client = AsyncMock()
        client.get_issue_state.side_effect = RuntimeError("boom")

        wt_mgr = AsyncMock()
        inst = _make_instance(tmp_path, client=client, worktree_mgr=wt_mgr)

        item = ActiveWorkItem(
            id="CLA-RAISE", uuid="uuid-raise", slug="cla-raise",
            worktree_path=str(tmp_path / "wt-raise"),
            project_root=str(tmp_path / "repo"),
            added_at=now_iso_utc(),
        )
        await inst._workitems_store.write([item])

        # Must NOT propagate — the cycle-level try/except in _scan_once is
        # a last-resort; per-item errors are handled inside the method.
        await inst._cleanup_completed_worktrees()

        wt_mgr.cleanup_worktree.assert_not_called()
        assert await inst._workitems_store.count() == 1

    @pytest.mark.asyncio
    async def test_cleanup_keeps_item_when_cleanup_worktree_raises(self, tmp_path):
        """Distinct branch from ``returns False``: if the worktree manager
        raises, the item must be preserved (retry next cycle)."""
        from claude_mobile.linear.workitems import ActiveWorkItem, now_iso_utc

        client = AsyncMock()
        client.get_issue_state.return_value = IssueState(id="state-done", name="Done")

        wt_mgr = AsyncMock()
        wt_mgr.cleanup_worktree.side_effect = OSError("fs locked")

        inst = _make_instance(tmp_path, client=client, worktree_mgr=wt_mgr)

        item = ActiveWorkItem(
            id="CLA-OS", uuid="uuid-os", slug="cla-os",
            worktree_path=str(tmp_path / "wt-os"),
            project_root=str(tmp_path / "repo"),
            added_at=now_iso_utc(),
        )
        await inst._workitems_store.write([item])

        await inst._cleanup_completed_worktrees()

        wt_mgr.cleanup_worktree.assert_awaited_once()
        assert await inst._workitems_store.count() == 1

    @pytest.mark.asyncio
    async def test_cleanup_batch_removes_only_done_items(self, tmp_path):
        """With multiple tracked items, only those reporting Done are removed."""
        from claude_mobile.linear.workitems import ActiveWorkItem, now_iso_utc

        repo_root = str(tmp_path / "repo")
        items = [
            ActiveWorkItem(id="CLA-A", uuid="u-a", slug="a",
                           worktree_path=str(tmp_path / "wt-a"),
                           project_root=repo_root, added_at=now_iso_utc()),
            ActiveWorkItem(id="CLA-B", uuid="u-b", slug="b",
                           worktree_path=str(tmp_path / "wt-b"),
                           project_root=repo_root, added_at=now_iso_utc()),
            ActiveWorkItem(id="CLA-C", uuid="u-c", slug="c",
                           worktree_path=str(tmp_path / "wt-c"),
                           project_root=repo_root, added_at=now_iso_utc()),
        ]

        state_map = {
            "u-a": IssueState(id="s1", name="Done"),
            "u-b": IssueState(id="s2", name="In Progress"),
            "u-c": IssueState(id="s3", name="Done"),
        }

        async def fake_get_state(uuid):
            return state_map[uuid]

        client = AsyncMock()
        client.get_issue_state.side_effect = fake_get_state

        wt_mgr = AsyncMock()
        wt_mgr.cleanup_worktree.return_value = True

        inst = _make_instance(tmp_path, client=client, worktree_mgr=wt_mgr)
        await inst._workitems_store.write(items)

        await inst._cleanup_completed_worktrees()

        # Only A and C (Done) should have been removed.
        remaining = await inst._workitems_store.read()
        assert [i.id for i in remaining] == ["CLA-B"]
        assert wt_mgr.cleanup_worktree.await_count == 2

    @pytest.mark.asyncio
    async def test_cleanup_ignores_items_from_other_projects(self, tmp_path):
        """The cleanup loop filters on ``project_root`` so a shared state
        file cannot cause one project's scanner to tear down another
        project's worktrees."""
        from claude_mobile.linear.workitems import ActiveWorkItem, now_iso_utc

        our_root = str(tmp_path / "repo")  # matches _make_config's project path
        foreign_root = str(tmp_path / "other-repo")

        client = AsyncMock()
        client.get_issue_state.return_value = IssueState(id="s", name="Done")

        wt_mgr = AsyncMock()
        wt_mgr.cleanup_worktree.return_value = True

        inst = _make_instance(tmp_path, client=client, worktree_mgr=wt_mgr)

        foreign_item = ActiveWorkItem(
            id="OTHER-1", uuid="u-other", slug="other",
            worktree_path=str(tmp_path / "wt-other"),
            project_root=foreign_root, added_at=now_iso_utc(),
        )
        await inst._workitems_store.write([foreign_item])

        await inst._cleanup_completed_worktrees()

        # Foreign item must not be queried, not be removed from disk,
        # and not be dropped from the store.
        client.get_issue_state.assert_not_called()
        wt_mgr.cleanup_worktree.assert_not_called()
        remaining = await inst._workitems_store.read()
        assert [i.id for i in remaining] == ["OTHER-1"]

    @pytest.mark.asyncio
    async def test_process_issue_does_not_persist_when_auto_remove_disabled(self, tmp_path):
        """When the feature is disabled globally, _process_issue must not
        write to the workitems store even if the worktree was created."""
        client = AsyncMock()
        client.update_issue_state.return_value = True
        client.remove_issue_label.return_value = True

        wt_mgr = AsyncMock()
        wt_result = MagicMock()
        wt_result.path = str(tmp_path / "worktree")
        wt_mgr.create_worktree.return_value = wt_result

        sess_mgr = AsyncMock()
        sess_mgr.create_session.return_value = MagicMock(pid=7)

        launcher = AsyncMock()
        launcher.build.return_value = MagicMock(argv=["claude"], env={})

        config = _make_config(tmp_path, auto_remove_worktrees=False)
        inst = _make_instance(
            tmp_path, client=client, worktree_mgr=wt_mgr,
            session_manager=sess_mgr, launcher=launcher, config=config,
        )

        await inst._process_issue(_make_issue(issue_id="uuid-dis", identifier="CLA-DIS"))

        assert await inst._workitems_store.count() == 0


class TestProcessedCountDTO:
    """The DTO field ``processed_count`` was repurposed: it now reflects
    the number of active work items currently tracked for this project.
    The Android client reads this field — its meaning must not silently
    drift. The implementation caches the count in-memory and updates it
    via ``_process_issue`` (increment) and ``_cleanup_completed_worktrees``
    (decrement); this test exercises both paths end-to-end."""

    @pytest.mark.asyncio
    async def test_processed_count_tracks_add_and_cleanup(self, tmp_path):
        client = AsyncMock()
        client.update_issue_state.return_value = True
        client.remove_issue_label.return_value = True

        wt_mgr = AsyncMock()
        wt_result = MagicMock()
        wt_result.path = str(tmp_path / "wt")
        wt_mgr.create_worktree.return_value = wt_result
        wt_mgr.cleanup_worktree.return_value = True

        sess_mgr = AsyncMock()
        sess_mgr.create_session.return_value = MagicMock(pid=1)

        launcher = AsyncMock()
        launcher.build.return_value = MagicMock(argv=["claude"], env={})

        inst = _make_instance(
            tmp_path, client=client, worktree_mgr=wt_mgr,
            session_manager=sess_mgr, launcher=launcher,
        )
        assert inst.to_dto()["processed_count"] == 0

        await inst._process_issue(_make_issue(issue_id="u1", identifier="CLA-1"))
        await inst._process_issue(_make_issue(issue_id="u2", identifier="CLA-2"))
        assert inst.to_dto()["processed_count"] == 2

        # Re-processing an already-tracked issue must NOT double-count
        # (dedup happens inside the store).
        await inst._process_issue(_make_issue(issue_id="u1", identifier="CLA-1"))
        assert inst.to_dto()["processed_count"] == 2

        # Clean up one via the Done-state path.
        client.get_issue_state.side_effect = [
            IssueState(id="s1", name="Done"),
            IssueState(id="s2", name="In Progress"),
        ]
        await inst._cleanup_completed_worktrees()
        assert inst.to_dto()["processed_count"] == 1

    @pytest.mark.asyncio
    async def test_processed_count_not_decremented_when_cleanup_fails(self, tmp_path):
        """The cache must only decrement when ``store.remove()`` actually
        dropped an entry. If the Linear query fails, the worktree removal
        fails, or the state is not Done, the cached count must stay put —
        otherwise a long-running agent with transient cleanup errors
        would report ``processed_count=0`` while the store still holds
        items."""
        from claude_mobile.linear.workitems import ActiveWorkItem, now_iso_utc

        # Seed one item that cleanup will attempt and fail to remove.
        client = AsyncMock()
        client.get_issue_state.return_value = IssueState(id="s-done", name="Done")

        wt_mgr = AsyncMock()
        wt_mgr.cleanup_worktree.return_value = False  # removal fails

        inst = _make_instance(tmp_path, client=client, worktree_mgr=wt_mgr)

        item = ActiveWorkItem(
            id="CLA-STUCK", uuid="u-stuck", slug="cla-stuck",
            worktree_path=str(tmp_path / "wt-stuck"),
            project_root=str(tmp_path / "repo"),
            added_at=now_iso_utc(),
        )
        await inst._workitems_store.write([item])
        # Simulate the cache being primed by a prior process/cycle.
        inst._active_workitem_count = 1

        await inst._cleanup_completed_worktrees()

        # Store still has the item AND the cached count is unchanged.
        assert await inst._workitems_store.count() == 1
        assert inst.processed_count == 1
        assert inst.to_dto()["processed_count"] == 1

    @pytest.mark.asyncio
    async def test_processed_count_batch_decrements_only_for_removed_items(self, tmp_path):
        """With a mix of Done / not-Done items, the cache must decrement
        by exactly the number of successful removals — not by the number
        of items examined."""
        from claude_mobile.linear.workitems import ActiveWorkItem, now_iso_utc

        repo_root = str(tmp_path / "repo")
        items = [
            ActiveWorkItem(id="CLA-A", uuid="u-a", slug="a",
                           worktree_path=str(tmp_path / "wt-a"),
                           project_root=repo_root, added_at=now_iso_utc()),
            ActiveWorkItem(id="CLA-B", uuid="u-b", slug="b",
                           worktree_path=str(tmp_path / "wt-b"),
                           project_root=repo_root, added_at=now_iso_utc()),
            ActiveWorkItem(id="CLA-C", uuid="u-c", slug="c",
                           worktree_path=str(tmp_path / "wt-c"),
                           project_root=repo_root, added_at=now_iso_utc()),
        ]
        state_map = {
            "u-a": IssueState(id="s1", name="Done"),
            "u-b": IssueState(id="s2", name="In Progress"),
            "u-c": IssueState(id="s3", name="Done"),
        }

        async def fake_get_state(uuid):
            return state_map[uuid]

        client = AsyncMock()
        client.get_issue_state.side_effect = fake_get_state

        wt_mgr = AsyncMock()
        wt_mgr.cleanup_worktree.return_value = True

        inst = _make_instance(tmp_path, client=client, worktree_mgr=wt_mgr)
        await inst._workitems_store.write(items)
        inst._active_workitem_count = 3  # prime as if added in prior cycles

        await inst._cleanup_completed_worktrees()

        # Only A and C were Done → cache dropped by 2, not 3.
        assert inst.processed_count == 1
        assert inst.to_dto()["processed_count"] == 1

    @pytest.mark.asyncio
    async def test_processed_count_primed_from_disk_on_construction(self, tmp_path):
        """When the agent restarts, the cache must reflect items already
        persisted by a previous process, not start at zero."""
        from claude_mobile.linear.workitems import (
            ActiveWorkItem,
            ActiveWorkItemsStore,
            active_workitems_path,
            now_iso_utc,
        )

        state_dir = tmp_path / "state"
        pre_store = ActiveWorkItemsStore(
            active_workitems_path(state_dir, "test-project")
        )
        await pre_store.write([
            ActiveWorkItem(
                id="CLA-PRIOR", uuid="u-prior", slug="cla-prior",
                worktree_path=str(tmp_path / "wt-prior"),
                project_root=str(tmp_path / "repo"),
                added_at=now_iso_utc(),
            ),
        ])

        config = _make_config(tmp_path)
        # Point config at the pre-populated state dir.
        from dataclasses import replace
        config = replace(config, state_dir=str(state_dir))

        inst = _make_instance(tmp_path, config=config)
        assert inst.to_dto()["processed_count"] == 1


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
