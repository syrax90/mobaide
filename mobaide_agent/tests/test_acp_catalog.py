"""Stored chat sessions: the ``ChatCatalog`` lister (against the fake ACP
agent and tiny scripted ones, including an Antigravity-like agent that lists
every folder at once), the shared ``wire`` helpers, and the direct REST
surface in test mode (``GET /api/chat/{kind}/sessions``, chat create and
resume, ``capabilities.chat``)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from mobaide.acp import agents as agents_mod
from mobaide.acp import catalog as catalog_mod
from mobaide.acp.agents import ChatAgentUnavailableError, ChatLaunchPlan, fake_agent_argv
from mobaide.acp.catalog import ChatCatalog, ChatCatalogError
from mobaide.acp.wire import chat_request_error, list_chat_sessions, match_chat_sessions_route
from mobaide.agent.app import create_app
from mobaide.agent.install_paths import state_prefix
from mobaide.agent.sessions.models import ChatStoredSessionsResponse
from mobaide.core.models import SessionKind
from tests.conftest import TEST_PASSWORD, TEST_USERNAME

pytestmark = pytest.mark.timeout(60)

# Resolved from this file, never the CWD.
_CHAT_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "protocol_fixtures"
    / "dto"
    / "agent"
    / "ChatStoredSessionsResponse.json"
)

# Answers ``initialize`` and ``session/list`` only. MINI_AGENT_MODE=nolist
# drops the list capability; =badcursor rejects every list call.
MINI_AGENT = r"""
import json, os, sys
mode = os.environ.get("MINI_AGENT_MODE", "")
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg or "method" not in msg:
        continue
    if msg["method"] == "initialize":
        caps = {"loadSession": True}
        if mode != "nolist":
            caps["sessionCapabilities"] = {"list": {}}
        reply = {"result": {"protocolVersion": 1, "agentCapabilities": caps}}
    elif mode == "badcursor":
        reply = {"error": {"code": -32602, "message": "Invalid cursor"}}
    else:
        first = not msg["params"].get("cursor")
        reply = {"result": {
            "sessions": [
                {"sessionId": "s1", "cwd": "/p", "title": "  Many\n  words ", "updatedAt": "2026-11-07T10:00:00Z"},
                {"sessionId": "", "cwd": "/p"},
                {"sessionId": "s2"},
                "junk",
            ],
            "nextCursor": "page-2" if first else "",
        }}
    reply.update({"jsonrpc": "2.0", "id": msg["id"]})
    sys.stdout.write(json.dumps(reply) + "\n")
    sys.stdout.flush()
"""


class StubSpec:
    kind = "claude"
    display_name = "Stub Agent"

    def __init__(
        self,
        argv: list[str],
        *,
        env: dict[str, str] | None = None,
        unavailable: bool = False,
        supports_resume: bool = True,
    ) -> None:
        self.argv = argv
        self.env = env or {}
        self.unavailable = unavailable
        self.supports_resume = supports_resume
        self.plans = 0

    def available(self) -> bool:
        return not self.unavailable

    async def catalog_plan(self) -> ChatLaunchPlan:
        self.plans += 1
        if self.unavailable:
            raise ChatAgentUnavailableError("claude", "Install Node.js")
        return ChatLaunchPlan(argv=self.argv, env={**os.environ, **self.env}, cwd=os.getcwd())


def _mini(mode: str = "") -> StubSpec:
    return StubSpec([sys.executable, "-c", MINI_AGENT], env={"MINI_AGENT_MODE": mode})


# Like ``agy acp``: ``session/list`` answers the rows in AGENT_ROWS (JSON)
# whatever ``cwd`` it is given, in the order given. AGENT_RESULT (JSON), when
# set, is the whole answer instead.
ALL_FOLDERS_AGENT = r"""
import json, os, sys
rows = json.loads(os.environ["AGENT_ROWS"])
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg or "method" not in msg:
        continue
    if msg["method"] == "initialize":
        caps = {"loadSession": True, "sessionCapabilities": {"list": {}}}
        reply = {"result": {"protocolVersion": 1, "agentCapabilities": caps}}
    elif "AGENT_RESULT" in os.environ:
        reply = {"result": json.loads(os.environ["AGENT_RESULT"])}
    else:
        reply = {"result": {"sessions": rows}}
    reply.update({"jsonrpc": "2.0", "id": msg["id"]})
    sys.stdout.write(json.dumps(reply) + "\n")
    sys.stdout.flush()
