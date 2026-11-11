"""Tests for the in-process pub/sub ``EventBus``."""
from __future__ import annotations

from mobaide.agent.events.bus import EventBus, AgentEvent


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


    async def test_publish_after_unsubscribe_drops_only_that_sub(self):
        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        bus.unsubscribe(q1)

        bus.publish("sessions.changed", {"sessions": []})
        assert q1.empty()
        ev = q2.get_nowait()
        assert ev.type == "sessions.changed"
