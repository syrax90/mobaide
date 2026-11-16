"""Tests for ``HerdrPaneSession``: a herdr pane shown as a terminal session.

The pane and its program belong to herdr and to a user at the desktop, so each
rule below guards something that user would notice:

* watch mode never resizes the pane and tells the client the real grid,
* the stream runs only while a client is attached, and one stream serves all,
* control is one explicit mode that ends with the last viewer,
* stopping the session detaches and never closes the pane.

The stream is a ``ScriptedStream`` (``herdr_support.py``); nothing here opens
a socket.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from mobaide.core.models import SessionState
from mobaide.herdr import errors
from mobaide.herdr import pane_session as pane_session_module
from mobaide.herdr.backend import CONTROL, WATCH
from mobaide.herdr.pane_session import (
    MAX_PENDING_SUBMITS,
    PANE_EVENT,
    PASTE_PREFIX_DELAY_SECONDS,
    RETRY_SECONDS,
    STREAM_HEALTHY_SECONDS,
    SUBMIT_CR_DELAY_SECONDS,
    HerdrPaneSession,
    submit_text,
)

from .herdr_support import (  # noqa: F401  (``scheduler`` is a fixture)
    AGENT_PANE,
    GRID,
    ScriptedLink,
    ScriptedStream,
    scheduler,
    settle,
)

# Client ids as the apps send them with "Take control".
PHONE = "phone"
TABLET = "tablet"


@pytest.fixture
def link() -> ScriptedLink:
    return ScriptedLink()


@pytest.fixture
async def session(link) -> HerdrPaneSession:
    pane = HerdrPaneSession("herdr-test", link, AGENT_PANE)
    await pane.start()
    return pane


def _events(session: HerdrPaneSession) -> list[str]:
    """Drain the session's event queue; return the event types in order."""
    types: list[str] = []
    while not session.event_queue.empty():
        types.append(session.event_queue.get_nowait().event_type)
    return types


class HeldSubmit:
    """Stands in for ``link.submit`` of a herdr that holds a message until
    the program takes it: a message is delivered only after ``release()``."""

    def __init__(self, link: ScriptedLink) -> None:
        self._link = link
        self._gate = asyncio.Event()
        # The messages that reached herdr, delivered or not.
        self.started: list[str] = []

    async def __call__(self, pane_id: str, text: str) -> None:
        self.started.append(text)
        await self._gate.wait()
        self._link.submitted.append((pane_id, text))

    def release(self) -> None:
        self._gate.set()


class _EndsInStart(ScriptedStream):
    """A bridge that leaves at once: the end is reported before ``start``
    returns."""

    async def start(self) -> None:
        await super().start()
        self.sink.on_closed("the bridge left")


class TestSubmitText:
    @pytest.mark.parametrize(
        "frame,expected",
        [
            (b"fix the bug", "fix the bug"),
            # The line-clear keys a client puts in front of a message.
            (b"\x01\x0bfix the bug", "fix the bug"),
            # Herdr adds its own paste markers; a second pair would be typed
            # into the agent as text.
            (b"\x01\x0b\x1b[200~line one\nline two\x1b[201~", "line one\nline two"),
            (b"one\r\ntwo\rthree", "one\ntwo\nthree"),
            (b"\x01\x0b", ""),
        ],
        ids=["plain", "clear-keys", "bracketed-paste", "carriage-returns", "empty"],
    )
    def test_only_the_message_goes_to_herdr(self, frame, expected) -> None:
        assert submit_text(frame) == expected


