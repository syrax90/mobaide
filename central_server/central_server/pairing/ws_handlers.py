"""In-process registry mapping pairing nonces to waiting WebSockets.

The agent connects to ``/ws/pairing/{nonce}`` and registers a queue here
keyed by the nonce. When the mobile's ``POST /api/pairing/claim`` request
finishes constructing the signed push payload, it dispatches the payload
into the queue, which the WS handler then forwards to the agent.

This is in-memory and per-process: single-uvicorn deployments are fine.
Multi-worker (gunicorn -w N) deployments would need a shared backing
(Redis pub/sub or DB polling); flagged in ``docs/qr-pairing.md``.
"""
from __future__ import annotations

import asyncio

from ..monitoring import record_send_queue_drop


class PairingWsRegistry:
    """Single-flight registry; one waiter per nonce."""

    def __init__(self) -> None:
        self._waiters: dict[str, asyncio.Queue[bytes]] = {}
        self._lock = asyncio.Lock()

    async def register_waiter(self, nonce: str) -> asyncio.Queue[bytes]:
        """Register a waiter for ``nonce``. Raises ``KeyError`` on duplicate."""
        async with self._lock:
            if nonce in self._waiters:
                raise KeyError(f"nonce already has a registered waiter")
            queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1)
            self._waiters[nonce] = queue
            return queue

    async def unregister_waiter(self, nonce: str) -> None:
        """Idempotent removal."""
        async with self._lock:
            self._waiters.pop(nonce, None)

    async def dispatch_to_waiter(self, nonce: str, payload: bytes) -> bool:
        """Push ``payload`` onto the queue for ``nonce``.

        Returns True if a waiter was registered and accepted the message,
        False if no waiter exists or the queue is full (which would mean
        a previous push was already pending — defensive fallback).
        """
        async with self._lock:
            queue = self._waiters.get(nonce)
        if queue is None:
            return False
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            record_send_queue_drop("pairing_push")
            return False
        return True
