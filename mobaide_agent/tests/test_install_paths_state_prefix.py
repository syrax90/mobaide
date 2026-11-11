"""Tests for the writable-state-prefix resolution in ``install_paths``.

``state_prefix()`` is what keeps an unprivileged ``python -m mobaide`` from a
source checkout from crashing on its first store write: the OS-wide install
prefix (``C:\\Program Files\\MobaideAgent`` / ``/opt/mobaide``) is unwritable
to a non-elevated process, so when the prefix isn't writable it falls back to
``user_data_dir()``. An explicit ``MOBAIDE_AGENT_PREFIX`` is always honoured
verbatim. ``default_prefix()`` itself is intentionally left unchanged (the
updater and trust-key reader must keep pointing at the real install dir), so
its behaviour is not re-tested here.
"""
from __future__ import annotations

import sys

import pytest

from mobaide.agent.install_paths import (
    _is_writable_dir,
    default_prefix,
    state_prefix,
    user_data_dir,
)

_WRITABLE = "mobaide.agent.install_paths._is_writable_dir"
_USER_DATA_DIR = "mobaide.agent.install_paths.user_data_dir"


@pytest.fixture(autouse=True)
def _clear_layout_env(monkeypatch):
    """Stop host values for the dirs under test from leaking in."""
    for var in (
        "MOBAIDE_AGENT_PREFIX", "MOBAIDE_ENV", "LOCALAPPDATA", "XDG_DATA_HOME"
    ):
        monkeypatch.delenv(var, raising=False)


_ = _clear_layout_env  # silence pyright "not accessed" hint


class TestUserDataDir:
    def test_windows_uses_localappdata(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
        assert user_data_dir() == tmp_path / "Local" / "MobaideAgent"

    def test_posix_uses_xdg_data_home(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
        assert user_data_dir() == tmp_path / "share" / "mobaide"

    def test_posix_without_xdg_falls_back_under_home(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        result = user_data_dir()
        assert result.parts[-3:] == (".local", "share", "mobaide")


class TestIsWritableDir:
    def test_writable_dir_returns_true_and_leaves_no_probe(self, tmp_path):
        target = tmp_path / "state"
        assert _is_writable_dir(target) is True
        assert target.is_dir()
        # The probe file is removed on success.
        assert list(target.iterdir()) == []

    def test_unwritable_dir_returns_false_and_creates_nothing(self, tmp_path):
        # A path *under an existing regular file* can't be made into a
        # directory — ``mkdir`` raises ``OSError`` (NotADirectoryError),
        # the cross-platform stand-in for "this location rejects writes".
        a_file = tmp_path / "blocker"
        a_file.write_text("", encoding="utf-8")
        target = a_file / "subdir"
        assert _is_writable_dir(target) is False
        assert not target.exists()


class TestStatePrefix:
    def test_explicit_prefix_is_honoured_even_when_unwritable(
        self, monkeypatch, tmp_path
    ):
        """An operator-pinned prefix is never second-guessed by writability."""
        monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(tmp_path / "pinned"))
        monkeypatch.setattr(_WRITABLE, lambda *_: False)
        assert state_prefix() == tmp_path / "pinned"

    def test_writable_default_prefix_is_used_directly(self, monkeypatch):
        monkeypatch.setattr(_WRITABLE, lambda *_: True)
        assert state_prefix() == default_prefix()

    def test_unwritable_default_prefix_falls_back_to_user_data_dir(
        self, monkeypatch, tmp_path
    ):
        fallback = tmp_path / "userdata"
        monkeypatch.setattr(_WRITABLE, lambda *_: False)
        monkeypatch.setattr(_USER_DATA_DIR, lambda: fallback)
        assert state_prefix() == fallback
