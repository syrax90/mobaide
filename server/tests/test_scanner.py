"""Tests for AutoScanService — mock Linear client, worktree manager, and session manager."""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_mobile.core import SessionManager
from claude_mobile.linear.config import AutoScanConfig, ProjectConfig
from claude_mobile.linear.models import (
    IssueLabel,
    IssueState,
    LinearIssue,
    ScanStatus,
)
from claude_mobile.linear.scanner import AutoScanService
from claude_mobile.linear.worktree import WorktreeResult


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
        scan_interval_seconds=1,  # fast for tests
        branch_prefix="feature/claude",
        main_branch="main",
        linear_active_state="In Progress",
        auto_scan_prompt=None,
        processed_items_path=str(tmp_path / "processed.json"),
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


@pytest.fixture
def config(tmp_path):
    return _make_config(tmp_path)


@pytest.fixture
def session_manager():
    mgr = AsyncMock(spec=SessionManager)
    mgr.create_session = AsyncMock()
    return mgr


@pytest.fixture
def service(config, session_manager):
    return AutoScanService(config=config, session_manager=session_manager)


class TestProcessedItemsPersistence:
    def test_save_and_load(self, service, tmp_path):
        service._processed_items = {"id-1", "id-2", "id-3"}
        service._save_processed_items()

        path = tmp_path / "processed.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert set(data) == {"id-1", "id-2", "id-3"}

        # Load into fresh service
        service._processed_items = set()
        service._load_processed_items()
        assert service._processed_items == {"id-1", "id-2", "id-3"}

    def test_load_nonexistent_file(self, service):
        service._load_processed_items()
        assert service._processed_items == set()

    def test_load_corrupt_file(self, service, tmp_path):
        path = tmp_path / "processed.json"
        path.write_text("not json!!!")
        service._load_processed_items()
        assert service._processed_items == set()


class TestGetStatus:
    def test_initial_status(self, service):
        status = service.get_status()
        assert status["status"] == "idle"
        assert status["active_project"] is None
        assert status["processed_count"] == 0
        assert status["last_scan_time"] is None
        assert isinstance(status["recent_events"], list)


class TestEventPubSub:
    def test_subscribe_and_receive(self, service):
        queue = service.subscribe_events()
        service._emit_event("test_event", {"key": "value"})
        assert not queue.empty()
        event = queue.get_nowait()
        assert event.event_type == "test_event"
        assert event.data == {"key": "value"}

    def test_unsubscribe(self, service):
        queue = service.subscribe_events()
        service.unsubscribe_events(queue)
        service._emit_event("test_event", None)
        assert queue.empty()

    def test_recent_events_capped(self, service):
        for i in range(150):
            service._emit_event(f"event_{i}", None)
        assert len(service._recent_events) <= service._max_recent_events


class TestProcessIssue:
    async def test_full_issue_processing_flow(self, service, session_manager, tmp_path):
        """Test the complete issue processing: state transition, label removal, worktree, session."""
        issue = _make_issue()

        mock_client = AsyncMock()
        mock_client.update_issue_state.return_value = True
        mock_client.remove_issue_label.return_value = True
        service._client = mock_client
        service._active_project = "test-project"

        mock_wt_mgr = AsyncMock()
        mock_wt_mgr.create_worktree.return_value = WorktreeResult(
            path=str(tmp_path / "worktree"), is_new=True, branch="feature/claude/cla-8-test-issue"
        )
        service._worktree_mgr = mock_wt_mgr

        await service._process_issue(issue)

        # Verify state was updated
        mock_client.update_issue_state.assert_called_once_with(
            "uuid-1", "state-inprogress"
        )

        # Verify label was removed
        mock_client.remove_issue_label.assert_called_once_with(
            "uuid-1", "label-auto"
        )

        # Verify worktree was created
        mock_wt_mgr.create_worktree.assert_called_once()

        # Verify session was started
        session_manager.create_session.assert_called_once()
        config_arg = session_manager.create_session.call_args[0][0]
        assert config_arg.cwd == str(tmp_path / "worktree")
        assert "claude" in config_arg.command[0]

        # Verify added to processed
        assert "uuid-1" in service._processed_items

    async def test_worktree_failure_falls_back_to_project_root(
        self, service, session_manager, config, tmp_path
    ):
        """When worktree creation fails, session starts in project root."""
        issue = _make_issue()

        mock_client = AsyncMock()
        mock_client.update_issue_state.return_value = True
        mock_client.remove_issue_label.return_value = True
        service._client = mock_client
        service._active_project = "test-project"

        mock_wt_mgr = AsyncMock()
        mock_wt_mgr.create_worktree.return_value = None  # failure
        service._worktree_mgr = mock_wt_mgr

        await service._process_issue(issue)

        config_arg = session_manager.create_session.call_args[0][0]
        assert config_arg.cwd == config.projects["test-project"].path

    async def test_missing_target_state_skips_update(self, service, session_manager):
        """If the target state isn't in team workflow, skip state update."""
        issue = _make_issue()
        issue.team_states = [IssueState(id="s1", name="Proposals")]  # no "In Progress"

        mock_client = AsyncMock()
        mock_client.remove_issue_label.return_value = True
        service._client = mock_client
        service._active_project = "test-project"

        mock_wt_mgr = AsyncMock()
        mock_wt_mgr.create_worktree.return_value = None
        service._worktree_mgr = mock_wt_mgr

        await service._process_issue(issue)

        mock_client.update_issue_state.assert_not_called()


