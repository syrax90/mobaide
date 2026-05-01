"""Tests for the in-process pub/sub ``EventBus``."""
from __future__ import annotations

import asyncio

import pytest

from mobaide.agent.events.bus import EventBus, AgentEvent


class TestEventBusSubscription:
    async def test_subscribe_increments_count(self):
        bus = EventBus()
        assert bus.subscriber_count == 0
        q1 = bus.subscribe()
        assert bus.subscriber_count == 1
        q2 = bus.subscribe()
        assert bus.subscriber_count == 2
        # Queues are distinct asyncio.Queue instances.
        assert q1 is not q2
        assert isinstance(q1, asyncio.Queue)

    async def test_unsubscribe_decrements_count(self):
        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        bus.unsubscribe(q1)
        assert bus.subscriber_count == 1
        bus.unsubscribe(q2)
        assert bus.subscriber_count == 0

    async def test_unsubscribe_unknown_queue_is_noop(self):
        bus = EventBus()
        stranger: asyncio.Queue[AgentEvent] = asyncio.Queue()
        # Must not raise — discard() semantics.
        bus.unsubscribe(stranger)
        assert bus.subscriber_count == 0

        q = bus.subscribe()
        bus.unsubscribe(stranger)  # still a no-op with real subs present
        assert bus.subscriber_count == 1
        bus.unsubscribe(q)

    async def test_subscribe_respects_maxsize(self):
        bus = EventBus()
        q = bus.subscribe(maxsize=1)
        assert q.maxsize == 1


class TestEventBusPublish:
    async def test_publish_delivers_to_all_subscribers(self):
        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()

        payload = {"sessions": [{"session_id": "s1"}]}
        bus.publish("sessions.changed", payload)

        ev1 = q1.get_nowait()
        ev2 = q2.get_nowait()
        assert isinstance(ev1, AgentEvent)
        assert ev1.type == "sessions.changed"
        assert ev1.payload == payload
        assert ev2.type == "sessions.changed"
        assert ev2.payload == payload

    async def test_publish_no_subscribers_is_noop(self):
        bus = EventBus()
        # Must not raise regardless of how many times we publish.
        bus.publish("sessions.changed", {"sessions": []})
        bus.publish("projects.changed", {"projects": []})
        assert bus.subscriber_count == 0

    async def test_publish_payload_is_same_object(self):
        bus = EventBus()
        q = bus.subscribe()
        payload = {"projects": [{"name": "p1"}]}
        bus.publish("projects.changed", payload)
        ev = q.get_nowait()
        # AgentEvent holds the exact dict reference (no copy, no mutation).
        assert ev.payload is payload
        assert ev.type == "projects.changed"

    async def test_publish_queue_full_isolation(self):
        """A full queue on subscriber A must not block delivery to subscriber B."""
        bus = EventBus()
        q_full = bus.subscribe(maxsize=1)
        q_ok = bus.subscribe()

        # Fill q_full directly so the next publish hits QueueFull for it.
        sentinel = AgentEvent(type="prefill", payload={})
        q_full.put_nowait(sentinel)
        assert q_full.full()

        bus.publish("sessions.changed", {"sessions": []})

        # q_full still at 1 item — and it's the prefill sentinel (silent drop).
        assert q_full.qsize() == 1
        first = q_full.get_nowait()
        assert first is sentinel

        # q_ok received the published event.
        ev = q_ok.get_nowait()
        assert ev.type == "sessions.changed"
        assert ev.payload == {"sessions": []}

    async def test_publish_preserves_order_for_single_subscriber(self):
        bus = EventBus()
        q = bus.subscribe()

        bus.publish("sessions.changed", {"sessions": [1]})
        bus.publish("projects.changed", {"projects": ["p"]})

        ev1 = q.get_nowait()
        ev2 = q.get_nowait()
        assert ev1.type == "sessions.changed"
        assert ev1.payload == {"sessions": [1]}
        assert ev2.type == "projects.changed"
        assert ev2.payload == {"projects": ["p"]}

    async def test_publish_after_unsubscribe_drops_only_that_sub(self):
        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        bus.unsubscribe(q1)

        bus.publish("sessions.changed", {"sessions": []})
        assert q1.empty()
        ev = q2.get_nowait()
        assert ev.type == "sessions.changed"


class TestAgentEventDataclass:
    def test_agent_event_is_frozen(self):
        ev = AgentEvent(type="x", payload={"a": 1})
        with pytest.raises(Exception):
            # Frozen dataclass rejects mutation.
            ev.type = "y"  # type: ignore[misc]

    def test_agent_event_equality(self):
        a = AgentEvent(type="x", payload={"k": "v"})
        b = AgentEvent(type="x", payload={"k": "v"})
        assert a == b
