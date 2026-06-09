"""Tests for ``SessionEventFanout`` (pump) and ``SessionListBroadcaster``."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from mobaide.core.models import (
    SessionEvent,
    SessionInfo,
    SessionState,
    session_to_wire_dict,
)
from mobaide.core.session import TerminalSession
from mobaide.agent.ws.event_fanout import (
    SessionEventFanout,
    SessionListBroadcaster,
)


# ---------- helpers ----------


def _fake_session(session_id: str = "s1") -> TerminalSession:
    """A bare-bones stand-in for TerminalSession that the fanout pump uses.

    The pump only touches ``session.session_id`` and ``session.event_queue``.
    Cast to TerminalSession so call sites type-check without duck-typed params.
    """
    fake = SimpleNamespace(
        session_id=session_id,
        event_queue=asyncio.Queue(),
    )
    return cast(TerminalSession, fake)


def _session_info(
    session_id: str = "s1",
    state: SessionState = SessionState.RUNNING,
    title: str | None = None,
    project_name: str | None = None,
) -> SessionInfo:
    return SessionInfo(
        session_id=session_id,
        state=state,
        created_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        pid=4242,
        command=["cmd.exe"],
        cwd="C:/tmp",
        title=title,
        project_name=project_name,
    )


async def _drain_one(q: asyncio.Queue, timeout: float = 1.0):
    return await asyncio.wait_for(q.get(), timeout=timeout)


async def _stop_and_await(fanout: SessionEventFanout) -> None:
    fanout.stop()
    task = fanout._task
    if task is not None:
        await asyncio.gather(task, return_exceptions=True)


# ---------- SessionEventFanout ----------


class TestSessionEventFanoutLifecycle:
    async def test_start_creates_active_task(self):
        session = _fake_session()
        fanout = SessionEventFanout(session)
        try:
            fanout.start()
            assert fanout._task is not None
            assert not fanout._task.done()
        finally:
            await _stop_and_await(fanout)

    async def test_start_is_idempotent_while_running(self):
        session = _fake_session()
        fanout = SessionEventFanout(session)
        try:
            fanout.start()
            first_task = fanout._task
            fanout.start()
            assert fanout._task is first_task
        finally:
            await _stop_and_await(fanout)

    async def test_stop_cancels_pump_task(self):
        session = _fake_session()
        fanout = SessionEventFanout(session)
        fanout.start()
        task = fanout._task
        assert task is not None
        fanout.stop()
        await asyncio.gather(task, return_exceptions=True)
        assert task.done()

    async def test_stop_before_start_is_noop(self):
        session = _fake_session()
        fanout = SessionEventFanout(session)
        fanout.stop()  # no task yet
        assert fanout._task is None

    async def test_has_subscribers_reflects_count(self):
        session = _fake_session()
        fanout = SessionEventFanout(session)
        assert fanout.has_subscribers is False
        q = fanout.subscribe()
        assert fanout.has_subscribers is True
        fanout.unsubscribe(q)
        assert fanout.has_subscribers is False


class TestSessionEventFanoutPump:
    async def test_output_event_fans_out_to_subscribers_only(self):
        session = _fake_session("s1")
        broadcaster = MagicMock()
        broadcaster.notify_serialized = AsyncMock()
        event_bus = MagicMock()
        event_bus.publish = MagicMock()

        def get_sessions():
            return [_session_info("s1")]

        fanout = SessionEventFanout(
            session,
            broadcaster=broadcaster,
            get_sessions=get_sessions,
            event_bus=event_bus,
        )
        try:
            sub = fanout.subscribe()
            fanout.start()

            ev = SessionEvent(event_type="output", session_id="s1", data=b"hi")
            await session.event_queue.put(ev)

            received = await _drain_one(sub)
            assert received is ev
            # Output events must NOT trigger state-change notifications.
            broadcaster.notify_serialized.assert_not_called()
            event_bus.publish.assert_not_called()
        finally:
            await _stop_and_await(fanout)

    async def test_state_event_triggers_broadcaster_and_bus(self):
        session = _fake_session("s1")
        broadcaster = MagicMock()
        broadcaster.notify_serialized = AsyncMock()
        event_bus = MagicMock()
        event_bus.publish = MagicMock()

        info = _session_info("s1", SessionState.RUNNING)

        def get_sessions():
            return [info]

        fanout = SessionEventFanout(
            session,
            broadcaster=broadcaster,
            get_sessions=get_sessions,
            event_bus=event_bus,
        )
        try:
            sub = fanout.subscribe()
            fanout.start()

            await session.event_queue.put(
                SessionEvent(event_type="started", session_id="s1"),
            )
            # Wait for the fanout subscriber first — guarantees the pump has
            # progressed past the state-change side effects.
            received = await _drain_one(sub)
            assert received.event_type == "started"

            # Yield a tick for any remaining scheduled callbacks.
            await asyncio.sleep(0)

            broadcaster.notify_serialized.assert_awaited_once()
            session_dicts = broadcaster.notify_serialized.await_args.args[0]
            assert isinstance(session_dicts, list)
            assert session_dicts[0]["session_id"] == "s1"
            assert session_dicts[0]["state"] == "running"
            assert session_dicts[0]["pid"] == 4242
            assert session_dicts[0]["command"] == ["cmd.exe"]
            assert session_dicts[0]["cwd"] == "C:/tmp"
            assert "created_at" in session_dicts[0]

            # Both sessions.changed and session.updated must be published.
            types_published = [call.args[0] for call in event_bus.publish.call_args_list]
            assert "sessions.changed" in types_published
            assert "session.updated" in types_published

            # sessions.changed payload has the session list.
            changed_call = next(
                c for c in event_bus.publish.call_args_list
                if c.args[0] == "sessions.changed"
            )
            assert changed_call.args[1] == {"sessions": session_dicts}

            # session.updated payload has the single matching dict.
            updated_call = next(
                c for c in event_bus.publish.call_args_list
                if c.args[0] == "session.updated"
            )
            assert updated_call.args[1] == {"session": session_dicts[0]}
        finally:
            await _stop_and_await(fanout)

    async def test_state_event_without_get_sessions_skips_notifications(self):
        session = _fake_session("s1")
        broadcaster = MagicMock()
        broadcaster.notify_serialized = AsyncMock()
        event_bus = MagicMock()
        event_bus.publish = MagicMock()

        fanout = SessionEventFanout(
            session,
            broadcaster=broadcaster,
            get_sessions=None,  # guard must skip broadcaster + event_bus
            event_bus=event_bus,
        )
        try:
            sub = fanout.subscribe()
            fanout.start()

            await session.event_queue.put(
                SessionEvent(event_type="started", session_id="s1"),
            )
            received = await _drain_one(sub)
            assert received.event_type == "started"

            await asyncio.sleep(0)
            broadcaster.notify_serialized.assert_not_called()
            event_bus.publish.assert_not_called()
        finally:
            await _stop_and_await(fanout)

    async def test_session_updated_skipped_when_not_in_list(self):
        """If get_sessions returns a list without the current session,
        ``session.updated`` is NOT published, but ``sessions.changed`` still is.
        """
        session = _fake_session("s1")
        broadcaster = MagicMock()
        broadcaster.notify_serialized = AsyncMock()
        event_bus = MagicMock()
        event_bus.publish = MagicMock()

        def get_sessions():
            # Return a list missing s1 — e.g. the session was just removed.
            return [_session_info("other-session", SessionState.RUNNING)]

        fanout = SessionEventFanout(
            session,
            broadcaster=broadcaster,
            get_sessions=get_sessions,
            event_bus=event_bus,
        )
        try:
            sub = fanout.subscribe()
            fanout.start()

            await session.event_queue.put(
                SessionEvent(event_type="stopped", session_id="s1"),
            )
            received = await _drain_one(sub)
            assert received.event_type == "stopped"

            await asyncio.sleep(0)
            broadcaster.notify_serialized.assert_awaited_once()
            published_types = [c.args[0] for c in event_bus.publish.call_args_list]
            assert "sessions.changed" in published_types
            assert "session.updated" not in published_types
        finally:
            await _stop_and_await(fanout)

    async def test_title_event_publishes_session_updated_only(self):
        """A ``title`` event must publish exactly one ``session.updated`` and
        must NOT trigger ``broadcaster.notify_serialized`` or
        ``sessions.changed`` — title is a per-session delta, not a list
        membership change.
        """
        session = _fake_session("s1")
        broadcaster = MagicMock()
        broadcaster.notify_serialized = AsyncMock()
        event_bus = MagicMock()
        event_bus.publish = MagicMock()

        info = _session_info("s1", SessionState.RUNNING, title="* New Task")

        def get_sessions():
            return [info]

        fanout = SessionEventFanout(
            session,
            broadcaster=broadcaster,
            get_sessions=get_sessions,
            event_bus=event_bus,
        )
        try:
            sub = fanout.subscribe()
            fanout.start()

            await session.event_queue.put(
                SessionEvent(event_type="title", session_id="s1", data=None),
            )
            received = await _drain_one(sub)
            assert received.event_type == "title"

            await asyncio.sleep(0)
            broadcaster.notify_serialized.assert_not_called()

            published = event_bus.publish.call_args_list
            types_published = [c.args[0] for c in published]
            assert types_published == ["session.updated"]
            assert published[0].args[1] == {"session": session_to_wire_dict(info)}
            # Sanity: the wire dict actually contains the new title.
            assert published[0].args[1]["session"]["title"] == "* New Task"
        finally:
            await _stop_and_await(fanout)

    async def test_title_event_without_get_sessions_is_noop(self):
        session = _fake_session("s1")
        broadcaster = MagicMock()
        broadcaster.notify_serialized = AsyncMock()
        event_bus = MagicMock()
        event_bus.publish = MagicMock()

        fanout = SessionEventFanout(
            session,
            broadcaster=broadcaster,
            get_sessions=None,
            event_bus=event_bus,
        )
        try:
            sub = fanout.subscribe()
            fanout.start()

            await session.event_queue.put(
                SessionEvent(event_type="title", session_id="s1", data=None),
            )
            received = await _drain_one(sub)
            assert received.event_type == "title"

            await asyncio.sleep(0)
            broadcaster.notify_serialized.assert_not_called()
            event_bus.publish.assert_not_called()
        finally:
            await _stop_and_await(fanout)

    async def test_title_event_when_session_missing_from_list_is_noop(self):
        """If ``get_sessions()`` does not contain the current session — e.g.
        it was just removed in a race — the title branch must publish
        nothing rather than crash or fabricate a payload.
        """
        session = _fake_session("s1")
        broadcaster = MagicMock()
        broadcaster.notify_serialized = AsyncMock()
        event_bus = MagicMock()
        event_bus.publish = MagicMock()

        def get_sessions():
            return [_session_info("other-session", title="other")]

        fanout = SessionEventFanout(
            session,
            broadcaster=broadcaster,
            get_sessions=get_sessions,
            event_bus=event_bus,
        )
        try:
            sub = fanout.subscribe()
            fanout.start()

            await session.event_queue.put(
                SessionEvent(event_type="title", session_id="s1", data=None),
            )
            received = await _drain_one(sub)
            assert received.event_type == "title"

            await asyncio.sleep(0)
            broadcaster.notify_serialized.assert_not_called()
            event_bus.publish.assert_not_called()
        finally:
            await _stop_and_await(fanout)

    async def test_attention_set_publishes_attention_and_updated(self):
        """A ``attention`` event with a reason set must publish BOTH
        ``session.attention`` (the one-shot notify trigger) and
        ``session.updated`` (the snapshot that carries the badge), and must
        NOT touch the broadcaster or ``sessions.changed``.
        """
        session = _fake_session("s1")
        broadcaster = MagicMock()
        broadcaster.notify_serialized = AsyncMock()
        event_bus = MagicMock()
        event_bus.publish = MagicMock()

        info = _session_info("s1", SessionState.RUNNING)
        info.attention = "awaiting_permission"

        def get_sessions():
            return [info]

        fanout = SessionEventFanout(
            session,
            broadcaster=broadcaster,
            get_sessions=get_sessions,
            event_bus=event_bus,
        )
        try:
            sub = fanout.subscribe()
            fanout.start()

            await session.event_queue.put(
                SessionEvent(
                    event_type="attention",
                    session_id="s1",
                    attention="awaiting_permission",
                ),
            )
            received = await _drain_one(sub)
            assert received.event_type == "attention"

            await asyncio.sleep(0)
            broadcaster.notify_serialized.assert_not_called()

            types_published = [c.args[0] for c in event_bus.publish.call_args_list]
            assert types_published == ["session.attention", "session.updated"]
            wire = session_to_wire_dict(info)
            for call in event_bus.publish.call_args_list:
                assert call.args[1] == {"session": wire}
            assert wire["attention"] == "awaiting_permission"
        finally:
            await _stop_and_await(fanout)

    async def test_attention_clear_publishes_updated_only(self):
        """A ``attention`` event with ``attention=None`` is a *clear*: it must
        refresh the snapshot via ``session.updated`` but must NOT fire the
        ``session.attention`` notify trigger.
        """
        session = _fake_session("s1")
        broadcaster = MagicMock()
        broadcaster.notify_serialized = AsyncMock()
        event_bus = MagicMock()
        event_bus.publish = MagicMock()

        info = _session_info("s1", SessionState.RUNNING)  # attention back to None

        def get_sessions():
            return [info]

        fanout = SessionEventFanout(
            session,
            broadcaster=broadcaster,
            get_sessions=get_sessions,
            event_bus=event_bus,
        )
        try:
            sub = fanout.subscribe()
            fanout.start()

            await session.event_queue.put(
                SessionEvent(
                    event_type="attention", session_id="s1", attention=None,
                ),
            )
            received = await _drain_one(sub)
            assert received.event_type == "attention"

            await asyncio.sleep(0)
            broadcaster.notify_serialized.assert_not_called()
            types_published = [c.args[0] for c in event_bus.publish.call_args_list]
            assert types_published == ["session.updated"]
        finally:
            await _stop_and_await(fanout)

    async def test_attention_without_event_bus_is_noop(self):
        session = _fake_session("s1")

        def get_sessions():
            return [_session_info("s1")]

        fanout = SessionEventFanout(
            session,
            broadcaster=None,
            get_sessions=get_sessions,
            event_bus=None,  # no bus → branch must skip cleanly
        )
        try:
            sub = fanout.subscribe()
            fanout.start()

            await session.event_queue.put(
                SessionEvent(
                    event_type="attention",
                    session_id="s1",
                    attention="turn_finished",
                ),
            )
            received = await _drain_one(sub)
            assert received.event_type == "attention"
            # No bus to assert on; reaching here without raising is the contract.
        finally:
            await _stop_and_await(fanout)

    async def test_attention_when_session_missing_from_list_is_noop(self):
        session = _fake_session("s1")
        broadcaster = MagicMock()
        broadcaster.notify_serialized = AsyncMock()
        event_bus = MagicMock()
        event_bus.publish = MagicMock()

        def get_sessions():
            return [_session_info("other-session")]

        fanout = SessionEventFanout(
            session,
            broadcaster=broadcaster,
            get_sessions=get_sessions,
            event_bus=event_bus,
        )
        try:
            sub = fanout.subscribe()
            fanout.start()

            await session.event_queue.put(
                SessionEvent(
                    event_type="attention",
                    session_id="s1",
                    attention="turn_finished",
                ),
            )
            received = await _drain_one(sub)
            assert received.event_type == "attention"

            await asyncio.sleep(0)
            event_bus.publish.assert_not_called()
        finally:
            await _stop_and_await(fanout)

    async def test_full_subscriber_does_not_block_others(self):
        session = _fake_session("s1")
        fanout = SessionEventFanout(session)
        try:
            full_sub = fanout.subscribe()
            # Drain-then-stuff full_sub to maxsize (1000) is heavy — instead,
            # swap out the subscriber with a maxsize-1 queue via the internal
            # set so we can exercise the QueueFull branch cheaply.
            fanout._subscribers.discard(full_sub)
            tiny_full: asyncio.Queue[SessionEvent] = asyncio.Queue(maxsize=1)
            tiny_full.put_nowait(
                SessionEvent(event_type="prefill", session_id="s1"),
            )
            fanout._subscribers.add(tiny_full)

            ok_sub = fanout.subscribe()
            fanout.start()

            await session.event_queue.put(
                SessionEvent(event_type="output", session_id="s1", data=b"x"),
            )

            received = await _drain_one(ok_sub)
            assert received.event_type == "output"
            assert received.data == b"x"

            # tiny_full is still at 1 item (silently dropped).
            assert tiny_full.qsize() == 1
            first = tiny_full.get_nowait()
            assert first.event_type == "prefill"
        finally:
            await _stop_and_await(fanout)


# ---------- SessionListBroadcaster ----------


class TestSessionListBroadcaster:
    async def test_subscribe_creates_bounded_queue(self):
        bc = SessionListBroadcaster()
        q = bc.subscribe()
        assert isinstance(q, asyncio.Queue)
        assert q.maxsize == 100

    def test_unsubscribe_is_idempotent(self):
        bc = SessionListBroadcaster()
        q = bc.subscribe()
        bc.unsubscribe(q)
        bc.unsubscribe(q)  # double-unsubscribe must not raise

    async def test_notify_serialized_emits_json_to_each_subscriber(self):
        bc = SessionListBroadcaster()
        q1 = bc.subscribe()
        q2 = bc.subscribe()

        dicts = [
            {
                "session_id": "s1",
                "state": "running",
                "created_at": "2026-01-01T12:00:00+00:00",
                "pid": 4242,
                "command": ["cmd.exe"],
                "cwd": "C:/tmp",
            },
        ]
        await bc.notify_serialized(dicts)

        payload1 = q1.get_nowait()
        payload2 = q2.get_nowait()
        assert isinstance(payload1, str)
        assert json.loads(payload1) == dicts
        assert json.loads(payload2) == dicts

    async def test_notify_serialized_full_queue_isolation(self):
        bc = SessionListBroadcaster()
        full_q = bc.subscribe()
        ok_q = bc.subscribe()

        # Fill full_q to its hard maxsize (100) directly.
        for i in range(100):
            full_q.put_nowait(f"prefill-{i}")
        assert full_q.full()

        await bc.notify_serialized([{"session_id": "s1"}])

        # full_q untouched — still 100 prefill items, none of them JSON.
        assert full_q.qsize() == 100
        sample = full_q.get_nowait()
        assert sample == "prefill-0"

        # ok_q received the JSON-encoded list.
        payload = ok_q.get_nowait()
        assert json.loads(payload) == [{"session_id": "s1"}]

    async def test_notify_from_session_info_list(self):
        bc = SessionListBroadcaster()
        q = bc.subscribe()

        info = _session_info("s1", SessionState.RUNNING)
        await bc.notify([info])

        payload = q.get_nowait()
        decoded = json.loads(payload)
        assert isinstance(decoded, list)
        assert len(decoded) == 1
        assert decoded[0]["session_id"] == "s1"
        assert decoded[0]["state"] == "running"
        assert decoded[0]["pid"] == 4242
        assert decoded[0]["command"] == ["cmd.exe"]
        assert decoded[0]["cwd"] == "C:/tmp"
        # created_at is the SessionInfo datetime serialized to ISO.
        assert decoded[0]["created_at"].startswith("2026-01-01T12:00:00")

    async def test_notify_with_no_subscribers_is_noop(self):
        bc = SessionListBroadcaster()
        # Must not raise.
        await bc.notify_serialized([{"session_id": "s1"}])
