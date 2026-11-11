"""Tests for per-session Claude Code hook settings files.

``write_session_hooks`` writes a ``--settings`` file whose ``http`` hooks
POST to the agent's ``/internal/hook`` with the ``session_id``, ``token`` and
gate-facing ``hook`` kind baked literally into each URL — the mechanism that
makes multi-concurrent-session correlation independent of cwd and hook env-var
interpolation, and that classifies each hook without parsing its body.

Files live under a per-port temp dir so two agents on one host don't sweep
each other's files. These tests redirect that temp dir into ``tmp_path`` via
``monkeypatch`` so they never touch the real system temp directory.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from mobaide.core import hook_settings

# Every ``(event, matcher)`` registration the settings file must carry, and the
# ``hook`` kind its URL must be baked with.
EXPECTED_REGISTRATIONS = {
    ("Notification", "permission_prompt"): "permission",
    ("Notification", "idle_prompt"): "idle",
    ("Stop", "*"): "stop",
    ("UserPromptSubmit", "*"): "prompt_submit",
    ("SessionEnd", "*"): "end",
}


@pytest.fixture(autouse=True)
def _isolated_tempdir(monkeypatch, tmp_path):
    """Point ``hook_settings``' temp-dir lookup at an isolated ``tmp_path``."""
    monkeypatch.setattr(hook_settings.tempfile, "gettempdir", lambda: str(tmp_path))
    return tmp_path


def _load(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _registrations(doc: dict) -> dict[tuple[str, str], dict]:
    """Flatten ``hooks`` into ``(event, matcher) -> the single hook dict``."""
    flat: dict[tuple[str, str], dict] = {}
    for event, entries in doc["hooks"].items():
        for entry in entries:
            assert len(entry["hooks"]) == 1
            flat[(event, entry["matcher"])] = entry["hooks"][0]
    return flat


def _query(hook: dict) -> dict[str, list[str]]:
    return parse_qs(urlsplit(hook["url"]).query)


class TestWriteSessionHooks:

    def test_registered_events_stay_inside_the_frozen_allow_list(self):
        # Claude Code strips an unrecognised ``hooks.<Event>`` key with a
        # validation *warning*, and its startup path raises a blocking
        # invalid-settings dialog for any validation record whenever the process
        # is interactive — which a PTY session always is. Registering an event
        # newer than the oldest CLI we support would therefore hang every
        # session at a modal, and the startup-stall watchdog would not catch it
        # because the dialog renders far more than ``STARTUP_STALL_MIN_BYTES``.
        # Widening this set is a compatibility decision, not a refactor: read
        # ``REGISTERED_HOOK_EVENTS`` before touching either side.
        assert set(hook_settings.REGISTERED_HOOK_EVENTS) == {
            "Notification", "Stop", "UserPromptSubmit", "SessionEnd",
        }
        path = hook_settings.write_session_hooks("sid-1", port=9001, token="tok")
        assert set(_load(path)["hooks"]) <= set(hook_settings.REGISTERED_HOOK_EVENTS)

    def test_each_registration_bakes_its_own_hook_kind(self):
        # Sub-classification rides in the URL so it never depends on the body.
        # On a current CLI the two ``Notification`` matchers are mutually
        # exclusive — ``notification_type`` is required in the hook input schema
        # and supplies the match query, and matching is exact membership — so
        # exactly one registration fires per notification. The URL param covers
        # the case that is otherwise undecidable: upstream skips matcher
        # filtering entirely when an event carries no match query, and a CLI
        # that omits ``notification_type`` would then fire both registrations
        # with a body that carries no discriminator to tell them apart.
        path = hook_settings.write_session_hooks("sid-1", port=9001, token="tok")
        kinds = {
            key: _query(hook)["hook"]
            for key, hook in _registrations(_load(path)).items()
        }
        assert kinds == {key: [k] for key, k in EXPECTED_REGISTRATIONS.items()}


    def test_every_url_bakes_session_id_and_token(self):
        # Each registration now carries its own hook dict, so the correlation
        # params have to be re-checked on all of them, not just the first.
        path = hook_settings.write_session_hooks("sid-abc", port=9100, token="tok-xyz")
        for hook in _registrations(_load(path)).values():
            parts = urlsplit(hook["url"])
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


    def test_distinct_ports_use_distinct_dirs(self, tmp_path):
        a = hook_settings.write_session_hooks("sid", port=9001, token="tok")
        b = hook_settings.write_session_hooks("sid", port=9002, token="tok")
        assert a.parent != b.parent
        assert a.parent == tmp_path / "mobaide_hooks_9001"
        assert b.parent == tmp_path / "mobaide_hooks_9002"


class TestWriteSessionHooksExtra:
    def test_extra_keys_merged_at_top_level_beside_hooks(self):
        path = hook_settings.write_session_hooks(
            "sid-1", port=9001, token="tok",
            extra={"fastMode": True, "ultracode": True},
        )
        doc = _load(path)
        assert doc["fastMode"] is True
        assert doc["ultracode"] is True
        # The merge augments the document — it must not displace ``hooks``.
        assert set(_registrations(doc)) == set(EXPECTED_REGISTRATIONS)


class TestRemoveSessionHooks:
    def test_removes_existing_file(self):
        path = hook_settings.write_session_hooks("sid-1", port=9001, token="tok")
        assert path.exists()
        hook_settings.remove_session_hooks("sid-1", port=9001)
        assert not path.exists()


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


    def test_does_not_touch_other_ports(self):
        keep = hook_settings.write_session_hooks("x", port=9002, token="tok")
        hook_settings.write_session_hooks("x", port=9001, token="tok")
        hook_settings.sweep_hooks_dir(port=9001)
        assert keep.exists()
