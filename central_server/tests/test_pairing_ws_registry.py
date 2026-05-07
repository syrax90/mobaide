"""Tests for ``central_server.pairing.ws_handlers.PairingWsRegistry``.

The registry is the in-process single-flight switchboard between the
``POST /api/pairing/claim`` handler and the ``WS /ws/pairing/{nonce}``
handler. Concurrency invariants:

* Exactly one waiter per nonce; second register raises KeyError.
* Dispatch returns False when no waiter is registered, or when the
  queue's bounded slot (maxsize=1) is already taken — the latter is a
  defensive guard against double-claim against the same waiter.
* unregister is idempotent.
"""
from __future__ import annotations

import asyncio

import pytest

from central_server.pairing.ws_handlers import PairingWsRegistry


class TestRegisterWaiter:
    async def test_returns_queue(self):
        registry = PairingWsRegistry()

        queue = await registry.register_waiter("nonce-1")

        assert isinstance(queue, asyncio.Queue)
        assert queue.empty()

    async def test_duplicate_nonce_raises_keyerror(self):
        registry = PairingWsRegistry()
        await registry.register_waiter("nonce-1")

        with pytest.raises(KeyError):
            await registry.register_waiter("nonce-1")

    async def test_distinct_nonces_yield_distinct_queues(self):
        registry = PairingWsRegistry()

        q1 = await registry.register_waiter("a")
        q2 = await registry.register_waiter("b")

        assert q1 is not q2

    async def test_after_unregister_register_succeeds_again(self):
        registry = PairingWsRegistry()
        await registry.register_waiter("nonce-1")
        await registry.unregister_waiter("nonce-1")

        # Re-registering after the WS disconnects must work — the next
        # WS connect for the same (still-valid) nonce should succeed.
        q = await registry.register_waiter("nonce-1")
        assert q.empty()


class TestUnregisterWaiter:
    async def test_is_idempotent_on_unknown_nonce(self):
        registry = PairingWsRegistry()

        # Must not raise.
        await registry.unregister_waiter("never-registered")
        await registry.unregister_waiter("never-registered")


class TestDispatchToWaiter:
    async def test_returns_false_when_no_waiter(self):
        registry = PairingWsRegistry()

        delivered = await registry.dispatch_to_waiter("absent", b"payload")

        assert delivered is False

    async def test_delivers_payload_to_registered_queue(self):
        registry = PairingWsRegistry()
        queue = await registry.register_waiter("nonce-1")

        delivered = await registry.dispatch_to_waiter("nonce-1", b"hello")

        assert delivered is True
        assert queue.get_nowait() == b"hello"

    async def test_second_dispatch_returns_false_when_queue_full(self):
        # Queue is bounded at maxsize=1; a second push without the WS
        # consuming the first must surface as delivered=False so the
        # claim handler can log "ws_delivered=False".
        registry = PairingWsRegistry()
        await registry.register_waiter("nonce-1")

        first = await registry.dispatch_to_waiter("nonce-1", b"a")
        second = await registry.dispatch_to_waiter("nonce-1", b"b")

        assert first is True
        assert second is False

    async def test_after_unregister_dispatch_returns_false(self):
        registry = PairingWsRegistry()
        await registry.register_waiter("nonce-1")
        await registry.unregister_waiter("nonce-1")

        delivered = await registry.dispatch_to_waiter("nonce-1", b"x")

        assert delivered is False
