"""Tests for ``HerdrService``: the mirror of the herdr servers, the alerts and
the pane sessions.

A wrong answer here is a notification nobody asked for, a missed "your move",
a pane view that outlives its pane, or a client action that reaches a herdr
server with unchecked input. The server is a ``ScriptedLink`` and the timers
are a manual ``Scheduler`` (``herdr_support.py``): no test sleeps, opens a
socket or starts a ``herdr`` process.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

import pytest

from mobaide.core.models import SessionConfig
from mobaide.core.pty_backend import FakePtyBackend
from mobaide.core.session_manager import MaxSessionsError, SessionManager
from mobaide.herdr import errors
from mobaide.herdr import service as service_module
from mobaide.herdr.dispatch import dispatch_herdr
from mobaide.herdr.models import HerdrCloseRequest, HerdrCreateRequest, HerdrRenameRequest
from mobaide.herdr.service import (
    DONE_QUIET_SECONDS,
    EVENT_ATTENTION,
    EVENT_CHANGED,
    PUBLISH_DEBOUNCE_SECONDS,
    HerdrService,
    pane_session_id,
)

from .conftest import default_shell
from .herdr_support import (  # noqa: F401  (``scheduler`` is a fixture)
    AGENT_PANE,
    SERVER_ID,
    SHELL_PANE,
    ScriptedBackend,
    ScriptedLink,
    ScriptedStream,
    Scheduler,
    scheduler,
    settle,
)

DISCOVER_SECONDS = 5.0


@dataclass
class Harness:
    service: HerdrService
    backend: ScriptedBackend
    link: ScriptedLink
    manager: SessionManager
    scheduler: Scheduler
    published: list[tuple[str, dict]]

    def flush(self) -> int:
        """Let the publish debounce elapse."""
        return self.scheduler.elapse(PUBLISH_DEBOUNCE_SECONDS)

    def events(self, event_type: str) -> list[dict]:
        return [payload for kind, payload in self.published if kind == event_type]

    def set_status(self, status: str, turn: int | None = None) -> None:
        self.link.set_pane(AGENT_PANE, status=status, turn=turn)
        self.flush()


async def _started(scheduler, manager: SessionManager) -> Harness:  # noqa: F811
    """A running service on ``manager``, attached to its scripted server."""
    published: list[tuple[str, dict]] = []
    backend = ScriptedBackend()
    service = HerdrService(
        publish=lambda kind, payload: published.append((kind, payload)),
        session_manager=manager,
        backends=[lambda _agents: backend],
        discover_seconds=DISCOVER_SECONDS,
    )
    await service.start()
    await asyncio.wait_for(backend.attached.wait(), 5)
    assert backend.link is not None
    await asyncio.wait_for(backend.link.running.wait(), 5)
    return Harness(service, backend, backend.link, manager, scheduler, published)


@pytest.fixture
async def herdr(scheduler):  # noqa: F811
    harness = await _started(
        scheduler, SessionManager(max_sessions=1, backend_factory=FakePtyBackend)
    )
    yield harness
    await harness.service.shutdown()
    await harness.manager.shutdown()


@pytest.fixture
async def seen(herdr) -> Harness:
    """The service after its first read of the server."""
    herdr.flush()
    herdr.published.clear()
    return herdr


def _session_config(session_id: str) -> SessionConfig:
    return SessionConfig(
        session_id=session_id, command=default_shell(), rows=24, cols=80,
        scrollback_size=50,
    )


class TestSnapshot:
    async def test_first_read_publishes_the_whole_state(self, herdr) -> None:
        herdr.flush()

        assert herdr.published == [
            (EVENT_CHANGED, {"servers": [herdr.link.state().model_dump(mode="json")]})
        ]

    async def test_changes_in_one_burst_leave_as_one_event(self, seen) -> None:
        seen.link.set_pane(AGENT_PANE, title="one")
        seen.link.set_pane(AGENT_PANE, title="two")

        assert seen.flush() == 1
        (payload,) = seen.events(EVENT_CHANGED)
        titles = [
            pane["title"]
            for pane in payload["servers"][0]["workspaces"][0]["tabs"][0]["panes"]
        ]
        assert titles == ["two", "shell"]

    async def test_a_signal_without_a_change_sends_nothing(self, seen) -> None:
        seen.link.set_pane(AGENT_PANE)
        seen.flush()

        assert seen.published == []

    async def test_a_new_client_gets_the_state_again(self, seen) -> None:
        seen.service.publish_snapshot()

        assert seen.events(EVENT_CHANGED) == [
            seen.service.snapshot().model_dump(mode="json")
        ]

    async def test_a_stopped_server_leaves_the_state(self, seen, monkeypatch) -> None:
        # Without the wait for a server that restarts: the refusal comes at once.
        monkeypatch.setattr(service_module, "REOPEN_WAIT_SECONDS", 0.0)
        seen.link.stop_server()
        await settle()
        seen.flush()

        assert seen.events(EVENT_CHANGED) == [{"servers": []}]
        with pytest.raises(errors.HerdrError) as refused:
            await seen.service.open_pane(SERVER_ID, AGENT_PANE)
        assert (refused.value.status, refused.value.code) == (404, "server_not_found")


class TestAlerts:
    async def test_the_state_at_first_sight_does_not_alert(self, herdr) -> None:
        # An agent that was blocked before Mobaide attached is not news.
        herdr.link.set_pane(AGENT_PANE, status="blocked")
        herdr.flush()

        assert herdr.events(EVENT_ATTENTION) == []
        assert herdr.scheduler.pending(DONE_QUIET_SECONDS) == []

    async def test_blocked_alerts_at_once_with_the_pane_identity(self, seen) -> None:
        seen.set_status("blocked")

        assert seen.events(EVENT_ATTENTION) == [
            {
                "server_id": SERVER_ID,
                "pane_id": AGENT_PANE,
                "reason": "awaiting_permission",
                "title": "api refactor",
                "agent": "claude",
            }
        ]

    @pytest.mark.parametrize(
        "status,reason",
        [("done", "turn_finished"), ("failed", "turn_failed")],
    )
    async def test_a_finished_turn_alerts_after_the_quiet_window(
        self, seen, status, reason
    ) -> None:
        seen.set_status(status, turn=1)

        assert seen.events(EVENT_ATTENTION) == []
        assert seen.scheduler.elapse(DONE_QUIET_SECONDS) == 1
        assert [a["reason"] for a in seen.events(EVENT_ATTENTION)] == [reason]

    async def test_a_turn_that_ran_between_two_reads_alerts_again(self, seen) -> None:
        # The ``working`` of turn 2 fell between two reads: only the new
        # ``turn`` tells it apart from the turn that already alerted.
        seen.set_status("done", turn=1)
        seen.scheduler.elapse(DONE_QUIET_SECONDS)
        seen.set_status("done", turn=2)

        # Alerts of one pane keep a minimum spacing, so the second one waits.
        waiting = [h for h in seen.scheduler.handles if not (h.cancelled or h.fired)
                   and h.delay not in (PUBLISH_DEBOUNCE_SECONDS, DISCOVER_SECONDS)]
        assert len(waiting) == 1
        waiting[0].callback(*waiting[0].args)
        assert [a["reason"] for a in seen.events(EVENT_ATTENTION)] == [
            "turn_finished", "turn_finished",
        ]

    async def test_new_work_inside_the_window_cancels_the_alert(self, seen) -> None:
        # Herdr reads the state off the screen and can say ``done`` while the
        # agent only paused.
        seen.set_status("done", turn=1)
        seen.set_status("working", turn=1)

        assert seen.scheduler.elapse(DONE_QUIET_SECONDS) == 0
        assert seen.events(EVENT_ATTENTION) == []

    async def test_client_input_cancels_a_pending_alert(self, seen) -> None:
        session_id = await seen.service.open_pane(SERVER_ID, AGENT_PANE)
        session = await seen.manager.get_session(session_id)
        seen.set_status("done", turn=1)

        await session.write(b"y")

        assert seen.scheduler.elapse(DONE_QUIET_SECONDS) == 0
        assert seen.events(EVENT_ATTENTION) == []

    async def test_a_closed_pane_drops_its_pending_alert(self, seen) -> None:
        seen.set_status("done", turn=1)

        seen.link.remove_pane(AGENT_PANE)
        seen.flush()

        assert seen.scheduler.elapse(DONE_QUIET_SECONDS) == 0
        assert seen.events(EVENT_ATTENTION) == []

    async def test_a_plain_terminal_never_alerts(self, seen) -> None:
        seen.link.set_pane(SHELL_PANE, status="blocked")
        seen.flush()

        assert seen.events(EVENT_ATTENTION) == []

    async def test_an_answered_prompt_can_alert_again(self, seen) -> None:
        seen.set_status("blocked")
        seen.set_status("working")
        seen.set_status("blocked")

        # Alerts of one pane keep a minimum spacing, so the second one waits.
        waiting = [h for h in seen.scheduler.handles if not (h.cancelled or h.fired)
                   and h.delay not in (PUBLISH_DEBOUNCE_SECONDS, DISCOVER_SECONDS)]
        assert len(seen.events(EVENT_ATTENTION)) == 1
        assert len(waiting) == 1
        waiting[0].callback(*waiting[0].args)
        assert [a["reason"] for a in seen.events(EVENT_ATTENTION)] == [
            "awaiting_permission", "awaiting_permission",
        ]


class TestPaneSessions:
    async def test_a_pane_always_gets_the_same_session_id(self, seen) -> None:
        first = await seen.service.open_pane(SERVER_ID, AGENT_PANE)
        second = await seen.service.open_pane(SERVER_ID, AGENT_PANE)
        other = await seen.service.open_pane(SERVER_ID, SHELL_PANE)

        assert first == second == pane_session_id(SERVER_ID, AGENT_PANE)
        assert re.fullmatch(r"herdr-[0-9a-f]{24}", first)
        assert other != first
        assert len(seen.manager.external_sessions()) == 2

    def test_the_session_id_is_one_per_server_and_pane(self) -> None:
        # ``a`` + ``b:c`` and ``a:b`` + ``c`` must not share a view.
        assert pane_session_id("tui:a", "b:c") != pane_session_id("tui:a:b", "c")

    async def test_a_pane_session_is_in_no_list_and_takes_no_session_slot(
        self, seen
    ) -> None:
        session_id = await seen.service.open_pane(SERVER_ID, AGENT_PANE)

        assert seen.manager.list_sessions() == []
        assert (await seen.manager.get_session(session_id)).session_id == session_id
        # ``max_sessions`` is 1: the pane view must not use the one slot.
        await seen.manager.create_session(_session_config("owned"))
        with pytest.raises(MaxSessionsError):
            await seen.manager.create_session(_session_config("one-too-many"))
        # A full agent still shows panes.
        await seen.service.open_pane(SERVER_ID, SHELL_PANE)
        assert [s.session_id for s in seen.manager.list_sessions()] == ["owned"]

    async def test_a_closed_pane_ends_and_forgets_its_session(self, seen) -> None:
        session_id = await seen.service.open_pane(SERVER_ID, AGENT_PANE)
        session = await seen.manager.get_session(session_id)

        seen.link.remove_pane(AGENT_PANE)
        seen.flush()
        await settle()

        assert not session.is_alive()
        assert await seen.manager.get_session(session_id) is None
        with pytest.raises(errors.HerdrError) as refused:
            await seen.service.open_pane(SERVER_ID, AGENT_PANE)
        assert (refused.value.status, refused.value.code) == (404, "pane_not_found")

    async def test_a_stopped_server_ends_its_pane_sessions(self, seen) -> None:
        session_id = await seen.service.open_pane(SERVER_ID, AGENT_PANE)
        session = await seen.manager.get_session(session_id)

        seen.link.stop_server()
        await settle()

        assert not session.is_alive()

    async def test_only_an_unwatched_session_is_swept(self, seen, monkeypatch) -> None:
        monkeypatch.setattr(service_module, "PANE_SESSION_IDLE_SECONDS", -1.0)
        watched_id = await seen.service.open_pane(SERVER_ID, AGENT_PANE)
        idle_id = await seen.service.open_pane(SERVER_ID, SHELL_PANE)
        (await seen.manager.get_session(watched_id)).viewer_attached()
        await settle()

        assert seen.scheduler.elapse(DISCOVER_SECONDS) == 1
        await settle()

        assert await seen.manager.get_session(idle_id) is None
        assert await seen.manager.get_session(watched_id) is not None
        # The sweep detaches; it never closes the pane.
        assert seen.link.closed_targets == []
        # The id is stable, so the client that comes back opens the pane again.
        assert await seen.service.open_pane(SERVER_ID, SHELL_PANE) == idle_id

    async def test_a_pane_opens_again_when_its_server_comes_back(self, seen) -> None:
        # A pane view ends the moment its link drops, and the client asks for
        # the pane again at once. The new link has no mirror yet: an answer
        # now would call a pane closed that is only between two links.
        session_id = await seen.service.open_pane(SERVER_ID, AGENT_PANE)
        seen.link.stop_server()
        await settle()
        first_read = asyncio.Event()
        seen.backend.restart(first_read)
        passes = seen.backend.discover_calls

        opening = asyncio.ensure_future(seen.service.open_pane(SERVER_ID, AGENT_PANE))
        await settle()

        assert not opening.done()
        assert seen.backend.discover_calls == passes + 1

        first_read.set()

        assert await asyncio.wait_for(opening, 5) == session_id
        assert seen.backend.link is not seen.link
        assert (await seen.manager.get_session(session_id)).is_alive()

    async def test_a_pane_opens_when_its_restarting_server_is_found_late(self, seen) -> None:
        # The first discovery pass after the drop does not find the server
        # yet: the open must try again, not call the pane closed.
        session_id = await seen.service.open_pane(SERVER_ID, AGENT_PANE)
        seen.link.stop_server()
        await settle()

        opening = asyncio.ensure_future(seen.service.open_pane(SERVER_ID, AGENT_PANE))
        await settle()
        assert not opening.done()

        seen.backend.restart()
        assert seen.scheduler.elapse(service_module.REOPEN_RETRY_SECONDS) == 1

        assert await asyncio.wait_for(opening, 5) == session_id
        assert seen.backend.link is not seen.link

    async def test_an_outdated_server_opens_no_pane(self, seen) -> None:
        seen.link.server_state = "outdated"
        seen.link.message = "Update Herdr to version 0.9.2 or later."

        with pytest.raises(errors.HerdrError) as refused:
            await seen.service.open_pane(SERVER_ID, AGENT_PANE)

        assert refused.value.status == 409
        assert refused.value.detail() == {
            "code": "server_outdated",
            "message": "Update Herdr to version 0.9.2 or later.",
        }

    async def test_control_needs_an_open_pane_session(self, seen) -> None:
        with pytest.raises(errors.HerdrError) as refused:
            await seen.service.set_control(SERVER_ID, AGENT_PANE, True)

        assert refused.value.code == "pane_not_found"

    async def test_agent_shutdown_leaves_every_pane_running(self, seen) -> None:
        session_id = await seen.service.open_pane(SERVER_ID, AGENT_PANE)
        (await seen.manager.get_session(session_id)).viewer_attached()
        await settle()

        await seen.service.shutdown()
        await seen.manager.shutdown()

        assert seen.link.closed_targets == []
        assert set(seen.link.panes) == {AGENT_PANE, SHELL_PANE}


class TestReopenDuringRelease:
    """A pane keeps its session id, and the last step of a release ends the
    fanout of that id. A view that is adopted while the release of the one
    before it still runs loses its fanout to that step and stays empty."""

    @pytest.fixture
    async def live(self, app, scheduler):  # noqa: F811
        """The service on the app's session manager: a pane session gets its
        fanout from the created hook and loses it in the removed hook, as in
        the running agent. The ``app`` fixture stops the manager and the
        fanouts."""
        harness = await _started(scheduler, app.state.session_manager)
        yield harness
        await harness.service.shutdown()

    async def test_the_new_view_is_made_after_the_release_and_keeps_its_fanout(
        self, app, live, monkeypatch
    ) -> None:
        monkeypatch.setattr(service_module, "PANE_SESSION_IDLE_SECONDS", -1.0)
        fanouts = app.state.fanouts
        closing = asyncio.Event()

        class HeldClose(ScriptedStream):
            """A bridge that is slow to leave."""

            async def aclose(self) -> None:
                await closing.wait()
                await super().aclose()

        live.link.stream_class = HeldClose
        session_id = await live.service.open_pane(SERVER_ID, AGENT_PANE)
        old = await live.manager.get_session(session_id)
        old_fanout = fanouts[session_id]
        old.viewer_attached()
        await settle()
        # The last client leaves: the stream begins to close and does not end.
        old.viewer_detached()
        await settle()
        live.link.stream_class = ScriptedStream

        # The idle sweep drops the view, and its ``stop()`` waits for the stream.
        assert live.scheduler.elapse(DISCOVER_SECONDS) == 1
        await settle()
        opening = asyncio.ensure_future(live.service.open_pane(SERVER_ID, AGENT_PANE))
        await settle()

        assert not opening.done()
        assert await live.manager.get_session(session_id) is None
        assert fanouts[session_id] is old_fanout

        closing.set()

        assert await asyncio.wait_for(opening, 5) == session_id
        await settle()
        new = await live.manager.get_session(session_id)
        assert new is not old and new.is_alive()
        assert fanouts[session_id] is not old_fanout
        events = fanouts[session_id].subscribe()
        new.viewer_attached()
        await settle()
        live.link.live_streams[-1].sink.on_output(b"second view", repaint=True)
        await settle()

        received = [events.get_nowait() for _ in range(events.qsize())]
        assert [e.data for e in received if e.event_type == "output"] == [b"second view"]

    async def test_a_cancelled_open_does_not_cut_the_release_of_an_ended_view_short(
        self, scheduler  # noqa: F811
    ) -> None:
        # The client that asked for the pane again went away. A release that
        # ended with that call would leave the id half released, and the next
        # view would get what is left of the old one.
        removed: list[str] = []
        ending = asyncio.Event()

        async def held_hook(session_id: str) -> None:
            """The last step of a release, slow to finish."""
            await ending.wait()
            removed.append(session_id)

        manager = SessionManager(on_session_removed=held_hook)
        herdr = await _started(scheduler, manager)
        try:
            session_id = await herdr.service.open_pane(SERVER_ID, AGENT_PANE)
            old = await manager.get_session(session_id)
            # The view ended, and the service has not dropped it yet.
            old.end()

            opening = asyncio.ensure_future(herdr.service.open_pane(SERVER_ID, AGENT_PANE))
            await settle()
            assert not opening.done()
            opening.cancel()
            await settle()

            assert opening.cancelled()
            assert removed == []

            ending.set()
            await settle()

            assert removed == [session_id]
            assert await manager.get_session(session_id) is None
            assert (
                await asyncio.wait_for(herdr.service.open_pane(SERVER_ID, AGENT_PANE), 5)
                == session_id
            )
            new = await manager.get_session(session_id)
            assert new is not old and new.is_alive()
            new.viewer_attached()
            await settle()
            herdr.link.live_streams[-1].sink.on_output(b"second view", repaint=True)

            events = new.event_queue
            received = [events.get_nowait() for _ in range(events.qsize())]
            assert [e.data for e in received if e.event_type == "output"] == [b"second view"]
        finally:
            ending.set()
            await herdr.service.shutdown()
            await manager.shutdown()

    @pytest.mark.parametrize(
        ("lose", "code"),
        [
            (lambda link: link.stop_server(), "server_not_found"),
            (lambda link: link.remove_pane(AGENT_PANE), "pane_not_found"),
        ],
        ids=["the server stopped", "the pane was closed"],
    )
    async def test_a_pane_that_is_lost_during_the_release_gets_no_new_view(
        self, scheduler, lose, code  # noqa: F811
    ) -> None:
        # A link that ends takes only the views it knows with it. A view that
        # is made after the wait on a link that is gone stays "alive" for good.
        ending = asyncio.Event()

        async def held_hook(session_id: str) -> None:
            """The last step of a release, slow to finish."""
            await ending.wait()

        manager = SessionManager(on_session_removed=held_hook)
        herdr = await _started(scheduler, manager)
        try:
            session_id = await herdr.service.open_pane(SERVER_ID, AGENT_PANE)
            (await manager.get_session(session_id)).end()
            opening = asyncio.ensure_future(herdr.service.open_pane(SERVER_ID, AGENT_PANE))
            await settle()
            assert not opening.done()

            lose(herdr.link)
            await settle()
            assert not opening.done()
            ending.set()

            with pytest.raises(errors.HerdrError) as refused:
                await asyncio.wait_for(opening, 5)
            assert (refused.value.status, refused.value.code) == (404, code)
            await settle()
            assert await manager.get_session(session_id) is None
            assert manager.external_sessions() == []
        finally:
            ending.set()
            await herdr.service.shutdown()
            await manager.shutdown()


class TestActions:
    @pytest.mark.parametrize("cwd", ["relative/folder", "{tmp}/missing", "{tmp}/a-file"])
    async def test_a_folder_that_is_not_an_existing_absolute_folder_is_refused(
        self, seen, tmp_path, cwd
    ) -> None:
        (tmp_path / "a-file").write_text("x")
        request = HerdrCreateRequest(
            server_id=SERVER_ID, placement="workspace", cwd=cwd.format(tmp=tmp_path)
        )

        with pytest.raises(errors.HerdrError) as refused:
            await seen.service.create(request)

        assert (refused.value.status, refused.value.code) == (422, "invalid_folder")
        assert seen.link.created == []

    async def test_create_sends_the_checked_folder_and_the_trimmed_label(
        self, seen, tmp_path
    ) -> None:
        request = HerdrCreateRequest(
            server_id=SERVER_ID, placement="tab", workspace_id="w1",
            cwd=f"  {tmp_path}  ", label="  api  ", agent="claude",
        )

        assert await seen.service.create(request) == ("w1:p9", None)
        assert seen.link.created == [
            {
                "placement": "tab", "workspace_id": "w1", "pane_id": None,
                "cwd": str(tmp_path), "label": "api", "agent": "claude",
            }
        ]

    async def test_a_create_whose_agent_does_not_start_still_answers_the_pane(
        self, seen, tmp_path
    ) -> None:
        # The pane exists. An error alone would make the user create it again.
        seen.link.agent_start_error = errors.upstream("claude is not installed")
        body = {
            "server_id": SERVER_ID, "placement": "workspace",
            "cwd": str(tmp_path), "agent": "claude",
        }

        pane_id, agent_error = await seen.service.create(HerdrCreateRequest(**body))
        relayed = await dispatch_herdr(seen.service, "POST", "/api/herdr/terminals", body, None)

        assert pane_id == "w1:p9"
        assert agent_error is not None and "claude is not installed" in agent_error
        assert relayed == {
            "status": 200, "body": {"pane_id": "w1:p9", "agent_error": agent_error},
        }

    @pytest.mark.parametrize("asked,read", [(5000, 1000), (0, 1), (300, 300)])
    async def test_a_text_read_stays_inside_the_line_limit(
        self, seen, asked, read
    ) -> None:
        await seen.service.read_text(SERVER_ID, AGENT_PANE, asked)

        assert seen.link.text_reads == [(AGENT_PANE, read)]

    async def test_an_unknown_server_is_refused(self, seen) -> None:
        with pytest.raises(errors.HerdrError) as refused:
            await seen.service.read_text("tui:other", AGENT_PANE, 10)

        assert (refused.value.status, refused.value.code) == (404, "server_not_found")


class TestAudit:
    """Herdr actions change programs Mobaide does not own, so each leaves one
    audit record, as session and git actions do."""

    @pytest.fixture
    def records(self, monkeypatch) -> list[tuple[str, dict]]:
        written: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            service_module, "audit_event",
            lambda event, **fields: written.append((event, fields)),
        )
        return written

    async def test_each_action_leaves_one_record(self, seen, records, tmp_path) -> None:
        service = seen.service
        session_id = await service.open_pane(SERVER_ID, AGENT_PANE)
        (await seen.manager.get_session(session_id)).viewer_attached()
        await settle()

        await service.create(
            HerdrCreateRequest(
                server_id=SERVER_ID, placement="split_down", pane_id=AGENT_PANE,
                cwd=str(tmp_path), label="secret project", agent="claude",
            )
        )
        await service.start_agent(SERVER_ID, SHELL_PANE, "claude")
        await service.rename(
            HerdrRenameRequest(
                server_id=SERVER_ID, target="pane", id=SHELL_PANE, label="secret project"
            )
        )
        await service.set_control(SERVER_ID, AGENT_PANE, True, "phone")
        await service.close(HerdrCloseRequest(server_id=SERVER_ID, target="tab", id="w1:t1"))

        assert records == [
            (
                "audit.herdr.created",
                {
                    "target": f"{SERVER_ID}/pane/w1:p9", "placement": "split_down",
                    "agent": "claude", "agent_started": True, "actor": None,
                },
            ),
            (
                "audit.herdr.agent_started",
                {"target": f"{SERVER_ID}/pane/{SHELL_PANE}", "agent": "claude", "actor": None},
            ),
            ("audit.herdr.renamed", {"target": f"{SERVER_ID}/pane/{SHELL_PANE}", "actor": None}),
            (
                "audit.herdr.control",
                {
                    "target": f"{SERVER_ID}/pane/{AGENT_PANE}", "control": True,
                    "client_id": "phone", "actor": None,
                },
            ),
            ("audit.herdr.closed", {"target": f"{SERVER_ID}/tab/w1:t1", "actor": None}),
        ]
        # A name or a folder the user chose is not log content.
        assert "secret project" not in repr(records)
        assert str(tmp_path) not in repr(records)
        # The actions themselves reached the server.
        assert seen.link.renamed == [("pane", SHELL_PANE, "secret project")]
        assert seen.link.started_agents == [(SHELL_PANE, "claude")]

    async def test_a_create_whose_agent_did_not_start_is_recorded_as_such(
        self, seen, records
    ) -> None:
        seen.link.agent_start_error = errors.upstream("claude is not installed")

        await seen.service.create(
            HerdrCreateRequest(server_id=SERVER_ID, placement="workspace", agent="claude")
        )

        assert records == [
            (
                "audit.herdr.created",
                {
                    "target": f"{SERVER_ID}/pane/w1:p9", "placement": "workspace",
                    "agent": "claude", "agent_started": False, "actor": None,
                },
            )
        ]

    async def test_each_record_names_the_user_of_the_action(
        self, seen, records, tmp_path
    ) -> None:
        # The record must say who changed a program on the desktop. The key is
        # there with no value for an action that came through the relay.
        service = seen.service
        session_id = await service.open_pane(SERVER_ID, AGENT_PANE)
        (await seen.manager.get_session(session_id)).viewer_attached()
        await settle()

        await service.create(
            HerdrCreateRequest(server_id=SERVER_ID, placement="workspace", cwd=str(tmp_path)),
            actor="alice",
        )
        await service.start_agent(SERVER_ID, SHELL_PANE, "claude", actor="alice")
        await service.rename(
            HerdrRenameRequest(server_id=SERVER_ID, target="pane", id=SHELL_PANE, label="logs"),
            actor="alice",
        )
        await service.set_control(SERVER_ID, AGENT_PANE, True, "phone", actor="alice")
        await service.close(
            HerdrCloseRequest(server_id=SERVER_ID, target="pane", id=SHELL_PANE), actor="alice"
        )
        await service.close(HerdrCloseRequest(server_id=SERVER_ID, target="tab", id="w1:t1"))

        assert [(event, fields["actor"]) for event, fields in records] == [
            ("audit.herdr.created", "alice"),
            ("audit.herdr.agent_started", "alice"),
            ("audit.herdr.renamed", "alice"),
            ("audit.herdr.control", "alice"),
            ("audit.herdr.closed", "alice"),
            ("audit.herdr.closed", None),
        ]

    async def test_only_a_change_of_the_controller_is_recorded(self, seen, records) -> None:
        # Every client that leaves a pane view sends a release, and an app
        # sends its take again. A record for each would bury the real ones.
        service = seen.service
        session_id = await service.open_pane(SERVER_ID, AGENT_PANE)
        session = await seen.manager.get_session(session_id)
        session.viewer_attached()
        await settle()

        await service.set_control(SERVER_ID, AGENT_PANE, True, "phone")
        await service.set_control(SERVER_ID, AGENT_PANE, False, "tablet")
        await service.set_control(SERVER_ID, AGENT_PANE, True, "phone")
        assert session.wire_state_extras()["pane"]["controller"] == "phone"
        await service.set_control(SERVER_ID, AGENT_PANE, True, "tablet")
        await service.set_control(SERVER_ID, AGENT_PANE, False, "tablet")
        await service.set_control(SERVER_ID, AGENT_PANE, False, "tablet")

        assert [
            (event, fields["control"], fields["client_id"]) for event, fields in records
        ] == [
            ("audit.herdr.control", True, "phone"),
            ("audit.herdr.control", True, "tablet"),
            ("audit.herdr.control", False, "tablet"),
        ]

    async def test_a_refused_action_leaves_no_record(self, seen, records) -> None:
        with pytest.raises(errors.HerdrError):
            await seen.service.close(
                HerdrCloseRequest(server_id="tui:other", target="pane", id=AGENT_PANE)
            )
        with pytest.raises(errors.HerdrError):
            await seen.service.set_control(SERVER_ID, AGENT_PANE, True, "phone")

        assert records == []
