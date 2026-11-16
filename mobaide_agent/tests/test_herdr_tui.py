"""Tests for the Herdr TUI backend (``mobaide.herdr.tui``).

The herdr socket has no authentication and one of its methods stops the
server, so the list of methods the link can call is pinned here. The other
tests guard the mapping from a herdr snapshot to Mobaide's records, the
version gate, and the refusals a client sees.

``FakeHerdr`` stands in for the socket client: it answers each request from a
script and records it. No test opens a socket or runs a ``herdr`` binary (the
binary lookup is pinned to nothing).
"""
from __future__ import annotations

import asyncio
import sys
from typing import Any

import pytest

from mobaide.agent.app import create_app
from mobaide.agent.config import AgentConfig
from mobaide.herdr import errors, tui
from mobaide.herdr.agents import AgentChoice
from mobaide.herdr.backend import CONTROL, WATCH, AgentNotStarted, ServerGone
from mobaide.herdr.tui import (
    OUTDATED_MESSAGE,
    TuiApiError,
    TuiClient,
    TuiLink,
    config_dir,
    installed_binaries,
    parse_version,
)

from .conftest import TEST_JWT_SECRET
from .herdr_support import scheduler, settle  # noqa: F401  (``scheduler`` is a fixture)

# The real lookups and the real socket client, taken before a fixture pins
# them (``conftest._pin_herdr_discovery`` and ``herdr`` below).
REAL_CONFIG_DIR = config_dir
REAL_INSTALLED_BINARIES = installed_binaries
REAL_TUI_CLIENT = TuiClient
REAL_SPAWN_PIPED = tui.spawn_piped

SERVER_ID = "tui:default"
CLAUDE = "w1:p1"
AGY = "w1:p2"
SHELL = "w1:p3"

# Every herdr method the link may send. A new entry is a review of what that
# method can do to a user's herdr; ``server.*``, ``plugin.*``,
# ``integration.*`` and ``layout.apply`` must never be here.
ALLOWED_METHODS = frozenset(
    {
        "ping",
        "session.snapshot",
        "events.subscribe",
        "pane.process_info",
        "pane.read",
        "pane.send_text",
        "pane.send_input",
        "agent.prompt",
        "agent.start",
        "workspace.create",
        "workspace.rename",
        "workspace.close",
        "tab.create",
        "tab.rename",
        "tab.close",
        "pane.split",
        "pane.rename",
        "pane.close",
    }
)


def _snapshot() -> dict[str, Any]:
    return {
        "workspaces": [{"workspace_id": "w1", "label": "mobaide", "number": 1}],
        "tabs": [{"tab_id": "w1:t1", "workspace_id": "w1", "label": "", "number": 2}],
        "panes": [
            {
                "pane_id": CLAUDE, "tab_id": "w1:t1", "terminal_id": "term-1",
                "label": "api refactor", "agent": "claude", "agent_status": "working",
                "cwd": "/work/api", "foreground_cwd": "/work/api/src",
                "agent_session": {"kind": "id", "value": "sess-9"},
            },
            {
                "pane_id": AGY, "tab_id": "w1:t1", "terminal_id": "term-2",
                "display_agent": "agy", "agent_status": "idle", "cwd": "/work/site",
            },
            {"pane_id": SHELL, "tab_id": "w1:t1", "agent_status": "idle", "cwd": "/work/logs/"},
        ],
        "agents": [
            {"pane_id": CLAUDE, "name": "claude-1"},
            {"pane_id": AGY, "name": "", "completion_seq": 4},
        ],
        "layouts": [
            {
                "tab_id": "w1:t1",
                "area": {"width": 174, "height": 49},
                "panes": [
                    {"pane_id": CLAUDE, "rect": {"x": 0, "y": 0, "width": 87, "height": 49}},
                ],
            }
        ],
    }


class _Subscription:
    async def next(self) -> None:
        await asyncio.Event().wait()

    async def drain(self, seconds: float) -> None:
        return None

    async def aclose(self) -> None:
        return None


