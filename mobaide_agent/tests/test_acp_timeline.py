"""Tests for the chat timeline: batching inside one frame, event sequence
numbers, removed items, replay of missed events versus a snapshot, and the
memory bounds.

Async tests use a flush interval that never fires and call ``flush()``
themselves, so nothing waits on the batching timer. Sync tests run with no
event loop, where every change is sent at once.
"""
from __future__ import annotations

import orjson

from mobaide.acp import timeline as timeline_module
from mobaide.acp.timeline import ChatTimeline

NEVER = 3600.0


def _make(**kwargs) -> tuple[ChatTimeline, list[dict]]:
    frames: list[dict] = []
    tl = ChatTimeline(lambda data: frames.append(orjson.loads(data)), **kwargs)
    return tl, frames


def _agent(item_id: str, text: str = "") -> dict:
    return {"id": item_id, "type": "agent", "text": text}


class TestBatching:
    async def test_new_item_and_its_appends_go_out_as_one_upsert(self):
        tl, frames = _make(flush_interval=NEVER)
        tl.upsert(_agent("m:1", "Hel"))
        tl.append_text("m:1", "lo")
        tl.append_text("m:1", " world")
        assert frames == []

        tl.flush()

        assert len(frames) == 1
        assert frames[0]["events"] == [
            {"type": "item", "item": {"id": "m:1", "type": "agent", "text": "Hello world"}, "seq": 1}
        ]

    async def test_appends_to_a_sent_item_merge_into_one_append(self):
        tl, frames = _make(flush_interval=NEVER)
        tl.upsert(_agent("m:1", "a"))
        tl.flush()
        tl.append_text("m:1", "b")
        tl.append_text("m:1", "c")
        tl.flush()

        assert frames[1]["events"] == [{"type": "append", "id": "m:1", "text": "bc", "seq": 2}]
        assert tl.get("m:1")["text"] == "abc"

    async def test_touch_after_append_resends_the_whole_item(self):
        tl, frames = _make(flush_interval=NEVER)
        tl.upsert(_agent("m:1", "a"))
        tl.flush()
        tl.append_text("m:1", "b")
        tl.touch("m:1")
        tl.flush()

        assert frames[1]["events"] == [
            {"type": "item", "item": {"id": "m:1", "type": "agent", "text": "ab"}, "seq": 2}
        ]

    async def test_items_and_state_share_one_frame(self):
        tl, frames = _make(flush_interval=NEVER)
        tl.upsert(_agent("m:1"))
        tl.upsert({"id": "tool-1", "type": "tool", "status": "pending"})
        tl.patch_state(status="running")
        tl.patch_state(cancelling=True)
        tl.flush()

        assert len(frames) == 1
        events = frames[0]["events"]
        assert [e["type"] for e in events] == ["item", "item", "state"]
        assert events[2]["patch"] == {"status": "running", "cancelling": True}
        assert [e["seq"] for e in events] == [1, 2, 3]
        assert frames[0]["seq"] == 3
        assert frames[0]["epoch"] == tl.epoch

    async def test_urgent_change_is_sent_at_once(self):
        tl, frames = _make(flush_interval=NEVER)
        tl.upsert(_agent("m:1"))
        tl.patch_state(status="error", urgent=True)

        assert len(frames) == 1
        assert [e["type"] for e in frames[0]["events"]] == ["item", "state"]

    def test_unchanged_state_triggers_no_frame(self):
        tl, frames = _make()
        tl.patch_state(status="ready")
        tl.patch_state(status="ready")

        assert len(frames) == 1

    def test_without_an_event_loop_each_change_is_sent_at_once(self):
        tl, frames = _make()
        tl.upsert(_agent("m:1"))
        tl.append_text("m:1", "x")

        assert [f["events"][0]["type"] for f in frames] == ["item", "append"]

    async def test_a_burst_is_sent_once_the_batch_passes_the_size_bound(self, monkeypatch):
        one_item = len(orjson.dumps(_agent("m:0", "x" * 100)))
        monkeypatch.setattr(timeline_module, "BATCH_FLUSH_BYTES", one_item * 3)
        tl, frames = _make(flush_interval=NEVER)

        tl.upsert(_agent("m:0", "x" * 100))
        tl.upsert(_agent("m:1", "x" * 100))
        assert frames == []
        tl.upsert(_agent("m:2", "x" * 100))

        assert len(frames) == 1
        assert [e["item"]["id"] for e in frames[0]["events"]] == ["m:0", "m:1", "m:2"]

        tl.upsert(_agent("m:3", "x" * 100))
        assert len(frames) == 1

    async def test_appended_text_counts_toward_the_size_bound(self, monkeypatch):
        monkeypatch.setattr(timeline_module, "BATCH_FLUSH_BYTES", 50)
        tl, frames = _make(flush_interval=NEVER)
        tl.upsert(_agent("m:1"), urgent=True)

        tl.append_text("m:1", "x" * 30)
        assert len(frames) == 1
        tl.append_text("m:1", "y" * 30)

        assert len(frames) == 2
        assert frames[1]["events"] == [{"type": "append", "id": "m:1", "text": "x" * 30 + "y" * 30, "seq": 2}]

    def test_append_stops_at_the_item_text_cap(self, monkeypatch):
        monkeypatch.setattr(timeline_module, "ITEM_TEXT_MAX", 5)
        tl, frames = _make()
        tl.upsert(_agent("m:1", "abc"))
        tl.append_text("m:1", "defgh")
        tl.append_text("m:1", "ijk")

        assert tl.get("m:1")["text"] == "abcde"
        assert frames[1]["events"][0]["text"] == "de"
        assert len(frames) == 2


