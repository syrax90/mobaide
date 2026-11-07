"""Stored chat sessions: the ``ChatCatalog`` lister (against the fake ACP
agent and a tiny scripted one), the shared ``wire`` helpers, and the
direct REST surface in test mode (``GET /api/chat/{kind}/sessions``, chat
create and resume, ``capabilities.chat``)."""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

from mobaide.acp import catalog as catalog_mod
from mobaide.acp.agents import ChatAgentUnavailableError, ChatLaunchPlan, fake_agent_argv
from mobaide.acp.catalog import ChatCatalog, ChatCatalogError
from mobaide.acp.wire import chat_request_error, list_chat_sessions, match_chat_sessions_route
from mobaide.agent.app import create_app
from mobaide.core.models import SessionKind
from tests.conftest import TEST_PASSWORD, TEST_USERNAME

pytestmark = pytest.mark.timeout(60)

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


@pytest.fixture
async def make_catalog():
    catalogs: list[ChatCatalog] = []

    def make(spec: StubSpec) -> ChatCatalog:
        catalog = ChatCatalog({"claude": spec})
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

    async def test_agent_that_cannot_start(self, make_catalog, tmp_path):
        catalog = make_catalog(StubSpec([str(tmp_path / "no-such-agent")]))

        with pytest.raises(ChatCatalogError) as excinfo:
            await catalog.list_sessions("claude", cwd=None, cursor=None)

        assert excinfo.value.status == 502
        assert excinfo.value.detail.startswith("Could not start Stub Agent")


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

    async def test_catalog_errors_become_status_and_detail(self):
        catalog = FakeCatalog(error=ChatCatalogError(409, "cannot list"))

        assert await list_chat_sessions(catalog, FakeManager(), "claude", cwd=None, cursor=None) == (
            409,
            {"detail": "cannot list"},
        )

    async def test_no_catalog_is_404(self):
        status, body = await list_chat_sessions(None, FakeManager(), "claude", cwd=None, cursor=None)
        assert (status, body) == (404, {"detail": "Chat sessions are not available on this agent"})


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
        assert resp.json()["capabilities"]["chat"] == {"claude": {"resume": True}}

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
            ({"cli": "codex", "ui": "chat"}, "The chat view is not available for codex sessions"),
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
            ("/api/chat/codex/sessions", 404),
            ("/api/chat/claude/sessions?cwd=relative", 400),
        ],
    )
    async def test_list_refusals(self, client, auth_headers, url, status):
        resp = await client.get(url, headers=auth_headers)
        assert resp.status_code == status

    async def test_list_needs_auth(self, client):
        resp = await client.get("/api/chat/claude/sessions")
        assert resp.status_code == 401
