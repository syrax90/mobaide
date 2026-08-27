"""``GET /api/sessions/{id}/git/status|diff`` through the FastAPI app and the
relay ``_dispatch_session_git`` dispatcher, asserting both transports answer
byte-identically for every status code the contract defines."""
from __future__ import annotations

import types

import pytest
from httpx import ASGITransport, AsyncClient

from mobaide.agent.app import create_app
from mobaide.agent.config import AgentConfig
from mobaide.core import git_exec
from mobaide.core.git_exec import CmdResult, reset_caches
from mobaide.relay.client import RelayClient

from .conftest import TEST_JWT_SECRET, TEST_PASSWORD, TEST_USERNAME
from .git_repo_support import (
    repo,  # noqa: F401 - pytest fixture
    requires_git,
    stub_git,
    write_file,
)

SESSION_ID = "sess-1"


@pytest.fixture(autouse=True)
def _fresh_caches():
    reset_caches()
    yield
    reset_caches()


@pytest.fixture
async def git_app(tmp_path):
    application = create_app(
        config=AgentConfig(
            jwt_secret_key=TEST_JWT_SECRET,
            user_store_path=str(tmp_path / "users.json"),
            projects_store_path=str(tmp_path / "projects.json"),
            max_sessions=4,
            log_level="DEBUG",
        )
    )
    application.state.user_store.add_user(TEST_USERNAME, TEST_PASSWORD)
    yield application
    await application.state.session_manager.shutdown()


@pytest.fixture
async def http(git_app):
    async with AsyncClient(
        transport=ASGITransport(app=git_app), base_url="http://test"
    ) as client:
        yield client


@pytest.fixture
async def headers(http) -> dict[str, str]:
    resp = await http.post(
        "/api/auth/token", json={"username": TEST_USERNAME, "password": TEST_PASSWORD}
    )
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
def relay(relay_config, mock_session_manager, mock_event_bus, mock_broadcaster,
          mock_app_state):
    return RelayClient(
        config=relay_config,
        session_manager=mock_session_manager,
        event_bus=mock_event_bus,
        broadcaster=mock_broadcaster,
        app_state=mock_app_state,
    )


@pytest.fixture
def session_at(git_app, mock_session_manager, monkeypatch):
    """Register one session whose ``to_info().cwd`` is the given directory on
    both the app's session manager and the relay's stub."""

    def install(cwd: str | None) -> None:
        session = types.SimpleNamespace(
            to_info=lambda: types.SimpleNamespace(cwd=cwd)
        )

        async def get_session(session_id: str):
            return session if session_id == SESSION_ID else None

        monkeypatch.setattr(git_app.state.session_manager, "get_session", get_session)
        mock_session_manager.get_session.side_effect = get_session

    return install


def _status_url(session_id: str = SESSION_ID) -> str:
    return f"/api/sessions/{session_id}/git/status"


def _diff_url(session_id: str = SESSION_ID) -> str:
    return f"/api/sessions/{session_id}/git/diff"


async def _assert_parity(http, headers, relay, url, query=None):
    resp = await http.get(url, headers=headers, params=query)
    dispatched = await relay._dispatch_session_git("GET", url, query)
    assert dispatched == {"status": resp.status_code, "body": resp.json()}
    return resp


class TestAuthAndRouting:
    async def test_status_requires_auth(self, http):
        resp = await http.get(_status_url())

        assert resp.status_code == 401

    async def test_unknown_session_is_404_on_both_transports(
        self, http, headers, relay, session_at, tmp_path
    ):
        session_at(str(tmp_path))

        resp = await _assert_parity(http, headers, relay, _status_url("nope"))

        assert resp.status_code == 404
        assert resp.json() == {"detail": "Session 'nope' not found"}

    async def test_relay_ignores_other_methods_and_routes(self, relay):
        assert await relay._dispatch_session_git("POST", _status_url(), None) is None
        assert await relay._dispatch_session_git("GET", "/api/sessions/x/git/log", None) is None

    async def test_version_advertises_git_capability(self, http, headers):
        resp = await http.get("/api/version", headers=headers)

        assert resp.status_code == 200
        assert resp.json()["capabilities"]["git"] == {"changes": True}