class FakeHerdr:
    def __init__(self) -> None:
        self.version: str | None = "0.9.5"
        self.snapshot = _snapshot()
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.replies: dict[str, dict[str, Any]] = {
            "workspace.create": {"root_pane": {"pane_id": "w2:p1"}},
            "tab.create": {"root_pane": {"pane_id": "w1:p8"}},
            "pane.split": {"pane": {"pane_id": "w1:p9"}},
            "pane.read": {"read": {"text": "last line", "truncated": True}},
        }
        self.failures: dict[str, Exception] = {}
        # How many of the next ``events.subscribe`` calls herdr refuses.
        self.subscribe_refusals = 0

    def client(self, socket_path: str) -> "FakeHerdr":
        self.socket_path = socket_path
        return self

    async def request(
        self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 0
    ) -> dict[str, Any]:
        self.requests.append((method, params or {}))
        if method in self.failures:
            raise self.failures[method]
        if method == "ping":
            return {"version": self.version}
        if method == "session.snapshot":
            return {"snapshot": self.snapshot}
        return self.replies.get(method, {})

    async def subscribe(self, subscriptions: list[dict[str, Any]]) -> _Subscription:
        self.requests.append(("events.subscribe", {"subscriptions": subscriptions}))
        if self.subscribe_refusals > 0:
            self.subscribe_refusals -= 1
            raise TuiApiError("invalid_params", "unknown event type")
        return _Subscription()

    def sent(self, method: str) -> list[dict[str, Any]]:
        return [params for name, params in self.requests if name == method]

    @property
    def methods(self) -> set[str]:
        return {name for name, _ in self.requests}


@pytest.fixture
def herdr(monkeypatch) -> FakeHerdr:
    fake = FakeHerdr()

    async def no_text(_argv, **_kwargs) -> None:
        return None

    monkeypatch.setattr(tui, "TuiClient", fake.client)
    monkeypatch.setattr(tui, "installed_binaries", lambda *_: [])
    monkeypatch.setattr(tui, "run_text", no_text)
    return fake


@pytest.fixture
async def link(herdr):
    """A link whose mirror holds the scripted snapshot."""
    changes: list[None] = []
    agents = [
        AgentChoice(id="claude", label="Claude Code", binary="/bin/claude"),
        AgentChoice(id="antigravity", label="Antigravity", binary="/bin/agy"),
    ]
    tui_link = TuiLink(
        SERVER_ID, "default", "/unused/herdr.sock", lambda: changes.append(None),
        lambda: agents,
    )
    task = asyncio.create_task(tui_link.run())
    await settle()
    assert tui_link.state() is not None and changes
    yield tui_link
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def _panes(link: TuiLink) -> dict[str, dict[str, Any]]:
    server = link.state()
    assert server is not None
    return {
        pane.pane_id: pane.model_dump()
        for workspace in server.workspaces
        for tab in workspace.tabs
        for pane in tab.panes
    }