class TestRemove:
    async def test_a_sent_item_goes_away_with_one_remove_event(self):
        tl, frames = _make(flush_interval=NEVER)
        tl.upsert(_agent("n:1", "Setting up"))
        tl.upsert(_agent("m:2", "hi"))
        tl.flush()

        tl.remove("n:1")
        tl.flush()

        assert frames[1]["events"] == [{"type": "remove", "id": "n:1", "seq": 3}]
        assert tl.get("n:1") is None
        snap = orjson.loads(tl.snapshot_frame())
        assert [item["id"] for item in snap["items"]] == ["m:2"]

    async def test_an_urgent_remove_is_sent_at_once(self):
        tl, frames = _make(flush_interval=NEVER)
        tl.upsert(_agent("n:1"), urgent=True)

        tl.remove("n:1", urgent=True)

        assert len(frames) == 2
        assert frames[1]["events"] == [{"type": "remove", "id": "n:1", "seq": 2}]

    async def test_a_remove_replaces_the_pending_appends_of_its_item(self):
        tl, frames = _make(flush_interval=NEVER)
        tl.upsert(_agent("m:1", "a"))
        tl.flush()
        tl.append_text("m:1", "b")

        tl.remove("m:1")
        tl.flush()

        assert frames[1]["events"] == [{"type": "remove", "id": "m:1", "seq": 2}]

    async def test_an_item_added_and_removed_in_one_batch_is_never_sent_as_an_item(self):
        tl, frames = _make(flush_interval=NEVER)
        tl.upsert(_agent("n:1", "Setting up"))

        tl.remove("n:1")
        tl.flush()

        assert [e["type"] for e in frames[0]["events"]] == ["remove"]

    def test_a_reconnecting_client_gets_the_remove_in_the_replay(self):
        tl, _ = _make()
        tl.upsert(_agent("n:1"))
        tl.remove("n:1")

        frame = orjson.loads(tl.replay_frame(1))

        assert frame["events"] == [{"type": "remove", "id": "n:1", "seq": 2}]

    def test_removing_an_unknown_item_sends_nothing(self):
        tl, frames = _make()
        tl.upsert(_agent("m:1"))
        tl.remove("m:1")

        tl.remove("m:1")
        tl.remove("missing")

        assert len(frames) == 2
        assert tl.seq == 2

    def test_a_removed_item_frees_its_share_of_the_memory_cap(self):
        one_item = len(orjson.dumps(_agent("m:0", "x" * 100)))
        tl, _ = _make(memory_max_bytes=one_item * 3 + 10)
        for i in range(3):
            tl.upsert(_agent(f"m:{i}", "x" * 100))

        tl.remove("m:1")
        tl.upsert(_agent("m:3", "x" * 100))

        assert [item["id"] for item in tl.items()] == ["m:0", "m:2", "m:3"]
        assert orjson.loads(tl.snapshot_frame())["truncated"] is False


