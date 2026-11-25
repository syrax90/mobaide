"""``AutoScanSessionManager.start`` with the wizard's choices
(``AutoScanStart``): which starts it refuses, and what it remembers on the
workspace so the next wizard shows the same choices.

Uses a real ``WorkspacesStore`` on disk. ``AutoScanInstance.start`` is a no-op
so no source is contacted and no poll task runs.
"""
from __future__ import annotations

import atexit
import shutil
import tempfile
from unittest.mock import AsyncMock

import pytest

from mobaide.agent.autoscan.models import StartScanRequest
from mobaide.agent.sessions.models import ClaudeScanOptionsModel, CopilotScanOptionsModel
from mobaide.agent.workspaces.models import WorkspaceReadModel
from mobaide.core import SessionManager, WorkspaceRecord, WorkspacesStore, cli_launcher, codex_launcher
from mobaide.core.cli_launcher import CliBinaryNotFoundError
from mobaide.core.codex_launcher import CodexLauncher
from mobaide.core.workspaces_config import AutoScanFilter, GitHubScanFilter
from mobaide.linear.config import AutoScanConfig
from mobaide.linear.scanner import (
    AutoScanAlreadyRunning,
    AutoScanInstance,
    AutoScanSessionManager,
    AutoScanStart,
)
from mobaide.linear.sources import AutoScanSourceError


class _RecordingEventBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def publish(self, topic: str, payload: object) -> None:
        self.events.append((topic, payload))

    def topics(self) -> list[str]:
        return [t for t, _ in self.events]


class _Agent:
    """A launcher whose binary is either on the PC or not."""

    def __init__(self, installed: bool) -> None:
        self._installed = installed

    def resolve_binary(self) -> str:
        if not self._installed:
            raise CliBinaryNotFoundError(searched_paths=[])
        return "agent"


@pytest.fixture(autouse=True)
def _no_op_instance_start(monkeypatch):
    async def _noop(self) -> None:  # noqa: ANN001
        return None

    monkeypatch.setattr(AutoScanInstance, "start", _noop)


_WORKTREES_DIR = tempfile.mkdtemp(prefix="mobaide-wt-test-")
atexit.register(shutil.rmtree, _WORKTREES_DIR, ignore_errors=True)

_GITHUB = GitHubScanFilter(labels=["ai-ready"], assignee="@me", active_label="ai-doing")
_LINEAR = AutoScanFilter(
    state_names=["Todo"], label_names=["bot"], active_state="Doing",
    done_state="Shipped", removal_label="bot",
)


def _make_store(tmp_path, *, key: str | None = None, prompt: str | None = None) -> WorkspacesStore:
    store = WorkspacesStore(str(tmp_path / "projects.json"))
    store.load()
    store.create(
        WorkspaceRecord(
            name="alpha", path=str(tmp_path / "repo"), linear_api_key=key, auto_scan_prompt=prompt,
        ),
    )
    return store


def _make_manager(store, bus, *, agents=None, session_manager=None) -> AutoScanSessionManager:
    return AutoScanSessionManager(
        config=AutoScanConfig(
            worktrees_dir=_WORKTREES_DIR,
            worktree_base="mobaide_worktree",
            scan_interval_seconds=1,
            branch_prefix="feature/mobaide",
            main_branch="main",
            auto_remove_worktrees=False,
            state_dir="state",
        ),
        session_manager=session_manager or AsyncMock(spec=SessionManager),
        workspaces_store=store,
        event_bus=bus,
        cli_launchers=agents or {"copilot": _Agent(installed=True)},
    )


def _saved(store: WorkspacesStore) -> dict:
    [row] = store.list_public()
    return row["auto_scan"]  # type: ignore[return-value]


async def test_github_start_remembers_every_choice(tmp_path):
    store = _make_store(tmp_path)
    bus = _RecordingEventBus()
    mgr = _make_manager(store, bus)

    instance = await mgr.start(
        "alpha",
        request=AutoScanStart(
            source="github", cli="copilot", agent_options={},
            initial_prompt="Fix it.", github_filter=_GITHUB,
        ),
    )

    assert (instance.to_dto()["source"], instance.to_dto()["cli"]) == ("github", "copilot")
    assert _saved(store) == {
        "source": "github",
        "cli": "copilot",
        "linear_filter": None,
        "github_filter": {
            "labels": ["ai-ready"], "assignee": "@me", "active_label": "ai-doing",
            "issue_types": [],
        },
        "claude_options": None,
        "codex_options": None,
        "antigravity_options": None,
        "opencode_options": None,
        "copilot_options": {},
        "cursor_options": None,
        "junie_options": None,
        "pi_options": None,
        "ui": None,
        "interval_minutes": None,
    }
    snap = store.get("alpha")
    assert snap is not None and snap.auto_scan_prompt == "Fix it."
    reloaded = WorkspacesStore(str(tmp_path / "projects.json"))
    reloaded.load()
    assert _saved(reloaded) == _saved(store)
    assert "projects.changed" in bus.topics()