class TestScanOnce:
    async def test_filters_already_processed(self, service):
        """Already-processed issues are skipped."""
        issue = _make_issue()

        mock_client = AsyncMock()
        mock_client.get_auto_scan_issues.return_value = [issue]
        service._client = mock_client
        service._active_project = "test-project"
        service._processed_items = {issue.id}

        mock_wt_mgr = AsyncMock()
        service._worktree_mgr = mock_wt_mgr

        await service._scan_once()

        # No worktree creation should have happened
        mock_wt_mgr.create_worktree.assert_not_called()

    async def test_processes_new_issues(self, service, session_manager):
        """New issues are processed and added to the set."""
        issue = _make_issue()

        mock_client = AsyncMock()
        mock_client.get_auto_scan_issues.return_value = [issue]
        mock_client.update_issue_state.return_value = True
        mock_client.remove_issue_label.return_value = True
        service._client = mock_client
        service._active_project = "test-project"

        mock_wt_mgr = AsyncMock()
        mock_wt_mgr.create_worktree.return_value = None
        service._worktree_mgr = mock_wt_mgr

        await service._scan_once()

        assert issue.id in service._processed_items


class TestBuildClaudeCommand:
    """The argv is now built by the shared ClaudeLauncher; AutoScanService
    just delegates. Verify the delegation produces the expected shape."""

    async def test_argv_includes_skip_permissions(self, service):
        from claude_mobile.core.claude_launcher import ClaudeOptions

        spec = await service._launcher.build(
            service._config.projects["test-project"],
            ClaudeOptions(skip_permissions=True),
        )
        assert spec.argv[0] == "claude"
        assert "--allow-dangerously-skip-permissions" in spec.argv

    async def test_argv_with_prompt(self, service):
        from claude_mobile.core.claude_launcher import ClaudeOptions

        spec = await service._launcher.build(
            service._config.projects["test-project"],
            ClaudeOptions(skip_permissions=True, initial_prompt="Read it. Fix it."),
        )
        assert spec.argv[-2:] == ["--", "Read it. Fix it."]


class TestStartStop:
    async def test_start_validates_linear_auth(self, service):
        """start() calls get_viewer to validate auth."""
        mock_client = AsyncMock()
        mock_client.get_viewer.return_value = {"id": "u1", "name": "Test"}

        with patch(
            "claude_mobile.linear.scanner.LinearApiClient", return_value=mock_client
        ):
            await service.start("test-project")

        mock_client.get_viewer.assert_called_once()
        assert service._status == ScanStatus.SCANNING

        # Clean up
        await service.stop()
        assert service._status == ScanStatus.STOPPED

    async def test_start_rejects_unknown_project(self, service):
        with pytest.raises(ValueError, match="not found"):
            await service.start("nonexistent-project")

    async def test_start_rejects_double_start(self, service):
        mock_client = AsyncMock()
        mock_client.get_viewer.return_value = {"id": "u1", "name": "Test"}

        with patch(
            "claude_mobile.linear.scanner.LinearApiClient", return_value=mock_client
        ):
            await service.start("test-project")

        with pytest.raises(RuntimeError, match="already running"):
            await service.start("test-project")

        await service.stop()