class TestReplayAndSnapshot:
    def test_replay_returns_only_the_missed_events(self):
        tl, _ = _make()
        for i in range(3):
            tl.upsert(_agent(f"m:{i}"))

        frame = orjson.loads(tl.replay_frame(1))

        assert frame["t"] == "events"
        assert [e["seq"] for e in frame["events"]] == [2, 3]
        assert frame["seq"] == 3

    def test_replay_when_up_to_date_is_an_empty_ack(self):
        tl, _ = _make()
        tl.upsert(_agent("m:1"))

        frame = orjson.loads(tl.replay_frame(1))

        assert frame["events"] == []
        assert frame["seq"] == 1

    def test_replay_from_the_future_needs_a_snapshot(self):
        tl, _ = _make()
        tl.upsert(_agent("m:1"))
        assert tl.replay_frame(5) is None

    def test_replay_past_the_ring_needs_a_snapshot(self, monkeypatch):
        monkeypatch.setattr(timeline_module, "RECENT_EVENTS_MAX", 3)
        tl, _ = _make()
        for i in range(5):
            tl.upsert(_agent(f"m:{i}"))

        kept = orjson.loads(tl.replay_frame(2))
        assert [e["seq"] for e in kept["events"]] == [3, 4, 5]
        assert tl.replay_frame(1) is None

    async def test_snapshot_flushes_pending_changes_first(self):
        tl, frames = _make(flush_interval=NEVER)
        tl.upsert(_agent("m:1", "hi"))
        tl.patch_state(status="running")

        snap = orjson.loads(tl.snapshot_frame())

        assert len(frames) == 1
        assert snap["t"] == "snapshot"
        assert snap["epoch"] == tl.epoch
        assert snap["seq"] == 2
        assert snap["truncated"] is False
        assert snap["state"]["status"] == "running"
        assert snap["items"] == [{"id": "m:1", "type": "agent", "text": "hi"}]

    def test_snapshot_over_its_size_cap_keeps_the_newest_items(self):
        one_item = len(orjson.dumps(_agent("m:0", "x" * 100)))
        tl, _ = _make(snapshot_max_bytes=one_item * 2 + 10)
        for i in range(5):
            tl.upsert(_agent(f"m:{i}", "x" * 100))

        snap = orjson.loads(tl.snapshot_frame())

        assert [item["id"] for item in snap["items"]] == ["m:3", "m:4"]
        assert snap["truncated"] is True

    def test_snapshot_always_holds_the_newest_item(self):
        tl, _ = _make(snapshot_max_bytes=10)
        tl.upsert(_agent("m:1", "x" * 100))

        snap = orjson.loads(tl.snapshot_frame())

        assert [item["id"] for item in snap["items"]] == ["m:1"]
        assert snap["truncated"] is False


class TestMemoryBoundAndReset:
    def test_oldest_items_are_dropped_over_the_memory_cap(self):
        one_item = len(orjson.dumps(_agent("m:0", "x" * 100)))
        tl, _ = _make(memory_max_bytes=one_item * 3 + 10)
        for i in range(6):
            tl.upsert(_agent(f"m:{i}", "x" * 100))

        assert [item["id"] for item in tl.items()] == ["m:3", "m:4", "m:5"]
        snap = orjson.loads(tl.snapshot_frame())
        assert snap["truncated"] is True

    def test_reset_starts_a_new_empty_epoch_and_keeps_state(self):
        tl, _ = _make()
        tl.upsert(_agent("m:1"))
        tl.patch_state(title="Fix login")
        old_epoch = tl.epoch

        tl.reset()

        assert tl.epoch != old_epoch
        assert tl.seq == 0
        assert tl.items() == []
        assert tl.state["title"] == "Fix login"
        snap = orjson.loads(tl.snapshot_frame())
        assert (snap["seq"], snap["items"], snap["truncated"]) == (0, [], False)

    def test_changes_to_unknown_items_are_ignored(self):
        tl, frames = _make()
        tl.append_text("missing", "x")
        tl.touch("missing")
        assert frames == []

