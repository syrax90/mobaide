"""``AutoScanInstance`` running the chosen agent on each issue.

The real launchers build each argv (their binaries are pinned by
``conftest.py``); the issue source, worktree manager and session manager are
hand-written fakes, so every assertion reads what the scan left behind: the
session config, the worktree it ran in, the tracked work item.
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from mobaide.core import SessionKind, SessionManager, WorkspacesStore
from mobaide.core import claude_launcher, codex_launcher, pi_launcher
from mobaide.core.antigravity_launcher import AntigravityLauncher
from mobaide.core.claude_launcher import ClaudeBinaryNotFoundError, ClaudeLauncher
from mobaide.core.cli_launcher import CliBinaryNotFoundError, LaunchSpec
from mobaide.core.codex_launcher import CodexLauncher
from mobaide.core.copilot_launcher import CopilotLauncher
from mobaide.core.cursor_launcher import CursorLauncher
from mobaide.core.junie_launcher import JunieLauncher
from mobaide.core.opencode_launcher import INITIAL_PROMPT_MAX, OpenCodeLauncher
from mobaide.core.pi_launcher import PiBinaryNotFoundError, PiLauncher
from mobaide.core.workspaces_config import GitHubScanFilter, WorkspaceConfig
from mobaide.github.gh import GhBinaryNotFoundError, GhCli, GhIssue
from mobaide.linear import scanner, sources
from mobaide.linear.config import AutoScanConfig
from mobaide.linear.scanner import AutoScanInstance
from mobaide.linear.sources import GitHubIssueSource, ScanIssue
from mobaide.linear.workitems import ActiveWorkItem, now_iso_utc
from mobaide.linear.worktree import WorktreeManager, WorktreeResult


class _FakeSource:
    kind = "github"
    label = "GitHub"

    def __init__(self, issues: list[ScanIssue], done: dict[str, bool] | None = None) -> None:
        self.issues = issues
        self.done = done or {}
        self.checked: list[str] = []
        self.claimed: list[str] = []

    async def connect(self) -> str:
        return "octocat on acme/app"

    def refresh(self, workspace) -> None:
        pass

    def describe(self) -> str:
        return "labels=['ai']"

    async def fetch(self) -> list[ScanIssue]:
        return list(self.issues)

    async def claim(self, issue, emit) -> None:
        self.claimed.append(issue.key)
        emit(issue, "state_update", "ok", None)
        emit(issue, "label_removal", "ok", None)

    async def is_done(self, item: ActiveWorkItem) -> bool | None:
        self.checked.append(item.uuid)
        return self.done.get(item.uuid)


class _FakeWorktrees:
    def __init__(self, root: Path) -> None:
        self._root = root
        self.created: list[str] = []
        self.removed: list[str] = []
        self.forced: list[bool] = []

    async def create_worktree(self, slug, workspace_root, **_kwargs) -> WorktreeResult:
        path = self._root / slug
        path.mkdir(parents=True)
        self.created.append(slug)
        return WorktreeResult(path=path.as_posix(), is_new=True, branch=f"feature/mobaide/{slug}")

    async def cleanup_worktree(self, path: str, workspace_root: str, *, force: bool = True) -> bool:
        self.removed.append(path)
        self.forced.append(force)
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


def _issue(number: int = 7, title: str = "Fix the login crash", body: str = "Steps to reproduce") -> ScanIssue:
    return ScanIssue(
        key=str(number), identifier=f"#{number}", title=title, description=body,
        url=f"https://github.com/acme/app/issues/{number}", slug_seed=f"gh-{number}",
    )


def _workspace(tmp_path: Path, *, saved_prompt: str | None = None) -> WorkspaceConfig:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return WorkspaceConfig(name="app", path=repo.as_posix(), auto_scan_prompt=saved_prompt)


def _instance(
    tmp_path: Path, *, cli: str, launcher, source: _FakeSource | GitHubIssueSource,
    agent_options: dict[str, object] | None = None, initial_prompt: str | None = None,
    workspace: WorkspaceConfig | None = None,
    worktrees: _FakeWorktrees | None = None,
):
    workspace = workspace or _workspace(tmp_path)
    worktrees = worktrees or _FakeWorktrees(tmp_path / "worktrees")
    sessions = _RecordingSessions()
    inst = AutoScanInstance(
        workspace_name=workspace.name,
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
        launcher=cast("scanner.ClaudeLauncher", launcher if cli == "claude" else object()),
        worktree_mgr=cast("WorktreeManager", worktrees),
        workspaces_store=cast("WorkspacesStore", _Store(workspace)),
        source=source,
        cli=cli,
        launchers={cli: launcher},
        agent_options=agent_options,
        initial_prompt=initial_prompt,
    )
    return inst, worktrees, sessions


def _issue_events(inst: AutoScanInstance) -> list[tuple[str, str, str]]:
    return [
        (e["payload"]["identifier"], e["payload"]["step"], e["payload"]["status"])
        for e in inst.get_status()["recent_events"]
        if e["type"] == "issue"
    ]


_PROMPT = "Fix it carefully."
# The PROTOCOL.md first message: the prompt, a blank line, the issue line,
# its URL, a blank line and the description.
_EXPECTED_MESSAGE = (
    "Fix it carefully.\n\nGitHub issue #7: Fix the login crash\n"
    "https://github.com/acme/app/issues/7\n\nSteps to reproduce"
)


@pytest.fixture(autouse=True)
def _codex_signed_in(monkeypatch):
    async def present(*_args, **_kwargs) -> bool:
        return True

    monkeypatch.setattr(codex_launcher, "codex_auth_present", present)


@pytest.mark.parametrize("auto_approve", [True, False], ids=["auto-approve", "ask"])
@pytest.mark.parametrize(
    ("cli", "make_launcher", "approve_flag", "message_args"),
    [
        ("codex", CodexLauncher, "--dangerously-bypass-approvals-and-sandbox", lambda p: [p]),
        ("antigravity", AntigravityLauncher, "--dangerously-skip-permissions",
         lambda p: [f"--prompt-interactive={p}"]),
        ("opencode", OpenCodeLauncher, "--auto", lambda p: [f"--prompt={p}"]),
        ("copilot", CopilotLauncher, "--allow-all", lambda p: [f"--interactive={p}"]),
        ("cursor", CursorLauncher, "--force", lambda p: ["--", p]),
        ("junie", JunieLauncher, "--brave", lambda p: [f"--prompt={p}"]),
        ("pi", PiLauncher, None, lambda p: ["--", p]),
    ],
)
async def test_agent_runs_the_issue_in_its_own_worktree(
    tmp_path, cli, make_launcher, approve_flag, message_args, auto_approve,
):
    source = _FakeSource([_issue()])
    inst, worktrees, sessions = _instance(
        tmp_path, cli=cli, launcher=make_launcher(), source=source,
        agent_options=None if auto_approve else {}, initial_prompt=_PROMPT,
    )

    await inst._scan_once()

    assert worktrees.created == ["gh-7-fix-the-login-crash"]
    [config] = sessions.configs
    assert config.kind == SessionKind(cli)
    assert config.cwd == (tmp_path / "worktrees" / "gh-7-fix-the-login-crash").as_posix()
    tail = message_args(_EXPECTED_MESSAGE)
    assert config.command[-len(tail):] == tail
    if approve_flag is not None:
        assert (approve_flag in config.command) is auto_approve
    [item] = await inst.list_workitems()
    assert (item.id, item.uuid, item.source) == ("#7", "7", "github")


async def test_missing_agent_creates_no_worktree_and_ends_the_cycle(tmp_path, monkeypatch):
    def missing(*_args, **_kwargs):
        raise PiBinaryNotFoundError(searched_paths=["PATH:pi"])

    monkeypatch.setattr(pi_launcher, "_resolve_pi_binary", missing)
    source = _FakeSource([_issue(1, "First"), _issue(2, "Second")])
    inst, worktrees, sessions = _instance(tmp_path, cli="pi", launcher=PiLauncher(), source=source)

    await inst._scan_once()

    assert worktrees.created == []
    assert sessions.configs == []
    assert await inst.list_workitems() == []
    events = _issue_events(inst)
    assert events == [("#1", "session_spawn", "failed")]


async def test_first_message_caps_the_description_and_the_whole_prompt(tmp_path):
    long_body = "x" * 5000
    source = _FakeSource([_issue(1, "Long", long_body)])
    inst, _, sessions = _instance(
        tmp_path, cli="pi", launcher=PiLauncher(), source=source, initial_prompt="Go.",
    )

    await inst._scan_once()

    message = sessions.configs[0].command[-1]
    assert message.endswith("\n\n" + "x" * 4000 + "\n[…]")

    capped = _FakeSource([_issue(2, "Long", long_body)])
    inst2, _, sessions2 = _instance(
        tmp_path / "second", cli="pi", launcher=PiLauncher(), source=capped,
        initial_prompt="p" * INITIAL_PROMPT_MAX,
    )
    await inst2._scan_once()
    assert sessions2.configs[0].command[-1] == "p" * INITIAL_PROMPT_MAX


@pytest.mark.parametrize("body_length", [5000, INITIAL_PROMPT_MAX + 1000])
async def test_first_message_keeps_a_linear_description_whole_up_to_the_prompt_cap(
    tmp_path, body_length,
):
    issue = ScanIssue(
        key="uuid-1", identifier="TEAM-1", title="Long", description="x" * body_length,
        url=None, slug_seed="TEAM-1",
    )
    source = _FakeSource([issue])
    source.kind, source.label = "linear", "Linear"
    inst, _, sessions = _instance(
        tmp_path, cli="pi", launcher=PiLauncher(), source=source, initial_prompt="Go.",
    )

    await inst._scan_once()

    # A Linear issue has no link to the rest of its description, and only
    # Claude Code can read Linear.
    full = "Go.\n\nLinear issue TEAM-1: Long\n\n" + "x" * body_length
    prompt = sessions.configs[0].command[-1]
    if len(full) <= INITIAL_PROMPT_MAX:
        assert prompt == full
    else:
        assert len(prompt) <= INITIAL_PROMPT_MAX
        assert prompt.endswith("\n[…]")
        assert prompt.startswith("Go.\n\nLinear issue TEAM-1: Long\n\nxxx")


_LINEAR_HEAD = "Go.\n\nLinear issue TEAM-1: Long"


def _linear_source(body: str, url: str | None = None) -> _FakeSource:
    source = _FakeSource([
        ScanIssue(
            key="uuid-1", identifier="TEAM-1", title="Long", description=body,
            url=url, slug_seed="TEAM-1",
        ),
    ])
    source.kind, source.label = "linear", "Linear"
    return source


async def test_first_message_cuts_at_4000_by_source_kind_not_by_link(tmp_path):
    url = "https://linear.app/acme/issue/TEAM-1"
    inst, _, sessions = _instance(
        tmp_path, cli="pi", launcher=PiLauncher(), source=_linear_source("x" * 5000, url),
        initial_prompt="Go.",
    )

    await inst._scan_once()

    # A link does not let the agent read the rest of a Linear issue.
    assert sessions.configs[0].command[-1] == f"{_LINEAR_HEAD}\n{url}\n\n" + "x" * 5000


async def test_first_message_drops_control_characters_from_the_issue_text(tmp_path):
    source = _FakeSource([
        _issue(7, "Fix\x1b]52;c;ZXZpbA==\x07 crash\x9b2J", "Line one\r\n\tindented\x1b[31m red\x00\rend"),
    ])
    inst, _, sessions = _instance(
        tmp_path, cli="pi", launcher=PiLauncher(), source=source, initial_prompt="Go.",
    )

    await inst._scan_once()

    # An agent that echoes its prompt would pass escape sequences (here an
    # OSC 52 clipboard write and a C1 CSI) on to the apps' terminals; line
    # feeds and tabs stay.
    assert sessions.configs[0].command[-1] == (
        "Go.\n\nGitHub issue #7: Fix]52;c;ZXZpbA== crash2J\n"
        "https://github.com/acme/app/issues/7\n\nLine one\n\tindented[31m redend"
    )


@pytest.mark.parametrize(
    ("initial_prompt", "starts_with"),
    [
        pytest.param("Client prompt", "Client prompt", id="client-prompt-wins"),
        pytest.param(None, "Saved prompt", id="saved-prompt-when-omitted"),
        pytest.param("", scanner._GITHUB_DEFAULT_PROMPT, id="cleared-prompt-skips-the-saved-one"),
    ],
)
async def test_first_message_prompt_choice(tmp_path, initial_prompt, starts_with):
    inst, _, sessions = _instance(
        tmp_path, cli="pi", launcher=PiLauncher(), source=_FakeSource([_issue()]),
        initial_prompt=initial_prompt,
        workspace=_workspace(tmp_path, saved_prompt="Saved prompt"),
    )

    await inst._scan_once()

    message = sessions.configs[0].command[-1]
    assert message.startswith(starts_with + "\n\nGitHub issue #7: Fix the login crash")


async def test_cleanup_checks_only_the_items_of_its_own_source(tmp_path):
    source = _FakeSource([], done={"7": True, "uuid-1": True})
    inst, worktrees, _ = _instance(tmp_path, cli="pi", launcher=PiLauncher(), source=source)
    root = inst._workspace_config.path
    linear_item = ActiveWorkItem(
        id="TEAM-1", uuid="uuid-1", slug="team-1", worktree_path="/wt/team-1",
        project_root=root, added_at=now_iso_utc(),
    )
    github_item = ActiveWorkItem(
        id="#7", uuid="7", slug="gh-7", worktree_path="/wt/gh-7",
        project_root=root, added_at=now_iso_utc(), source="github",
    )
    await inst._workitems_store.write([linear_item, github_item])

    await inst._cleanup_completed_worktrees()

    assert source.checked == ["7"]
    assert worktrees.removed == ["/wt/gh-7"]
    assert [i.id for i in await inst.list_workitems()] == ["TEAM-1"]


def _claude_missing(monkeypatch) -> tuple[str, object]:
    def missing(*_args, **_kwargs):
        raise ClaudeBinaryNotFoundError(searched_paths=["PATH:claude"])

    monkeypatch.setattr(claude_launcher, "_resolve_claude_binary", missing)
    return "claude", ClaudeLauncher()


def _codex_signed_out(monkeypatch) -> tuple[str, object]:
    async def absent(*_args, **_kwargs) -> bool:
        return False

    monkeypatch.setattr(codex_launcher, "codex_auth_present", absent)
    return "codex", CodexLauncher()


@pytest.mark.parametrize(
    "unusable_agent",
    [
        pytest.param(_claude_missing, id="claude-not-installed"),
        pytest.param(_codex_signed_out, id="codex-not-signed-in"),
    ],
)
async def test_an_agent_that_cannot_run_ends_the_cycle_before_any_issue_is_taken(
    tmp_path, monkeypatch, unusable_agent,
):
    cli, launcher = unusable_agent(monkeypatch)
    source = _FakeSource([_issue(1, "First"), _issue(2, "Second")])
    inst, worktrees, sessions = _instance(tmp_path, cli=cli, launcher=launcher, source=source)

    await inst._scan_once()

    # A taken issue stops matching the filter: taking it with no agent to run
    # it would lose it for good.
    assert source.claimed == []
    assert worktrees.created == []
    assert sessions.configs == []
    assert _issue_events(inst) == [("#1", "session_spawn", "failed")]


@pytest.mark.parametrize(
    ("source_kind", "key_reaches_the_session"),
    [("github", False), ("linear", True)],
)
async def test_claude_gets_the_linear_key_only_on_a_linear_scan(
    tmp_path, monkeypatch, source_kind, key_reaches_the_session,
):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    source = _FakeSource([_issue()])
    source.kind = source_kind
    worktrees = _FakeWorktrees(tmp_path / "worktrees")
    inst, _, sessions = _instance(
        tmp_path, cli="claude", launcher=ClaudeLauncher(worktree_mgr=worktrees), source=source,
        workspace=replace(_workspace(tmp_path), linear_api_key="lin_api_secret"),
        worktrees=worktrees,
    )

    await inst._scan_once()

    # A GitHub issue is third-party text: the agent reading it must not hold
    # the workspace's Linear key, in its env or in the worktree's .mcp.json.
    [config] = sessions.configs
    assert ("LINEAR_API_KEY" in config.env) is key_reaches_the_session
    assert (Path(config.cwd) / ".mcp.json").exists() is key_reaches_the_session


class _Gh(GhCli):
    """A ``gh`` at ``binary`` (``None``: not installed) whose repository has
    issue #7 open with the asked label."""

    def __init__(self, binary: str | None) -> None:
        super().__init__()
        self._binary = binary

    def resolve_binary(self) -> str:
        if self._binary is None:
            raise GhBinaryNotFoundError(searched_paths=[])
        return self._binary

    async def list_issues(self, repo, *, label, assignee, issue_type=None, with_types=False):
        return [GhIssue(number=7, title="Fix the login crash", body="", url="", labels=(label,))]

    async def edit_labels(self, repo, number, *, add=(), remove=()) -> None:
        pass

    async def issue_state(self, repo, number) -> str | None:
        return "OPEN"