class TestVersionGate:
    @pytest.mark.parametrize(
        "text,version",
        [
            ("0.9.5", (0, 9, 5)),
            ("herdr 0.9.5", (0, 9, 5)),
            ("0.9.5-preview.1", (0, 9, 5)),
            ("0.10.0", (0, 10, 0)),
            ("", None),
            (None, None),
            ("nightly", None),
            # Neither of these may raise: ``int`` refuses both.
            ("0.9.²", (0, 9)),
            ("0." + "9" * 5000, (0,)),
            ("0.9.1234567890", (0, 9)),
        ],
    )
    def test_parse_version(self, text, version) -> None:
        assert parse_version(text) == version

    @pytest.mark.parametrize("version", ["0.9.1", "0.8.12", None, "nightly"])
    async def test_an_old_or_unknown_server_is_outdated_and_is_not_read(
        self, herdr, version
    ) -> None:
        herdr.version = version
        link = TuiLink(SERVER_ID, "default", "/unused", lambda: None, lambda: [])

        task = asyncio.create_task(link.run())
        await settle()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        server = link.state()
        assert (server.state, server.message, server.workspaces) == (
            "outdated", OUTDATED_MESSAGE, [],
        )
        assert herdr.methods == {"ping"}

    @pytest.mark.parametrize("version", ["0.9.2", "0.10.0", "herdr 1.0.0"])
    async def test_a_supported_server_is_ready(self, herdr, version) -> None:
        # ``0.10.0`` is newer than ``0.9.2``: versions compare as numbers.
        herdr.version = version
        link = TuiLink(SERVER_ID, "default", "/unused", lambda: None, lambda: [])

        task = asyncio.create_task(link.run())
        await settle()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        assert link.state().state == "ready"

    @pytest.mark.parametrize("still_old", ["0.9.1", None], ids=["old", "no-version"])
    async def test_an_outdated_server_ends_its_link_when_a_later_ping_is_supported(
        self, herdr, scheduler, still_old  # noqa: F811
    ) -> None:
        # An update restarts herdr. A restart that fits inside one wait fails
        # no ping, so only the version of the answer shows it. The link must
        # end: the next discovery pass then sets the server up as ready.
        herdr.version = still_old
        link = TuiLink(SERVER_ID, "default", "/unused", lambda: None, lambda: [])
        task = asyncio.create_task(link.run())
        await settle()

        assert scheduler.elapse(tui.OUTDATED_RECHECK_SECONDS) == 1
        await settle()

        assert not task.done()
        assert link.state().state == "outdated"

        herdr.version = "0.9.2"
        assert scheduler.elapse(tui.OUTDATED_RECHECK_SECONDS) == 1
        await settle()
        ended = task.done()
        task.cancel()
        (result,) = await asyncio.gather(task, return_exceptions=True)

        assert ended
        assert isinstance(result, ServerGone)
        # The link of the old server never reads the new one.
        assert herdr.methods == {"ping"}


