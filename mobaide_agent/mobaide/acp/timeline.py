"""The chat timeline: ordered items, session state, and the event stream.

Every change is recorded as an event with a sequence number inside an
``epoch`` (a fresh epoch means "throw your copy away"). Changes are batched
for ``flush_interval`` seconds and sent as one ``events`` frame, so a token
stream costs one frame per batch instead of one per token. Inside a batch an
item is sent once: as a full ``item`` upsert when it was created or changed,
or as one ``append`` when only text was added to it.
"""
from __future__ import annotations

import asyncio
import collections
import secrets
from collections.abc import Callable
from typing import Any

import orjson

from .normalize import ITEM_TEXT_MAX

SNAPSHOT_MAX_BYTES = 2 * 1024 * 1024
MEMORY_MAX_BYTES = 12 * 1024 * 1024
RECENT_EVENTS_MAX = 5000
RECENT_EVENTS_MAX_BYTES = 4 * 1024 * 1024
BATCH_FLUSH_BYTES = 512 * 1024
REPLAY_SLACK_BYTES = 64 * 1024
FLUSH_INTERVAL = 0.06

FrameSink = Callable[[bytes], None]


def default_state() -> dict[str, Any]:
    return {
        "status": "starting",
        "error": None,
        "agent": None,
        "title": None,
        "cwd": None,
        "acp_session_id": None,
        "config": [],
        "commands": [],
        "usage": None,
        "plan": [],
        "requests": [],
        "queue": [],
        "prompt_caps": {"image": False},
        "turn_started_at": None,
        "cancelling": False,
        "steering": False,
    }