@pytest.mark.parametrize(
    ("source_kind", "gh_installed", "gh_on_host_path", "gh_added"),
    [
        ("github", True, False, True),
        ("github", True, True, False),
        ("github", False, False, False),
        ("linear", True, False, False),
    ],
    ids=["github", "github-gh-already-on-path", "github-gh-missing", "linear"],
)
async def test_a_github_scan_adds_its_gh_folder_last_on_the_session_path(
    tmp_path, monkeypatch, source_kind, gh_installed, gh_on_host_path, gh_added,
):
    async def ready(_gh, _path):
        return {"problem": None, "host": "github.com", "repo": "acme/app",
                "repo_url": "https://github.com/acme/app"}

    monkeypatch.setattr(sources, "github_status", ready)
    gh_dir = tmp_path / "gh-bin"
    if gh_on_host_path:
        monkeypatch.setenv("PATH", f"{gh_dir}{os.pathsep}{os.environ['PATH']}")
    gh = _Gh(str(gh_dir / "gh") if gh_installed else None)
    source = (
        GitHubIssueSource(gh, "/srv/app", GitHubScanFilter(labels=["ai"]))
        if source_kind == "github" else _linear_source("Steps to reproduce")
    )
    await source.connect()
    inst, _, sessions = _instance(tmp_path, cli="pi", launcher=PiLauncher(), source=source)

    await inst._scan_once()

    # The first prompt asks the agent to run ``gh issue view``; MOBAIDE_GH_BIN
    # or a fallback folder may have found a gh that is not on the agent's PATH.
    # Without a gh, or with its folder already on PATH, PATH stays as it was.
    [config] = sessions.configs
    host_path = os.environ["PATH"]
    assert config.env["PATH"] == (f"{host_path}{os.pathsep}{gh_dir}" if gh_added else host_path)


