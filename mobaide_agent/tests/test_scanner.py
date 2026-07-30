"""Tests for AutoScanInstance — focused on the structured-event model
and the run-now wakeup mechanism introduced for CLA-22.

Mocks the Linear client, worktree manager, and session manager.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from typing import cast

from mobaide.core import ProjectsStore, SessionManager
from mobaide.core.projects_config import AutoScanFilter, ProjectConfig
from mobaide.linear.config import AutoScanConfig
from mobaide.linear.models import (
    IssueLabel,
    IssueState,
    LinearIssue,
    ScanStatus,
)
from mobaide.linear.scanner import AutoScanInstance


def _make_config(tmp_path, *, auto_remove_worktrees: bool = True) -> AutoScanConfig:
    return AutoScanConfig(
        worktree_base="mobaide_worktree",
        scan_interval_seconds=1,
        branch_prefix="feature/mobaide",
        main_branch="main",
        auto_scan_prompt=None,
        auto_remove_worktrees=auto_remove_worktrees,
        state_dir=str(tmp_path / "state"),
    )


def _make_project_config(
    tmp_path, *, auto_scan_filter: AutoScanFilter | None = None,
) -> ProjectConfig:
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
        auto_scan_filter=auto_scan_filter or AutoScanFilter(),
    )


class _FakeProjectsStore:
    """In-memory stand-in that exposes only ``get`` to the scanner.

    The scanner uses ``projects_store.get(name)`` once per cycle to refresh
    its filter snapshot. Tests mutate ``self.config`` to simulate a UI-level
    filter edit without rebuilding the whole instance.
    """

    def __init__(self, config: ProjectConfig | None) -> None:
        self.config = config

    def get(self, name: str) -> ProjectConfig | None:
        if self.config is None or self.config.name != name:
            return None
        return self.config


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


def _make_instance(
    tmp_path, *,
    client=None, worktree_mgr=None, session_manager=None,
    launcher=None, config=None, project_config=None,
    projects_store=None,
) -> AutoScanInstance:
    project_config = project_config or _make_project_config(tmp_path)
    store = projects_store or _FakeProjectsStore(project_config)
    return AutoScanInstance(
        project_name="test-project",
        project_config=project_config,
        config=config or _make_config(tmp_path),
        session_manager=session_manager or AsyncMock(spec=SessionManager),
        launcher=launcher or AsyncMock(),
        client=client or AsyncMock(),
        worktree_mgr=worktree_mgr or AsyncMock(),
        projects_store=cast("ProjectsStore", store),
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
        inst = _make_instance(tmp_path)
        # Build a fresh config with a long interval (AutoScanConfig is frozen).
        inst._config = inst._config.model_copy(update={"scan_interval_seconds": 30})

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

        sess_mgr = AsyncMock()
        session_obj = MagicMock()
        session_obj.pid = 999
        sess_mgr.create_session.return_value = session_obj

        # Each call to launcher.build returns a fresh spec so subsequent
        # _scan_once cycles don't clobber each other's cwd state.
        launcher = AsyncMock()
        launcher.build.side_effect = [
            MagicMock(argv=["claude"], env={}, cwd=str(tmp_path / "wt")),
            MagicMock(argv=["claude"], env={}, cwd=str(tmp_path / "wt")),
        ]

        inst = _make_instance(
            tmp_path,
            client=client,
            session_manager=sess_mgr,
            launcher=launcher,
        )

        await inst._scan_once()
        assert client.update_issue_state.call_count == 1

        # Second scan, no Run-Now: because the Linear query still returns
        # the issue, it must be processed AGAIN (the bug would have
        # skipped it silently).
        await inst._scan_once()
        assert client.update_issue_state.call_count == 2


def _launcher_with_worktree(path: str) -> AsyncMock:
    """Launcher stub that mirrors the new scanner → launcher contract.

    The launcher now owns worktree creation, so the scanner only sees
    ``spec.cwd`` — wired to the caller-supplied ``path`` here.
    """
    launcher = AsyncMock()
    spec = MagicMock()
    spec.argv = ["claude"]
    spec.env = {}
    spec.cwd = path
    launcher.build.return_value = spec
    return launcher


class TestProcessIssueEmitsPerStepEvents:
    @pytest.mark.asyncio
    async def test_emits_state_label_worktree_session_in_order(self, tmp_path):
        client = AsyncMock()
        client.update_issue_state.return_value = True
        client.remove_issue_label.return_value = True

        sess_mgr = AsyncMock()
        session_obj = MagicMock()
        session_obj.pid = 12345
        sess_mgr.create_session.return_value = session_obj

        launcher = _launcher_with_worktree(str(tmp_path / "worktree"))

        inst = _make_instance(
            tmp_path,
            client=client,
            session_manager=sess_mgr,
            launcher=launcher,
        )

        await inst._process_issue(_make_issue())

        steps = [
            (e.payload["step"], e.payload["status"])
            for e in inst._recent_events
            if e.type == "issue"
        ]
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

        types = [e.type for e in inst._recent_events]
        assert "worktree" in types
        assert "session" in types

    @pytest.mark.asyncio
    async def test_emits_worktree_failed_when_launcher_raises(self, tmp_path):
        """When the launcher raises ``WorktreeCreationError``, the scanner
        must emit ``worktree_create/failed`` (not ``/ok``), skip the session
        spawn for this issue, and let the poll loop continue."""
        from mobaide.linear.worktree import WorktreeCreationError

        client = AsyncMock()
        client.update_issue_state.return_value = True
        client.remove_issue_label.return_value = True

        sess_mgr = AsyncMock()

        launcher = AsyncMock()
        launcher.build.side_effect = WorktreeCreationError(
            branch="feature/mobaide/cla-8-test-issue", path="/x", stderr="boom",
        )

        inst = _make_instance(
            tmp_path,
            client=client,
            session_manager=sess_mgr,
            launcher=launcher,
        )

        await inst._process_issue(_make_issue())

        steps = [
            (e.payload["step"], e.payload["status"])
            for e in inst._recent_events
            if e.type == "issue"
        ]
        assert ("worktree_create", "failed") in steps
        assert ("session_spawn", "started") not in steps
        sess_mgr.create_session.assert_not_awaited()


class TestProcessIssueSetsSessionKindAutoScan:
    """Scanner-spawned sub-sessions must carry
    ``kind=SessionKind.AUTO_SCAN`` on the ``SessionConfig`` handed to
    ``SessionManager.create_session``. The default ``kind=CLAUDE`` would
    misclassify scanner sessions as regular Claude sessions in the UI list
    (Android renders the subtitle by ``session.kind``). This pins the
    explicit ``kind=`` override at the spawn site so a future refactor that
    drops it fails loudly here rather than silently in the mobile UI.
    """

    @pytest.mark.asyncio
    async def test_create_session_called_with_kind_auto_scan(self, tmp_path):
        from mobaide.core.models import SessionConfig, SessionKind

        client = AsyncMock()
        client.update_issue_state.return_value = True
        client.remove_issue_label.return_value = True

        sess_mgr = AsyncMock()
        session_obj = MagicMock()
        session_obj.pid = 4711
        sess_mgr.create_session.return_value = session_obj

        launcher = _launcher_with_worktree(str(tmp_path / "worktree"))

        inst = _make_instance(
            tmp_path,
            client=client,
            session_manager=sess_mgr,
            launcher=launcher,
        )

        await inst._process_issue(_make_issue())

        # create_session is called exactly once with a SessionConfig whose
        # kind is AUTO_SCAN — not the CLAUDE default that the dataclass
        # would silently apply if the explicit assignment regressed.
        sess_mgr.create_session.assert_awaited_once()
        call = sess_mgr.create_session.await_args
        assert call is not None
        config: SessionConfig = call.args[0] if call.args else call.kwargs["config"]
        assert isinstance(config, SessionConfig)
        assert config.kind is SessionKind.AUTO_SCAN
        # Sanity: the rest of the launch surface still comes from the
        # launcher spec — kind is the only field this test pins.
        assert config.command == ["claude"]


class TestClaudeBinaryNotFoundHandling:
    """Coverage for the ``ClaudeBinaryNotFoundError`` path added to keep
    journalctl from being flooded when the Claude CLI is missing.

    Two layers cooperate:
    1. ``_process_issue`` emits a ``session_spawn/failed`` issue event with
       a stable ``error="Claude CLI not found"`` string and re-raises so
       the outer cycle can short-circuit.
    2. ``_scan_once`` catches the re-raised error, emits a single
       operator-facing ``log`` event, and breaks out of the per-issue
       loop — the next issue in the same scan cycle is NEVER processed,
       which is the actual bug fix (without this break, every issue in
       the cycle would re-spawn the same FileNotFoundError chain and
       spam the journal).
    """

    @pytest.mark.asyncio
    async def test_process_issue_emits_session_spawn_failed_and_reraises(
        self, tmp_path,
    ):
        from mobaide.core import ClaudeBinaryNotFoundError

        client = AsyncMock()
        client.update_issue_state.return_value = True
        client.remove_issue_label.return_value = True

        sess_mgr = AsyncMock()

        launcher = AsyncMock()
        launcher.build.side_effect = ClaudeBinaryNotFoundError(
            searched_paths=["PATH:claude", "/home/test/.npm-global/bin/claude"],
        )

        inst = _make_instance(
            tmp_path,
            client=client,
            session_manager=sess_mgr,
            launcher=launcher,
        )

        with pytest.raises(ClaudeBinaryNotFoundError):
            await inst._process_issue(_make_issue(identifier="CLA-MISS"))

        steps = [
            (e.payload["step"], e.payload["status"], e.payload.get("error"))
            for e in inst._recent_events
            if e.type == "issue"
        ]
        assert ("session_spawn", "failed", "Claude CLI not found") in steps
        # State-machine completeness: ``worktree_create`` must reach a
        # terminal status (``failed``) so consumers building off the event
        # stream don't see a dangling ``started``. ``ok`` would be a lie
        # since launcher.build short-circuited on argv resolution before
        # any worktree was created.
        assert ("worktree_create", "failed", "Claude CLI not found") in steps
        assert ("worktree_create", "ok") not in [(s, st) for s, st, _ in steps]
        sess_mgr.create_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_scan_once_breaks_loop_on_first_claude_cli_missing(
        self, tmp_path,
    ):
        """The bug fix: after the first ``ClaudeBinaryNotFoundError`` in a
        scan cycle, the remaining issues in that cycle must be skipped.
        Without the break, the launcher would be invoked for every queued
        issue and each invocation would spam the same error into journalctl.
        """
        from mobaide.core import ClaudeBinaryNotFoundError

        first = _make_issue(issue_id="uuid-1", identifier="CLA-FIRST")
        second = _make_issue(issue_id="uuid-2", identifier="CLA-SECOND")

        client = AsyncMock()
        client.get_auto_scan_issues.return_value = [first, second]
        client.update_issue_state.return_value = True
        client.remove_issue_label.return_value = True

        sess_mgr = AsyncMock()

        launcher = AsyncMock()
        launcher.build.side_effect = ClaudeBinaryNotFoundError(
            searched_paths=["PATH:claude"],
        )

        inst = _make_instance(
            tmp_path,
            client=client,
            session_manager=sess_mgr,
            launcher=launcher,
        )

        await inst._scan_once()

        # The launcher was invoked exactly once — for the first issue. The
        # second issue must NOT have triggered another build attempt.
        assert launcher.build.await_count == 1

        # Issue events were only emitted for the first issue's identifier.
        issue_identifiers = {
            e.payload.get("identifier")
            for e in inst._recent_events
            if e.type == "issue"
        }
        assert "CLA-FIRST" in issue_identifiers
        assert "CLA-SECOND" not in issue_identifiers

    @pytest.mark.asyncio
    async def test_scan_once_emits_actionable_log_when_claude_cli_missing(
        self, tmp_path,
    ):
        """The operator-facing ``log`` event must mention both the
        ``MOBAIDE_CLAUDE_BIN`` override and ``claude-code`` install hint so
        a sysadmin reading the live event stream knows what to do —
        otherwise the only signal is a generic 'aborted' phase event.
        """
        from mobaide.core import ClaudeBinaryNotFoundError

        issue = _make_issue(identifier="CLA-LOG")

        client = AsyncMock()
        client.get_auto_scan_issues.return_value = [issue]
        client.update_issue_state.return_value = True
        client.remove_issue_label.return_value = True

        sess_mgr = AsyncMock()

        launcher = AsyncMock()
        launcher.build.side_effect = ClaudeBinaryNotFoundError(
            searched_paths=["PATH:claude"],
        )

        inst = _make_instance(
            tmp_path,
            client=client,
            session_manager=sess_mgr,
            launcher=launcher,
        )

        await inst._scan_once()

        log_events = [
            e for e in inst._recent_events
            if e.type == "log" and e.payload.get("level") == "error"
        ]
        cli_log_lines = [
            e.payload.get("line", "") for e in log_events
            if "Claude CLI not found" in e.payload.get("line", "")
        ]
        assert cli_log_lines, "expected an error-level log event mentioning Claude CLI"
        line = cli_log_lines[0]
        assert "MOBAIDE_CLAUDE_BIN" in line
        assert "claude-code" in line


class TestProcessIssueForwardsClientOptions:
    """The scanner's spawn path merges a ``ClaudeOptions`` from the client's
    stored preferences with per-issue fields (identifier/title). The Claude
    reasoning options (model/effort/...) must round-trip so that a user who
    picked, say, a lower effort on the Auto Scan config screen actually gets a
    session launched with it. Nothing else in the suite asserts what
    ``launcher.build`` receives, so this class is the regression boundary for
    that wire-up."""

    @pytest.mark.asyncio
    async def test_process_issue_propagates_effort_from_client_options(
        self, tmp_path,
    ):
        from mobaide.core.claude_launcher import ClaudeOptions

        client = AsyncMock()
        client.update_issue_state.return_value = True
        client.remove_issue_label.return_value = True

        sess_mgr = AsyncMock()
        sess_mgr.create_session.return_value = MagicMock(pid=1)

        launcher = _launcher_with_worktree(str(tmp_path / "worktree"))

        inst = _make_instance(
            tmp_path, client=client,
            session_manager=sess_mgr, launcher=launcher,
        )
        # Simulate the Auto Scan config-screen submit path where the user
        # picked a non-default model + effort. The HTTP router stores the
        # client-supplied options on ``_client_options``; the scanner reads them
        # when it spawns a session for a matching issue.
        inst._client_options = ClaudeOptions(effort="low", model="opus")

        await inst._process_issue(_make_issue(issue_id="uuid-off", identifier="CLA-OFF"))

        # launcher.build was called with (project, options). Pull the
        # second positional arg regardless of whether it was passed by
        # keyword.
        call = launcher.build.await_args
        forwarded = call.args[1] if len(call.args) > 1 else call.kwargs["options"]
        assert forwarded.effort == "low"
        assert forwarded.model == "opus"
        # And the per-issue fields must still be set correctly.
        assert forwarded.identifier == "CLA-OFF"
        assert forwarded.title == "Test issue"

    @pytest.mark.asyncio
    async def test_process_issue_defaults_effort_none_when_no_client_options(
        self, tmp_path,
    ):
        """Covers the default branch: when the scanner was started without any
        stored client options (e.g. server restart before any mobile client
        submitted), the spawned session falls back to ``ClaudeOptions()``
        defaults (effort None ⇒ no ``--effort`` flag)."""
        client = AsyncMock()
        client.update_issue_state.return_value = True
        client.remove_issue_label.return_value = True

        sess_mgr = AsyncMock()
        sess_mgr.create_session.return_value = MagicMock(pid=1)

        launcher = _launcher_with_worktree(str(tmp_path / "worktree"))

        inst = _make_instance(
            tmp_path, client=client,
            session_manager=sess_mgr, launcher=launcher,
        )
        assert inst._client_options is None

        await inst._process_issue(_make_issue(issue_id="uuid-def", identifier="CLA-DEF"))

        call = launcher.build.await_args
        forwarded = call.args[1] if len(call.args) > 1 else call.kwargs["options"]
        assert forwarded.effort is None
        assert forwarded.worktree is True, (
            "scanner must always set worktree=True so the launcher creates "
            "the per-issue worktree — regression guard for the dropped-field bug"
        )

    @pytest.mark.asyncio
    async def test_process_issue_forces_skip_permissions_true_even_if_client_disables_it(
        self, tmp_path,
    ):
        """Auto Scan's build() call carries no session_id, so it never gets the
        permission-attention hooks a live terminal relies on — an unattended
        scan that hit a real approval prompt would hang forever. The scanner
        must force bypass mode regardless of what the client submitted."""
        from mobaide.core.claude_launcher import ClaudeOptions

        client = AsyncMock()
        client.update_issue_state.return_value = True
        client.remove_issue_label.return_value = True

        sess_mgr = AsyncMock()
        sess_mgr.create_session.return_value = MagicMock(pid=1)

        launcher = _launcher_with_worktree(str(tmp_path / "worktree"))

        inst = _make_instance(
            tmp_path, client=client,
            session_manager=sess_mgr, launcher=launcher,
        )
        inst._client_options = ClaudeOptions(skip_permissions=False)

        await inst._process_issue(_make_issue(issue_id="uuid-noskip", identifier="CLA-NOSKIP"))

        call = launcher.build.await_args
        forwarded = call.args[1] if len(call.args) > 1 else call.kwargs["options"]
        assert forwarded.skip_permissions is True

    @pytest.mark.asyncio
    async def test_process_issue_uses_project_prompt_when_no_client_prompt(
        self, tmp_path,
    ):
        """When no per-scan prompt is supplied (optionless start), the scanner
        falls back to the project's saved ``auto_scan_prompt`` before the
        engine-wide default."""
        client = AsyncMock()
        client.update_issue_state.return_value = True
        client.remove_issue_label.return_value = True

        sess_mgr = AsyncMock()
        sess_mgr.create_session.return_value = MagicMock(pid=1)

        launcher = _launcher_with_worktree(str(tmp_path / "worktree"))

        project_config = ProjectConfig(
            name="test-project",
            path=Path(tmp_path / "repo").as_posix(),
            linear_api_key="lin_api_test",
            auto_scan_prompt="PROJECT SAVED PROMPT",
        )
        config = AutoScanConfig(
            worktree_base="mobaide_worktree",
            scan_interval_seconds=1,
            branch_prefix="feature/mobaide",
            main_branch="main",
            auto_scan_prompt="ENGINE DEFAULT",
            auto_remove_worktrees=False,
            state_dir=str(tmp_path / "state"),
        )

        inst = _make_instance(
            tmp_path, client=client,
            session_manager=sess_mgr, launcher=launcher,
            config=config, project_config=project_config,
        )
        assert inst._client_options is None  # optionless start

        await inst._process_issue(_make_issue(issue_id="uuid-pp", identifier="CLA-PP"))

        call = launcher.build.await_args
        forwarded = call.args[1] if len(call.args) > 1 else call.kwargs["options"]
        assert forwarded.initial_prompt == "PROJECT SAVED PROMPT"

    @pytest.mark.asyncio
    async def test_process_issue_client_prompt_wins_over_project_prompt(
        self, tmp_path,
    ):
        """An explicit per-scan prompt takes precedence over both the project's
        saved prompt and the engine-wide default."""
        from mobaide.core.claude_launcher import ClaudeOptions

        client = AsyncMock()
        client.update_issue_state.return_value = True
        client.remove_issue_label.return_value = True

        sess_mgr = AsyncMock()
        sess_mgr.create_session.return_value = MagicMock(pid=1)

        launcher = _launcher_with_worktree(str(tmp_path / "worktree"))

        project_config = ProjectConfig(
            name="test-project",
            path=Path(tmp_path / "repo").as_posix(),
            linear_api_key="lin_api_test",
            auto_scan_prompt="PROJECT SAVED PROMPT",
        )

        inst = _make_instance(
            tmp_path, client=client,
            session_manager=sess_mgr, launcher=launcher,
            project_config=project_config,
        )
        inst._client_options = ClaudeOptions(initial_prompt="PER SCAN PROMPT")

        await inst._process_issue(_make_issue(issue_id="uuid-cp", identifier="CLA-CP"))

        call = launcher.build.await_args
        forwarded = call.args[1] if len(call.args) > 1 else call.kwargs["options"]
        assert forwarded.initial_prompt == "PER SCAN PROMPT"


class TestWorktreeCleanup:
    """Coverage for the active_workitems.json + cleanup-on-Done feature."""

    @pytest.mark.asyncio
    async def test_process_issue_persists_workitem_when_worktree_created(self, tmp_path):
        client = AsyncMock()
        client.update_issue_state.return_value = True
        client.remove_issue_label.return_value = True

        sess_mgr = AsyncMock()
        session_obj = MagicMock()
        session_obj.pid = 1234
        sess_mgr.create_session.return_value = session_obj

        launcher = _launcher_with_worktree(str(tmp_path / "worktree"))

        inst = _make_instance(
            tmp_path, client=client,
            session_manager=sess_mgr, launcher=launcher,
        )

        await inst._process_issue(_make_issue(issue_id="uuid-1", identifier="CLA-100"))

        items = await inst._workitems_store.read()
        assert len(items) == 1
        assert items[0].id == "CLA-100"
        assert items[0].uuid == "uuid-1"
        assert items[0].worktree_path == Path(tmp_path / "worktree").as_posix()

    @pytest.mark.asyncio
    async def test_process_issue_dedups_on_repeat_persistence(self, tmp_path):
        """If the same issue is processed twice (the bug-fix scenario),
        the JSON must not contain duplicate entries."""
        client = AsyncMock()
        client.update_issue_state.return_value = True
        client.remove_issue_label.return_value = True

        sess_mgr = AsyncMock()
        session_obj = MagicMock()
        session_obj.pid = 1234
        sess_mgr.create_session.return_value = session_obj

        launcher = _launcher_with_worktree(str(tmp_path / "worktree"))

        inst = _make_instance(
            tmp_path, client=client,
            session_manager=sess_mgr, launcher=launcher,
        )

        issue = _make_issue(issue_id="uuid-x", identifier="CLA-200")
        await inst._process_issue(issue)
        await inst._process_issue(issue)

        items = await inst._workitems_store.read()
        assert len(items) == 1

    @pytest.mark.asyncio
    async def test_process_issue_does_not_persist_when_worktree_creation_fails(self, tmp_path):
        """Worktree creation failure surfaces as ``WorktreeCreationError`` from
        the launcher — the scanner must skip both the persistence step AND the
        session spawn for this issue, but keep the poll loop alive."""
        from mobaide.linear.worktree import WorktreeCreationError

        client = AsyncMock()
        client.update_issue_state.return_value = True
        client.remove_issue_label.return_value = True

        sess_mgr = AsyncMock()

        launcher = AsyncMock()
        launcher.build.side_effect = WorktreeCreationError(
            branch="feature/mobaide/cla-noWT-test-issue", path="/x", stderr="boom",
        )

        inst = _make_instance(
            tmp_path, client=client,
            session_manager=sess_mgr, launcher=launcher,
        )

        await inst._process_issue(_make_issue(issue_id="uuid-noWT", identifier="CLA-noWT"))

        assert await inst._workitems_store.count() == 0
        sess_mgr.create_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cleanup_removes_worktree_when_issue_state_is_done(self, tmp_path):
        from mobaide.linear.workitems import ActiveWorkItem, now_iso_utc

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
        from mobaide.linear.workitems import ActiveWorkItem, now_iso_utc

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
        from mobaide.linear.workitems import ActiveWorkItem, now_iso_utc

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
        from mobaide.linear.workitems import ActiveWorkItem, now_iso_utc

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
        from mobaide.linear.workitems import ActiveWorkItem, now_iso_utc

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
        from mobaide.linear.workitems import ActiveWorkItem, now_iso_utc

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
        from mobaide.linear.workitems import ActiveWorkItem, now_iso_utc

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
        from mobaide.linear.workitems import ActiveWorkItem, now_iso_utc

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

        sess_mgr = AsyncMock()
        sess_mgr.create_session.return_value = MagicMock(pid=7)

        launcher = _launcher_with_worktree(str(tmp_path / "worktree"))

        config = _make_config(tmp_path, auto_remove_worktrees=False)
        inst = _make_instance(
            tmp_path, client=client,
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
        wt_mgr.cleanup_worktree.return_value = True

        sess_mgr = AsyncMock()
        sess_mgr.create_session.return_value = MagicMock(pid=1)

        launcher = _launcher_with_worktree(str(tmp_path / "wt"))

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
        from mobaide.linear.workitems import ActiveWorkItem, now_iso_utc

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
        from mobaide.linear.workitems import ActiveWorkItem, now_iso_utc

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
        from mobaide.linear.workitems import (
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
        config = config.model_copy(update={"state_dir": str(state_dir)})

        inst = _make_instance(tmp_path, config=config)
        assert inst.to_dto()["processed_count"] == 1


class TestStopEmitsPhase:
    @pytest.mark.asyncio
    async def test_stop_emits_scan_stopped_phase(self, tmp_path):
        inst = _make_instance(tmp_path)
        await inst.stop()
        phase_events = [e for e in inst._recent_events if e.type == "phase"]
        assert any(e.payload["phase"] == "scan_stopped" for e in phase_events)


class TestClientOptionsTypeGuard:
    """``AutoScanInstance.__init__`` raises ``TypeError`` if ``client_options``
    is anything other than a ``ClaudeOptions`` dataclass or ``None``.

    Regression boundary for the Auto Scan relay-mode bug where a plain
    ``dict`` was slipping through the API edge and surfacing deep inside
    ``_process_issue`` as ``'dict' object has no attribute 'plan_mode'``.
    The constructor-level guard is the domain-boundary backstop: if any
    future transport (a new relay handler, a test harness, a CLI) ever
    hands in a raw dict again, it must fail loudly at construction with a
    message that names the offending type — not silently reach
    ``base.plan_mode`` three call-stacks deep.
    """

    @staticmethod
    def _build_instance(tmp_path, client_options: Any) -> AutoScanInstance:
        """Build ``AutoScanInstance`` directly with the guard's input under
        test. The module-local ``_make_instance`` helper swallows the
        ``client_options`` kwarg, so these tests construct explicitly.
        ``client_options`` is typed ``Any`` at the call boundary so the
        tests can pass the very inputs the guard is supposed to reject
        (dict, str) without fighting the static type checker."""
        project_config = _make_project_config(tmp_path)
        return AutoScanInstance(
            project_name="test-project",
            project_config=project_config,
            config=_make_config(tmp_path),
            session_manager=AsyncMock(spec=SessionManager),
            launcher=AsyncMock(),
            client=AsyncMock(),
            worktree_mgr=AsyncMock(),
            projects_store=cast("ProjectsStore", _FakeProjectsStore(project_config)),
            client_options=client_options,
        )

    def test_dict_raises_typeerror_with_type_name_in_message(self, tmp_path):
        """Passing a ``dict`` (the exact regression shape) must raise
        ``TypeError``; the message must include ``dict`` so an operator
        reading the log can pinpoint the offending type immediately."""
        with pytest.raises(TypeError) as excinfo:
            self._build_instance(tmp_path, {"plan_mode": True})

        msg = str(excinfo.value)
        assert "dict" in msg
        assert "ClaudeOptions" in msg

    def test_none_is_accepted(self, tmp_path):
        """``client_options=None`` is the happy default path — no exception,
        and the instance stores ``None`` on its private attribute."""
        inst = self._build_instance(tmp_path, None)
        assert inst._client_options is None

    def test_real_claude_options_is_accepted_and_stored(self, tmp_path):
        """A genuine ``ClaudeOptions`` dataclass must pass the guard and be
        stored verbatim — the guard must not accidentally reject the
        valid case (would break both direct and fixed relay paths)."""
        from mobaide.core.claude_launcher import ClaudeOptions

        opts = ClaudeOptions(plan_mode=True, effort="low")
        inst = self._build_instance(tmp_path, opts)
        assert inst._client_options is opts

    def test_arbitrary_type_raises_typeerror_naming_that_type(self, tmp_path):
        """Guard is a pure isinstance check: ANY non-None non-ClaudeOptions
        value must be rejected with its type name in the error message,
        not just ``dict``. This pins the guard's breadth so a future
        refactor to a narrower check (e.g. ``isinstance(x, (dict,))``)
        cannot silently regress.
        """
        with pytest.raises(TypeError) as excinfo:
            self._build_instance(tmp_path, "not options")

        msg = str(excinfo.value)
        assert "str" in msg
        assert "ClaudeOptions" in msg


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

        from mobaide.agent.logging import (
            bind_request_context,
            clear_request_context,
        )

        # Simulate an HTTP request binding its context (mirrors what the
        # middleware at mobaide_agent/mobaide/agent/app.py:98 does on every request).
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


class TestPerProjectFilter:
    """The scanner reads filter values from the project's
    ``auto_scan_filter``, not from hardcoded literals or global env."""

    @pytest.mark.asyncio
    async def test_scan_once_passes_filter_to_client(self, tmp_path):
        client = AsyncMock()
        client.get_auto_scan_issues.return_value = []
        custom = AutoScanFilter(
            state_names=["Backlog", "Proposals"],
            label_names=["AI-ready"],
            active_state="Doing",
            done_state="Released",
            removal_label="AI-ready",
        )
        project_config = _make_project_config(tmp_path, auto_scan_filter=custom)
        inst = _make_instance(
            tmp_path,
            client=client,
            project_config=project_config,
        )

        await inst._scan_once()

        # The client received the project's filter — not a default-built one.
        assert client.get_auto_scan_issues.await_count == 1
        passed = client.get_auto_scan_issues.await_args.args[0]
        assert isinstance(passed, AutoScanFilter)
        assert passed == custom

    @pytest.mark.asyncio
    async def test_process_issue_uses_active_state_from_filter(self, tmp_path):
        client = AsyncMock()
        client.update_issue_state.return_value = True
        client.remove_issue_label.return_value = True

        custom = AutoScanFilter(active_state="Doing")
        project_config = _make_project_config(tmp_path, auto_scan_filter=custom)
        inst = _make_instance(
            tmp_path,
            client=client,
            project_config=project_config,
            launcher=_launcher_with_worktree(str(tmp_path / "wt")),
        )
        # Inject the team-state set the scanner will look up.
        issue = LinearIssue(
            id="uuid-1",
            identifier="CLA-9",
            title="Test",
            description=None,
            state=IssueState(id="s-prop", name="Proposals"),
            labels=[IssueLabel(id="lab-auto", name="AutoPipeline")],
            team_states=[
                IssueState(id="s-prop", name="Proposals"),
                IssueState(id="s-doing", name="Doing"),
                IssueState(id="s-done", name="Done"),
            ],
        )

        await inst._process_issue(issue)

        # update_issue_state was called with the "Doing" state's id, not
        # the historical "In Progress".
        assert client.update_issue_state.await_count == 1
        called_state = client.update_issue_state.await_args.args[1]
        assert called_state == "s-doing"

    @pytest.mark.asyncio
    async def test_process_issue_uses_removal_label_from_filter(self, tmp_path):
        client = AsyncMock()
        client.update_issue_state.return_value = True
        client.remove_issue_label.return_value = True

        custom = AutoScanFilter(removal_label="AI-ready")
        project_config = _make_project_config(tmp_path, auto_scan_filter=custom)
        inst = _make_instance(
            tmp_path,
            client=client,
            project_config=project_config,
            launcher=_launcher_with_worktree(str(tmp_path / "wt")),
        )
        # Issue carries both labels — scanner must strip ONLY the
        # configured ``removal_label``.
        issue = LinearIssue(
            id="uuid-1",
            identifier="CLA-9",
            title="Test",
            description=None,
            state=IssueState(id="s-prop", name="Proposals"),
            labels=[
                IssueLabel(id="lab-auto", name="AutoPipeline"),
                IssueLabel(id="lab-ai", name="AI-ready"),
            ],
            team_states=[
                IssueState(id="s-prop", name="Proposals"),
                IssueState(id="s-doing", name="In Progress"),
            ],
        )

        await inst._process_issue(issue)

        assert client.remove_issue_label.await_count == 1
        called_label = client.remove_issue_label.await_args.args[1]
        assert called_label == "lab-ai"

    @pytest.mark.asyncio
    async def test_filter_refreshes_from_store_between_cycles(self, tmp_path):
        """Editing the project's filter via the store takes effect on the
        next ``_scan_once`` call without a restart — the snapshot at
        construction time is intentionally NOT load-bearing for filters."""
        client = AsyncMock()
        client.get_auto_scan_issues.return_value = []

        starting = AutoScanFilter(state_names=["Proposals"])
        project_config = _make_project_config(tmp_path, auto_scan_filter=starting)
        store = _FakeProjectsStore(project_config)
        inst = _make_instance(
            tmp_path,
            client=client,
            project_config=project_config,
            projects_store=store,
        )

        await inst._scan_once()
        first_filter = client.get_auto_scan_issues.await_args.args[0]
        assert first_filter.state_names == ["Proposals"]

        # Simulate a UI edit: replace the project_config in the store.
        new_filter = AutoScanFilter(state_names=["Backlog"])
        store.config = ProjectConfig(
            name=project_config.name,
            path=project_config.path,
            linear_api_key=project_config.linear_api_key,
            auto_scan_filter=new_filter,
        )

        await inst._scan_once()
        second_filter = client.get_auto_scan_issues.await_args.args[0]
        assert second_filter.state_names == ["Backlog"]

    @pytest.mark.asyncio
    async def test_cleanup_uses_done_state_from_filter(self, tmp_path):
        # Simplest path: with no tracked workitems, the cleanup helper
        # exits early but still reads ``self._filter.done_state``. We pin
        # that the access is via the filter (not a removed config field)
        # by ensuring the call doesn't raise AttributeError.
        custom = AutoScanFilter(done_state="Released")
        project_config = _make_project_config(tmp_path, auto_scan_filter=custom)
        inst = _make_instance(tmp_path, project_config=project_config)

        await inst._cleanup_completed_worktrees()
        assert inst._filter.done_state == "Released"