"""


# A paging agent: ``session/list`` answers AGENT_PAGES[cursor] (JSON, the
# first page under the empty cursor).
PAGED_AGENT = r"""
import json, os, sys
pages = json.loads(os.environ["AGENT_PAGES"])
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg or "method" not in msg:
        continue
    if msg["method"] == "initialize":
        caps = {"loadSession": True, "sessionCapabilities": {"list": {}}}
        reply = {"result": {"protocolVersion": 1, "agentCapabilities": caps}}
    else:
        reply = {"result": pages[(msg.get("params") or {}).get("cursor") or ""]}
    reply.update({"jsonrpc": "2.0", "id": msg["id"]})
    sys.stdout.write(json.dumps(reply) + "\n")
    sys.stdout.flush()
"""


class FakePresence:
    """The conversations another app on the PC has open."""

    def __init__(self, *open_ids: str) -> None:
        self.open_ids = set(open_ids)

    def open_elsewhere(self, session_id: str) -> bool:
        return session_id in self.open_ids


class AllFoldersSpec(StubSpec):
    kind = "antigravity"
    display_name = "Antigravity"
    lists_all_folders = True

    def __init__(self, rows: list[object], presence: FakePresence | None = None) -> None:
        super().__init__(
            [sys.executable, "-c", ALL_FOLDERS_AGENT], env={"AGENT_ROWS": json.dumps(rows)}
        )
        self.presence = presence


@pytest.fixture
async def make_catalog():
    catalogs: list[ChatCatalog] = []

    def make(spec: StubSpec, kind: str = "claude") -> ChatCatalog:
        catalog = ChatCatalog({kind: spec})
        catalogs.append(catalog)
        return catalog

    yield make
    for catalog in catalogs:
        await catalog.close()


class TestChatCatalog:
    async def test_lists_the_stored_sessions_of_a_folder(self, make_catalog, tmp_path):
        catalog = make_catalog(StubSpec(fake_agent_argv()))

        page = await catalog.list_sessions("claude", cwd=str(tmp_path), cursor=None)

        assert [row["session_id"] for row in page["sessions"]] == ["external-1111", "external-2222"]
        first = page["sessions"][0]
        assert first["cwd"] == str(tmp_path)
        assert first["title"] == "Refactor the login flow"
        assert isinstance(first["updated_at"], str)
        assert first["open_session_id"] is None
        assert page["next_cursor"] is None

    async def test_paging_reuses_one_agent_process(self, make_catalog):
        spec = _mini()
        catalog = make_catalog(spec)

        first = await catalog.list_sessions("claude", cwd=None, cursor=None)
        second = await catalog.list_sessions("claude", cwd=None, cursor=first["next_cursor"])

        assert spec.plans == 1
        assert first["next_cursor"] == "page-2"
        assert second["next_cursor"] is None

    async def test_close_ends_the_agent_and_the_next_call_starts_one(self, make_catalog):
        spec = _mini()
        catalog = make_catalog(spec)
        await catalog.list_sessions("claude", cwd=None, cursor=None)

        await catalog.close()
        await catalog.list_sessions("claude", cwd=None, cursor=None)

        assert spec.plans == 2

    async def test_page_rows_are_normalized(self, make_catalog):
        catalog = make_catalog(_mini())

        page = await catalog.list_sessions("claude", cwd=None, cursor=None)

        assert page["sessions"] == [
            {
                "session_id": "s1",
                "cwd": "/p",
                "title": "Many words",
                "updated_at": "2026-11-07T10:00:00Z",
                "open_session_id": None,
                "open_elsewhere": False,
            }
        ]

    @pytest.mark.parametrize(
        ("spec", "kind", "cwd", "status", "detail"),
        [
            (StubSpec(["unused"]), "codex", None, 404, "Chat sessions are not available for codex"),
            (StubSpec(["unused"], supports_resume=False), "claude", None, 404, "Chat sessions are not available for claude"),
            (StubSpec(["unused"]), "claude", "relative/dir", 400, "cwd must be an absolute path"),
            (StubSpec(["unused"], unavailable=True), "claude", None, 503, "Install Node.js"),
            (_mini("nolist"), "claude", None, 409, "Stub Agent cannot list its sessions"),
            (_mini("badcursor"), "claude", None, 400, "Invalid cursor"),
        ],
        ids=["unknown-kind", "no-resume", "relative-cwd", "unavailable", "cannot-list", "bad-cursor"],
    )
    async def test_errors(self, make_catalog, spec, kind, cwd, status, detail):
        catalog = make_catalog(spec)

        with pytest.raises(ChatCatalogError) as excinfo:
            await catalog.list_sessions(kind, cwd=cwd, cursor=None)

        assert (excinfo.value.status, excinfo.value.detail) == (status, detail)

    async def test_agent_that_cannot_list_is_still_closed_when_idle(self, make_catalog, monkeypatch):
        monkeypatch.setattr(catalog_mod, "IDLE_SECONDS", 0.0)
        spec = _mini("nolist")
        catalog = make_catalog(spec)
        with pytest.raises(ChatCatalogError):
            await catalog.list_sessions("claude", cwd=None, cursor=None)

        # The zero-delay idle timer and the drop it starts run on the next passes.
        for _ in range(5):
            await asyncio.sleep(0)
        with pytest.raises(ChatCatalogError):
            await catalog.list_sessions("claude", cwd=None, cursor=None)

        assert spec.plans == 2

    async def test_a_long_first_use_setup_answers_before_the_relay_gives_up(
        self, make_catalog, monkeypatch
    ):
        monkeypatch.setattr(catalog_mod, "SETUP_WAIT", 0.05)
        installed = asyncio.Event()
        spec = _mini()
        plan = spec.catalog_plan

        async def slow_install() -> ChatLaunchPlan:
            await installed.wait()
            return await plan()

        spec.catalog_plan = slow_install  # type: ignore[method-assign]
        catalog = make_catalog(spec)

        with pytest.raises(ChatCatalogError) as excinfo:
            await catalog.list_sessions("claude", cwd=None, cursor=None)
        assert (excinfo.value.status, excinfo.value.detail) == (
            503,
            "Setting up Stub Agent chat for first use. Try again in a minute.",
        )

        # The setup went on; the retry joins it instead of starting another.
        retry = asyncio.ensure_future(catalog.list_sessions("claude", cwd=None, cursor=None))
        await asyncio.sleep(0)
        installed.set()
        page = await retry

        assert page["next_cursor"] == "page-2"
        assert spec.plans == 1

    async def test_agent_that_cannot_start(self, make_catalog, tmp_path):
        catalog = make_catalog(StubSpec([str(tmp_path / "no-such-agent")]))

        with pytest.raises(ChatCatalogError) as excinfo:
            await catalog.list_sessions("claude", cwd=None, cursor=None)

        assert excinfo.value.status == 502
        assert excinfo.value.detail.startswith("Could not start Stub Agent")


class TestAllFoldersLister:
    """An agent whose ``session/list`` ignores ``cwd`` (Antigravity): the
    catalog keeps the folder's rows, newest first, and marks the ones another
    app on the PC has open."""

    @pytest.fixture
    def folders(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        link = tmp_path / "link"
        if sys.platform != "win32":
            link.symlink_to(project, target_is_directory=True)
        rows = [
            {"sessionId": "old", "cwd": str(project), "title": "Old", "updatedAt": "2026-11-07T10:00:00Z"},
            {"sessionId": "other", "cwd": "/elsewhere/other-project", "updatedAt": "2026-11-09T10:00:00Z"},
            {"sessionId": "undated", "cwd": str(project)},
            {"sessionId": "new", "cwd": str(link), "title": "New", "updatedAt": "2026-11-09T08:00:00Z"},
            {"sessionId": "no-folder", "updatedAt": "2026-11-09T11:00:00Z"},
        ]
        return project, link, rows

    @pytest.mark.skipif(sys.platform == "win32", reason="symlinks")
    @pytest.mark.parametrize("asked", ["folder", "symlink"])
    async def test_the_rows_of_the_folder_newest_first(self, make_catalog, folders, asked):
        project, link, rows = folders
        catalog = make_catalog(AllFoldersSpec(rows, FakePresence("old")), kind="antigravity")

        page = await catalog.list_sessions(
            "antigravity", cwd=str(project if asked == "folder" else link), cursor=None
        )

        assert [(r["session_id"], r["open_elsewhere"]) for r in page["sessions"]] == [
            ("new", False),
            ("old", True),
            ("undated", False),
        ]
        assert all(r["open_session_id"] is None for r in page["sessions"])
        assert page["next_cursor"] is None


    @pytest.mark.parametrize("cwd", ["folder", None], ids=["one-folder", "all-folders"])
    async def test_the_agent_s_own_work_folders_are_never_listed(self, tmp_path, cwd):
        # A voice cleanup run leaves a stored session in its work folder.
        work = tmp_path / "voice-cleanup"
        work.mkdir()
        rows = [
            {"sessionId": "cleanup-1", "cwd": str(work), "title": "Voice-cleanup", "updatedAt": "2026-11-12T10:00:00Z"},
            {"sessionId": "mine-1", "cwd": str(tmp_path), "title": "Mine", "updatedAt": "2026-11-12T09:00:00Z"},
        ]
        catalog = ChatCatalog({"antigravity": AllFoldersSpec(rows)}, hidden_folders=[work])
        try:
            page = await catalog.list_sessions(
                "antigravity", cwd=str(work) if cwd else None, cursor=None
            )
        finally:
            await catalog.close()

        assert [r["session_id"] for r in page["sessions"]] == ([] if cwd else ["mine-1"])

    async def test_hidden_rows_do_not_take_the_places_of_a_cut_page(self, tmp_path):
        work = tmp_path / "voice-cleanup"
        rows = [
            {"sessionId": f"cleanup-{i}", "cwd": str(work), "updatedAt": f"2026-11-12T10:{i // 60:02}:{i % 60:02}Z"}
            for i in range(catalog_mod.PAGE_MAX + 5)
        ] + [{"sessionId": "mine-1", "cwd": str(tmp_path), "updatedAt": "2026-11-01T00:00:00Z"}]
        catalog = ChatCatalog({"antigravity": AllFoldersSpec(rows)}, hidden_folders=[work])
        try:
            page = await catalog.list_sessions("antigravity", cwd=None, cursor=None)
        finally:
            await catalog.close()

        assert [r["session_id"] for r in page["sessions"]] == ["mine-1"]

    async def test_a_page_of_hidden_rows_alone_reads_on_to_the_next_page(self, tmp_path):
        # Clients drop the cursor of an empty page, so the user's sessions
        # behind a page of cleanup runs would be out of reach.
        work = tmp_path / "voice-cleanup"
        pages = {
            "": {"sessions": [{"sessionId": "cleanup-1", "cwd": str(work)}], "nextCursor": "p2"},
            "p2": {"sessions": [{"sessionId": "cleanup-2", "cwd": str(work)}], "nextCursor": "p3"},
            "p3": {"sessions": [{"sessionId": "mine-1", "cwd": str(tmp_path)}], "nextCursor": "p4"},
        }
        spec = StubSpec([sys.executable, "-c", PAGED_AGENT], env={"AGENT_PAGES": json.dumps(pages)})
        catalog = ChatCatalog({"claude": spec}, hidden_folders=[work])
        try:
            page = await catalog.list_sessions("claude", cwd=None, cursor=None)
        finally:
            await catalog.close()

        assert [r["session_id"] for r in page["sessions"]] == ["mine-1"]
        assert page["next_cursor"] == "p4"

    async def test_hidden_rows_do_not_cut_the_page_of_an_agent_that_filters_by_folder(self, tmp_path):
        # Copilot answers the whole list at once, with one stored session
        # per voice cleanup run in front of the user's own.
        work = tmp_path / "voice-cleanup"
        rows = [
            {"sessionId": f"cleanup-{i}", "cwd": str(work)} for i in range(catalog_mod.PAGE_MAX + 5)
        ] + [{"sessionId": "broken", "cwd": ["not", "a", "path"]}, {"sessionId": "mine-1", "cwd": str(tmp_path)}]
        spec = StubSpec(
            [sys.executable, "-c", PAGED_AGENT],
            env={"AGENT_PAGES": json.dumps({"": {"sessions": rows}})},
        )
        catalog = ChatCatalog({"copilot": spec}, hidden_folders=[work])
        try:
            page = await catalog.list_sessions("copilot", cwd=None, cursor=None)
        finally:
            await catalog.close()

        assert [r["session_id"] for r in page["sessions"]] == ["mine-1"]

    async def test_a_page_keeps_the_newest_rows(self, make_catalog, tmp_path):
        rows = [
            {"sessionId": f"s{i:03}", "cwd": str(tmp_path), "updatedAt": f"2026-11-09T00:{i // 60:02}:{i % 60:02}Z"}
            for i in range(catalog_mod.PAGE_MAX + 20)
        ]
        catalog = make_catalog(AllFoldersSpec(rows), kind="antigravity")

        page = await catalog.list_sessions("antigravity", cwd=str(tmp_path), cursor=None)

        ids = [r["session_id"] for r in page["sessions"]]
        assert len(ids) == catalog_mod.PAGE_MAX
        assert ids[0] == f"s{catalog_mod.PAGE_MAX + 19:03}"
        assert ids[-1] == "s020"

    @pytest.mark.parametrize(
        "result",
        [None, [], "junk", {"sessions": "junk"}],
        ids=["null", "list", "string", "sessions-not-a-list"],
    )
    @pytest.mark.parametrize("cwd", ["folder", None], ids=["one-folder", "all-folders"])
    async def test_an_answer_that_is_not_a_page_lists_nothing(
        self, make_catalog, tmp_path, result, cwd
    ):
        spec = AllFoldersSpec([], FakePresence())
        spec.env["AGENT_RESULT"] = json.dumps(result)
        catalog = make_catalog(spec, kind="antigravity")

        page = await catalog.list_sessions(
            "antigravity", cwd=str(tmp_path) if cwd else None, cursor=None
        )

        assert page == {"sessions": [], "next_cursor": None}


class FakeManager:
    def __init__(self, chat_kinds=(), open_sessions=None) -> None:
        self.chat_agents = {kind: object() for kind in chat_kinds}
        self._open = open_sessions or {}

    def find_chat_session(self, kind, chat_resume_id):
        return self._open.get((kind, chat_resume_id))


class FakeCatalog:
    def __init__(self, page=None, error=None) -> None:
        self.page = page
        self.error = error
        self.calls: list[tuple[str, str | None, str | None]] = []

    async def list_sessions(self, kind, *, cwd, cursor):
        self.calls.append((kind, cwd, cursor))
        if self.error is not None:
            raise self.error
        return {
            "sessions": [dict(row) for row in self.page["sessions"]],
            "next_cursor": self.page["next_cursor"],
        }


class TestWire:
    @pytest.mark.parametrize(
        ("path", "kind"),
        [
            ("/api/chat/claude/sessions", "claude"),
            ("/api/chat/custom_ab12/sessions", "custom_ab12"),
            ("/api/chat/Claude/sessions", None),
            ("/api/chat//sessions", None),
            ("/api/chat/claude/sessions/x", None),
            ("/api/chat/../sessions", None),
        ],
    )
    def test_chat_sessions_route(self, path, kind):
        assert match_chat_sessions_route(path) == kind

    @pytest.mark.parametrize(
        ("ui", "kind", "resume", "error"),
        [
            (None, "claude", None, None),
            ("terminal", "claude", "abc", "chat_resume_id needs ui=chat"),
            ("chat", "codex", None, "The chat view is not available for codex sessions"),
            ("chat", SessionKind.CLAUDE, "abc", None),
        ],
        ids=["terminal", "resume-needs-chat", "no-chat-agent", "chat-ok"],
    )
    def test_chat_request_error(self, ui, kind, resume, error):
        assert chat_request_error(ui, kind, resume, FakeManager(["claude"])) == error

    async def test_rows_name_the_open_session(self):
        catalog = FakeCatalog(
            page={
                "sessions": [
                    {"session_id": "a", "cwd": "/p", "open_session_id": None},
                    {"session_id": "b", "cwd": "/p", "open_session_id": None},
                ],
                "next_cursor": "n",
            }
        )
        manager = FakeManager(open_sessions={("claude", "b"): SimpleNamespace(session_id="mob-7")})

        status, body = await list_chat_sessions(catalog, manager, "claude", cwd="", cursor="c1")

        assert status == 200
        assert [row["open_session_id"] for row in body["sessions"]] == [None, "mob-7"]
        assert body["next_cursor"] == "n"
        assert catalog.calls == [("claude", None, "c1")]

    async def test_a_row_open_in_mobaide_is_not_open_in_another_app(self):
        """The presence lock another app sees is Mobaide's own session."""
        catalog = FakeCatalog(
            page={
                "sessions": [
                    {"session_id": "a", "cwd": "/p", "open_session_id": None, "open_elsewhere": True},
                    {"session_id": "b", "cwd": "/p", "open_session_id": None, "open_elsewhere": True},
                ],
                "next_cursor": None,
            }
        )
        manager = FakeManager(
            open_sessions={("antigravity", "b"): SimpleNamespace(session_id="mob-7")}
        )

        status, body = await list_chat_sessions(catalog, manager, "antigravity", cwd="/p", cursor=None)

        assert status == 200
        assert [(r["open_session_id"], r["open_elsewhere"]) for r in body["sessions"]] == [
            (None, True),
            ("mob-7", False),
        ]

    def test_the_agent_model_sends_the_golden_wire(self):
        """The Python arm of the fixture the three clients decode: a page as
        the agent sends it now, every key present."""
        cases = json.loads(_CHAT_FIXTURE.read_text(encoding="utf-8"))["cases"]
        wire = next(c["wire"] for c in cases if c["name"] == "row_open_in_another_app")

        assert ChatStoredSessionsResponse.model_validate(wire).model_dump(mode="json") == wire

    async def test_catalog_errors_become_status_and_detail(self):
        catalog = FakeCatalog(error=ChatCatalogError(409, "cannot list"))

        assert await list_chat_sessions(catalog, FakeManager(), "claude", cwd=None, cursor=None) == (
            409,
            {"detail": "cannot list"},
        )