@pytest.mark.parametrize(("source_kind", "forced"), [("github", False), ("linear", True)])
async def test_cleanup_forces_away_uncommitted_work_only_on_a_linear_scan(
    tmp_path, source_kind, forced,
):
    source = _FakeSource([], done={"7": True})
    source.kind = source_kind
    inst, worktrees, _ = _instance(tmp_path, cli="pi", launcher=PiLauncher(), source=source)
    await inst._workitems_store.write([
        ActiveWorkItem(
            id="#7", uuid="7", slug="gh-7", worktree_path="/wt/gh-7",
            project_root=inst._workspace_config.path, added_at=now_iso_utc(), source=source_kind,
        ),
    ])

    await inst._cleanup_completed_worktrees()

    # Anyone can close a GitHub issue; that must not delete unsaved work.
    assert list(zip(worktrees.removed, worktrees.forced)) == [("/wt/gh-7", forced)]


class _WindowsAgent:
    """An agent install on Windows: ``resolve_binary`` names the file the PC
    runs, and ``build`` records the options it was given."""

    def __init__(self, binary: str | None) -> None:
        self._binary = binary
        self.options: list = []

    def resolve_binary(self) -> str:
        if self._binary is None:
            raise CliBinaryNotFoundError(searched_paths=[])
        return self._binary

    async def build(self, target, options, session_id=None) -> LaunchSpec:
        self.options.append(options)
        return LaunchSpec(
            argv=[self.resolve_binary(), "--", options.initial_prompt], cwd=target.path, env={},
        )


