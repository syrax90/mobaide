"""Auto Scan templates: the on-disk store
(``mobaide.agent.autoscan.templates.AutoScanTemplatesStore``), the save
request's name rules, and the ``/api/autoscan/templates`` routes over both
transports (the FastAPI router and the relay ``_dispatch_proxy``).

The store writes a real JSON file under ``tmp_path``; no clock is read in an
assertion.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from mobaide.agent.autoscan import templates as templates_module
from mobaide.agent.autoscan.models import (
    TEMPLATES_MAX,
    AutoScanTemplateSettings,
    AutoScanTemplatesResponse,
    SaveTemplateRequest,
)
from mobaide.agent.autoscan.templates import AutoScanTemplatesStore, TemplateLimitError
from mobaide.agent.sessions.models import CodexScanOptionsModel
from mobaide.agent.workspaces.models import GitHubScanFilterDraftModel
from mobaide.relay.client import RelayClient

_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "protocol_fixtures" / "dto" / "agent"

_SMALL_BUGS = AutoScanTemplateSettings(
    source="github",
    cli="codex",
    codex_options=CodexScanOptionsModel(full_auto=True, reasoning_effort="low"),
    # A template may keep a filter the wizard still has to finish.
    github_filter=GitHubScanFilterDraftModel(labels=[], issue_types=["Bug"]),
)
_COMPLEX = AutoScanTemplateSettings(source="github", cli="claude", initial_prompt="Plan first.")
_AT = "2026-11-19T10:00:00+00:00"
_posix_non_root = pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="file modes are POSIX only, and root reads any file",
)


def _store(tmp_path) -> AutoScanTemplatesStore:
    return AutoScanTemplatesStore(tmp_path / "autoscan_templates.json")


class TestStore:
    def test_saved_templates_are_listed_by_name_and_survive_a_restart(self, tmp_path):
        store = _store(tmp_path)

        store.save("small bugs", _SMALL_BUGS)
        store.save("Complex", _COMPLEX)

        reloaded = _store(tmp_path)
        assert [t.name for t in reloaded.list()] == ["Complex", "small bugs"]
        small = next(t for t in reloaded.list() if t.name == "small bugs")
        assert small.settings == _SMALL_BUGS
        assert not (tmp_path / "autoscan_templates.json.tmp").exists()

    def test_same_name_ignoring_case_and_spaces_replaces_and_keeps_the_id(self, tmp_path):
        store = _store(tmp_path)
        [first] = store.save("Small bugs", _SMALL_BUGS)

        [replaced] = store.save("  SMALL \t bugs ", _COMPLEX)

        assert replaced.id == first.id
        assert replaced.name == "  SMALL \t bugs "
        assert replaced.settings == _COMPLEX
        assert [t.id for t in _store(tmp_path).list()] == [first.id]

    def test_a_new_name_past_the_cap_is_refused_but_a_replace_is_not(self, tmp_path):
        store = _store(tmp_path)
        for i in range(TEMPLATES_MAX):
            store.save(f"t{i:02d}", _COMPLEX)
        on_disk = (tmp_path / "autoscan_templates.json").read_text()

        with pytest.raises(TemplateLimitError):
            store.save("one more", _COMPLEX)

        assert (tmp_path / "autoscan_templates.json").read_text() == on_disk
        templates = store.save("T00", _SMALL_BUGS)
        assert len(templates) == TEMPLATES_MAX

    def test_a_save_past_the_size_cap_is_refused_even_when_it_replaces(
        self, tmp_path, monkeypatch,
    ):
        # Every answer carries the whole list in one relay frame.
        monkeypatch.setattr(templates_module, "TEMPLATES_BYTES_MAX", 4096)
        store = _store(tmp_path)
        store.save("Complex", _COMPLEX)
        path = tmp_path / "autoscan_templates.json"
        on_disk = path.read_text(encoding="utf-8")
        too_large = AutoScanTemplateSettings(cli="claude", initial_prompt="x" * 4096)

        for name in ("COMPLEX", "another"):
            with pytest.raises(TemplateLimitError):
                store.save(name, too_large)

        assert path.read_text(encoding="utf-8") == on_disk
        assert [(t.name, t.settings) for t in store.list()] == [("Complex", _COMPLEX)]

    def test_delete_removes_one_template_and_refuses_an_unknown_id(self, tmp_path):
        store = _store(tmp_path)
        store.save("a", _COMPLEX)
        templates = store.save("b", _SMALL_BUGS)
        b_id = next(t.id for t in templates if t.name == "b")

        left = store.delete(b_id)

        assert [t.name for t in left] == ["a"]
        assert [t.name for t in _store(tmp_path).list()] == ["a"]
        with pytest.raises(KeyError):
            store.delete(b_id)

    def test_an_entry_the_models_reject_does_not_hide_the_others(self, tmp_path):
        good = {"id": "g1", "name": "Good", "updated_at": "2026-11-19T10:00:00+00:00",
                "settings": {"cli": "pi"}}
        unknown_agent = {"id": "x1", "name": "Bad", "updated_at": "2026-11-19T10:00:00+00:00",
                         "settings": {"cli": "aider"}}
        (tmp_path / "autoscan_templates.json").write_text(
            json.dumps([unknown_agent, "garbage", good]), encoding="utf-8",
        )

        assert [t.id for t in _store(tmp_path).list()] == ["g1"]

    def test_a_rejected_entry_is_written_back_unchanged_and_not_counted_to_the_cap(
        self, tmp_path,
    ):
        # An older agent must not erase a template a newer agent wrote.
        newer = {"id": "x1", "name": "From a newer agent", "updated_at": _AT,
                 "settings": {"cli": "aider"}}
        kept = [{"id": f"g{i:02d}", "name": f"t{i:02d}", "updated_at": _AT,
                 "settings": {"cli": "pi"}} for i in range(TEMPLATES_MAX - 1)]
        path = tmp_path / "autoscan_templates.json"
        path.write_text(json.dumps([newer, *kept]), encoding="utf-8")

        templates = _store(tmp_path).save("one more", _COMPLEX)

        assert len(templates) == TEMPLATES_MAX
        assert "x1" not in {t.id for t in templates}
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert len(on_disk) == TEMPLATES_MAX + 1
        assert on_disk[-1] == newer

    @pytest.mark.parametrize(
        ("content", "unreadable", "backed_up"),
        [
            pytest.param(b"{not json", False, True, id="not-json"),
            pytest.param(b'{"templates": []}', False, True, id="not-a-list"),
            # A hand edit saved in a legacy code page is not JSON either.
            pytest.param('[{"name": "Café"}]'.encode("cp1252"), False, True, id="not-utf-8"),
            pytest.param(b"[]", True, True, id="no-read-permission", marks=_posix_non_root),
            pytest.param(b"", False, False, id="empty-file"),
        ],
    )
    def test_a_file_that_cannot_be_read_as_a_list_is_moved_to_bak_on_the_first_write(
        self, tmp_path, content, unreadable, backed_up,
    ):
        path = tmp_path / "autoscan_templates.json"
        backup = tmp_path / "autoscan_templates.json.bak"
        path.write_bytes(content)
        if unreadable:
            path.chmod(0)
        store = _store(tmp_path)

        assert store.list() == []
        # Reading alone leaves the file where it is.
        assert not backup.exists()

        store.save("a", _COMPLEX)

        assert [t.name for t in _store(tmp_path).list()] == ["a"]
        if backed_up:
            backup.chmod(0o600)
            assert backup.read_bytes() == content
        else:
            assert not backup.exists()


@pytest.mark.parametrize(
    ("name", "cleaned"),
    [
        pytest.param("  Small \n  bugs ", "Small bugs", id="white-space-runs-become-one-space"),
        pytest.param("  " + "x" * 60 + "  ", "x" * 60, id="60-characters-after-cleaning"),
        pytest.param(" \t ", None, id="blank-is-refused"),
        pytest.param("x" * 61, None, id="61-characters-are-refused"),
    ],
)
def test_save_request_cleans_or_refuses_the_name(name, cleaned):
    if cleaned is None:
        with pytest.raises(ValidationError):
            SaveTemplateRequest(name=name, settings=AutoScanTemplateSettings())
        return

    assert SaveTemplateRequest(name=name, settings=AutoScanTemplateSettings()).name == cleaned


def test_templates_fixture_round_trips_through_the_agent_model():
    doc = json.loads((_FIXTURES / "AutoScanTemplatesResponse.json").read_text(encoding="utf-8"))
    for case in doc["cases"]:
        wire = case["wire"]

        assert AutoScanTemplatesResponse.model_validate(wire).model_dump(mode="json") == wire


@pytest.fixture
def relay(app, relay_config, mock_session_manager, mock_event_bus, mock_broadcaster,
          mock_app_state):
    mock_app_state.autoscan_templates = app.state.autoscan_templates
    return RelayClient(
        config=relay_config,
        session_manager=mock_session_manager,
        event_bus=mock_event_bus,
        broadcaster=mock_broadcaster,
        app_state=mock_app_state,
    )


_PATH = "/api/autoscan/templates"
_BODY = {
    "name": "Small bugs",
    "settings": {
        "source": "github", "cli": "codex",
        "codex_options": {"full_auto": True, "reasoning_effort": "low"},
        "github_filter": {"labels": [], "issue_types": ["Bug"]},
    },
}


class TestTransports:
    async def test_save_list_and_delete_answer_identically(
        self, client, auth_headers, relay, agent_config,
    ):
        assert (await client.get(_PATH, headers=auth_headers)).json() == {"templates": []}

        saved = await client.post(_PATH, headers=auth_headers, json=_BODY)
        relayed_list = await relay._dispatch_proxy("GET", _PATH, None, None, AsyncMock())

        assert saved.status_code == 200
        [template] = saved.json()["templates"]
        assert template["name"] == "Small bugs"
        assert template["settings"]["codex_options"]["full_auto"] is True
        assert template["settings"]["github_filter"]["issue_types"] == ["Bug"]
        assert relayed_list == {"status": 200, "body": saved.json()}
        # The file sits next to the workspaces store.
        assert (Path(agent_config.projects_store_path).parent / "autoscan_templates.json").exists()

        replaced = await relay._dispatch_proxy(
            "POST", _PATH, {**_BODY, "name": "small   BUGS"}, None, AsyncMock(),
        )
        assert replaced["status"] == 200
        [again] = replaced["body"]["templates"]
        assert (again["id"], again["name"]) == (template["id"], "small BUGS")

        path = f"{_PATH}/{template['id']}"
        deleted = await relay._dispatch_proxy("DELETE", path, None, None, AsyncMock())
        assert deleted == {"status": 200, "body": {"templates": []}}
        missing = await client.delete(path, headers=auth_headers)
        relayed_missing = await relay._dispatch_proxy("DELETE", path, None, None, AsyncMock())
        assert missing.status_code == 404
        # The id is quoted once; ``str(KeyError)`` would quote it again.
        assert missing.json() == {"detail": f"Template '{template['id']}' not found"}
        assert relayed_missing == {"status": 404, "body": missing.json()}

    @pytest.mark.parametrize("cap", ["count", "size"])
    async def test_a_save_past_a_cap_is_409_on_both_transports(
        self, app, client, auth_headers, relay, monkeypatch, cap,
    ):
        store = app.state.autoscan_templates
        if cap == "count":
            for i in range(TEMPLATES_MAX):
                store.save(f"t{i:02d}", AutoScanTemplateSettings())
            body = {"name": "one more", "settings": {}}
        else:
            monkeypatch.setattr(templates_module, "TEMPLATES_BYTES_MAX", 4096)
            body = {"name": "one more", "settings": {"initial_prompt": "x" * 4096}}
        before = store.list()

        resp = await client.post(_PATH, headers=auth_headers, json=body)
        relayed = await relay._dispatch_proxy("POST", _PATH, body, None, AsyncMock())

        assert resp.status_code == 409
        assert relayed == {"status": 409, "body": resp.json()}
        assert store.list() == before

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param({"name": "  ", "settings": {}}, id="blank-name"),
            pytest.param({"name": "a", "settings": {"codex_options": {"sandbox": "everything"}}},
                         id="invalid-agent-option"),
        ],
    )
    async def test_an_invalid_save_is_422_on_both_transports_and_saves_nothing(
        self, app, client, auth_headers, relay, body,
    ):
        resp = await client.post(_PATH, headers=auth_headers, json=body)
        relayed = await relay._dispatch_proxy("POST", _PATH, body, None, AsyncMock())

        assert resp.status_code == relayed["status"] == 422
        assert app.state.autoscan_templates.list() == []
