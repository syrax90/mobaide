"""Tests for the agent-side relay-client auto-scan bridge.

``RelayClient`` grew three responsibilities around CH_AUTOSCAN:

1. ``_handle_open_autoscan`` — on receipt of a CH_CONTROL ``open_autoscan``
   message, spawn a per-subscription :func:`run_autoscan_stream` task with
   adapter callables that encode outbound frames as CH_AUTOSCAN envelopes
   and dequeue inbound frames from a per-subscription inbox.
2. ``_deliver_autoscan_inbound`` — route incoming CH_AUTOSCAN envelopes
   (mobile text frames forwarded by the central server) into the matching
   inbox queue.
3. ``_handle_close_autoscan`` / ``_cancel_all_subtasks`` — cancel the task
   and drop its bookkeeping so nothing leaks across tunnel reconnects.

These tests stub :func:`run_autoscan_stream` so the bridge mechanics can
be verified without driving the real protocol; the protocol itself lives
in ``test_autoscan_stream.py``.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mobaide.relay.client import MAX_AUTOSCAN_STREAMS, RelayClient
from mobaide.relay.envelope import (
    CH_AUTOSCAN,
    CH_CONTROL,
    decode,
    encode,
)


# ``relay_config``, ``mock_session_manager``, ``mock_event_bus``,
# ``mock_broadcaster``, and ``mock_app_state`` are shared with
# ``test_relay_client.py`` via ``tests/conftest.py``. The ``client`` /
# ``client_no_mgr`` fixtures stay local so they don't clash with the
# conftest's async HTTP ``client`` fixture.


@pytest.fixture
def mock_app_state_no_mgr():
    """An app_state explicitly without ``autoscan_manager`` — triggers the
    ``close_autoscan`` short-circuit in ``_handle_open_autoscan``.
    """
    state = MagicMock(spec=[])  # empty spec → no attributes at all
    return state


@pytest.fixture
def client(
    relay_config,
    mock_session_manager,
    mock_event_bus,
    mock_broadcaster,
    mock_app_state,
) -> RelayClient:
    return RelayClient(
        config=relay_config,
        session_manager=mock_session_manager,
        event_bus=mock_event_bus,
        broadcaster=mock_broadcaster,
        app_state=mock_app_state,
    )


@pytest.fixture
def client_no_mgr(
    relay_config,
    mock_session_manager,
    mock_event_bus,
    mock_broadcaster,
    mock_app_state_no_mgr,
) -> RelayClient:
    return RelayClient(
        config=relay_config,
        session_manager=mock_session_manager,
        event_bus=mock_event_bus,
        broadcaster=mock_broadcaster,
        app_state=mock_app_state_no_mgr,
    )


class TestHandleOpenAutoscan:
    async def test_open_spawns_task_and_registers_inbox(self, client: RelayClient):
        """A valid ``open_autoscan`` control message must spawn a stream task
        and register the per-subscription inbox so incoming CH_AUTOSCAN frames
        have a destination.
        """
        ws = AsyncMock()
        ctrl = {"type": "open_autoscan", "subscription_id": "sub-xyz-1"}

        stream_started = asyncio.Event()
        stream_release = asyncio.Event()

        async def _fake_stream(mgr, send_text, receive_text, grace_timeout_s=1.0):
            stream_started.set()
            await stream_release.wait()
            return 0

        with patch(
            "mobaide.relay.client.run_autoscan_stream",
            new=_fake_stream,
        ):
            await client._handle_control(json.dumps(ctrl).encode(), ws)
            # The spawned task should be running within one scheduler tick.
            await asyncio.wait_for(stream_started.wait(), timeout=1.0)

            assert "sub-xyz-1" in client._autoscan_streams
            assert "sub-xyz-1" in client._autoscan_inboxes

            task = client._autoscan_streams["sub-xyz-1"]
            assert not task.done()

            # Release the stubbed coroutine and wait for the task's own
            # ``finally`` to pop its bookkeeping.
            stream_release.set()
            await asyncio.wait_for(task, timeout=1.0)

        assert "sub-xyz-1" not in client._autoscan_streams
        assert "sub-xyz-1" not in client._autoscan_inboxes

    async def test_open_duplicate_subscription_is_ignored(
        self, client: RelayClient
    ):
        """A second ``open_autoscan`` for the same ``subscription_id`` must
        not spawn a second task (would leak a ghost task + duplicate inbox).
        """
        ws = AsyncMock()
        ctrl = {"type": "open_autoscan", "subscription_id": "sub-dup"}

        stream_release = asyncio.Event()

        async def _fake_stream(*_args, **_kwargs):
            await stream_release.wait()
            return 0

        with patch(
            "mobaide.relay.client.run_autoscan_stream",
            new=_fake_stream,
        ):
            await client._handle_control(json.dumps(ctrl).encode(), ws)
            first_task = client._autoscan_streams["sub-dup"]

            # Second open with the same id must not replace the task.
            await client._handle_control(json.dumps(ctrl).encode(), ws)

            assert client._autoscan_streams["sub-dup"] is first_task
            assert len(client._autoscan_streams) == 1

            stream_release.set()
            await asyncio.wait_for(first_task, timeout=1.0)

    async def test_open_at_capacity_sends_close_back(
        self, client: RelayClient
    ):
        """When the agent already has MAX_AUTOSCAN_STREAMS live subscriptions,
        a new ``open_autoscan`` must be rejected with a CH_CONTROL
        ``close_autoscan`` carrying ``reason='capacity_reached'`` — so the
        central server can close the mobile socket rather than leave it
        hanging with no stream serving it. No new task or inbox is created.
        """
        ws = AsyncMock()

        # Pre-populate the live-streams dict up to the cap with dummy sentinels.
        # The cap check only inspects ``len(self._autoscan_streams)`` so any
        # distinct keys work.
        for i in range(MAX_AUTOSCAN_STREAMS):
            fake_task = asyncio.create_task(asyncio.sleep(0))
            client._autoscan_streams[f"pre-{i}"] = fake_task

        ctrl = {"type": "open_autoscan", "subscription_id": "sub-overflow"}

        called = False

        async def _should_not_be_called(*_a, **_kw):
            nonlocal called
            called = True

        with patch(
            "mobaide.relay.client.run_autoscan_stream",
            new=_should_not_be_called,
        ):
            await client._handle_control(json.dumps(ctrl).encode(), ws)

        assert called is False

        ws.send.assert_awaited_once()
        sent_bytes = ws.send.call_args[0][0]
        channel, session_id, payload = decode(sent_bytes)
        assert channel == CH_CONTROL
        assert session_id == ""
        msg = json.loads(payload)
        assert msg["type"] == "close_autoscan"
        assert msg["subscription_id"] == "sub-overflow"
        assert msg.get("reason") == "capacity_reached"

        # Overflow slot was not registered.
        assert "sub-overflow" not in client._autoscan_streams
        assert "sub-overflow" not in client._autoscan_inboxes

        # Let the sentinel tasks finish so pytest-asyncio doesn't warn.
        for t in list(client._autoscan_streams.values()):
            await t
        client._autoscan_streams.clear()

    async def test_open_without_autoscan_manager_sends_close_back(
        self, client_no_mgr: RelayClient
    ):
        """When ``app_state.autoscan_manager`` is absent, the handler sends a
        CH_CONTROL ``close_autoscan`` back to the central server so the
        mobile socket can be closed; no task is spawned.
        """
        ws = AsyncMock()
        ctrl = {"type": "open_autoscan", "subscription_id": "sub-nomgr"}

        called = False

        async def _should_not_be_called(*_a, **_kw):
            nonlocal called
            called = True

        with patch(
            "mobaide.relay.client.run_autoscan_stream",
            new=_should_not_be_called,
        ):
            await client_no_mgr._handle_control(json.dumps(ctrl).encode(), ws)

        assert called is False

        ws.send.assert_awaited_once()
        sent_bytes = ws.send.call_args[0][0]
        channel, session_id, payload = decode(sent_bytes)
        assert channel == CH_CONTROL
        assert session_id == ""
        msg = json.loads(payload)
        assert msg["type"] == "close_autoscan"
        assert msg["subscription_id"] == "sub-nomgr"
        assert msg.get("reason") == "autoscan_not_configured"

        # No task or inbox was registered.
        assert client_no_mgr._autoscan_streams == {}
        assert client_no_mgr._autoscan_inboxes == {}

    async def test_send_text_closure_encodes_ch_autoscan_envelope(
        self, client: RelayClient
    ):
        """The ``send_text`` closure passed to ``run_autoscan_stream`` must
        wrap each outbound text frame in a CH_AUTOSCAN envelope keyed on the
        subscription id — otherwise the central server cannot route it back
        to the right mobile.
        """
        ws = AsyncMock()
        ctrl = {"type": "open_autoscan", "subscription_id": "sub-sendtest"}

        captured_send_text = {}

        async def _fake_stream(mgr, send_text, receive_text, grace_timeout_s=1.0):
            captured_send_text["fn"] = send_text
            # Immediately return so the task cleans itself up.
            return 0

        with patch(
            "mobaide.relay.client.run_autoscan_stream",
            new=_fake_stream,
        ):
            await client._handle_control(json.dumps(ctrl).encode(), ws)
            # Wait for the spawned task to finish.
            task = client._autoscan_streams.get("sub-sendtest")
            if task is not None:
                await asyncio.wait_for(task, timeout=1.0)

        # Now exercise the captured closure with a representative frame.
        send_text_fn = captured_send_text["fn"]
        ws.send.reset_mock()
        await send_text_fn('{"type":"caught_up"}')

        ws.send.assert_awaited_once()
        sent_bytes = ws.send.call_args[0][0]
        channel, session_id, payload = decode(sent_bytes)
        assert channel == CH_AUTOSCAN
        assert session_id == "sub-sendtest"
        assert payload == b'{"type":"caught_up"}'


class TestDeliverAutoscanInbound:
    async def test_inbound_frame_lands_in_matching_inbox(
        self, client: RelayClient
    ):
        """A CH_AUTOSCAN envelope for a registered subscription must decode
        and land in that subscription's inbox as utf-8 text.
        """
        inbox: asyncio.Queue[str] = asyncio.Queue(maxsize=10)
        client._autoscan_inboxes["sub-in"] = inbox

        frame = encode(
            CH_AUTOSCAN, "sub-in", json.dumps({"subscribe": ["p"]}).encode()
        )
        await client._handle_binary(frame, ws=AsyncMock())

        text = inbox.get_nowait()
        assert json.loads(text) == {"subscribe": ["p"]}

    async def test_inbound_frame_when_inbox_full_drops_silently(
        self, client: RelayClient
    ):
        """A full inbox queue must not raise out of ``_handle_binary`` —
        the stale frame is dropped and logged.
        """
        full_inbox: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
        full_inbox.put_nowait("existing")
        client._autoscan_inboxes["sub-full"] = full_inbox

        frame = encode(CH_AUTOSCAN, "sub-full", b'{"late":true}')
        # Must not raise QueueFull.
        await client._handle_binary(frame, ws=AsyncMock())

        assert full_inbox.qsize() == 1
        assert full_inbox.get_nowait() == "existing"


class TestHandleCloseAutoscan:
    async def test_close_cancels_running_task(self, client: RelayClient):
        ws = AsyncMock()

        stream_entered = asyncio.Event()

        async def _fake_stream(*_args, **_kwargs):
            # Signal that the stream task has actually started, then park
            # on an awaitable that will raise CancelledError on cancel.
            stream_entered.set()
            await asyncio.Event().wait()

        open_ctrl = {"type": "open_autoscan", "subscription_id": "sub-close"}
        close_ctrl = {"type": "close_autoscan", "subscription_id": "sub-close"}

        with patch(
            "mobaide.relay.client.run_autoscan_stream",
            new=_fake_stream,
        ):
            await client._handle_control(json.dumps(open_ctrl).encode(), ws)
            task = client._autoscan_streams["sub-close"]

            # CRITICAL: wait for the task's coroutine to actually begin.
            # ``asyncio.create_task`` schedules the coroutine but does not
            # run it until the current coroutine yields. Calling
            # ``task.cancel()`` on a never-started task marks the task
            # cancelled WITHOUT running its body or its ``finally``, so the
            # per-subscription cleanup would never happen. Real production
            # conditions guarantee the task is well past its own ``try``
            # (blocked on ``receive_text``) before ``close_autoscan``
            # arrives; mirror that by awaiting the start marker.
            await asyncio.wait_for(stream_entered.wait(), timeout=1.0)

            await client._handle_control(json.dumps(close_ctrl).encode(), ws)

            # Poll until the task's ``finally`` has popped the bookkeeping.
            for _ in range(50):
                await asyncio.sleep(0.01)
                if "sub-close" not in client._autoscan_streams:
                    break

        assert task.done()
        assert task.cancelled()
        assert "sub-close" not in client._autoscan_streams
        assert "sub-close" not in client._autoscan_inboxes


class TestCancelAllSubtasksAutoscan:
    async def test_cancels_all_autoscan_tasks_and_clears_state(
        self, client: RelayClient
    ):
        """On tunnel teardown every outstanding autoscan stream task must be
        cancelled and both dicts cleared — otherwise stale tasks and inboxes
        leak into the next connection.
        """

        async def _forever():
            await asyncio.sleep(999)

        t1 = asyncio.create_task(_forever())
        t2 = asyncio.create_task(_forever())
        client._autoscan_streams["a"] = t1
        client._autoscan_streams["b"] = t2
        client._autoscan_inboxes["a"] = asyncio.Queue()
        client._autoscan_inboxes["b"] = asyncio.Queue()

        client._cancel_all_subtasks()

        assert client._autoscan_streams == {}
        assert client._autoscan_inboxes == {}

        for t in (t1, t2):
            with pytest.raises(asyncio.CancelledError):
                await t
            assert t.cancelled()