# What cmd.exe acts on in a command line, inside quotes or not.
_CMD_SPECIALS = set('"%!^&|<>\r\n')


@pytest.mark.parametrize(
    "text", ['a" & calc & "b', "%PATH%", "!x!", "a\r\nb", "a^b|c<d>e", "&" * 7000],
)
def test_cmd_safe_prompt_leaves_nothing_cmd_exe_acts_on(text):
    safe = scanner._cmd_safe_prompt(text)

    assert not _CMD_SPECIALS & set(safe)
    # cmd.exe takes at most 8,191 characters per command line.
    assert len(safe) <= 6000


@pytest.mark.parametrize(
    ("platform", "binary", "through_cmd"),
    [
        pytest.param("win32", r"C:\x\codex.cmd", True, id="windows-cmd-shim"),
        pytest.param("win32", r"C:\x\CODEX.BAT", True, id="windows-bat-shim"),
        pytest.param("win32", r"C:\x\codex.exe", False, id="windows-exe"),
        pytest.param("win32", None, False, id="windows-not-installed"),
        pytest.param("linux", "/x/codex.cmd", False, id="not-windows"),
    ],
)
def test_only_a_windows_cmd_or_bat_shim_runs_through_cmd_exe(
    monkeypatch, platform, binary, through_cmd,
):
    monkeypatch.setattr(sys, "platform", platform)

    assert scanner._through_cmd_shim(_WindowsAgent(binary)) is through_cmd


