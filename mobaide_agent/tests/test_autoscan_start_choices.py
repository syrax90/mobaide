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
from mobaide.core import SessionManager, WorkspaceRecord, WorkspacesStore, codex_launcher
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


def _make_manager(store, bus, *, agents=None) -> AutoScanSessionManager:
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
        session_manager=AsyncMock(spec=SessionManager),
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
            source="github", cli="copilot", auto_approve=False,
            initial_prompt="Fix it.", github_filter=_GITHUB,
        ),
    )

    assert (instance.to_dto()["source"], instance.to_dto()["cli"]) == ("github", "copilot")
    assert _saved(store) == {
        "source": "github",
        "cli": "copilot",
        "linear_filter": None,
        "github_filter": {"labels": ["ai-ready"], "assignee": "@me", "active_label": "ai-doing"},
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