class TestMirror:
    async def test_a_snapshot_becomes_mobaide_records(self, link) -> None:
        server = link.state()
        (workspace,) = server.workspaces
        (tab,) = workspace.tabs

        assert (server.server_id, server.product, server.name, server.version) == (
            SERVER_ID, "tui", "default", "0.9.5",
        )
        assert (workspace.workspace_id, workspace.label) == ("w1", "mobaide")
        # A tab without a label shows its number.
        assert (tab.tab_id, tab.label, tab.width, tab.height) == ("w1:t1", "2", 174, 49)
        assert _panes(link) == {
            CLAUDE: {
                "pane_id": CLAUDE, "title": "api refactor", "agent": "claude",
                "status": "working", "turn": None, "cwd": "/work/api/src",
                "agent_session_id": "sess-9",
                "rect": {"x": 0, "y": 0, "width": 87, "height": 49},
            },
            # Herdr says ``idle`` once a desktop client looked at the agent;
            # ``completion_seq`` still marks the finished turn.
            AGY: {
                "pane_id": AGY, "title": "site", "agent": "antigravity",
                "status": "done", "turn": 4, "cwd": "/work/site",
                "agent_session_id": None, "rect": None,
            },
            # A plain terminal has no status, whatever herdr reports for it.
            SHELL: {
                "pane_id": SHELL, "title": "logs", "agent": None,
                "status": None, "turn": None, "cwd": "/work/logs/",
                "agent_session_id": None, "rect": None,
            },
        }

    async def test_an_unknown_agent_status_is_unknown(self, herdr, link) -> None:
        herdr.snapshot["panes"][0]["agent_status"] = "hibernating"

        await link.rename("pane", CLAUDE, "x")

        assert _panes(link)[CLAUDE]["status"] == "unknown"

    async def test_the_subscription_names_every_pane(self, herdr, link) -> None:
        # Herdr sends an agent status change only on a subscription that is
        # scoped to that pane.
        (subscribed,) = herdr.sent("events.subscribe")

        scoped = [
            s["pane_id"] for s in subscribed["subscriptions"]
            if s["type"] == "pane.agent_status_changed"
        ]
        assert scoped == [CLAUDE, AGY, SHELL]

    async def test_a_moved_pane_keeps_its_key(self, herdr, link) -> None:
        key = link.pane_key(CLAUDE)
        herdr.snapshot["panes"][0]["pane_id"] = "w2:p1"

        await link.rename("pane", CLAUDE, "x")

        assert key == "term-1"
        assert link.pane_id_for_key(key) == "w2:p1"
        assert link.pane_key(CLAUDE) is None

    async def test_a_refused_subscribe_is_tried_again_more_slowly(
        self, herdr, monkeypatch
    ) -> None:
        # A herdr build that does not know one of the events refuses every
        # subscribe. Without a wait, the link reads the whole server in a
        # loop that never yields the socket.
        herdr.subscribe_refusals = 8
        waits: list[float] = []
        real_sleep = asyncio.sleep

        async def recorded_sleep(delay: float, *args: Any) -> None:
            if delay:
                waits.append(delay)
            await real_sleep(0)

        monkeypatch.setattr(asyncio, "sleep", recorded_sleep)
        link = TuiLink(SERVER_ID, "default", "/unused", lambda: None, lambda: [])

        task = asyncio.create_task(link.run())
        for _ in range(40):
            if len(herdr.sent("events.subscribe")) == 9:
                break
            await settle()
        await settle()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        assert waits == pytest.approx(
            [0.2, 0.4, 0.8, 1.6, 3.2, 6.4, 12.8, tui.SAFETY_POLL_SECONDS]
        )
        # The ninth subscribe was accepted: the link stays on it.
        assert len(herdr.sent("events.subscribe")) == 9
        assert link.state().state == "ready"

    async def test_a_refusal_that_stays_for_a_day_does_not_end_the_link(
        self, herdr, monkeypatch
    ) -> None:
        # The delay doubles with each refusal. Past 1024 doublings the power
        # no longer fits a float: the link would end with an error and the
        # server would leave the dashboard.
        refusals = 1100
        herdr.subscribe_refusals = refusals
        waits: list[float] = []
        real_sleep = asyncio.sleep

        async def recorded_sleep(delay: float, *args: Any) -> None:
            if delay:
                waits.append(delay)
            await real_sleep(0)

        monkeypatch.setattr(asyncio, "sleep", recorded_sleep)
        link = TuiLink(SERVER_ID, "default", "/unused", lambda: None, lambda: [])

        task = asyncio.create_task(link.run())
        for _ in range(refusals):
            if task.done() or len(herdr.sent("events.subscribe")) == refusals + 1:
                break
            await settle()
        await settle()
        still_running = not task.done()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        assert still_running
        assert len(waits) == refusals
        assert set(waits[7:]) == {tui.SAFETY_POLL_SECONDS}
        # The refusals ended: the next subscribe was accepted.
        assert len(herdr.sent("events.subscribe")) == refusals + 1
        assert link.state().state == "ready"

    async def test_the_features_offer_only_agents_herdr_can_start(self, link) -> None:
        features = link.state().features

        assert [a.id for a in features.agents] == ["claude", "antigravity"]
        # No herdr binary of the server's version: no control, and the pane
        # view polls.
        assert features.control is False
        with pytest.raises(errors.HerdrError) as refused:
            await link.open_stream(CLAUDE, CONTROL, 80, 24, sink=None)
        assert refused.value.code == "not_supported"