async def test_refused_start_still_remembers_the_choices(tmp_path):
    store = _make_store(tmp_path)
    bus = _RecordingEventBus()
    mgr = _make_manager(store, bus)

    with pytest.raises(AutoScanSourceError):
        await mgr.start("alpha", request=AutoScanStart(source="github", cli="copilot"))

    assert mgr.get("alpha") is None
    assert (_saved(store)["source"], _saved(store)["cli"]) == ("github", "copilot")
    assert "projects.changed" in bus.topics()


async def test_start_rejected_as_already_running_changes_nothing(tmp_path):
    store = _make_store(tmp_path)
    mgr = _make_manager(store, _RecordingEventBus(), agents={
        "copilot": _Agent(installed=True), "cursor": _Agent(installed=True),
    })
    await mgr.start(
        "alpha", request=AutoScanStart(source="github", cli="copilot", github_filter=_GITHUB),
    )
    before = _saved(store)

    with pytest.raises(AutoScanAlreadyRunning):
        await mgr.start(
            "alpha",
            request=AutoScanStart(
                source="github", cli="cursor", github_filter=GitHubScanFilter(labels=["other"]),
            ),
        )

    assert _saved(store) == before


@pytest.mark.parametrize(
    ("agents", "cli"),
    [
        pytest.param({"copilot": _Agent(installed=False)}, "copilot", id="not-installed"),
        pytest.param({"pi": _Agent(installed=True)}, "junie", id="not-registered"),
    ],
)
async def test_an_agent_that_cannot_run_refuses_the_start(tmp_path, agents, cli):
    store = _make_store(tmp_path)
    mgr = _make_manager(store, _RecordingEventBus(), agents=agents)

    with pytest.raises(AutoScanSourceError):
        await mgr.start(
            "alpha", request=AutoScanStart(source="github", cli=cli, github_filter=_GITHUB),
        )

    assert mgr.get("alpha") is None


async def test_linear_needs_a_chosen_filter_from_a_newer_client_only(tmp_path):
    # A Linear key, but nobody ever chose the Linear filter.
    store = _make_store(tmp_path, key="lin_api_test")
    mgr = _make_manager(store, _RecordingEventBus())

    with pytest.raises(AutoScanSourceError):
        await mgr.start("alpha", request=AutoScanStart(source="linear", cli="claude"))
    # An older client sends no source: Linear with the legacy defaults, as before.
    instance = await mgr.start("alpha")

    assert (instance.source_kind, instance.cli) == ("linear", "claude")


async def test_linear_start_saves_the_key_and_filter_it_was_sent(tmp_path):
    store = _make_store(tmp_path)
    mgr = _make_manager(store, _RecordingEventBus())

    instance = await mgr.start(
        "alpha",
        request=AutoScanStart(
            source="linear", cli="claude", linear_filter=_LINEAR, linear_api_key="lin_api_new",
        ),
    )

    snap = store.get("alpha")
    assert snap is not None
    assert snap.linear_api_key == "lin_api_new"
    assert snap.auto_scan_filter == _LINEAR
    assert instance.source_kind == "linear"


async def test_empty_initial_prompt_clears_the_saved_prompt(tmp_path):
    store = _make_store(tmp_path, prompt="old prompt")
    mgr = _make_manager(store, _RecordingEventBus())

    await mgr.start(
        "alpha",
        request=AutoScanStart(
            source="github", cli="copilot", initial_prompt="", github_filter=_GITHUB,
        ),
    )

    snap = store.get("alpha")
    assert snap is not None and snap.auto_scan_prompt is None


@pytest.mark.parametrize(
    ("wire_prompt", "saved"),
    [
        pytest.param(" \n\t ", None, id="blank-clears"),
        pytest.param(None, "old prompt", id="omitted-keeps"),
    ],
)
async def test_a_blank_prompt_on_the_wire_clears_the_saved_prompt(tmp_path, wire_prompt, saved):
    store = _make_store(tmp_path, prompt="old prompt")
    mgr = _make_manager(store, _RecordingEventBus())
    wire = StartScanRequest.model_validate({
        "project_name": "alpha", "source": "github", "cli": "copilot",
        "initial_prompt": wire_prompt, "github_filter": {"labels": ["ai-ready"]},
    })

    await mgr.start("alpha", request=wire.to_start())

    # A saved blank prompt would become the instruction of every issue.
    snap = store.get("alpha")
    assert snap is not None and snap.auto_scan_prompt == saved