@requires_git
class TestStatusAndDiff:
    async def test_status_body_matches_on_both_transports(
        self, http, headers, relay, session_at, repo
    ):
        session_at(repo)
        write_file(repo, "new.txt", b"n\n")

        resp = await _assert_parity(http, headers, relay, _status_url())

        body = resp.json()
        assert resp.status_code == 200
        assert (body["session_id"], body["cwd"], body["unchanged"]) == (SESSION_ID, repo, False)
        assert [e["path"] for e in body["entries"]] == ["new.txt"]
        assert body["repo"]["head"]["branch"] == "main"
        assert body["counts"]["untracked"] == 1

    async def test_etag_query_short_circuits_on_both_transports(
        self, http, headers, relay, session_at, repo
    ):
        session_at(repo)
        first = await http.get(_status_url(), headers=headers)

        resp = await _assert_parity(
            http, headers, relay, _status_url(), {"etag": first.json()["etag"]}
        )

        assert resp.json()["unchanged"] is True
        assert resp.json()["etag"] == first.json()["etag"]
        assert resp.json()["repo"] is None

    async def test_diff_body_matches_on_both_transports(
        self, http, headers, relay, session_at, repo
    ):
        session_at(repo)
        write_file(repo, "keep.txt", b"one\nTWO\nthree\n")

        resp = await _assert_parity(
            http, headers, relay, _diff_url(),
            {"scope": "unstaged", "path": "keep.txt", "context": "0"},
        )

        body = resp.json()
        assert resp.status_code == 200
        assert (body["path"], body["scope"], body["kind"], body["change"]) == (
            "keep.txt", "unstaged", "text", "modified")
        assert [l["t"] for l in body["hunks"][0]["lines"]] == ["del", "add"]

    @pytest.mark.parametrize(
        ("query", "code"),
        [
            ({"scope": "all", "path": "a.txt"}, "bad_scope"),
            ({"path": "a.txt"}, "bad_scope"),
            ({"scope": "staged", "path": "../a.txt"}, "bad_path"),
            ({"scope": "staged"}, "bad_path"),
        ],
    )
    async def test_bad_scope_or_path_is_400(
        self, http, headers, relay, session_at, repo, query, code
    ):
        session_at(repo)

        resp = await _assert_parity(http, headers, relay, _diff_url(), query)

        assert resp.status_code == 400
        assert resp.json() == {"detail": {"code": code}}

    async def test_diff_in_non_repo_is_400_with_reason(
        self, http, headers, relay, session_at, tmp_path
    ):
        session_at(str(tmp_path))

        resp = await _assert_parity(
            http, headers, relay, _diff_url(), {"scope": "unstaged", "path": "a.txt"}
        )

        assert resp.status_code == 400
        assert resp.json() == {"detail": {"code": "not_a_repo"}}

    async def test_status_in_non_repo_is_200_with_reason(
        self, http, headers, relay, session_at, tmp_path
    ):
        session_at(str(tmp_path))

        resp = await _assert_parity(http, headers, relay, _status_url())

        assert resp.status_code == 200
        assert resp.json()["unavailable_reason"] == "not_a_repo"
        assert resp.json()["repo"] is None


class TestErrorMapping:
    async def test_missing_git_is_503(self, http, headers, relay, session_at, tmp_path, monkeypatch):
        session_at(str(tmp_path))
        monkeypatch.setattr(git_exec.shutil, "which", lambda _name: None)
        reset_caches()

        resp = await _assert_parity(http, headers, relay, _status_url())

        assert resp.status_code == 503
        assert resp.json() == {"detail": {"code": "git_unavailable"}}

    async def test_timeout_is_504(self, http, headers, relay, session_at, tmp_path, monkeypatch):
        session_at(str(tmp_path))
        stub_git(monkeypatch, CmdResult(rc=-9, stdout=b"", stderr=b"", timed_out=True))

        resp = await _assert_parity(http, headers, relay, _status_url())

        assert resp.status_code == 504
        assert resp.json() == {"detail": {"code": "git_timeout"}}

    async def test_unexpected_git_failure_is_500(
        self, http, headers, relay, session_at, tmp_path, monkeypatch
    ):
        session_at(str(tmp_path))
        stub_git(monkeypatch, CmdResult(rc=128, stdout=b"", stderr=b"fatal: index corrupt\n"))

        resp = await _assert_parity(
            http, headers, relay, _diff_url(), {"scope": "unstaged", "path": "a.txt"}
        )

        assert resp.status_code == 500
        assert resp.json() == {"detail": {"code": "git_error", "message": "fatal: index corrupt"}}