class TestWatchMode:
    async def test_no_stream_runs_before_a_client_attaches(self, link, session) -> None:
        await settle()

        assert link.streams == []
        assert session.idle_since is not None

    async def test_first_viewer_opens_a_watch_stream_at_the_pane_size(
        self, link, session
    ) -> None:
        session.viewer_attached()
        await settle()

        (stream,) = link.live_streams
        assert (stream.pane_id, stream.mode, stream.size) == (AGENT_PANE, WATCH, GRID)
        assert session.wire_state_extras() == {
            "pane": {"mode": "watch", "cols": GRID[0], "rows": GRID[1]}
        }
        # The client learns the grid from a new state frame.
        assert PANE_EVENT in _events(session)
        assert session.idle_since is None

    async def test_all_viewers_share_one_stream_until_the_last_leaves(
        self, link, session
    ) -> None:
        session.viewer_attached()
        session.viewer_attached()
        await settle()
        assert len(link.streams) == 1

        session.viewer_detached()
        await settle()
        assert len(link.live_streams) == 1

        session.viewer_detached()
        await settle()
        assert link.live_streams == []
        assert session.idle_since is not None
        assert session.is_alive()

    async def test_a_client_resize_never_reaches_the_pane(self, link, session) -> None:
        session.viewer_attached()
        await settle()

        await session.resize(rows=20, cols=40)
        await settle()

        (stream,) = link.streams
        assert stream.resizes == []
        assert session.wire_state_extras()["pane"]["cols"] == GRID[0]

    async def test_a_new_pane_size_sends_a_new_state_block(self, link, session) -> None:
        session.viewer_attached()
        await settle()
        _events(session)

        link.streams[0].sink.on_grid(120, 30)

        assert session.wire_state_extras() == {
            "pane": {"mode": "watch", "cols": 120, "rows": 30}
        }
        assert _events(session) == [PANE_EVENT]

    async def test_a_message_that_herdr_holds_does_not_hold_the_next_key(
        self, link, session, monkeypatch
    ) -> None:
        # Herdr holds a message until the program takes it. Esc and Ctrl-C
        # are how the user stops that program: they must not wait behind it.
        held = HeldSubmit(link)
        monkeypatch.setattr(link, "submit", held)
        session.viewer_attached()
        await settle()

        async def send() -> None:
            await session.write_submit(b"\x01\x0bfirst")
            await session.write_submit(b"second")
            await session.write(b"\x1b")

        # The deadline only turns a hang into a failure.
        await asyncio.wait_for(send(), 5)
        await settle()

        assert link.typed == [(AGENT_PANE, b"\x1b")]
        # One message at a time: the second is not at herdr before the first
        # is delivered, so the two cannot change places.
        assert held.started == ["first"]
        assert link.submitted == []

        held.release()
        await settle()

        assert link.submitted == [(AGENT_PANE, "first"), (AGENT_PANE, "second")]
        assert link.streams[0].written == []

    async def test_stop_drops_the_messages_that_are_not_delivered(
        self, link, session, monkeypatch
    ) -> None:
        held = HeldSubmit(link)
        monkeypatch.setattr(link, "submit", held)
        session.viewer_attached()
        await settle()
        await session.write_submit(b"first")
        await session.write_submit(b"second")
        await settle()

        await session.stop()
        held.release()
        await settle()

        # The second message never reached herdr; the first ended with the view.
        assert held.started == ["first"]
        assert link.submitted == []

    async def test_a_message_past_the_pending_limit_is_dropped(
        self, link, session, monkeypatch
    ) -> None:
        # A client that sends faster than herdr takes must not grow the list
        # of waiting messages without a limit.
        held = HeldSubmit(link)
        monkeypatch.setattr(link, "submit", held)
        session.viewer_attached()
        await settle()
        messages = [f"message {n}" for n in range(MAX_PENDING_SUBMITS + 1)]

        for text in messages:
            await session.write_submit(text.encode())
        await settle()
        assert held.started == ["message 0"]
        held.release()
        for _ in messages:
            await settle()

        # The messages inside the limit arrive, in the order they were sent.
        assert link.submitted == [(AGENT_PANE, text) for text in messages[:-1]]

        # The limit counts the messages that wait, not the ones that arrived.
        await session.write_submit(b"later")
        await settle()

        assert link.submitted[-1] == (AGENT_PANE, "later")
        assert len(link.submitted) == MAX_PENDING_SUBMITS + 1

    @pytest.mark.parametrize("report", [b"\x1b[I", b"\x1b[O"])
    async def test_a_focus_report_is_not_typed_into_the_pane(
        self, link, session, report
    ) -> None:
        # A phone that gains focus must not type into a desktop agent.
        await session.write(report)

        assert link.typed == []