@pytest.mark.parametrize("signed_in", [True, False], ids=["signed-in", "signed-out"])
async def test_codex_must_be_signed_in_on_the_pc_to_start(tmp_path, monkeypatch, signed_in):
    async def auth_present(*_args, **_kwargs) -> bool:
        return signed_in

    monkeypatch.setattr(codex_launcher, "codex_auth_present", auth_present)
    store = _make_store(tmp_path)
    mgr = _make_manager(store, _RecordingEventBus(), agents={"codex": CodexLauncher()})
    request = AutoScanStart(source="github", cli="codex", github_filter=_GITHUB)

    if signed_in:
        assert (await mgr.start("alpha", request=request)).cli == "codex"
        return
    # Refused up front: every issue it took would otherwise fail to start.
    with pytest.raises(AutoScanSourceError, match=r"^Codex is not signed in on the PC\."):
        await mgr.start("alpha", request=request)
    assert mgr.get("alpha") is None


async def test_each_agent_kind_keeps_the_options_of_its_last_start(tmp_path):
    store = _make_store(tmp_path)
    mgr = _make_manager(store, _RecordingEventBus())
    claude = StartScanRequest.model_validate({
        "project_name": "alpha", "source": "github", "cli": "claude",
        "claude_options": {"model": "opus", "plan_mode": True, "initial_prompt": "x",
                           "worktree": True, "branch_name": "b"},
    })
    copilot = StartScanRequest.model_validate({
        "project_name": "alpha", "source": "github", "cli": "copilot",
        "copilot_options": {"allow_all": False}, "codex_options": {"full_auto": True},
        "github_filter": {"labels": ["ai-ready"]},
    })

    # Refused (no labels yet), but the choices are still remembered.
    with pytest.raises(AutoScanSourceError):
        await mgr.start("alpha", request=claude.to_start())
    await mgr.start("alpha", request=copilot.to_start())

    [row] = store.list_public()
    saved = WorkspaceReadModel.model_validate(row).auto_scan
    assert saved.claude_options == ClaudeScanOptionsModel(model="opus", plan_mode=True)
    assert saved.copilot_options == CopilotScanOptionsModel(allow_all=False)
    # Options sent for an agent the start did not choose are not saved.
    assert saved.codex_options is None


async def test_an_older_client_start_saves_no_agent_options(tmp_path):
    store = _make_store(tmp_path, key="lin_api_test")
    mgr = _make_manager(store, _RecordingEventBus())
    wire = StartScanRequest.model_validate(
        {"project_name": "alpha", "claude_options": {"model": "opus"}},
    )

    await mgr.start("alpha", request=wire.to_start())

    assert _saved(store)["claude_options"] is None


class _OldCodeModeAgent(_Agent):
    """An installed agent too old for Code Mode."""

    def __init__(self) -> None:
        super().__init__(installed=True)
        self.checked = 0

    async def check_code_mode(self, target) -> None:  # noqa: ANN001
        self.checked += 1
        raise ValueError("Code Mode needs Pi 1.1.0 or later.")


@pytest.mark.parametrize(
    ("cli", "wire", "refused"),
    [
        pytest.param("pi", {"pi_options": {"code_mode": True}}, True, id="pi-code-mode"),
        pytest.param("pi", {"pi_options": {"code_mode": False}}, False, id="pi-off"),
        pytest.param("claude", {"claude_options": {"code_mode": True}, "ui": "terminal"}, True,
                     id="claude-terminal"),
        # A Claude chat ignores Code Mode, so its version does not matter.
        pytest.param("claude", {"claude_options": {"code_mode": True}, "ui": "chat"}, False,
                     id="claude-chat"),
    ],
)
async def test_code_mode_on_a_too_old_agent_refuses_the_start(tmp_path, cli, wire, refused):
    store = _make_store(tmp_path)
    agent = _OldCodeModeAgent()
    chat = _ChatSessions({cli: _ChatAgent(True)})
    mgr = _make_manager(
        store, _RecordingEventBus(), agents={cli: agent},
        session_manager=chat,
    )
    request = StartScanRequest.model_validate({
        "project_name": "alpha", "source": "github", "cli": cli,
        "github_filter": {"labels": ["ai-ready"]}, **wire,
    })

    # As the router does: Claude's launch options travel on their own.
    claude = request.claude_options.to_options() if request.claude_options else None

    if refused:
        with pytest.raises(AutoScanSourceError, match="Code Mode needs"):
            await mgr.start("alpha", client_options=claude, request=request.to_start())
        assert mgr.get("alpha") is None
    else:
        await mgr.start("alpha", client_options=claude, request=request.to_start())
        assert agent.checked == 0
    # The choice is remembered either way, so the wizard shows it again.
    assert _saved(store)[f"{cli}_options"]["code_mode"] is wire[f"{cli}_options"]["code_mode"]


