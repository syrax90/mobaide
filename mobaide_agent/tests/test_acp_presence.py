"""Antigravity's "conversation already open" marker
(``mobaide/acp/presence.py``): the probe that another app has a conversation
open, the hold a chat session takes and releases, and the ids and files it
never touches.

``flock`` locks belong to an open file, not to a process, so a second
descriptor opened here stands in for another ``agy`` process.
"""
from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from mobaide.acp.presence import ConversationPresence, PresenceHold
from tests.conftest import skip_if_not_linux

pytestmark = skip_if_not_linux


@pytest.fixture
def app_data(tmp_path) -> Path:
    return tmp_path / "antigravity-cli"


@pytest.fixture
def presence(app_data) -> ConversationPresence:
    return ConversationPresence(app_data)


@pytest.fixture
def holds() -> Iterator[list[PresenceHold]]:
    """Releases every hold a test takes: a leaked one would stay marked as
    this process's own for the rest of the run."""
    taken: list[PresenceHold] = []
    yield taken
    for hold in taken:
        hold.release()


def _lock_file(app_data: Path, session_id: str) -> Path:
    return app_data / "presence" / f"{session_id}.lock"


@contextmanager
def _other_app_holds(path: Path) -> Iterator[None]:
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def _free_for_another_app(path: Path) -> bool:
    import fcntl

    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    finally:
        os.close(fd)
    return True


class TestOpenElsewhere:
    def test_a_conversation_nobody_has_open(self, presence, app_data):
        assert presence.open_elsewhere("conv-1") is False
        # The probe never creates the marker.
        assert not app_data.exists()

    def test_a_conversation_another_app_has_open(self, presence, app_data):
        with _other_app_holds(_lock_file(app_data, "conv-1")):
            assert presence.open_elsewhere("conv-1") is True
            assert presence.open_elsewhere("conv-2") is False

        assert presence.open_elsewhere("conv-1") is False

    def test_a_marker_left_behind_by_a_closed_app_is_not_open(self, presence, app_data):
        lock = _lock_file(app_data, "conv-1")
        lock.parent.mkdir(parents=True)
        lock.touch()

        assert presence.open_elsewhere("conv-1") is False


class TestAcquire:
    def test_a_hold_marks_the_conversation_open_for_other_apps_only(
        self, presence, app_data, holds
    ):
        hold = presence.acquire("conv-1")
        assert hold is not None
        holds.append(hold)

        lock = _lock_file(app_data, "conv-1")
        assert hold.session_id == "conv-1"
        assert _free_for_another_app(lock) is False
        # Its own hold is not "another app".
        assert presence.open_elsewhere("conv-1") is False
        assert stat.S_IMODE(lock.stat().st_mode) == 0o600

    def test_release_frees_it_and_a_second_release_does_nothing(self, presence, app_data, holds):
        hold = presence.acquire("conv-1")
        assert hold is not None
        holds.append(hold)

        hold.release()
        hold.release()

        assert _free_for_another_app(_lock_file(app_data, "conv-1")) is True
        again = presence.acquire("conv-1")
        assert again is not None
        holds.append(again)

    def test_a_conversation_another_app_has_open_cannot_be_held(self, presence, app_data):
        with _other_app_holds(_lock_file(app_data, "conv-1")):
            assert presence.acquire("conv-1") is None
            assert presence.open_elsewhere("conv-1") is True

    def test_each_app_data_folder_has_its_own_markers(self, tmp_path, holds):
        first = ConversationPresence(tmp_path / "one")
        second = ConversationPresence(tmp_path / "two")

        hold = first.acquire("conv-1")
        assert hold is not None
        holds.append(hold)

        assert second.open_elsewhere("conv-1") is False
        other = second.acquire("conv-1")
        assert other is not None
        holds.append(other)


class TestUnsafeInput:
    @pytest.mark.parametrize(
        "session_id",
        ["", ".", "..", "../outside", "a/b", "a\\b", "a\x00b", "x" * 201],
        ids=["empty", "dot", "dot-dot", "parent-path", "slash", "backslash", "nul", "too-long"],
    )
    def test_an_id_that_is_not_a_plain_name_is_never_a_path(self, presence, app_data, session_id):
        assert presence.open_elsewhere(session_id) is False
        assert presence.acquire(session_id) is None
        assert not app_data.exists()

    def test_a_symlinked_marker_is_never_followed(self, presence, app_data, tmp_path):
        target = tmp_path / "victim"
        target.write_text("keep me\n", encoding="utf-8")
        lock = _lock_file(app_data, "conv-1")
        lock.parent.mkdir(parents=True)
        lock.symlink_to(target)

        with _other_app_holds(target):
            assert presence.open_elsewhere("conv-1") is False
            assert presence.acquire("conv-1") is None

        assert _free_for_another_app(target) is True
        assert target.read_text(encoding="utf-8") == "keep me\n"