class TestReplay:
    async def test_replay_starts_at_the_last_full_repaint(self, link, session) -> None:
        session.viewer_attached()
        await settle()
        sink = link.streams[0].sink

        sink.on_output(b"old screen", repaint=True)
        sink.on_output(b"old diff", repaint=False)
        sink.on_output(b"new screen", repaint=True)
        sink.on_output(b"+diff", repaint=False)

        assert b"".join(session.iter_ring_chunks()) == b"new screen+diff"

    async def test_a_ring_past_its_limit_starts_a_new_repaint(
        self, link, session, monkeypatch
    ) -> None:
        monkeypatch.setattr(pane_session_module, "RING_MAX_BYTES", 8)
        session.viewer_attached()
        await settle()
        sink = link.streams[0].sink
        sink.on_output(b"screen", repaint=True)

        sink.on_output(b"diff past the limit", repaint=False)
        await settle()

        first, second = link.streams
        assert first.closed and second.started and not second.closed
        assert b"".join(session.iter_ring_chunks()) == b""


class TestControlMode:
    async def test_control_sends_keys_and_sizes_straight_to_the_stream(
        self, link, session, monkeypatch
    ) -> None:
        monkeypatch.setattr(pane_session_module, "SUBMIT_CR_DELAY_SECONDS", 0)
        session.viewer_attached()
        await session.set_control(True)
        await settle()
        control = link.live_streams[0]

        await session.write(b"y")
        await session.write_submit(b"\x01\x0bgo")
        await session.resize(rows=30, cols=100)

        assert control.written == [b"y", b"\x01\x0bgo", b"\r"]
        assert control.resizes == [(100, 30)]
        assert link.typed == [] and link.submitted == []

    async def test_the_keys_ahead_of_a_paste_go_out_on_their_own(
        self, link, session
    ) -> None:
        # The sequence of ``TerminalSession.write_submit``: Claude CLI drops
        # the Enter after a paste that shared its chunk with the clients'
        # Ctrl+A / Ctrl+K prefix.
        session.viewer_attached()
        await session.set_control(True)
        await settle()
        control = link.live_streams[0]
        events: list[tuple[str, object]] = []
        scripted_write = control.write

        async def recording_write(data: bytes) -> None:
            events.append(("write", data))
            await scripted_write(data)

        control.write = recording_write  # type: ignore[method-assign]
        sleep_mock = AsyncMock(side_effect=lambda delay: events.append(("sleep", delay)))

        paste = b"\x1b[200~line one\nline two\x1b[201~"
        with patch("mobaide.herdr.pane_session.asyncio.sleep", sleep_mock):
            await session.write_submit(b"\x01\x0b" + paste)

        assert events == [
            ("write", b"\x01\x0b"),
            ("sleep", PASTE_PREFIX_DELAY_SECONDS),
            ("write", paste),
            ("sleep", SUBMIT_CR_DELAY_SECONDS),
            ("write", b"\r"),
        ]
        assert link.typed == [] and link.submitted == []

    @pytest.mark.parametrize(
        "ends_in_wait,by_stream,by_link",
        [
            (1, [b"\x01\x0b"], [b"\x1b[200~one\ntwo\x1b[201~", b"\r"]),
            (2, [b"\x01\x0b", b"\x1b[200~one\ntwo\x1b[201~"], [b"\r"]),
        ],
        ids=["before-the-paste", "before-the-enter"],
    )
    async def test_a_message_is_finished_when_control_ends_during_one_of_its_waits(
        self, link, session, ends_in_wait, by_stream, by_link
    ) -> None:
        # Another device takes the pane between the text and its Enter. A
        # message without its Enter stays in the input line of the program.
        session.viewer_attached()
        await session.set_control(True)
        await settle()
        control = link.live_streams[0]
        waits: list[float] = []

        async def wait(delay: float) -> None:
            waits.append(delay)
            if len(waits) == ends_in_wait:
                control.sink.on_closed("control taken")

        with patch("mobaide.herdr.pane_session.asyncio.sleep", AsyncMock(side_effect=wait)):
            await session.write_submit(b"\x01\x0b\x1b[200~one\ntwo\x1b[201~")

        assert waits == [PASTE_PREFIX_DELAY_SECONDS, SUBMIT_CR_DELAY_SECONDS]
        assert control.written == by_stream
        # The rest is typed into the pane without control.
        assert link.typed == [(AGENT_PANE, part) for part in by_link]
        assert session.mode == WATCH

    async def test_release_goes_back_to_the_pane_size(self, link, session) -> None:
        session.viewer_attached()
        await session.set_control(True)
        await settle()

        await session.set_control(False)
        await settle()

        (stream,) = link.live_streams
        assert (stream.mode, stream.size) == (WATCH, GRID)
        assert session.wire_state_extras()["pane"]["mode"] == "watch"

    async def test_control_ends_when_the_last_viewer_leaves(self, link, session) -> None:
        # A phone that went to sleep must not keep a desktop pane at phone size.
        session.viewer_attached()
        await session.set_control(True)
        await settle()

        session.viewer_detached()
        await settle()

        assert session.mode == WATCH
        assert link.live_streams == []

    async def test_another_controller_puts_this_view_back_to_watch(
        self, link, session
    ) -> None:
        session.viewer_attached()
        await session.set_control(True)
        await settle()
        control = link.live_streams[0]

        control.sink.on_closed("control taken")
        await settle()

        assert session.mode == WATCH
        (stream,) = [s for s in link.streams if s is not control and not s.closed]
        assert stream.mode == WATCH

    async def test_a_server_without_control_refuses_it(self, link, session) -> None:
        link.control = False
        session.viewer_attached()
        await settle()

        with pytest.raises(errors.HerdrError) as refused:
            await session.set_control(True)

        assert (refused.value.status, refused.value.code) == (409, "not_supported")
        assert session.mode == WATCH

    async def test_a_failed_control_open_falls_back_to_watch(self, link, session) -> None:
        session.viewer_attached()
        await settle()
        link.open_errors[CONTROL] = errors.upstream("pane is busy")

        await session.set_control(True)
        await settle()

        assert session.mode == WATCH
        assert session.is_alive()
        assert [s.mode for s in link.live_streams] == [WATCH]