class ChatTimeline:
    def __init__(
        self,
        sink: FrameSink,
        *,
        flush_interval: float = FLUSH_INTERVAL,
        snapshot_max_bytes: int = SNAPSHOT_MAX_BYTES,
        memory_max_bytes: int = MEMORY_MAX_BYTES,
    ) -> None:
        self._sink = sink
        self._flush_interval = flush_interval
        self._snapshot_max = snapshot_max_bytes
        self._memory_max = memory_max_bytes
        self.epoch = secrets.token_hex(6)
        self.seq = 0
        self.state: dict[str, Any] = default_state()
        self._items: dict[str, dict[str, Any]] = {}
        self._sizes: dict[str, int] = {}
        self._total_bytes = 0
        self._dropped_old = False
        self._recent: collections.deque[tuple[int, bytes]] = collections.deque()
        self._recent_bytes = 0
        self._touched: dict[str, str] = {}
        self._appends: dict[str, list[str]] = {}
        self._state_patch: dict[str, Any] = {}
        self._batch_bytes = 0
        self._flush_handle: asyncio.TimerHandle | None = None

    def reset(self) -> None:
        """Start a new epoch with an empty timeline (state is kept)."""
        self._cancel_flush()
        self.epoch = secrets.token_hex(6)
        self.seq = 0
        self._items.clear()
        self._sizes.clear()
        self._total_bytes = 0
        self._dropped_old = False
        self._recent.clear()
        self._recent_bytes = 0
        self._touched.clear()
        self._appends.clear()
        self._state_patch.clear()
        self._batch_bytes = 0

    def get(self, item_id: str) -> dict[str, Any] | None:
        return self._items.get(item_id)

    def items(self) -> list[dict[str, Any]]:
        return list(self._items.values())

    def upsert(self, item: dict[str, Any], *, urgent: bool = False) -> None:
        item_id = item["id"]
        self._items[item_id] = item
        self._count_batch(item_id)
        self._touched[item_id] = "item"
        self._appends.pop(item_id, None)
        self._schedule(urgent)

    def touch(self, item_id: str, *, urgent: bool = False) -> None:
        """Mark an item changed in place (re-sent as a full upsert)."""
        if item_id not in self._items:
            return
        self._count_batch(item_id)
        self._touched[item_id] = "item"
        self._appends.pop(item_id, None)
        self._schedule(urgent)

    def append_text(self, item_id: str, text: str) -> None:
        item = self._items.get(item_id)
        if item is None or not text:
            return
        current = item.get("text") or ""
        if len(current) >= ITEM_TEXT_MAX:
            return
        text = text[: ITEM_TEXT_MAX - len(current)]
        item["text"] = current + text
        self._sizes[item_id] = self._sizes.get(item_id, 0) + len(text)
        self._total_bytes += len(text)
        self._batch_bytes += len(text)
        if self._touched.get(item_id) != "item":
            self._touched[item_id] = "append"
            self._appends.setdefault(item_id, []).append(text)
        self._schedule(False)

    def patch_state(self, *, urgent: bool = False, **patch: Any) -> None:
        changed = False
        for key, value in patch.items():
            if self.state.get(key) != value or key not in self.state:
                changed = True
            self.state[key] = value
            self._state_patch[key] = value
        if changed:
            self._schedule(urgent)
        elif urgent and (self._touched or self._state_patch):
            self.flush()

    def snapshot_frame(self) -> bytes:
        self.flush()
        items: list[bytes] = []
        size = 0
        truncated = self._dropped_old
        for item in reversed(list(self._items.values())):
            encoded = orjson.dumps(item)
            if items and size + len(encoded) > self._snapshot_max:
                truncated = True
                break
            items.append(encoded)
            size += len(encoded)
        items.reverse()
        head = orjson.dumps(
            {
                "t": "snapshot",
                "epoch": self.epoch,
                "seq": self.seq,
                "state": self.state,
                "truncated": truncated,
            }
        )
        return head[:-1] + b',"items":[' + b",".join(items) + b"]}"

    def replay_frame(self, after_seq: int) -> bytes | None:
        """The events after ``after_seq`` as one frame, or ``None`` when they
        are no longer held (the caller then sends a snapshot)."""
        self.flush()
        if after_seq == self.seq:
            return self._events_frame([])
        if after_seq > self.seq or not self._recent:
            return None
        oldest = self._recent[0][0]
        if after_seq < oldest - 1:
            return None
        events = [ev for seq, ev in self._recent if seq > after_seq]
        # The ring can hold many copies of one changing item: past the
        # snapshot's size (plus slack for the event envelopes), the snapshot
        # is the cheaper answer.
        if sum(map(len, events)) > min(self._total_bytes + REPLAY_SLACK_BYTES, self._snapshot_max):
            return None
        return self._events_frame(events)

    def flush(self) -> None:
        self._cancel_flush()
        if not self._touched and not self._state_patch:
            return
        encoded: list[bytes] = []
        for item_id, how in self._touched.items():
            item = self._items.get(item_id)
            if item is None:
                continue
            if how == "item":
                body = {"type": "item", "item": item}
            else:
                body = {"type": "append", "id": item_id, "text": "".join(self._appends.get(item_id, []))}
            encoded.append(self._record(body))
        if self._state_patch:
            encoded.append(self._record({"type": "state", "patch": self._state_patch}))
        self._touched = {}
        self._appends = {}
        self._state_patch = {}
        self._batch_bytes = 0
        self._evict_memory()
        self._sink(self._events_frame(encoded))

    def close(self) -> None:
        self._cancel_flush()

    def _record(self, body: dict[str, Any]) -> bytes:
        self.seq += 1
        body["seq"] = self.seq
        data = orjson.dumps(body)
        self._recent.append((self.seq, data))
        self._recent_bytes += len(data)
        while self._recent and (
            len(self._recent) > RECENT_EVENTS_MAX or self._recent_bytes > RECENT_EVENTS_MAX_BYTES
        ):
            _, old = self._recent.popleft()
            self._recent_bytes -= len(old)
        return data

    def _events_frame(self, encoded: list[bytes]) -> bytes:
        head = orjson.dumps({"t": "events", "epoch": self.epoch, "seq": self.seq})
        return head[:-1] + b',"events":[' + b",".join(encoded) + b"]}"

    def _count_batch(self, item_id: str) -> None:
        """Resize the item and count it once per batch (re-sent whole)."""
        before = self._sizes.get(item_id, 0)
        self._resize(item_id)
        if self._touched.get(item_id) == "item":
            self._batch_bytes += self._sizes[item_id] - before
        else:
            self._batch_bytes += self._sizes[item_id]

    def _resize(self, item_id: str) -> None:
        size = len(orjson.dumps(self._items[item_id]))
        self._total_bytes += size - self._sizes.get(item_id, 0)
        self._sizes[item_id] = size

    def _evict_memory(self) -> None:
        while self._total_bytes > self._memory_max and len(self._items) > 1:
            oldest = next(iter(self._items))
            self._items.pop(oldest)
            self._total_bytes -= self._sizes.pop(oldest, 0)
            self._dropped_old = True

    def _schedule(self, urgent: bool) -> None:
        # The size bound keeps a burst (a session/load replay runs as one
        # backlog on the loop) to a few frames, not one per item.
        if urgent or self._batch_bytes >= BATCH_FLUSH_BYTES:
            self.flush()
            return
        if self._flush_handle is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                self.flush()
                return
            self._flush_handle = loop.call_later(self._flush_interval, self.flush)

    def _cancel_flush(self) -> None:
        if self._flush_handle is not None:
            self._flush_handle.cancel()
            self._flush_handle = None