class TestActions:
    async def test_the_link_sends_only_allowed_methods(self, herdr, link) -> None:
        await link.read_text(CLAUDE, 50)
        await link.send_bytes(CLAUDE, b"y")
        await link.submit(CLAUDE, "go on")
        await link.submit(SHELL, "ls")
        await link.pane_grid(CLAUDE)
        for placement in ("workspace", "tab", "split_right", "split_down"):
            await link.create(
                placement, workspace_id="w1", pane_id=CLAUDE, cwd="/work", label="new",
            )
        await link.start_agent(SHELL, "claude")
        for target, target_id in (("workspace", "w1"), ("tab", "w1:t1"), ("pane", CLAUDE)):
            await link.rename(target, target_id, "name")
            await link.close(target, target_id)

        assert herdr.methods == ALLOWED_METHODS

    async def test_a_new_terminal_never_takes_the_desktop_focus(self, herdr, link) -> None:
        await link.create("workspace", workspace_id=None, pane_id=None, cwd="/work", label="api")
        await link.create("tab", workspace_id="w1", pane_id=None, cwd=None, label=None)
        await link.create("split_down", workspace_id=None, pane_id=CLAUDE, cwd=None, label="logs")

        assert herdr.sent("workspace.create") == [{"focus": False, "cwd": "/work", "label": "api"}]
        assert herdr.sent("tab.create") == [{"workspace_id": "w1", "focus": False}]
        assert herdr.sent("pane.split") == [
            {"target_pane_id": CLAUDE, "direction": "down", "focus": False}
        ]
        assert herdr.sent("pane.rename") == [{"pane_id": "w1:p9", "label": "logs"}]

    async def test_a_split_whose_label_is_refused_still_answers_the_pane(
        self, herdr, link
    ) -> None:
        # The pane exists. An error would make the user split again.
        herdr.failures["pane.rename"] = TuiApiError("invalid_params", "label is too long")

        pane_id = await link.create(
            "split_down", workspace_id=None, pane_id=CLAUDE, cwd=None, label="logs"
        )

        assert pane_id == "w1:p9"
        assert len(herdr.sent("pane.split")) == 1

    async def test_a_create_whose_agent_does_not_start_reports_its_pane(
        self, herdr, link
    ) -> None:
        # The pane exists. An error alone would make the user create it again.
        herdr.failures["agent.start"] = TuiApiError("start_failed", "claude is not installed")

        with pytest.raises(AgentNotStarted) as failed:
            await link.create(
                "workspace", workspace_id=None, pane_id=None, cwd="/work", label="api",
                agent="claude",
            )

        assert failed.value.pane_id == "w2:p1"
        assert failed.value.error.detail() == {
            "code": "herdr_refused", "message": "claude is not installed",
        }
        assert len(herdr.sent("workspace.create")) == 1

    @pytest.mark.parametrize("mode", [WATCH, CONTROL])
    async def test_a_terminal_id_that_reads_as_an_option_opens_no_stream(
        self, herdr, link, mode
    ) -> None:
        # The id goes on the command line of the bridge process.
        herdr.snapshot["panes"][0]["terminal_id"] = "--config=/tmp/other.toml"
        await link.rename("pane", CLAUDE, "x")

        with pytest.raises(errors.HerdrError) as refused:
            await link.open_stream(CLAUDE, mode, 80, 24, sink=None)

        assert (refused.value.status, refused.value.code) == (404, "pane_not_found")

    @pytest.mark.parametrize(
        "placement,ids",
        [("tab", {"workspace_id": None, "pane_id": CLAUDE}),
         ("split_right", {"workspace_id": "w1", "pane_id": None})],
    )
    async def test_a_placement_without_its_anchor_is_refused(
        self, herdr, link, placement, ids
    ) -> None:
        before = len(herdr.requests)

        with pytest.raises(errors.HerdrError) as refused:
            await link.create(placement, cwd=None, label=None, **ids)

        assert refused.value.status == 422
        assert len(herdr.requests) == before

    async def test_a_message_to_an_agent_goes_through_its_prompt(self, herdr, link) -> None:
        await link.submit(CLAUDE, "fix the bug")

        assert herdr.sent("agent.prompt") == [{"target": CLAUDE, "text": "fix the bug"}]
        assert herdr.sent("pane.send_input") == []

    async def test_a_refused_prompt_is_typed_as_the_answer(self, herdr, link) -> None:
        # An agent that waits for an answer refuses a prompt, and the user's
        # line may be that answer.
        herdr.failures["agent.prompt"] = TuiApiError("agent_blocked", "agent is blocked")

        await link.submit(CLAUDE, "yes")

        assert herdr.sent("pane.send_input") == [
            {"pane_id": CLAUDE, "text": "yes", "keys": ["enter"]}
        ]

    async def test_a_message_to_a_plain_terminal_is_typed(self, herdr, link) -> None:
        await link.submit(SHELL, "ls")

        assert herdr.sent("agent.prompt") == []
        assert herdr.sent("pane.send_input") == [
            {"pane_id": SHELL, "text": "ls", "keys": ["enter"]}
        ]

    async def test_start_agent_uses_herdrs_name_for_the_kind(self, herdr, link) -> None:
        await link.start_agent(SHELL, "antigravity")

        (started,) = herdr.sent("agent.start")
        assert (started["kind"], started["pane_id"], started["args"]) == ("agy", SHELL, [])

    async def test_an_agent_herdr_cannot_start_is_refused(self, herdr, link) -> None:
        with pytest.raises(errors.HerdrError) as refused:
            await link.start_agent(SHELL, "custom_0123456789ab")

        assert refused.value.code == "not_supported"
        assert herdr.sent("agent.start") == []

    async def test_an_unknown_pane_is_refused_before_any_request(self, herdr, link) -> None:
        before = len(herdr.requests)

        for call in (
            link.read_text("w9:p9", 10),
            link.send_bytes("w9:p9", b"y"),
            link.submit("w9:p9", "go"),
            link.pane_grid("w9:p9"),
        ):
            with pytest.raises(errors.HerdrError) as refused:
                await call
            assert (refused.value.status, refused.value.code) == (404, "pane_not_found")

        assert len(herdr.requests) == before

    @pytest.mark.parametrize(
        "failure,status,code",
        [
            (TuiApiError("pane_not_found", "no pane"), 404, "pane_not_found"),
            (TuiApiError("workspace_not_found", "no workspace"), 404, "not_found"),
            (TuiApiError("invalid_params", "label is too long"), 409, "herdr_refused"),
            (ServerGone("connection closed"), 404, "server_not_found"),
        ],
        ids=["pane-gone", "group-gone", "refused", "server-gone"],
    )
    async def test_a_herdr_failure_becomes_a_coded_refusal(
        self, herdr, link, failure, status, code
    ) -> None:
        herdr.failures["pane.rename"] = failure

        with pytest.raises(errors.HerdrError) as refused:
            await link.rename("pane", CLAUDE, "x")

        assert (refused.value.status, refused.value.code) == (status, code)

    async def test_a_refusal_carries_herdrs_own_words(self, herdr, link) -> None:
        herdr.failures["pane.close"] = TuiApiError("busy", "The pane has unsaved work.")

        with pytest.raises(errors.HerdrError) as refused:
            await link.close("pane", CLAUDE)

        assert refused.value.detail() == {
            "code": "herdr_refused", "message": "The pane has unsaved work.",
        }

    async def test_read_text_reports_older_lines(self, herdr, link) -> None:
        assert await link.read_text(CLAUDE, 50) == ("last line", True)
        assert herdr.sent("pane.read") == [
            {"pane_id": CLAUDE, "source": "recent", "lines": 50, "format": "text"}
        ]