class TestViewerCounts:
    """Relay viewers are counted apart from direct ones: a tunnel drop says
    nothing about a browser on the direct socket."""

    async def test_a_tunnel_drop_forgets_only_the_relay_viewers(self, link, session) -> None:
        session.viewer_attached(2, relay=True)
        session.viewer_attached()
        await settle()

        session.reset_relay_viewers()
        await settle()

        assert session.viewers == 1
        assert len(link.live_streams) == 1

        # The tunnel is back: one replayed attach stands for both relay clients.
        session.viewer_attached(2, relay=True)
        assert session.viewers == 3
        session.viewer_detached(relay=True)
        session.viewer_detached(relay=True)
        session.viewer_detached()
        await settle()

        assert session.viewers == 0
        assert link.live_streams == []

    async def test_a_late_relay_detach_takes_no_direct_viewer_away(self, link, session) -> None:
        session.viewer_attached(relay=True)
        session.viewer_attached()
        session.reset_relay_viewers()

        # The detach of a client that the tunnel drop already forgot.
        session.viewer_detached(relay=True)
        await settle()

        assert session.viewers == 1
        assert len(link.live_streams) == 1


    async def test_a_relay_count_is_a_number_and_not_a_step(self, link, session) -> None:
        # Two relay clients open a view that was made again under its id. The
        # detach of each old socket arrives ahead of the attach of the new
        # one; counted as steps, the first detach is lost and the second
        # takes a viewer that is there.
        session.viewer_attached()
        for count in (1, 2, 1, 2):
            session.set_relay_viewers(count)
        await settle()

        assert session.viewers == 3
        assert len(link.live_streams) == 1

        # No relay client is left: the browser on the direct socket still watches.
        session.set_relay_viewers(0)
        await settle()

        assert session.viewers == 1
        assert session.idle_since is None
        assert len(link.live_streams) == 1

    async def test_the_stream_follows_the_relay_count(self, link, session) -> None:
        session.set_relay_viewers(2)
        await settle()
        assert session.idle_since is None
        assert len(link.live_streams) == 1

        session.set_relay_viewers(1)
        await settle()
        assert len(link.live_streams) == 1

        session.set_relay_viewers(0)
        await settle()
        assert link.live_streams == []
        assert session.idle_since is not None