@pytest.fixture(autouse=True)
def _antigravity_data_in_tmp(tmp_path, monkeypatch):
    """The app's Antigravity spec probes presence markers under a temp
    folder, never the host's ``~/.gemini``."""
    monkeypatch.setattr(agents_mod, "default_app_data_dir", lambda: tmp_path / "agy-data")


@pytest.fixture
async def app(test_mode_agent_config):
    """Overrides the conftest ``app``: test mode, so chat sessions run the
    fake ACP agent."""
    application = create_app(config=test_mode_agent_config)
    application.state.user_store.add_user(TEST_USERNAME, TEST_PASSWORD)
    yield application
    for fanout in list(application.state.fanouts.values()):
        fanout.stop()
    await asyncio.wait_for(application.state.session_manager.shutdown(), timeout=15.0)
    await application.state.chat_catalog.close()


def _chat_body(cwd, **extra):
    return {
        "cli": "claude",
        "ui": "chat",
        "cwd": str(cwd),
        "claude_options": {"skip_permissions": False},
        **extra,
    }


class TestChatRest:
    async def test_version_lists_the_chat_capability(self, client, auth_headers):
        resp = await client.get("/api/version", headers=auth_headers)

        assert resp.status_code == 200
        # The fake agent never joins a shared Codex app-server: not live.
        assert resp.json()["capabilities"]["chat"] == {
            "claude": {"resume": True, "live": False},
            "codex": {"resume": True, "live": False},
            "antigravity": {"resume": True, "live": False},
            "opencode": {"resume": True, "live": False},
            "copilot": {"resume": True, "live": False},
        }

    async def test_create_chat_session(self, client, auth_headers, tmp_path):
        resp = await client.post("/api/sessions", headers=auth_headers, json=_chat_body(tmp_path))

        assert resp.status_code == 201
        body = resp.json()
        assert body["ui"] == "chat"
        assert body["state"] == "running"
        listed = await client.get("/api/sessions", headers=auth_headers)
        assert {s["session_id"]: s["ui"] for s in listed.json()}[body["session_id"]] == "chat"

    async def test_resume_marks_the_row_and_reuses_the_open_session(
        self, client, auth_headers, tmp_path
    ):
        url = f"/api/chat/claude/sessions?cwd={tmp_path}"
        before = await client.get(url, headers=auth_headers)
        assert before.status_code == 200
        assert [r["open_session_id"] for r in before.json()["sessions"]] == [None, None]

        body = _chat_body(tmp_path, chat_resume_id="external-1111")
        opened = await client.post("/api/sessions", headers=auth_headers, json=body)
        again = await client.post("/api/sessions", headers=auth_headers, json=body)
        after = await client.get(url, headers=auth_headers)

        session_id = opened.json()["session_id"]
        assert again.json()["session_id"] == session_id
        rows = {r["session_id"]: r["open_session_id"] for r in after.json()["sessions"]}
        assert rows == {"external-1111": session_id, "external-2222": None}

    @pytest.mark.parametrize(
        ("body", "detail"),
        [
            ({"ui": "chat"}, "The chat view is not available for shell sessions"),
            ({"cli": "claude", "chat_resume_id": "abc"}, "chat_resume_id needs ui=chat"),
        ],
        ids=["no-chat-agent", "resume-needs-chat"],
    )
    async def test_create_refusals(self, client, auth_headers, tmp_path, body, detail):
        resp = await client.post(
            "/api/sessions", headers=auth_headers, json={"cwd": str(tmp_path), **body}
        )

        assert resp.status_code == 400
        assert resp.json()["detail"] == detail

    @pytest.mark.parametrize(
        ("url", "status"),
        [
            ("/api/chat/shell/sessions", 404),
            ("/api/chat/claude/sessions?cwd=relative", 400),
        ],
    )
    async def test_list_refusals(self, client, auth_headers, url, status):
        resp = await client.get(url, headers=auth_headers)
        assert resp.status_code == status

    async def test_codex_chat_session_and_its_stored_sessions(
        self, client, auth_headers, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")  # passes the Codex auth guard on a clean host
        body = {
            "cli": "codex",
            "ui": "chat",
            "cwd": str(tmp_path),
            "codex_options": {"sandbox": "workspace-write", "approval": "on-request"},
        }

        created = await client.post("/api/sessions", headers=auth_headers, json=body)
        listed = await client.get(f"/api/chat/codex/sessions?cwd={tmp_path}", headers=auth_headers)

        assert created.status_code == 201
        assert created.json()["ui"] == "chat"
        assert listed.status_code == 200
        assert [r["title"] for r in listed.json()["sessions"]] == [
            "Add retries to the sync job",
            "Explain the cache layer",
        ]


    async def test_antigravity_chat_session_and_its_stored_sessions(
        self, client, auth_headers, tmp_path
    ):
        body = {
            "cli": "antigravity",
            "ui": "chat",
            "cwd": str(tmp_path),
            "antigravity_options": {"model": "Gemini 3.8 Flash (Low)", "sandbox": True},
        }

        created = await client.post("/api/sessions", headers=auth_headers, json=body)
        listed = await client.get(
            f"/api/chat/antigravity/sessions?cwd={tmp_path}", headers=auth_headers
        )

        assert created.status_code == 201, created.text
        assert created.json()["ui"] == "chat"
        assert listed.status_code == 200
        # The agent lists every folder; only this one's rows, newest first.
        assert [(r["title"], r["open_elsewhere"]) for r in listed.json()["sessions"]] == [
            ("Refactor the login flow", False),
            ("Fix flaky upload test", False),
        ]

    async def test_opencode_chat_session_and_its_stored_sessions(
        self, client, auth_headers, tmp_path
    ):
        body = {
            "cli": "opencode",
            "ui": "chat",
            "cwd": str(tmp_path),
            "opencode_options": {"auto_approve": True},
        }

        created = await client.post("/api/sessions", headers=auth_headers, json=body)
        listed = await client.get(
            f"/api/chat/opencode/sessions?cwd={tmp_path}", headers=auth_headers
        )

        assert created.status_code == 201, created.text
        assert (created.json()["kind"], created.json()["ui"]) == ("opencode", "chat")
        assert listed.status_code == 200
        assert [r["title"] for r in listed.json()["sessions"]] == [
            "Refactor the login flow",
            "Fix flaky upload test",
        ]

    async def test_copilot_chat_session_and_its_stored_sessions(
        self, client, auth_headers, tmp_path
    ):
        body = {
            "cli": "copilot",
            "ui": "chat",
            "cwd": str(tmp_path),
            "copilot_options": {"allow_all": True},
        }

        created = await client.post("/api/sessions", headers=auth_headers, json=body)
        listed = await client.get(
            f"/api/chat/copilot/sessions?cwd={tmp_path}", headers=auth_headers
        )
        version = await client.get("/api/version", headers=auth_headers)

        assert created.status_code == 201, created.text
        assert created.json()["ui"] == "chat"
        assert created.json()["kind"] == "copilot"
        assert listed.status_code == 200
        assert [(r["session_id"], r["cwd"]) for r in listed.json()["sessions"]] == [
            ("external-1111", str(tmp_path)),
            ("external-2222", str(tmp_path)),
        ]
        assert version.json()["capabilities"]["chat"]["copilot"] == {"resume": True, "live": False}


    async def test_list_needs_auth(self, client):
        resp = await client.get("/api/chat/claude/sessions")
        assert resp.status_code == 401

    async def test_the_voice_cleanup_work_folder_is_hidden_in_the_app(self, client, auth_headers):
        # Copilot keeps every cleanup run as a stored session of that folder;
        # the fake agent answers two rows for whatever folder is asked.
        work = state_prefix(test_mode=True) / "voice-cleanup"

        listed = await client.get(f"/api/chat/copilot/sessions?cwd={work}", headers=auth_headers)

        assert listed.status_code == 200
        assert listed.json()["sessions"] == []
