"""Transport-agnostic body of the ``/ws/autoscan`` streaming protocol.

Used by both the direct-mode FastAPI WebSocket handler
(:func:`claude_mobile.agent.autoscan.router.ws_autoscan`) and the relay-mode
bridge in :class:`claude_mobile.relay.client.RelayClient`, so that the wire
protocol — snapshot + caught_up + live events + subscribe-set filtering +
``resume_from_seq`` replay — is defined exactly once.

The coroutine takes abstract ``send_text``/``receive_text`` callables so
the caller can plug in a real WebSocket, an in-memory queue (relay bridge),
or a test fake (unit tests).

Protocol v1:
    Client may send within ``grace_timeout_s`` of start a JSON text frame:
        { "subscribe": ["projectA", ...],
          "resume_from_seq": {"projectA": 42, ...} }
    The server then sends:
        1. { "type": "snapshot",
             "data": { "scans": [...DTOs...],
                       "events": {projectName: [event,...]} } }
        2. { "type": "caught_up" }
        3. Live { "type": "event", "project_name": ..., "event": {...} } frames.
    Clients NOT in ``subscribe`` get all event types except ``log`` filtered out.
    Clients may update their subscription mid-stream by sending another
    ``{"subscribe": [...]}`` text frame; the filter set is replaced.
"""
from __future__ import annotations

import asyncio
import json
from typing import Awaitable, Callable

from claude_mobile.linear.scanner import AutoScanInstance, AutoScanSessionManager


SendText = Callable[[str], Awaitable[None]]
ReceiveText = Callable[[], Awaitable[str]]


async def run_autoscan_stream(
    mgr: AutoScanSessionManager,
    send_text: SendText,
    receive_text: ReceiveText,
    grace_timeout_s: float = 0.2,
) -> int:
    """Drive one auto-scan stream session over abstract send/receive callables.

    Returns the number of frames sent (useful for logging). Lets
    ``asyncio.CancelledError`` and peer-disconnect exceptions propagate so
    callers see the real termination cause; runs the subscription cleanup
    in ``finally`` so event-bus queues are released on every exit path.

    ``grace_timeout_s`` sets how long to wait for the optional initial
    config frame. Direct-mode callers keep the 200ms default; relay-mode
    callers should widen it to account for WAN round-trip.
    """
    resume_from_seq: dict[str, int] = {}
    subscribe_set: set[str] = set()
    try:
        first = await asyncio.wait_for(receive_text(), timeout=grace_timeout_s)
        try:
            payload = json.loads(first)
            rfs = payload.get("resume_from_seq") or {}
            if isinstance(rfs, dict):
                resume_from_seq = {str(k): int(v) for k, v in rfs.items()}
            sub = payload.get("subscribe") or []
            if isinstance(sub, list):
                subscribe_set = {str(s) for s in sub}
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    except asyncio.TimeoutError:
        pass

    instances: dict[str, AutoScanInstance] = {
        i.project_name: i for i in mgr.all_instances()
    }
    queues: dict[str, asyncio.Queue] = {
        name: inst.subscribe_events() for name, inst in instances.items()
    }

    async def _send(obj: dict) -> None:
        await send_text(json.dumps(obj))

    msg_count = 0
    try:
        events_snapshot: dict[str, list[dict]] = {}
        for name, inst in instances.items():
            ring = [e.to_dict() for e in inst.recent_events]
            resume_seq = resume_from_seq.get(name)
            if resume_seq is not None and ring:
                first_seq = ring[0]["seq"]
                if first_seq <= resume_seq + 1:
                    ring = [e for e in ring if e["seq"] > resume_seq]
            events_snapshot[name] = ring

        await _send({
            "type": "snapshot",
            "data": {
                "scans": mgr.list_response()["scans"],
                "events": events_snapshot,
            },
        })
        msg_count += 1

        await _send({"type": "caught_up"})
        msg_count += 1

        async def _pump_one(project_name: str, q: asyncio.Queue) -> None:
            nonlocal msg_count
            while True:
                evt = await q.get()
                if evt.type == "log" and project_name not in subscribe_set:
                    continue
                await _send({
                    "type": "event",
                    "project_name": project_name,
                    "event": evt.to_dict(),
                })
                msg_count += 1

        async def _reader() -> None:
            nonlocal subscribe_set
            while True:
                text = await receive_text()
                try:
                    obj = json.loads(text)
                    sub = obj.get("subscribe")
                    if isinstance(sub, list):
                        subscribe_set = {str(s) for s in sub}
                except (json.JSONDecodeError, ValueError):
                    pass

        if not queues:
            await _reader()
            return msg_count

        pump_tasks = [
            asyncio.create_task(_pump_one(n, q)) for n, q in queues.items()
        ]
        reader_task = asyncio.create_task(_reader())
        done, pending = await asyncio.wait(
            [*pump_tasks, reader_task], return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
        for t in done:
            if t.cancelled():
                continue
            exc = t.exception()
            if exc is not None:
                raise exc
        return msg_count
    finally:
        for name, q in queues.items():
            inst = mgr.get(name)
            if inst is not None:
                inst.unsubscribe_events(q)