class TestOneController:
    """One client controls a pane. Its id is in the state block, so every
    other client draws the controller's grid and sends no size."""

    @staticmethod
    async def _take(link, session, client_id: str = PHONE) -> ScriptedStream:
        session.viewer_attached()
        session.viewer_attached()
        await settle()
        await session.set_control(True, client_id)
        await settle()
        _events(session)
        (control,) = link.live_streams
        return control

    async def test_a_take_names_the_controller_and_gives_the_others_a_grid(
        self, link, session
    ) -> None:
        session.viewer_attached()
        session.viewer_attached()
        await settle()
        _events(session)

        await session.set_control(True, PHONE)
        await settle()

        # Until the controller sends its size, the pane keeps its own.
        assert session.wire_state_extras() == {
            "pane": {
                "mode": "control", "controller": PHONE,
                "cols": GRID[0], "rows": GRID[1],
            }
        }
        (control,) = link.live_streams
        assert (control.mode, control.size) == (CONTROL, GRID)
        assert PANE_EVENT in _events(session)

    async def test_the_controllers_size_reaches_the_pane_and_every_viewer(
        self, link, session
    ) -> None:
        control = await self._take(link, session)

        await session.resize(rows=20, cols=40)

        assert control.resizes == [(40, 20)]
        assert session.wire_state_extras() == {
            "pane": {"mode": "control", "controller": PHONE, "cols": 40, "rows": 20}
        }
        # The watchers learn the new grid from a new state frame.
        assert _events(session) == [PANE_EVENT]

        await session.resize(rows=20, cols=40)

        assert _events(session) == []

    async def test_only_the_controller_can_give_control_back(self, link, session) -> None:
        # A watcher that leaves the pane view sends a release too.
        control = await self._take(link, session)
        await session.resize(rows=20, cols=40)

        await session.set_control(False, TABLET)
        await settle()

        assert session.mode == CONTROL
        assert session.wire_state_extras()["pane"] == {
            "mode": "control", "controller": PHONE, "cols": 40, "rows": 20,
        }
        assert link.live_streams == [control]

        await session.set_control(False, PHONE)
        await settle()

        (stream,) = link.live_streams
        assert (stream.mode, stream.size) == (WATCH, GRID)
        assert session.wire_state_extras() == {
            "pane": {"mode": "watch", "cols": GRID[0], "rows": GRID[1]}
        }

    async def test_a_second_take_moves_control_to_that_client(self, link, session) -> None:
        control = await self._take(link, session)
        await session.resize(rows=20, cols=40)
        _events(session)

        await session.set_control(True, TABLET)
        await settle()

        assert session.wire_state_extras()["pane"]["controller"] == TABLET
        assert PANE_EVENT in _events(session)
        # The first controller is a watcher now: its release ends nothing.
        await session.set_control(False, PHONE)
        assert session.mode == CONTROL

        await session.resize(rows=30, cols=100)

        assert control.resizes[-1] == (100, 30)
        assert session.wire_state_extras()["pane"] == {
            "mode": "control", "controller": TABLET, "cols": 100, "rows": 30,
        }

    async def test_a_take_with_no_viewer_is_refused(self, link, session) -> None:
        # Control with no viewer would wait for the next client that attaches.
        with pytest.raises(errors.HerdrError) as refused:
            await session.set_control(True, PHONE)
        await settle()

        assert (refused.value.status, refused.value.code) == (409, "pane_not_open")
        assert session.mode == WATCH
        assert link.streams == []

    async def test_a_size_sent_in_watch_mode_is_not_kept_for_a_later_take(
        self, link, session
    ) -> None:
        # A watcher has no say in the size of the pane, now or later.
        session.viewer_attached()
        await settle()
        await session.resize(rows=20, cols=40)

        await session.set_control(True, PHONE)
        await settle()

        watch, control = link.streams
        assert watch.resizes == []
        assert (control.mode, control.size) == (CONTROL, GRID)
        assert control.resizes == []

    async def test_a_size_that_arrives_while_the_control_stream_opens_is_applied(
        self, link, session
    ) -> None:
        # The app sends its size right after the take, before the stream is up.
        session.viewer_attached()
        await settle()
        link.open_gate = asyncio.Event()
        await session.set_control(True, PHONE)
        await settle()

        await session.resize(rows=20, cols=40)
        link.open_gate.set()
        await settle()

        (control,) = link.live_streams
        assert (control.mode, control.size) == (CONTROL, GRID)
        assert control.resizes == [(40, 20)]


