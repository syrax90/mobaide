"""Capacity monitoring: the 60 s ``monitoring.sample`` log line.

One structured line per interval carrying event-loop lag, cgroup RSS vs the
container cap, open-fd count, routing-table gauges, and cumulative
send-queue drop counters. Greppable from the existing structlog JSON stream
— no metrics dependency, no exporter; the revisit triggers in
docs/deployment.md ("Capacity & revisit triggers") read exactly these
fields.

Drop counters live here as module state rather than on ``RoutingTable``:
the hottest drop site (``relay/agent_ws._try_enqueue``, the agent→mobile
fan-out) has no routing reference, and everything runs on the single relay
event loop, so a plain ``Counter`` increment is race-free and adds nothing
to the hot path. Tests reset via ``SEND_QUEUE_DROPS.clear()``.
"""
from __future__ import annotations

import asyncio
import os
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .logging_setup import get_logger

if TYPE_CHECKING:
    from .config import CentralServerConfig
    from .relay.routing import RoutingTable

logger = get_logger(__name__)

# Cumulative per-site send-queue drop counts. Keys are the stable semantic
# labels passed to record_send_queue_drop() at each asyncio.QueueFull site:
#   agent_to_mobile              relay/agent_ws._try_enqueue (fan-out)
#   stream_desync_close          relay/agent_ws._try_enqueue — once per
#                                connection when an ordered-channel drop
#                                severs it; agent_to_mobile counts the
#                                frames, this counts the disconnects
#   mobile_to_agent_session_io   relay/mobile_ws._session_reader
#   mobile_to_agent_autoscan     relay/mobile_ws._autoscan_reader
#   autoscan_open / autoscan_close  relay/mobile_ws autoscan control
#   control                      relay/routing.try_enqueue_control
#   user_event                   relay/routing.broadcast_user_event
#   proxy_request                relay/mobile_proxy (enqueue → 503)
#   manifest_broadcast           manifest/router broadcast
#   pairing_push                 pairing/ws_handlers nonce delivery
SEND_QUEUE_DROPS: Counter[str] = Counter()


def record_send_queue_drop(site: str) -> None:
    """O(1) synchronous increment — safe on the relay hot path."""
    SEND_QUEUE_DROPS[site] += 1


@dataclass(frozen=True)
class ProcessStats:
    rss_bytes: int | None
    mem_limit_bytes: int | None
    rss_pct_of_cap: float | None
    open_fds: int | None


def _read_int(path: str) -> int | None:
    try:
        with open(path, encoding="ascii") as handle:
            raw = handle.read().strip()
    except OSError:
        return None
    # cgroup v2 memory.max reads "max" when uncapped.
    return int(raw) if raw.isdigit() else None


def sample_process_stats(
    mem_current_path: str = "/sys/fs/cgroup/memory.current",
    mem_max_path: str = "/sys/fs/cgroup/memory.max",
    fd_dir: str = "/proc/self/fd",
) -> ProcessStats:
    """Read memory and fd usage from cgroup v2 + procfs.

    cgroup ``memory.current`` rather than ``/proc/self/status`` VmRSS on
    purpose: the compose ``mem_limit`` is enforced per-cgroup (including
    page cache), so this is the number the OOM killer compares against.
    All reads hit virtual filesystems (microseconds) and every field
    degrades to None off-Linux, so the loop stays quiet on dev machines.
    """
    rss = _read_int(mem_current_path)
    limit = _read_int(mem_max_path)
    try:
        # Minus one: the fd listdir itself holds open while counting.
        open_fds = len(os.listdir(fd_dir)) - 1
    except OSError:
        open_fds = None
    pct = round(rss / limit * 100.0, 1) if rss is not None and limit else None
    return ProcessStats(rss, limit, pct, open_fds)


def _emit_sample(
    routing: RoutingTable, lag_max_ms: float, lag_avg_ms: float
) -> None:
    stats = sample_process_stats()
    logger.info(
        "monitoring.sample",
        loop_lag_ms_max=round(lag_max_ms, 1),
        loop_lag_ms_avg=round(lag_avg_ms, 2),
        rss_bytes=stats.rss_bytes,
        mem_limit_bytes=stats.mem_limit_bytes,
        rss_pct_of_cap=stats.rss_pct_of_cap,
        open_fds=stats.open_fds,
        queue_drops_total=sum(SEND_QUEUE_DROPS.values()),
        queue_drops=dict(SEND_QUEUE_DROPS),
        **routing.gauges(),
    )


async def monitoring_loop(
    routing: RoutingTable, config: CentralServerConfig
) -> None:
    """Emit one monitoring.sample line every ``monitoring_interval_seconds``.

    Loop lag is accumulated over short ticks (1 s at the default interval)
    rather than measured once around a single long sleep: the max over ~60
    samples is what the ">100 ms p95 loop lag" revisit trigger needs, and a
    single per-minute drift sample would mostly miss stalls. The tick
    shrinks with the interval so fast-interval test configs still get
    multiple samples per emit.
    """
    interval = max(1, config.monitoring_interval_seconds)
    tick = min(1.0, interval / 5)
    ticks = max(1, round(interval / tick))
    loop = asyncio.get_running_loop()
    while True:
        try:
            lag_max = lag_sum = 0.0
            for _ in range(ticks):
                start = loop.time()
                await asyncio.sleep(tick)
                lag = max(0.0, loop.time() - start - tick)
                lag_max = max(lag_max, lag)
                lag_sum += lag
            _emit_sample(
                routing, lag_max * 1000.0, lag_sum / ticks * 1000.0
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("monitoring.error")
