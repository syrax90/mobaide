"""Each agent's own Auto Scan options: which options a start request keeps,
the command line each issue's session gets from them, and how a workspace
stores them and reads them back.

The real launchers build each argv (their binaries are pinned by
``conftest.py``); the issue source, worktree manager and session manager are
hand-written fakes. The workspaces store writes a real JSON file under
``tmp_path``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from mobaide.agent.autoscan.models import StartScanRequest
from mobaide.agent.sessions.models import (
    AntigravityScanOptionsModel,
    ClaudeScanOptionsModel,
    CodexScanOptionsModel,
    CopilotScanOptionsModel,
    CursorScanOptionsModel,
    JunieScanOptionsModel,
    OpenCodeScanOptionsModel,
    PiScanOptionsModel,
)
from mobaide.core import SessionManager, WorkspaceRecord, WorkspacesStore, codex_launcher
from mobaide.core.antigravity_launcher import AntigravityLauncher
from mobaide.core.codex_launcher import CodexLauncher
from mobaide.core.junie_launcher import JunieLauncher
from mobaide.core.pi_launcher import PiLauncher
from mobaide.core.workspaces_config import AUTO_SCAN_OPTION_FIELDS, WorkspaceConfig
from mobaide.core.workspaces_store import WorkspaceUpdatePatch
from mobaide.linear import scanner
from mobaide.linear.config import AutoScanConfig
from mobaide.linear.scanner import AutoScanInstance
from mobaide.linear.sources import ScanIssue
from mobaide.linear.worktree import WorktreeManager, WorktreeResult


@pytest.mark.parametrize(
    ("cli", "model"),
    [
        ("claude", ClaudeScanOptionsModel),
        ("codex", CodexScanOptionsModel),
        ("antigravity", AntigravityScanOptionsModel),
        ("opencode", OpenCodeScanOptionsModel),
        ("copilot", CopilotScanOptionsModel),
        ("cursor", CursorScanOptionsModel),
        ("junie", JunieScanOptionsModel),
        ("pi", PiScanOptionsModel),
    ],
)
def test_the_store_keeps_every_option_the_wire_model_has(cli, model):
    # A field missing here is silently dropped from every saved start.
    assert set(AUTO_SCAN_OPTION_FIELDS[cli]) == set(model.model_fields)


@pytest.mark.parametrize(
    ("wire", "expected"),
    [
        pytest.param(
            {"cli": "codex", "codex_options": {"model": "gpt-5.5", "full_auto": False},
             "copilot_options": {"allow_all": True}, "claude_options": {"model": "opus"}},
            CodexScanOptionsModel(model="gpt-5.5", full_auto=False).model_dump(),
            id="only-the-chosen-agent",
        ),
        pytest.param(
            {"cli": "claude", "claude_options": {
                "model": "opus", "plan_mode": True, "code_mode": True, "initial_prompt": "x",
                "worktree": True, "branch_name": "b", "base_branch": "main", "identifier": "i",
                "title": "t",
            }},
            ClaudeScanOptionsModel(model="opus", plan_mode=True, code_mode=True).model_dump(),
            id="claude-without-the-per-launch-fields",
        ),
        pytest.param(
            {"cli": "copilot", "codex_options": {"full_auto": True}}, None,
            id="none-sent-for-the-chosen-agent",
        ),
    ],
)
def test_start_request_keeps_the_options_of_the_chosen_agent(wire, expected):
    request = StartScanRequest.model_validate(
        {"project_name": "alpha", "source": "github", **wire},
    )

    assert request.to_start().agent_options == expected


class _OneIssueSource:
    kind = "github"
    label = "GitHub"

    async def connect(self) -> str:
        return "octocat on acme/app"

    def refresh(self, workspace) -> None:
        pass

    def describe(self) -> str:
        return "labels=['ai']"

    async def fetch(self) -> list[ScanIssue]:
        return [ScanIssue(
            key="7", identifier="#7", title="Fix it", description="Steps",
            url="https://github.com/acme/app/issues/7", slug_seed="gh-7",
        )]

    async def claim(self, issue, emit) -> None:
        emit(issue, "label_removal", "ok", None)

    async def is_done(self, item) -> bool | None:
        return None


class _FakeWorktrees:
    def __init__(self, root: Path) -> None:
        self._root = root

    async def create_worktree(self, slug, workspace_root, **_kwargs) -> WorktreeResult:
        path = self._root / slug
        path.mkdir(parents=True)
        return WorktreeResult(path=path.as_posix(), is_new=True, branch=f"feature/mobaide/{slug}")

    async def cleanup_worktree(self, path: str, workspace_root: str, *, force: bool = True) -> bool:
        return True


class _RecordingSessions:
    def __init__(self) -> None:
        self.configs: list = []

    async def create_session(self, config):
        self.configs.append(config)
        return type("Session", (), {"pid": 4242})()


class _Store:
    def __init__(self, workspace: WorkspaceConfig) -> None:
        self._workspace = workspace

    def get(self, name: str) -> WorkspaceConfig | None:
        return self._workspace if name == self._workspace.name else None


@pytest.fixture(autouse=True)
def _codex_signed_in(monkeypatch):
    async def present(*_args, **_kwargs) -> bool:
        return True

    monkeypatch.setattr(codex_launcher, "codex_auth_present", present)


async def _launch(tmp_path: Path, cli: str, launcher, options) -> list:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    workspace = WorkspaceConfig(name="app", path=repo.as_posix())
    sessions = _RecordingSessions()
    inst = AutoScanInstance(
        workspace_name="app",
        workspace_config=workspace,
        config=AutoScanConfig(
            worktrees_dir=str(tmp_path / "worktrees"),
            worktree_base="mobaide_worktree",
            scan_interval_seconds=1,
            branch_prefix="feature/mobaide",
            main_branch="main",
            auto_remove_worktrees=True,
            state_dir=str(tmp_path / "state"),
        ),
        session_manager=cast("SessionManager", sessions),
        launcher=cast("scanner.ClaudeLauncher", object()),
        worktree_mgr=cast("WorktreeManager", _FakeWorktrees(tmp_path / "worktrees")),
        workspaces_store=cast("WorkspacesStore", _Store(workspace)),
        source=_OneIssueSource(),
        cli=cli,
        launchers={cli: launcher},
        agent_options=options,
        initial_prompt="Go.",
    )

    await inst._scan_once()

    return sessions.configs


def _has(command: list[str], *args: str) -> bool:
    n = len(args)
    return any(command[i:i + n] == list(args) for i in range(len(command) - n + 1))


@pytest.mark.parametrize(
    ("cli", "make_launcher", "options", "present", "absent"),
    [
        pytest.param("codex", CodexLauncher, None,
                     [("--dangerously-bypass-approvals-and-sandbox",)], [("--sandbox",)],
                     id="codex-none-sent-runs-without-asking"),
        pytest.param("codex", CodexLauncher,
                     {"model": "gpt-5.5", "full_auto": False, "sandbox": "read-only",
                      "approval": "on-request", "reasoning_effort": "low"},
                     [("--model", "gpt-5.5"), ("--sandbox", "read-only"),
                      ("--ask-for-approval", "on-request"), ("-c", "model_reasoning_effort=low")],
                     [("--dangerously-bypass-approvals-and-sandbox",)],
                     id="codex-chosen-options"),
        pytest.param("codex", CodexLauncher, {"full_auto": False, "approval": "untrusted"},
                     [("--ask-for-approval", "on-request")],
                     [("--ask-for-approval", "untrusted")],
                     id="codex-saved-retired-untrusted-approval"),
        pytest.param("antigravity", AntigravityLauncher,
                     {"model": "Gemini 3.1 Pro (Low)", "skip_permissions": False,
                      "sandbox": True, "add_dirs": ["/srv/shared"]},
                     [("--model", "Gemini 3.1 Pro (Low)"), ("--sandbox",),
                      ("--add-dir", "/srv/shared")],
                     [("--dangerously-skip-permissions",)],
                     id="antigravity-chosen-options"),
        pytest.param("junie", JunieLauncher, None, [("--brave",)], [("--plan",)],
                     id="junie-none-sent-runs-without-asking"),
        pytest.param("junie", JunieLauncher, {"plan_mode": True, "brave": False},
                     [("--plan",)], [("--brave",)], id="junie-chosen-options"),
        pytest.param("pi", PiLauncher, None, [], [("--tools", "+codemode")],
                     id="pi-none-sent-runs-without-code-mode"),
        pytest.param("pi", PiLauncher, {"code_mode": True}, [("--tools", "+codemode")], [],
                     id="pi-code-mode"),
    ],
)
async def test_each_issue_session_runs_with_the_agents_scan_options(
    tmp_path, cli, make_launcher, options, present, absent,
):
    [config] = await _launch(tmp_path, cli, make_launcher(), options)

    for args in present:
        assert _has(config.command, *args), args
    for args in absent:
        assert not _has(config.command, *args), args


async def test_per_launch_fields_in_the_options_do_not_change_the_issue_session(tmp_path):
    options = {"full_auto": True, "worktree": True, "branch_name": "other",
               "initial_prompt": "Not the issue."}

    [config] = await _launch(tmp_path, "codex", CodexLauncher(), options)

    # Each issue runs in its own worktree with its own first message.
    assert config.cwd == (tmp_path / "worktrees" / "gh-7-fix-it").as_posix()
    assert config.command[-1].startswith("Go.\n\nGitHub issue #7: Fix it")


def _store_with(tmp_path, options: object) -> WorkspacesStore:
    path = tmp_path / "projects.json"
    path.write_text(json.dumps([
        {"name": "alpha", "path": str(tmp_path), "auto_scan_options": options},
    ]), encoding="utf-8")
    store = WorkspacesStore(str(path))
    store.load()
    return store


def _saved(store: WorkspacesStore) -> dict:
    [row] = store.list_public()
    return row["auto_scan"]  # type: ignore[return-value]


class TestStoredOptions:
    def test_a_stored_record_keeps_only_known_kinds_fields_and_json_types(self, tmp_path):
        store = _store_with(tmp_path, {
            "codex": {"full_auto": "yes", "model": None, "sandbox": "read-only", "worktree": True},
            "aider": {"yolo": True},
            "copilot": "not-an-object",
            "antigravity": {"add_dirs": ["/a", 3], "skip_permissions": True},
        })

        saved = _saved(store)

        assert saved["codex_options"] == {"model": None, "sandbox": "read-only"}
        assert saved["antigravity_options"] == {"skip_permissions": True}
        assert saved["copilot_options"] is None
        assert "aider_options" not in saved

    def test_an_update_replaces_the_kinds_it_names_and_keeps_the_others(self, tmp_path):
        store = _store_with(tmp_path, {"codex": {"model": "gpt-5.5", "full_auto": False}})

        store.update("alpha", WorkspaceUpdatePatch(auto_scan_options={"copilot": {"allow_all": False}}))
        store.update("alpha", WorkspaceUpdatePatch(auto_scan_options={"codex": {"full_auto": True}}))

        reloaded = WorkspacesStore(str(tmp_path / "projects.json"))
        reloaded.load()
        for saved in (_saved(store), _saved(reloaded)):
            assert saved["codex_options"] == {"full_auto": True}
            assert saved["copilot_options"] == {"allow_all": False}


async def test_stored_options_the_models_reject_read_as_null_without_failing_the_list(
    app, client, auth_headers, tmp_path,
):
    store = app.state.workspaces_store
    store.create(WorkspaceRecord(name="alpha", path=str(tmp_path)))
    # Right JSON types, so the store keeps them; a newer validator refuses them.
    store.update("alpha", WorkspaceUpdatePatch(auto_scan_options={
        "codex": {"sandbox": "everything"},
        "antigravity": {"skip_permissions": True, "sandbox": True},
        "copilot": {"allow_all": True},
    }))

    resp = await client.get("/api/projects", headers=auth_headers)

    assert resp.status_code == 200
    [workspace] = resp.json()
    assert workspace["auto_scan"]["codex_options"] is None
    assert workspace["auto_scan"]["antigravity_options"] is None
    assert workspace["auto_scan"]["copilot_options"] == {"allow_all": True}