class TestRetry:
    async def test_the_retry_delay_grows_and_a_repaint_does_not_reset_it(
        self, link, session, scheduler  # noqa: F811
    ) -> None:
        # Each failure puts a notice on the screen. The notice and a repaint
        # are the session's own output: neither counts as a sign that the
        # stream works.
        def armed() -> list[float]:
            return [h.delay for h in scheduler.handles if not (h.cancelled or h.fired)]

        link.open_errors[WATCH] = errors.upstream("pane is busy")
        session.viewer_attached()
        await settle()

        delays: list[float] = []
        for _ in range(len(RETRY_SECONDS) + 1):
            (delay,) = armed()
            delays.append(delay)
            scheduler.elapse(delay)
            await settle()

        assert delays == [*RETRY_SECONDS, RETRY_SECONDS[-1]]
        assert session.is_alive()
        assert b"pane is busy" in b"".join(session.iter_ring_chunks())

        del link.open_errors[WATCH]
        scheduler.elapse(RETRY_SECONDS[-1])
        await settle()
        (stream,) = link.live_streams
        stream.sink.on_output(b"screen", repaint=True)
        stream.sink.on_closed(None)

        assert armed() == [RETRY_SECONDS[-1]]


class TestEndedStream:
    """A stream that ends on its own still has its bridge process or its
    channel, and a pane whose stream keeps ending must not be opened in a
    tight loop."""

    @staticmethod
    def _armed(scheduler) -> list[float]:  # noqa: F811
        return [h.delay for h in scheduler.handles if not (h.cancelled or h.fired)]

    @pytest.fixture
    async def lived(self, monkeypatch):
        """Move the loop clock forward: ``lived(30)`` is 30 s of stream life."""
        loop = asyncio.get_running_loop()
        real_time = loop.time
        ahead = [0.0]
        monkeypatch.setattr(loop, "time", lambda: real_time() + ahead[0])

        def advance(seconds: float) -> None:
            ahead[0] += seconds

        return advance

    @pytest.mark.parametrize("mode", [WATCH, CONTROL])
    async def test_a_stream_that_ends_on_its_own_is_closed_once(
        self, link, session, scheduler, mode  # noqa: F811
    ) -> None:
        session.viewer_attached()
        if mode == CONTROL:
            await session.set_control(True)
        await settle()
        (ended,) = link.live_streams

        ended.sink.on_closed("the bridge left")
        await settle()
        # The next stream opens: at once after control, after the delay in watch.
        scheduler.elapse(RETRY_SECONDS[0])
        await settle()

        assert ended.close_calls == 1
        (stream,) = link.live_streams
        assert stream is not ended and stream.mode == WATCH

    async def test_a_stream_that_paints_and_ends_is_opened_again_more_slowly(
        self, link, session, scheduler  # noqa: F811
    ) -> None:
        # A first frame is no sign that the stream works: a bridge that draws
        # the screen and leaves would start again every second for ever.
        session.viewer_attached()
        await settle()

        delays: list[float] = []
        for _ in range(6):
            (stream,) = link.live_streams
            stream.sink.on_output(b"screen", repaint=True)
            stream.sink.on_closed(None)
            (delay,) = self._armed(scheduler)
            delays.append(delay)
            scheduler.elapse(delay)
            await settle()

        assert delays == [1.0, 2.0, 5.0, 15.0, 30.0, 30.0]
        assert session.is_alive()
        assert all(s.close_calls == 1 for s in link.streams[:-1])

    @pytest.mark.parametrize(
        "life,next_delay",
        [(STREAM_HEALTHY_SECONDS - 1, RETRY_SECONDS[2]), (STREAM_HEALTHY_SECONDS, RETRY_SECONDS[0])],
        ids=["short-life", "healthy"],
    )
    async def test_only_a_stream_that_lived_starts_the_delays_again(
        self, link, session, scheduler, lived, life, next_delay  # noqa: F811
    ) -> None:
        session.viewer_attached()
        await settle()
        for delay in RETRY_SECONDS[:2]:
            link.live_streams[0].sink.on_closed(None)
            scheduler.elapse(delay)
            await settle()
        (stream,) = link.live_streams

        lived(life)
        stream.sink.on_closed(None)

        assert self._armed(scheduler) == [next_delay]

    async def test_a_stream_that_ends_inside_its_start_is_tried_again(
        self, link, session, scheduler  # noqa: F811
    ) -> None:
        # The end is reported before ``start`` returns. A session that keeps
        # this stream shows a pane that never draws and never retries.
        link.stream_class = _EndsInStart
        session.viewer_attached()
        await settle()

        (ended,) = link.streams
        assert ended.close_calls == 1
        assert link.live_streams == []
        assert session.is_alive()
        assert self._armed(scheduler) == [RETRY_SECONDS[0]]

        link.stream_class = ScriptedStream
        scheduler.elapse(RETRY_SECONDS[0])
        await settle()

        (stream,) = link.live_streams
        assert (stream.mode, stream.size) == (WATCH, GRID)