async def test_issue_text_reaches_a_windows_cmd_shim_sanitized(tmp_path, monkeypatch):
    agent = _WindowsAgent(r"C:\x\codex.cmd")
    source = _FakeSource([_issue(7, 'a" & calc & "b', "%PATH% !x!\r\nnext ^|<>")])
    inst, _, sessions = _instance(
        tmp_path, cli="codex", launcher=agent, source=source, initial_prompt="Fix it.",
    )
    monkeypatch.setattr(sys, "platform", "win32")

    await inst._scan_once()

    [options] = agent.options
    assert not _CMD_SPECIALS & set(options.initial_prompt)
    assert "GitHub issue #7: a' + calc + 'b" in options.initial_prompt
    assert sessions.configs[0].command[-1] == options.initial_prompt


@pytest.mark.parametrize(
    ("platform", "binary", "limit", "starts_with", "ends_with"),
    [
        pytest.param("linux", "/x/codex", INITIAL_PROMPT_MAX,
                     f"{_LINEAR_HEAD}\n\nx", "x\n[…]", id="not-windows"),
        pytest.param("win32", r"C:\x\codex.exe", 24_000,
                     f"{_LINEAR_HEAD}\n\nx", "x\n[…]", id="windows-native"),
        pytest.param("win32", r"C:\x\codex.cmd", 6000,
                     " ".join(_LINEAR_HEAD.split()) + " x", "x […]", id="windows-cmd-shim"),
    ],
)
async def test_a_long_description_is_cut_to_the_launch_limit_and_marked(
    tmp_path, monkeypatch, platform, binary, limit, starts_with, ends_with,
):
    agent = _WindowsAgent(binary)
    inst, _, _ = _instance(
        tmp_path, cli="codex", launcher=agent,
        source=_linear_source("x" * (INITIAL_PROMPT_MAX + 1000)), initial_prompt="Go.",
    )
    monkeypatch.setattr(sys, "platform", platform)

    await inst._scan_once()

    # Windows takes at most 32,767 UTF-16 units per command line, cmd.exe
    # 8,191. The issue line must survive, and the agent must see the cut.
    [options] = agent.options
    assert len(options.initial_prompt) <= limit
    assert options.initial_prompt.startswith(starts_with)
    assert options.initial_prompt.endswith(ends_with)