class TestSettings:
    async def test_the_settings_of_the_env_file_reach_the_backend(
        self, herdr, monkeypatch, tmp_path
    ) -> None:
        # pydantic-settings reads ``.env`` and does not export it, and only the
        # Linux service exports it itself: a setting read from ``os.environ``
        # is lost on every other host.
        root = tmp_path / "herdr-config"
        socket_path = root / "sessions" / "work" / "herdr.sock"
        socket_path.parent.mkdir(parents=True)
        socket_path.touch()
        binary = tmp_path / "herdr"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        env_file = tmp_path / "agent.env"
        env_file.write_text(
            f"MOBAIDE_HERDR_BIN={binary}\n"
            f"MOBAIDE_HERDR_CONFIG_DIR={root}\n"
            "MOBAIDE_HERDR_ENABLED=1\n"
        )
        for name in ("MOBAIDE_HERDR_BIN", "MOBAIDE_HERDR_CONFIG_DIR", "MOBAIDE_HERDR_ENABLED"):
            monkeypatch.delenv(name, raising=False)

        async def version_of(argv, **_kwargs) -> str | None:
            return "herdr 0.9.5" if list(argv) == [str(binary), "--version"] else None

        monkeypatch.setattr(tui, "config_dir", REAL_CONFIG_DIR)
        monkeypatch.setattr(tui, "installed_binaries", REAL_INSTALLED_BINARIES)
        monkeypatch.setattr(tui, "run_text", version_of)
        app = create_app(
            config=AgentConfig(
                _env_file=str(env_file),
                jwt_secret_key=TEST_JWT_SECRET,
                user_store_path=str(tmp_path / "users.json"),
                projects_store_path=str(tmp_path / "projects.json"),
                voice_dir=str(tmp_path / "voice"),
            )
        )
        service = app.state.herdr
        events = app.state.event_bus.subscribe()

        await service.start()
        try:
            while (await asyncio.wait_for(events.get(), 5)).type != "herdr.changed":
                pass
            (server,) = service.snapshot().servers
        finally:
            await service.shutdown()
            await app.state.session_manager.shutdown()

        # The socket folder of the setting, and a bridge binary only the
        # setting names: without it this server could not give control.
        assert server.server_id == "tui:work"
        assert herdr.socket_path == str(socket_path)
        assert server.features.control is True


