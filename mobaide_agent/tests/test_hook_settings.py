"""Tests for per-session Claude Code hook settings files.

``write_session_hooks`` writes a ``--settings`` file whose ``http`` hooks
POST to the agent's ``/internal/hook`` with the ``session_id`` and ``token``
baked literally into each URL — the mechanism that makes multi-concurrent-
session correlation independent of cwd and hook env-var interpolation.

Files live under a per-port temp dir so two agents on one host don't sweep
each other's files. These tests redirect that temp dir into ``tmp_path`` via
``monkeypatch`` so they never touch the real system temp directory.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from mobaide.core import hook_settings


@pytest.fixture(autouse=True)
def _isolated_tempdir(monkeypatch, tmp_path):
    """Point ``hook_settings``' temp-dir lookup at an isolated ``tmp_path``."""
    monkeypatch.setattr(hook_settings.tempfile, "gettempdir", lambda: str(tmp_path))
    return tmp_path


def _load(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestWriteSessionHooks:
    def test_creates_file_under_per_port_dir(self, tmp_path):
        path = hook_settings.write_session_hooks("sid-1", port=9001, token="tok")
        assert path.exists()
        assert path.parent == tmp_path / "mobaide_hooks_9001"
        assert path.name == "sid-1.json"

    def test_registers_three_hook_events(self):
        path = hook_settings.write_session_hooks("sid-1", port=9001, token="tok")
        doc = _load(path)
        assert set(doc["hooks"]) == {"Notification", "Stop", "SessionEnd"}

    def test_notification_matcher_is_permission_prompt(self):
        path = hook_settings.write_session_hooks("sid-1", port=9001, token="tok")
        doc = _load(path)
        notification = doc["hooks"]["Notification"][0]
        assert notification["matcher"] == "permission_prompt"

    def test_hooks_are_http_type_with_timeout(self):
        path = hook_settings.write_session_hooks("sid-1", port=9001, token="tok")
        doc = _load(path)
        for event in ("Notification", "Stop", "SessionEnd"):
            hook = doc["hooks"][event][0]["hooks"][0]
            assert hook["type"] == "http"
            assert isinstance(hook["timeout"], int)
            assert hook["timeout"] > 0

    def test_url_bakes_session_id_and_token(self):
        path = hook_settings.write_session_hooks("sid-abc", port=9100, token="tok-xyz")
        doc = _load(path)
        url = doc["hooks"]["Stop"][0]["hooks"][0]["url"]
        parts = urlsplit(url)
        assert parts.hostname == "127.0.0.1"
        assert parts.port == 9100
        assert parts.path == "/internal/hook"
        query = parse_qs(parts.query)
        assert query["session_id"] == ["sid-abc"]
        assert query["token"] == ["tok-xyz"]

    def test_special_characters_are_percent_encoded_in_url(self):
        # A session id or token containing reserved chars must survive as a
        # single, correctly-decoded query value — not split the query string.
        path = hook_settings.write_session_hooks(
            "a&b=c d", port=9001, token="t/o k&z",
        )
        doc = _load(path)
        url = doc["hooks"]["Stop"][0]["hooks"][0]["url"]
        query = parse_qs(urlsplit(url).query)
        assert query["session_id"] == ["a&b=c d"]
        assert query["token"] == ["t/o k&z"]

    def test_distinct_sessions_get_distinct_files(self):
        a = hook_settings.write_session_hooks("sid-a", port=9001, token="tok")
        b = hook_settings.write_session_hooks("sid-b", port=9001, token="tok")
        assert a != b
        assert a.exists() and b.exists()

    def test_distinct_ports_use_distinct_dirs(self, tmp_path):
        a = hook_settings.write_session_hooks("sid", port=9001, token="tok")
        b = hook_settings.write_session_hooks("sid", port=9002, token="tok")
        assert a.parent != b.parent
        assert a.parent == tmp_path / "mobaide_hooks_9001"
        assert b.parent == tmp_path / "mobaide_hooks_9002"


class TestRemoveSessionHooks:
    def test_removes_existing_file(self):
        path = hook_settings.write_session_hooks("sid-1", port=9001, token="tok")
        assert path.exists()
        hook_settings.remove_session_hooks("sid-1", port=9001)
        assert not path.exists()

    def test_missing_file_is_silent_noop(self):
        # Late SessionEnd after a sweep is normal — must not raise.
        hook_settings.remove_session_hooks("never-written", port=9001)

    def test_only_targets_the_named_session(self):
        keep = hook_settings.write_session_hooks("keep", port=9001, token="tok")
        drop = hook_settings.write_session_hooks("drop", port=9001, token="tok")
        hook_settings.remove_session_hooks("drop", port=9001)
        assert keep.exists()
        assert not drop.exists()


class TestSweepHooksDir:
    def test_removes_all_session_files_for_port(self):
        a = hook_settings.write_session_hooks("a", port=9001, token="tok")
        b = hook_settings.write_session_hooks("b", port=9001, token="tok")
        hook_settings.sweep_hooks_dir(port=9001)
        assert not a.exists()
        assert not b.exists()

    def test_missing_dir_is_silent_noop(self):
        # First boot: the dir was never created. Must not raise.
        hook_settings.sweep_hooks_dir(port=9999)

    def test_does_not_touch_other_ports(self):
        keep = hook_settings.write_session_hooks("x", port=9002, token="tok")
        hook_settings.write_session_hooks("x", port=9001, token="tok")
        hook_settings.sweep_hooks_dir(port=9001)
        assert keep.exists()
