"""Tests for the transport-agnostic ``run_autoscan_stream`` coroutine.

``run_autoscan_stream`` was extracted out of the ``/ws/autoscan`` handler so
that direct-mode and relay-mode share one wire-protocol implementation
(snapshot + caught_up + live events + subscribe-set filtering +
``resume_from_seq`` replay).  These tests exercise the coroutine directly
through in-memory ``send_text`` / ``receive_text`` callables, independent of
FastAPI or any WebSocket transport — that is its whole purpose.

The direct-mode behavioural contract is already protected end-to-end by
``test_ws_autoscan.py``; this file is the relay-mode safety net for the
same protocol body.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from mobaide.agent.autoscan.stream import run_autoscan_stream
from mobaide.linear.models import ScanEvent


@dataclass
class FakeAutoScanInstance:
    """Minimal fake of ``AutoScanInstance`` — only the surface the coroutine uses.

    Unlike the fake in ``test_ws_autoscan.py`` this one runs inside the same
    event loop as the coroutine under test, so no cross-thread queueing is
    needed: ``push_event`` does a direct ``put_nowait``.
    """

    project_name: str
    _recent_events: list[ScanEvent] = field(default_factory=list)
    _event_subscribers: list[asyncio.Queue] = field(default_factory=list)
    _dto: dict[str, Any] = field(default_factory=dict)

    @property
    def recent_events(self) -> list[ScanEvent]:
        return list(self._recent_events)

    def subscribe_events(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._event_subscribers.append(q)
        return q

    def unsubscribe_events(self, q: asyncio.Queue) -> None:
        if q in self._event_subscribers:
            self._event_subscribers.remove(q)

    def to_dto(self) -> dict[str, Any]:
        return dict(self._dto)

    async def push_event(self, evt: ScanEvent) -> None:
        for q in list(self._event_subscribers):
            await q.put(evt)

    @property
    def subscriber_count(self) -> int:
        return len(self._event_subscribers)


class FakeAutoScanSessionManager:
    """Minimal fake of ``AutoScanSessionManager``."""

    def __init__(self, instances: list[FakeAutoScanInstance]) -> None:
        self._instances: dict[str, FakeAutoScanInstance] = {
            i.project_name: i for i in instances
        }
        self._list_response_data: dict[str, Any] = {
            "scans": [i.to_dto() for i in instances],
            "capacity": len(instances),
            "max_scans": 5,
            "available_projects": list(self._instances.keys()),
            "auto_scan_prompt": None,
        }

    def all_instances(self) -> list[FakeAutoScanInstance]:
        return list(self._instances.values())

    def get(self, name: str) -> FakeAutoScanInstance | None:
        return self._instances.get(name)

    def list_response(self) -> dict[str, Any]:
        return dict(self._list_response_data)


class FakeTransport:
    """In-memory send/receive pair for run_autoscan_stream.

    - ``send_text`` is the ``SendText`` callable the coroutine calls to emit
      outbound frames; captured into ``outbound`` in order.
    - ``receive_text`` is the ``ReceiveText`` callable the coroutine awaits
      for inbound frames; fed by the test via ``feed(text)``.
    - ``disconnect()`` raises a peer-disconnect ``RuntimeError`` on the
      *next* ``receive_text`` call — mirrors the way the direct-mode handler
      sees ``WebSocketDisconnect``.
    """

    def __init__(self) -> None:
        self.outbound: list[str] = []
        self._inbound: asyncio.Queue[str] = asyncio.Queue()
        self._disconnected = False

    async def send_text(self, text: str) -> None:
        self.outbound.append(text)

    async def receive_text(self) -> str:
        if self._disconnected and self._inbound.empty():
            raise RuntimeError("peer disconnected")
        return await self._inbound.get()

    def feed(self, text: str) -> None:
        self._inbound.put_nowait(text)

    def disconnect(self) -> None:
        self._disconnected = True
        self._inbound.put_nowait("")  # unblock the pending receive


def _make_event(seq: int, type_: str = "phase", extra: dict | None = None) -> ScanEvent:
    payload: dict[str, Any] = {"note": f"evt-{seq}"}
    if extra:
        payload.update(extra)
    return ScanEvent(
        seq=seq,
        ts=f"2026-04-11T00:00:{seq:02d}+00:00",
        type=type_,
        payload=payload,
    )


async def _drive_until(transport: FakeTransport, n: int, timeout: float = 2.0) -> None:
    """Wait until ``transport.outbound`` has at least ``n`` frames, else TimeoutError."""
    deadline = asyncio.get_running_loop().time() + timeout
    while len(transport.outbound) < n:
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError(
                f"expected {n} outbound frames, got {len(transport.outbound)}"
            )
        await asyncio.sleep(0.01)


class TestSnapshotAndCaughtUp:
    async def test_snapshot_and_caught_up_fire_with_no_initial_frame(self):
        """Grace timeout elapses with no client frame → snapshot + caught_up
        still flow. The coroutine keeps running waiting for events; we let it
        cancel via ``asyncio.wait_for``.
        """
        inst = FakeAutoScanInstance(
            project_name="projectA",
            _recent_events=[_make_event(1), _make_event(2)],
            _dto={"project_name": "projectA", "status": "running"},
        )
        mgr = FakeAutoScanSessionManager([inst])
        transport = FakeTransport()

        task = asyncio.create_task(
            run_autoscan_stream(
                mgr, transport.send_text, transport.receive_text, grace_timeout_s=0.05,
            )
        )
        try:
            await _drive_until(transport, 2)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        snap = json.loads(transport.outbound[0])
        caught_up = json.loads(transport.outbound[1])
        assert snap["type"] == "snapshot"
        assert [e["seq"] for e in snap["data"]["events"]["projectA"]] == [1, 2]
        assert caught_up == {"type": "caught_up"}

    async def test_snapshot_empty_instances_waits_for_reader(self):
        """No instances → ``_reader``-only branch: snapshot + caught_up fire,
        then the coroutine blocks in ``_reader`` until the peer disconnects.
        """
        mgr = FakeAutoScanSessionManager([])
        transport = FakeTransport()

        task = asyncio.create_task(
            run_autoscan_stream(
                mgr, transport.send_text, transport.receive_text, grace_timeout_s=0.05,
            )
        )
        try:
            await _drive_until(transport, 2)
            # The coroutine should NOT have returned yet — it is waiting for
            # the reader to receive an inbound frame.
            assert not task.done()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        snap = json.loads(transport.outbound[0])
        assert snap["type"] == "snapshot"
        assert snap["data"]["scans"] == []
        assert snap["data"]["events"] == {}
        assert json.loads(transport.outbound[1]) == {"type": "caught_up"}


class TestLiveEvents:
    async def test_non_log_event_flows_after_snapshot(self):
        inst = FakeAutoScanInstance("projectA")
        mgr = FakeAutoScanSessionManager([inst])
        transport = FakeTransport()

        task = asyncio.create_task(
            run_autoscan_stream(
                mgr, transport.send_text, transport.receive_text, grace_timeout_s=0.05,
            )
        )
        try:
            await _drive_until(transport, 2)  # snapshot + caught_up
            await inst.push_event(_make_event(7, type_="phase"))
            await _drive_until(transport, 3)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        frame = json.loads(transport.outbound[2])
        assert frame["type"] == "event"
        assert frame["project_name"] == "projectA"
        assert frame["event"]["seq"] == 7
        assert frame["event"]["type"] == "phase"

    async def test_log_event_filtered_without_subscribe(self):
        """``log`` events are dropped for projects NOT in ``subscribe_set``."""
        inst = FakeAutoScanInstance("projectA")
        mgr = FakeAutoScanSessionManager([inst])
        transport = FakeTransport()

        task = asyncio.create_task(
            run_autoscan_stream(
                mgr, transport.send_text, transport.receive_text, grace_timeout_s=0.05,
            )
        )
        try:
            await _drive_until(transport, 2)
            # log event → must be filtered. phase event → must flow.
            await inst.push_event(_make_event(10, type_="log"))
            await inst.push_event(_make_event(11, type_="phase"))
            await _drive_until(transport, 3)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        # Only 3 frames total: snapshot + caught_up + phase (log dropped).
        assert len(transport.outbound) == 3
        frame = json.loads(transport.outbound[2])
        assert frame["event"]["seq"] == 11
        assert frame["event"]["type"] == "phase"

    async def test_log_event_delivered_after_initial_subscribe(self):
        """An initial config frame with ``subscribe`` enables log delivery for that project."""
        inst = FakeAutoScanInstance("projectA")
        mgr = FakeAutoScanSessionManager([inst])
        transport = FakeTransport()

        transport.feed(json.dumps({"subscribe": ["projectA"]}))

        task = asyncio.create_task(
            run_autoscan_stream(
                mgr, transport.send_text, transport.receive_text, grace_timeout_s=1.0,
            )
        )
        try:
            await _drive_until(transport, 2)
            await inst.push_event(_make_event(42, type_="log"))
            await _drive_until(transport, 3)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        frame = json.loads(transport.outbound[2])
        assert frame["event"]["type"] == "log"
        assert frame["event"]["seq"] == 42


class TestResumeFromSeq:
    async def test_in_range_resume_filters_ring(self):
        """first_seq (1) <= resume_seq (2) + 1 → snapshot only contains seq > 2."""
        inst = FakeAutoScanInstance(
            "projectA",
            _recent_events=[_make_event(s) for s in (1, 2, 3, 4, 5)],
        )
        mgr = FakeAutoScanSessionManager([inst])
        transport = FakeTransport()

        transport.feed(json.dumps({"resume_from_seq": {"projectA": 2}}))

        task = asyncio.create_task(
            run_autoscan_stream(
                mgr, transport.send_text, transport.receive_text, grace_timeout_s=1.0,
            )
        )
        try:
            await _drive_until(transport, 2)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        snap = json.loads(transport.outbound[0])
        seqs = [e["seq"] for e in snap["data"]["events"]["projectA"]]
        assert seqs == [3, 4, 5]

    async def test_gap_returns_full_ring(self):
        """first_seq (10) > resume_seq (5) + 1 → filter skipped, ring returned in full."""
        inst = FakeAutoScanInstance(
            "projectA",
            _recent_events=[_make_event(s) for s in (10, 11, 12)],
        )
        mgr = FakeAutoScanSessionManager([inst])
        transport = FakeTransport()

        transport.feed(json.dumps({"resume_from_seq": {"projectA": 5}}))

        task = asyncio.create_task(
            run_autoscan_stream(
                mgr, transport.send_text, transport.receive_text, grace_timeout_s=1.0,
            )
        )
        try:
            await _drive_until(transport, 2)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        snap = json.loads(transport.outbound[0])
        seqs = [e["seq"] for e in snap["data"]["events"]["projectA"]]
        assert seqs == [10, 11, 12]


class TestSubscribeMidStream:
    async def test_subscribe_frame_after_snapshot_enables_log_delivery(self):
        inst = FakeAutoScanInstance("projectA")
        mgr = FakeAutoScanSessionManager([inst])
        transport = FakeTransport()

        task = asyncio.create_task(
            run_autoscan_stream(
                mgr, transport.send_text, transport.receive_text, grace_timeout_s=0.05,
            )
        )
        try:
            await _drive_until(transport, 2)

            # Late subscribe — reader task must process this and mutate subscribe_set.
            transport.feed(json.dumps({"subscribe": ["projectA"]}))
            # Small yield so the reader coroutine actually advances.
            await asyncio.sleep(0.05)

            await inst.push_event(_make_event(99, type_="log"))
            await _drive_until(transport, 3)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        frame = json.loads(transport.outbound[2])
        assert frame["event"]["type"] == "log"
        assert frame["event"]["seq"] == 99


class TestCleanup:
    async def test_cancellation_unsubscribes_all_queues(self):
        """On ``asyncio.CancelledError`` the ``finally`` block must call
        ``unsubscribe_events`` for every queue it created. Without this,
        relay-mode reconnects would leak subscribers.
        """
        inst_a = FakeAutoScanInstance("A")
        inst_b = FakeAutoScanInstance("B")
        mgr = FakeAutoScanSessionManager([inst_a, inst_b])
        transport = FakeTransport()

        task = asyncio.create_task(
            run_autoscan_stream(
                mgr, transport.send_text, transport.receive_text, grace_timeout_s=0.05,
            )
        )
        try:
            await _drive_until(transport, 2)
            assert inst_a.subscriber_count == 1
            assert inst_b.subscriber_count == 1
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert inst_a.subscriber_count == 0
        assert inst_b.subscriber_count == 0

    async def test_malformed_initial_frame_is_tolerated(self):
        """A non-JSON initial frame is silently ignored; snapshot still fires."""
        inst = FakeAutoScanInstance(
            "projectA", _recent_events=[_make_event(1)],
        )
        mgr = FakeAutoScanSessionManager([inst])
        transport = FakeTransport()

        transport.feed("{not valid json")

        task = asyncio.create_task(
            run_autoscan_stream(
                mgr, transport.send_text, transport.receive_text, grace_timeout_s=1.0,
            )
        )
        try:
            await _drive_until(transport, 2)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        snap = json.loads(transport.outbound[0])
        assert snap["type"] == "snapshot"
        assert [e["seq"] for e in snap["data"]["events"]["projectA"]] == [1]