class _StalledStream:
    """A connection to a server that is alive and does not read: a write
    never returns."""

    def __init__(self) -> None:
        self.reader = asyncio.StreamReader()
        self.closed = False

    async def write(self, data: bytes) -> None:
        await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closed = True


class TestDeadlines:
    @pytest.mark.parametrize(
        "call",
        [
            lambda client: client.request("pane.send_text", {"text": "x"}, timeout=0.01),
            lambda client: client.subscribe([]),
        ],
        ids=["request", "subscribe"],
    )
    async def test_a_server_that_does_not_read_fails_the_call_in_time(
        self, monkeypatch, call
    ) -> None:
        # Without a deadline on the write, the caller waits for as long as
        # that server lives.
        stream = _StalledStream()

        async def connect(_path: str) -> _StalledStream:
            return stream

        monkeypatch.setattr(tui, "open_local_stream", connect)
        monkeypatch.setattr(tui, "REQUEST_TIMEOUT_SECONDS", 0.01)

        with pytest.raises(errors.HerdrError) as failed:
            await asyncio.wait_for(call(REAL_TUI_CLIENT("/unused/herdr.sock")), 5)

        assert (failed.value.status, failed.value.code) == (502, "server_unreachable")
        assert stream.closed


# More than any pipe buffer holds, so the write waits for a reader.
PIPE_OVERFLOW_BYTES = 4 * 1024 * 1024
# Stands in for a bridge that stopped reading its stdin. It ends on its own
# if a test fails before it ends it.
IDLE_CHILD = [sys.executable, "-c", "import time; time.sleep(30)"]


class _EndSink:
    """The pane session's side of a stream: it only notes the end."""

    def __init__(self) -> None:
        self.ended = asyncio.Event()
        self.reasons: list[str | None] = []

    def on_output(self, data: bytes, *, repaint: bool) -> None:
        return None

    def on_grid(self, cols: int, rows: int) -> None:
        return None

    def on_closed(self, reason: str | None) -> None:
        self.reasons.append(reason)
        self.ended.set()


class TestBridgeProcess:
    async def test_a_bridge_that_does_not_read_its_input_is_ended_in_time(
        self, herdr, monkeypatch
    ) -> None:
        # Input is serial for a session. Without a deadline, one key to a
        # bridge that hangs holds every later key and the close of the view.
        async def spawn_idle(_argv, **_kwargs):
            return await REAL_SPAWN_PIPED(IDLE_CHILD)

        monkeypatch.setattr(tui, "spawn_piped", spawn_idle)
        monkeypatch.setattr(tui, "REQUEST_TIMEOUT_SECONDS", 0.2)
        link = TuiLink(SERVER_ID, "default", "/unused/herdr.sock", lambda: None, lambda: [])
        sink = _EndSink()
        stream = tui._BridgeStream(link, "/unused/herdr", "term-1", CONTROL, 80, 24, sink)
        await stream.start()

        try:
            await asyncio.wait_for(stream.write(b"x" * PIPE_OVERFLOW_BYTES), 10)
            # The process was ended: the pane session learns it and can open
            # a new stream.
            await asyncio.wait_for(sink.ended.wait(), 10)
        finally:
            await asyncio.wait_for(stream.aclose(), 10)

        assert len(sink.reasons) == 1
