"""``GET /api/sessions/{id}/git/status|diff|commit/context`` and
``POST .../git/stage|unstage|discard|commit`` through the FastAPI app and the
relay ``_dispatch_session_git`` dispatcher, asserting both transports answer
byte-identically for every status code the contract defines."""
from __future__ import annotations

import os
import shutil
import types
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from mobaide.agent.app import create_app
from mobaide.agent.config import AgentConfig
from mobaide.agent.git import changes_service
from mobaide.core import git_exec
from mobaide.core.git_exec import CmdResult, reset_caches
from mobaide.relay.client import RelayClient

from .conftest import TEST_JWT_SECRET, TEST_PASSWORD, TEST_USERNAME
from .git_repo_support import (
    init_repo,
    norm_path,
    repo,  # noqa: F401 - pytest fixture
    requires_git,
    run_git_sync,
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

    def install(cwd: str | None, project_name: str | None = None) -> None:
        info = types.SimpleNamespace(cwd=cwd)
        if project_name is not None:
            info.project_name = project_name
        session = types.SimpleNamespace(to_info=lambda: info)

        async def get_session(session_id: str):
            return session if session_id == SESSION_ID else None

        monkeypatch.setattr(git_app.state.session_manager, "get_session", get_session)
        mock_session_manager.get_session.side_effect = get_session

    return install


def _status_url(session_id: str = SESSION_ID) -> str:
    return f"/api/sessions/{session_id}/git/status"


def _diff_url(session_id: str = SESSION_ID) -> str:
    return f"/api/sessions/{session_id}/git/diff"


def _git_url(route: str, session_id: str = SESSION_ID) -> str:
    return f"/api/sessions/{session_id}/git/{route}"


async def _assert_parity(http, headers, relay, url, query=None):
    resp = await http.get(url, headers=headers, params=query)
    dispatched = await relay._dispatch_session_git("GET", url, query)
    assert dispatched == {"status": resp.status_code, "body": resp.json()}
    return resp


async def _assert_post_parity(http, headers, relay, url, body):
    """The direct call runs first, the relay's repeats it on the same repo:
    every mutation but ``commit`` is idempotent, so both answers match."""
    resp = await http.post(url, headers=headers, json=body)
    dispatched = await relay._dispatch_session_git("POST", url, None, body)
    assert dispatched == {"status": resp.status_code, "body": resp.json()}
    return resp


class _FakeEventBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    def publish(self, event_type: str, payload: dict) -> None:
        self.published.append((event_type, payload))


async def _groups(http, headers) -> list[tuple[str, str]]:
    resp = await http.get(_status_url(), headers=headers)
    return [(e["group"], e["path"]) for e in resp.json()["entries"]]


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
        assert resp.json()["capabilities"]["git"] == {
            "changes": True,
            "stage": True,
            "discard": True,
            "commit": True,
        }


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

    @requires_git
    async def test_cwd_vanishing_mid_diff_is_400(
        self, http, headers, relay, session_at, repo, monkeypatch
    ):
        session_at(repo)
        real_run_git = changes_service.run_git

        async def rmtree_after_resolve(args, **kwargs):
            if args[:2] != ["rev-parse", "--is-bare-repository"]:
                shutil.rmtree(repo, ignore_errors=True)
            return await real_run_git(args, **kwargs)

        monkeypatch.setattr(changes_service, "run_git", rmtree_after_resolve)

        resp = await _assert_parity(
            http, headers, relay, _diff_url(), {"scope": "unstaged", "path": "keep.txt"}
        )

        assert resp.status_code == 400
        assert resp.json() == {"detail": {"code": "cwd_missing"}}

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


class TestMutationRouting:
    async def test_mutations_require_auth(self, http):
        resp = await http.post(_git_url("stage"), json={"paths": ["a.txt"]})

        assert resp.status_code == 401

    async def test_unknown_session_is_404_on_both_transports(
        self, http, headers, relay, session_at, tmp_path
    ):
        session_at(str(tmp_path))

        resp = await _assert_post_parity(
            http, headers, relay, _git_url("stage", "nope"), {"paths": ["a.txt"]}
        )

        assert resp.status_code == 404
        assert resp.json() == {"detail": "Session 'nope' not found"}

    @pytest.mark.parametrize(
        ("method", "route"),
        [
            ("GET", "stage"),
            ("GET", "unstage"),
            ("GET", "discard"),
            ("GET", "commit"),
            ("POST", "commit/context"),
            ("POST", "commit/extra"),
        ],
    )
    async def test_relay_ignores_the_wrong_method_per_route(self, relay, method, route):
        assert await relay._dispatch_session_git(method, _git_url(route), None, {}) is None

    @pytest.mark.parametrize(
        ("route", "body", "loc"),
        [
            ("stage", {}, ["paths"]),
            ("stage", {"paths": []}, ["paths"]),
            ("unstage", {"paths": ["a"] * 5001}, ["paths"]),
            ("discard", {"paths": ["a.txt"]}, ["scope"]),
            ("discard", {"paths": ["a.txt"], "scope": "staged"}, ["scope"]),
            ("commit", {"message": ""}, ["message"]),
            ("commit", {"message": "x" * (64 * 1024 + 1)}, ["message"]),
            ("commit", {"message": "x", "amend": "maybe"}, ["amend"]),
        ],
    )
    async def test_malformed_body_is_422_on_both_transports(
        self, http, headers, relay, session_at, tmp_path, route, body, loc
    ):
        # Pydantic's records, so the two ``loc`` prefixes differ: FastAPI
        # names the body, the relay validates the model directly (and hands
        # the tuple back as is; ``json.dumps`` lists it on the wire).
        session_at(str(tmp_path))

        resp = await http.post(_git_url(route), headers=headers, json=body)
        dispatched = await relay._dispatch_session_git("POST", _git_url(route), None, body)

        assert resp.status_code == 422
        assert dispatched["status"] == 422
        assert [e["loc"] for e in resp.json()["detail"]] == [["body", *loc]]
        assert [list(e["loc"]) for e in dispatched["body"]["detail"]] == [loc]
        assert [e["type"] for e in dispatched["body"]["detail"]] == [
            e["type"] for e in resp.json()["detail"]]

    async def test_proxy_dispatch_hands_the_body_through(
        self, http, headers, relay, session_at, repo, mock_app_state
    ):
        session_at(repo)
        write_file(repo, "a.txt", b"a\n")

        result = await relay._dispatch_proxy(
            "POST", _git_url("stage"), {"paths": ["a.txt"]}, None, MagicMock()
        )

        assert result == {"status": 200, "body": {"ok": True}}
        assert await _groups(http, headers) == [("staged", "a.txt")]


@requires_git
class TestMutationRoutes:
    async def test_stage_matches_and_publishes_the_session_project(
        self, http, headers, relay, session_at, repo, mock_app_state
    ):
        session_at(repo, project_name="proj")
        bus = _FakeEventBus()
        mock_app_state.event_bus = bus
        write_file(repo, "a.txt", b"a\n")
        write_file(repo, "b.txt", b"b\n")

        resp = await _assert_post_parity(
            http, headers, relay, _git_url("stage"), {"paths": ["a.txt"]}
        )

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert await _groups(http, headers) == [("staged", "a.txt"), ("untracked", "b.txt")]
        assert bus.published == [(
            "git.changed",
            {"project_name": "proj", "toplevel": norm_path(repo),
             "common_dir": norm_path(repo) + "/.git"},
        )]

    async def test_session_without_a_project_publishes_an_empty_name(
        self, http, headers, relay, session_at, repo, mock_app_state
    ):
        session_at(repo)
        bus = _FakeEventBus()
        mock_app_state.event_bus = bus
        write_file(repo, "a.txt", b"a\n")

        await relay._dispatch_session_git("POST", _git_url("stage"), None, {"paths": ["a.txt"]})

        assert [p["project_name"] for _, p in bus.published] == [""]

    async def test_unstage_matches_on_both_transports(
        self, http, headers, relay, session_at, repo
    ):
        session_at(repo)
        write_file(repo, "keep.txt", b"one\nTWO\nthree\n")
        run_git_sync(repo, "add", "keep.txt")

        resp = await _assert_post_parity(
            http, headers, relay, _git_url("unstage"), {"paths": ["keep.txt"]}
        )

        assert resp.json() == {"ok": True}
        assert await _groups(http, headers) == [("unstaged", "keep.txt")]

    async def test_discard_matches_on_both_transports(
        self, http, headers, relay, session_at, repo
    ):
        session_at(repo)
        write_file(repo, "keep.txt", b"one\nTWO\nthree\n")
        write_file(repo, "junk.txt", b"j\n")

        tracked = await _assert_post_parity(
            http, headers, relay, _git_url("discard"),
            {"paths": ["keep.txt"], "scope": "unstaged"},
        )
        untracked = await _assert_post_parity(
            http, headers, relay, _git_url("discard"),
            {"paths": ["junk.txt"], "scope": "untracked"},
        )

        assert (tracked.json(), untracked.json()) == ({"ok": True}, {"ok": True})
        assert await _groups(http, headers) == []
        assert not os.path.exists(os.path.join(repo, "junk.txt"))

    async def test_commit_context_matches_on_both_transports(
        self, http, headers, relay, session_at, repo
    ):
        session_at(repo)

        resp = await _assert_parity(http, headers, relay, _git_url("commit/context"))

        body = resp.json()
        assert resp.status_code == 200
        assert body["author"] == {"name": "Test", "email": "test@example.com"}
        assert (body["head"]["subject"], body["head"]["body"]) == ("initial", "")
        assert body["head"]["oid"].startswith(body["head"]["short_oid"])

    async def test_commit_context_on_an_unborn_branch_carries_nulls(
        self, http, headers, relay, session_at, tmp_path
    ):
        session_at(init_repo(str(tmp_path / "unborn"), initial_commit=False))

        resp = await _assert_parity(http, headers, relay, _git_url("commit/context"))

        assert resp.json()["head"] is None
        assert resp.json()["author"]["name"] == "Test"

    async def test_commit_answers_the_new_head_on_each_transport(
        self, http, headers, relay, session_at, repo
    ):
        session_at(repo)
        write_file(repo, "a.txt", b"a\n")
        run_git_sync(repo, "add", "a.txt")

        direct = await http.post(
            _git_url("commit"), headers=headers, json={"message": "Add a"}
        )
        write_file(repo, "b.txt", b"b\n")
        run_git_sync(repo, "add", "b.txt")
        relayed = await relay._dispatch_session_git(
            "POST", _git_url("commit"), None, {"message": "Add b\n\nSecond."}
        )

        assert direct.status_code == 200
        assert relayed["status"] == 200
        assert set(direct.json()) == set(relayed["body"]) == {"oid", "short_oid", "subject"}
        assert (direct.json()["subject"], relayed["body"]["subject"]) == ("Add a", "Add b")
        context = await http.get(_git_url("commit/context"), headers=headers)
        assert context.json()["head"]["oid"] == relayed["body"]["oid"]
        assert context.json()["head"]["body"] == "Second."
        assert await _groups(http, headers) == []

    @pytest.mark.parametrize(
        ("route", "body", "status", "detail"),
        [
            ("stage", {"paths": ["../x"]}, 400, {"code": "bad_path"}),
            ("commit", {"message": "  \n"}, 400, {"code": "empty_message"}),
            ("commit", {"message": "x"}, 409,
             {"code": "nothing_to_commit", "message": "no changes added to commit"}),
            ("stage", {"paths": ["gone.txt"]}, 409, None),
        ],
    )
    async def test_refusals_and_bad_requests_match_on_both_transports(
        self, http, headers, relay, session_at, repo, route, body, status, detail
    ):
        session_at(repo)

        resp = await _assert_post_parity(http, headers, relay, _git_url(route), body)

        assert resp.status_code == status
        if detail is None:
            assert resp.json()["detail"]["code"] == "pathspec_mismatch"
            assert "gone.txt" in resp.json()["detail"]["message"]
        else:
            assert resp.json() == {"detail": detail}

    async def test_mutation_outside_a_repo_is_409_not_a_200_condition(
        self, http, headers, relay, session_at, tmp_path
    ):
        session_at(str(tmp_path))

        resp = await _assert_post_parity(
            http, headers, relay, _git_url("stage"), {"paths": ["a.txt"]}
        )

        assert resp.status_code == 409
        assert resp.json() == {"detail": {"code": "not_a_repo", "message": ""}}

    async def test_vanished_cwd_is_400_on_a_mutation(
        self, http, headers, relay, session_at, tmp_path
    ):
        session_at(str(tmp_path / "gone"))

        resp = await _assert_post_parity(
            http, headers, relay, _git_url("stage"), {"paths": ["a.txt"]}
        )

        assert resp.status_code == 400
        assert resp.json() == {"detail": {"code": "cwd_missing"}}
