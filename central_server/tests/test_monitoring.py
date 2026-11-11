"""Tests for the capacity-monitoring module.

Covers sample_process_stats degradation, RoutingTable.gauges(),
send-queue drop counters at representative QueueFull sites, and the
monitoring_loop surviving a failed emit (with time virtualized — no real
sleeps).
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
from unittest.mock import AsyncMock, MagicMock

import pytest

from central_server import monitoring
from central_server.monitoring import (
    SEND_QUEUE_DROPS,
    monitoring_loop,
    sample_process_stats,
)
from central_server.pairing.ws_handlers import PairingWsRegistry
from central_server.relay.agent_ws import _try_enqueue
from central_server.relay.mobile_ws import _session_reader
from central_server.relay.routing import (
    MobileClient,
    RoutingTable,
    UserClient,
)


@pytest.fixture(autouse=True)
def _reset_drop_counters():
    # SEND_QUEUE_DROPS is module-global; isolate every test.
    SEND_QUEUE_DROPS.clear()
    yield
    SEND_QUEUE_DROPS.clear()


def _full_queue(item):
    queue = asyncio.Queue(maxsize=1)
    queue.put_nowait(item)
    return queue


def test_sample_reads_cgroup_files_and_fd_count(tmp_path):
    mem_current = tmp_path / "memory.current"
    mem_current.write_text("52428800\n")
    mem_max = tmp_path / "memory.max"
    mem_max.write_text("104857600\n")
    fd_dir = tmp_path / "fd"
    fd_dir.mkdir()
    for name in ("0", "1", "2"):
        (fd_dir / name).touch()

    stats = sample_process_stats(str(mem_current), str(mem_max), str(fd_dir))

    assert stats.rss_bytes == 52428800
    assert stats.mem_limit_bytes == 104857600
    assert stats.rss_pct_of_cap == pytest.approx(50.0)
    # Minus one mirrors /proc/self/fd, where the listing holds its own fd.
    assert stats.open_fds == 2


def test_sample_uncapped_cgroup_max_sentinel(tmp_path):
    mem_current = tmp_path / "memory.current"
    mem_current.write_text("52428800\n")
    mem_max = tmp_path / "memory.max"
    mem_max.write_text("max\n")
    fd_dir = tmp_path / "fd"
    fd_dir.mkdir()

    stats = sample_process_stats(str(mem_current), str(mem_max), str(fd_dir))

    assert stats.rss_bytes == 52428800
    assert stats.mem_limit_bytes is None
    assert stats.rss_pct_of_cap is None


def test_sample_degrades_to_none_when_paths_missing(tmp_path):
    # Off-Linux there is no cgroupfs/procfs; every field must go None
    # instead of raising so the loop stays quiet on dev machines.
    stats = sample_process_stats(
        str(tmp_path / "nope1"), str(tmp_path / "nope2"), str(tmp_path / "nope3")
    )
    assert stats.rss_bytes is None
    assert stats.mem_limit_bytes is None
    assert stats.rss_pct_of_cap is None
    assert stats.open_fds is None


def test_gauges_counts_connections_and_queue_depths():
    routing = RoutingTable()
    a1 = routing.register_agent("agent-1", MagicMock())
    a2 = routing.register_agent("agent-2", MagicMock())
    for _ in range(3):
        a1.send_queue.put_nowait(b"x")
    a2.send_queue.put_nowait(b"x")

    m1 = MobileClient(device_id="d1", websocket=MagicMock())
    m2 = MobileClient(device_id="d2", websocket=MagicMock())
    m3 = MobileClient(device_id="d3", websocket=MagicMock())
    # Two sockets on the same session key count as ONE attached session.
    routing.register_mobile("agent-1", "session_io:s1", m1)
    routing.register_mobile("agent-1", "session_io:s1", m2)
    # Non-session channels count as sockets but not attached sessions.
    routing.register_mobile("agent-2", "events", m3)

    for user_id, device in (("u1", "da"), ("u1", "db"), ("u2", "dc")):
        routing.register_user_client(
            user_id,
            UserClient(user_id=user_id, device_id=device, websocket=MagicMock()),
        )

    assert routing.gauges() == {
        "agents_online": 2,
        "mobile_sockets": 3,
        "attached_sessions": 1,
        "users_connected": 2,
        "user_sockets": 3,
        "agent_queue_depth_max": 3,
        "agent_queue_depth_total": 4,
    }


def test_agent_to_mobile_drop_counts_only_on_full_queue():
    slow = MobileClient(device_id="slow", websocket=MagicMock())
    slow.send_queue = _full_queue(b"already-full")
    _try_enqueue(slow, b"payload", binary=True)
    assert SEND_QUEUE_DROPS["agent_to_mobile"] == 1

    healthy = MobileClient(device_id="healthy", websocket=MagicMock())
    _try_enqueue(healthy, b"payload", binary=True)
    assert SEND_QUEUE_DROPS["agent_to_mobile"] == 1
    assert healthy.send_queue.get_nowait() == b"payload"


async def test_session_io_drop_counts_and_reader_keeps_going():
    routing = RoutingTable()
    conn = routing.register_agent("agent-1", MagicMock())
    conn.send_queue = _full_queue(b"already-full")

    websocket = MagicMock()
    websocket.receive = AsyncMock(
        side_effect=[
            {"type": "websocket.receive", "bytes": b"pty-data"},
            {"type": "websocket.disconnect"},
        ]
    )

    await _session_reader(websocket, "agent-1", "sess-1", routing)

    assert SEND_QUEUE_DROPS["mobile_to_agent_session_io"] == 1
    assert conn.send_queue.get_nowait() == b"already-full"


async def test_pairing_push_drop_counts_and_returns_false():
    registry = PairingWsRegistry()
    queue = await registry.register_waiter("nonce-1")

    assert await registry.dispatch_to_waiter("nonce-1", b"first") is True
    assert await registry.dispatch_to_waiter("nonce-1", b"second") is False
    assert SEND_QUEUE_DROPS["pairing_push"] == 1
    assert queue.get_nowait() == b"first"


@pytest.fixture
def _instant_ticks(monkeypatch):
    """Virtualize the loop's sleeps: record requested delays, never wait."""
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def instant_sleep(delay, *args, **kwargs):
        sleeps.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(monitoring.asyncio, "sleep", instant_sleep)
    return sleeps


async def test_monitoring_loop_survives_emit_failure(
    config, monkeypatch, _instant_ticks
):
    second_emit = asyncio.Event()
    calls: list[int] = []

    def flaky_emit(routing, lag_max_ms, lag_avg_ms):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        second_emit.set()

    monkeypatch.setattr(monitoring, "_emit_sample", flaky_emit)

    fast_config = dataclasses.replace(config, monitoring_interval_seconds=1)
    task = asyncio.create_task(monitoring_loop(RoutingTable(), fast_config))
    try:
        await asyncio.wait_for(second_emit.wait(), timeout=5)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert len(calls) >= 2