class TestLifetime:
    async def test_stop_detaches_and_leaves_the_pane_open(self, link, session) -> None:
        session.viewer_attached()
        await settle()

        await session.stop()

        assert session.state is SessionState.STOPPED
        assert link.live_streams == []
        assert link.closed_targets == []
        assert AGENT_PANE in link.panes
        assert _events(session)[-1] == "stopped"

    async def test_a_closed_pane_ends_the_session(self, link, session) -> None:
        session.viewer_attached()
        await settle()
        _events(session)

        link.remove_pane(AGENT_PANE)
        session.server_changed()
        await settle()

        assert not session.is_alive()
        assert link.live_streams == []
        assert _events(session) == ["stopped"]

    async def test_a_pane_that_is_gone_at_open_ends_the_session(
        self, link, session
    ) -> None:
        link.open_errors[WATCH] = errors.pane_not_found()

        session.viewer_attached()
        await settle()

        assert not session.is_alive()

    async def test_a_stopped_session_takes_no_input_and_no_control(
        self, link, session
    ) -> None:
        await session.stop()

        await session.write(b"y")
        await session.write_submit(b"go")
        with pytest.raises(errors.HerdrError) as refused:
            await session.set_control(True)

        assert link.typed == [] and link.submitted == []
        assert refused.value.code == "pane_not_found"

    async def test_input_is_reported_to_the_alert_gate(self, link) -> None:
        inputs: list[None] = []
        pane = HerdrPaneSession(
            "herdr-test", link, AGENT_PANE, on_input=lambda: inputs.append(None)
        )
        await pane.start()

        await pane.write(b"y")
        await pane.write(b"\x1b[I")

        assert len(inputs) == 1


async def test_a_grid_revision_change_reopens_the_watch_stream(link, session) -> None:
    # A user at the desktop resized the pane: the frames of the old stream
    # address cells of the old grid.
    session.viewer_attached()
    await settle()

    link.grid = (120, 30)
    link.grid_revision += 1
    session.server_changed()
    await settle()

    first, second = link.streams
    assert first.closed
    assert (second.mode, second.size) == (WATCH, (120, 30))
    assert session.wire_state_extras() == {
        "pane": {"mode": "watch", "cols": 120, "rows": 30}
    }
