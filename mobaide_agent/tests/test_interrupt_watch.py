"""Tests for the transcript watch that confirms an Esc / Ctrl+C interrupt.

The watch must fire only on the ``[Request interrupted by user`` entry, and
only on one written after the key. ``_TickingClock`` replaces the module's
``time`` so a short watch ends in a few polls; no test sleeps.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from mobaide.core import interrupt_watch
from mobaide.core.interrupt_watch import InterruptWatch


class _TickingClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        self.now += 1.0
        return self.now


@pytest.fixture(autouse=True)
def clock(monkeypatch) -> _TickingClock:
    fake = _TickingClock()
    monkeypatch.setattr(interrupt_watch, "time", fake)
    return fake


def _entry(entry_type: str, text: str, **extra: object) -> dict[str, object]:
    role = "assistant" if entry_type == "assistant" else "user"
    return {
        "type": entry_type,
        "message": {"role": role, "content": [{"type": "text", "text": text}]},
        **extra,
    }


INTERRUPT = _entry("user", "[Request interrupted by user for tool use]")


def _append(path: Path, *entries: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")


class _Watch:
    def __init__(self) -> None:
        self.calls = 0
        self.watch = InterruptWatch(self._hit, window_seconds=5, poll_seconds=0)

    def _hit(self) -> None:
        self.calls += 1

    async def run_out(self) -> None:
        """Wait for the armed watch to fire, expire, or end on cancel."""
        task = self.watch._task
        assert task is not None
        await asyncio.wait({task}, timeout=5)
        assert task.done()


@pytest.fixture
def transcript(tmp_path) -> Path:
    path = tmp_path / "session.jsonl"
    _append(path, _entry("user", "run the tests"))
    return path


class TestInterruptWatch:
    @pytest.mark.parametrize(
        "entry",
        [
            INTERRUPT,
            {"type": "user", "message": {"role": "user", "content": "[Request interrupted by user]"}},
        ],
        ids=["tool-use-list-content", "plain-string-content"],
    )
    async def test_an_interrupt_entry_after_the_key_fires_once(
        self, transcript, entry,
    ) -> None:
        w = _Watch()
        w.watch.note_transcript(str(transcript))

        w.watch.arm()
        _append(transcript, entry, entry)
        await w.run_out()

        assert w.calls == 1
        assert not w.watch.active

    @pytest.mark.parametrize(
        "entries",
        [
            [],
            [_entry("user", "[Request interrupted by user]", isSidechain=True)],
            [_entry("assistant", "[Request interrupted by user]")],
            [_entry("user", "why did I see [Request interrupted by user] here?")],
            [{"type": "system", "content": "[Request interrupted by user]"}],
        ],
        ids=["no-entry", "sidechain", "assistant", "marker-mid-text", "not-user"],
    )
    async def test_anything_but_a_main_chain_interrupt_entry_is_ignored(
        self, transcript, entries,
    ) -> None:
        w = _Watch()
        w.watch.note_transcript(str(transcript))

        w.watch.arm()
        _append(transcript, *entries, _entry("assistant", "still working"))
        await w.run_out()

        assert w.calls == 0
        assert not w.watch.active

    async def test_an_interrupt_written_before_the_key_is_ignored(
        self, transcript,
    ) -> None:
        # The previous Esc's entry is still in the file; this key may only
        # have closed a menu.
        _append(transcript, INTERRUPT)
        w = _Watch()
        w.watch.note_transcript(str(transcript))

        w.watch.arm()
        await w.run_out()

        assert w.calls == 0

    async def test_a_new_transcript_cancels_the_running_watch(
        self, transcript, tmp_path,
    ) -> None:
        # ``/clear`` or ``--resume`` moves the transcript: the old file's
        # offset means nothing for the new one.
        w = _Watch()
        w.watch.note_transcript(str(transcript))
        w.watch.arm()
        task = w.watch._task

        w.watch.note_transcript(str(tmp_path / "other.jsonl"))
        _append(transcript, INTERRUPT)
        await asyncio.wait({task}, timeout=5)

        assert w.calls == 0
        assert not w.watch.active

    async def test_without_a_transcript_the_key_arms_nothing(self) -> None:
        w = _Watch()
        w.watch.note_transcript("relative/session.jsonl")

        w.watch.arm()

        assert not w.watch.active