@pytest.mark.parametrize("auto_review", [True, False], ids=["auto-review", "off"])
async def test_auto_review_on_a_too_old_codex_refuses_the_start(tmp_path, monkeypatch, auto_review):
    async def auth_present(*_args, **_kwargs) -> bool:
        return True

    asked: list[str] = []

    def _version(cli_bin, env=None):  # noqa: ANN001
        asked.append(cli_bin)
        return (0, 146, 0)

    monkeypatch.setattr(codex_launcher, "codex_auth_present", auth_present)
    monkeypatch.setattr(cli_launcher, "cli_version", _version)
    store = _make_store(tmp_path)
    mgr = _make_manager(store, _RecordingEventBus(), agents={"codex": CodexLauncher()})
    request = StartScanRequest.model_validate({
        "project_name": "alpha", "source": "github", "cli": "codex",
        "github_filter": {"labels": ["ai-ready"]},
        "codex_options": {"sandbox": "workspace-write", "approval": "on-request",
                          "auto_review": auto_review},
    })

    if auto_review:
        # Refused before any issue is claimed: each session would exit at once.
        with pytest.raises(AutoScanSourceError, match=r"^Auto review needs Codex 0\.147\.0"):
            await mgr.start("alpha", request=request.to_start())
        assert mgr.get("alpha") is None
    else:
        await mgr.start("alpha", request=request.to_start())
        assert asked == []
    assert _saved(store)["codex_options"]["auto_review"] is auto_review


class _ChatAgent:
    """``SessionManager.chat_agents[kind]``: its chat adapter is on the PC or not."""

    def __init__(self, available: bool) -> None:
        self._available = available

    def available(self) -> bool:
        return self._available


class _ChatSessions:
    def __init__(self, chat_agents: dict[str, _ChatAgent]) -> None:
        self.chat_agents = chat_agents


@pytest.mark.parametrize("chat_available", [True, False], ids=["chat-view", "no-chat-view"])
async def test_a_chat_scan_starts_only_where_the_agent_has_a_chat_view(tmp_path, chat_available):
    store = _make_store(tmp_path)
    mgr = _make_manager(
        store, _RecordingEventBus(),
        session_manager=_ChatSessions({"copilot": _ChatAgent(chat_available)}),
    )
    wire = StartScanRequest.model_validate({
        "project_name": "alpha", "source": "github", "cli": "copilot",
        "copilot_options": {"allow_all": True}, "github_filter": {"labels": ["ai-ready"]},
        "ui": "chat", "interval_minutes": 15,
    })

    if not chat_available:
        # Refused up front: every issue it took would otherwise fail to open.
        with pytest.raises(AutoScanSourceError, match="chat view is not available"):
            await mgr.start("alpha", request=wire.to_start())
        assert mgr.get("alpha") is None
        return
    instance = await mgr.start("alpha", request=wire.to_start())

    dto = instance.to_dto()
    assert (dto["ui"], dto["scan_interval_seconds"]) == ("chat", 900)
    reloaded = WorkspacesStore(str(tmp_path / "projects.json"))
    reloaded.load()
    assert (_saved(reloaded)["ui"], _saved(reloaded)["interval_minutes"]) == ("chat", 15)


async def test_a_start_without_a_view_or_interval_keeps_the_terminal_and_the_config_wait(tmp_path):
    store = _make_store(tmp_path)
    mgr = _make_manager(store, _RecordingEventBus())

    instance = await mgr.start(
        "alpha", request=AutoScanStart(source="github", cli="copilot", github_filter=_GITHUB),
    )

    # An older client sends neither; ``scan_interval_seconds`` is the config's.
    dto = instance.to_dto()
    assert (dto["ui"], dto["scan_interval_seconds"]) == ("terminal", 1)
    assert (_saved(store)["ui"], _saved(store)["interval_minutes"]) == (None, None)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("interval_minutes", 0, id="interval-below-one-minute"),
        pytest.param("interval_minutes", 1441, id="interval-above-a-day"),
        pytest.param("ui", "web", id="unknown-view"),
    ],
)
def test_the_wire_refuses_a_view_or_interval_outside_the_contract(field, value):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        StartScanRequest.model_validate({"project_name": "alpha", field: value})
