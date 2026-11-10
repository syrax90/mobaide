"""``GET /api/sessions/{id}/git/status|diff|commit/context``,
``POST .../git/stage|unstage|discard|commit``, the fetch/pull/push jobs,
``POST .../git/rebase[/continue|skip|abort]`` and ``GET .../git/conflict`` plus
``POST .../git/conflict/resolve|take|restore`` through the FastAPI app and the
relay ``_dispatch_session_git`` dispatcher, asserting both transports answer
byte-identically for every status code the contract defines."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import types
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from mobaide.agent.app import create_app
from mobaide.agent.config import AgentConfig
from mobaide.agent.git import changes_service
from mobaide.agent.git.changes_jobs import job_registry
from mobaide.core import git_exec
from mobaide.core.git_exec import CmdResult, reset_caches
from mobaide.relay.client import RelayClient

from .conftest import TEST_JWT_SECRET, TEST_PASSWORD, TEST_USERNAME
from .git_repo_support import (
    GIT_BIN,
    clone_repo,
    commit_all,
    init_repo,
    make_conflict_branches,
    norm_path,
    posix_only,
    repo,  # noqa: F401 - pytest fixture
    requires_git,
    run_git_sync,
    seed_remote,
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
            "fetch": True,
            "pull": True,
            "push": True,
            "branches": True,
            "rebase": True,
            "resolve": True,
            "delete_remote": True,
            "conflicts": True,
            "worktree_base": True,
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
            ("GET", "rebase"),
            ("GET", "rebase/continue"),
            ("GET", "rebase/skip"),
            ("GET", "rebase/abort"),
            ("POST", "rebase/"),
            ("POST", "rebase/restart"),
            ("POST", "rebase/abort/extra"),
            ("POST", "conflict"),
            ("GET", "conflict/resolve"),
            ("GET", "conflict/take"),
            ("GET", "conflict/restore"),
            ("POST", "conflict/"),
            ("POST", "conflict/undo"),
            ("POST", "conflict/take/extra"),
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
            ("rebase", {}, ["onto"]),
            ("rebase", {"onto": ""}, ["onto"]),
            ("conflict/resolve", {"path": "a.txt", "content": "x"}, ["token"]),
            ("conflict/resolve", {"path": "a.txt", "token": "t"}, ["content"]),
            ("conflict/resolve", {"path": "", "token": "t", "content": "x"}, ["path"]),
            ("conflict/resolve", {"path": "a.txt", "token": "", "content": "x"}, ["token"]),
            ("conflict/take", {"path": "a.txt"}, ["side"]),
            ("conflict/take", {"path": "a.txt", "side": "mine"}, ["side"]),
            ("conflict/restore", {}, ["path"]),
            ("conflict/restore", {"path": "a.txt", "style": "zdiff3"}, ["style"]),
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


@pytest.fixture(autouse=True)
async def _fresh_job_registry():
    job_registry.reset()
    yield
    await job_registry.shutdown()
    job_registry.reset()


@pytest.fixture
def local(tmp_path) -> str:
    """A clone of a seeded local bare remote, ``main`` tracking ``origin/main``."""
    return clone_repo(seed_remote(str(tmp_path)), str(tmp_path / "local"))


def _job_url(job_id: str, session_id: str = SESSION_ID) -> str:
    return _git_url(f"jobs/{job_id}", session_id)


async def _await_job(job_id: str) -> None:
    task = job_registry._jobs[job_id].task
    if task is not None:
        await task


def _without_id(body: dict) -> dict:
    return {k: v for k, v in body.items() if k != "job_id"}


def _install_sleeping_pre_push(repo_dir: str) -> None:
    run_git_sync(repo_dir, "config", "core.hooksPath", ".git/hooks")
    hook = write_file(repo_dir, ".git/hooks/pre-push", b"#!/bin/sh\nsleep 30\n")
    os.chmod(hook, 0o755)


class TestJobRouting:
    async def test_job_routes_require_auth(self, http):
        resp = await http.post(_git_url("fetch"))

        assert resp.status_code == 401

    @pytest.mark.parametrize(
        ("method", "route"),
        [
            ("GET", "fetch"),
            ("GET", "pull"),
            ("GET", "push"),
            ("POST", "jobs/abc"),
            ("GET", "jobs/abc/cancel"),
            ("GET", "jobs/"),
            ("POST", "jobs/abc/extra"),
        ],
    )
    async def test_relay_ignores_the_wrong_method_per_job_route(self, relay, method, route):
        assert await relay._dispatch_session_git(method, _git_url(route), None, {}) is None

    @pytest.mark.parametrize(
        ("method", "route"),
        [
            ("POST", "fetch"),
            ("POST", "pull"),
            ("POST", "push"),
            ("GET", "jobs/abc"),
            ("POST", "jobs/abc/cancel"),
        ],
    )
    async def test_unknown_session_is_404_on_both_transports(
        self, http, headers, relay, session_at, tmp_path, method, route
    ):
        session_at(str(tmp_path))
        url = _git_url(route, "nope")

        resp = await http.request(method, url, headers=headers)
        dispatched = await relay._dispatch_session_git(method, url, None, None)

        assert resp.status_code == 404
        assert resp.json() == {"detail": "Session 'nope' not found"}
        assert dispatched == {"status": 404, "body": resp.json()}

    @pytest.mark.parametrize(
        ("route", "body", "loc"),
        [
            ("pull", {"mode": "fast"}, ["mode"]),
            ("push", {"force": "maybe"}, ["force"]),
        ],
    )
    async def test_malformed_job_body_is_422_on_both_transports(
        self, http, headers, relay, session_at, tmp_path, route, body, loc
    ):
        session_at(str(tmp_path))

        resp = await http.post(_git_url(route), headers=headers, json=body)
        dispatched = await relay._dispatch_session_git("POST", _git_url(route), None, body)

        assert resp.status_code == 422
        assert dispatched["status"] == 422
        assert [e["loc"] for e in resp.json()["detail"]] == [["body", *loc]]
        assert [list(e["loc"]) for e in dispatched["body"]["detail"]] == [loc]

    async def test_unknown_job_is_404_with_a_coded_detail_on_both_transports(
        self, http, headers, relay, session_at, tmp_path
    ):
        session_at(str(tmp_path))

        got = await _assert_parity(http, headers, relay, _job_url("deadbeef"))
        cancelled = await _assert_post_parity(
            http, headers, relay, _job_url("deadbeef") + "/cancel", None
        )

        assert got.status_code == 404
        assert got.json() == {"detail": {"code": "job_not_found"}}
        assert cancelled.status_code == 404
        assert cancelled.json() == {"detail": {"code": "job_not_found"}}

    async def test_job_start_outside_a_repo_is_409_on_both_transports(
        self, http, headers, relay, session_at, tmp_path
    ):
        session_at(str(tmp_path))

        resp = await _assert_post_parity(http, headers, relay, _git_url("fetch"), None)

        assert resp.status_code == 409
        assert resp.json() == {"detail": {"code": "not_a_repo", "message": ""}}


@requires_git
class TestJobRoutes:
    async def test_fetch_answers_the_first_snapshot_then_the_finished_one_on_each_transport(
        self, http, headers, relay, session_at, local
    ):
        session_at(local)

        direct = await http.post(_git_url("fetch"), headers=headers)
        await _await_job(direct.json()["job_id"])
        relayed = await relay._dispatch_session_git("POST", _git_url("fetch"), None, None)
        await _await_job(relayed["body"]["job_id"])

        assert direct.status_code == 200
        assert relayed["status"] == 200
        assert re.fullmatch(r"[0-9a-f]{32}", direct.json()["job_id"])
        assert direct.json()["job_id"] != relayed["body"]["job_id"]
        assert _without_id(direct.json()) == {
            "session_id": SESSION_ID,
            "op": "fetch",
            "status": "running",
            "phase": None,
            "percent": None,
            "line": None,
            "ok": None,
            "error_code": None,
            "message": None,
        }
        assert _without_id(relayed["body"]) == _without_id(direct.json())
        finished = await _assert_parity(
            http, headers, relay, _job_url(relayed["body"]["job_id"])
        )
        assert finished.status_code == 200
        assert (finished.json()["status"], finished.json()["ok"]) == ("done", True)
        assert finished.json()["error_code"] is None

    async def test_pull_and_push_accept_an_absent_or_empty_body(
        self, http, headers, relay, session_at, local
    ):
        session_at(local)

        pulled = await http.post(_git_url("pull"), headers=headers)
        await _await_job(pulled.json()["job_id"])
        relayed_pull = await relay._dispatch_session_git("POST", _git_url("pull"), None, None)
        await _await_job(relayed_pull["body"]["job_id"])
        pushed = await http.post(_git_url("push"), headers=headers, json={})
        await _await_job(pushed.json()["job_id"])
        relayed_push = await relay._dispatch_session_git("POST", _git_url("push"), None, {})
        await _await_job(relayed_push["body"]["job_id"])

        assert (pulled.status_code, pulled.json()["op"]) == (200, "pull")
        assert (relayed_pull["status"], relayed_pull["body"]["op"]) == (200, "pull")
        assert (pushed.status_code, pushed.json()["op"]) == (200, "push")
        assert (relayed_push["status"], relayed_push["body"]["op"]) == (200, "push")
        for job_id in (
            pulled.json()["job_id"], relayed_pull["body"]["job_id"],
            pushed.json()["job_id"], relayed_push["body"]["job_id"],
        ):
            assert job_registry.get(job_id).ok is True

    async def test_cancel_of_a_finished_job_is_idempotent_on_both_transports(
        self, http, headers, relay, session_at, local
    ):
        session_at(local)
        started = await http.post(_git_url("fetch"), headers=headers)
        job_id = started.json()["job_id"]
        await _await_job(job_id)

        cancelled = await _assert_post_parity(
            http, headers, relay, _job_url(job_id) + "/cancel", None
        )

        assert cancelled.status_code == 200
        assert (cancelled.json()["status"], cancelled.json()["ok"]) == ("done", True)
        current = await http.get(_job_url(job_id), headers=headers)
        assert cancelled.json() == current.json()

    @posix_only
    async def test_second_job_on_the_repo_is_409_until_cancel_ends_the_first(
        self, http, headers, relay, session_at, git_app, mock_app_state, local
    ):
        session_at(local, project_name="proj")
        bus = _FakeEventBus()
        git_app.state.event_bus = bus
        mock_app_state.event_bus = bus
        _install_sleeping_pre_push(local)
        write_file(local, "a.txt", b"a\n")
        commit_all(local, "add a")

        started = await http.post(_git_url("push"), headers=headers, json={"force": False})
        job_id = started.json()["job_id"]
        refused = await _assert_post_parity(http, headers, relay, _git_url("fetch"), None)
        cancelled = await _assert_post_parity(
            http, headers, relay, _job_url(job_id) + "/cancel", None
        )

        assert started.status_code == 200
        assert refused.status_code == 409
        assert refused.json() == {"detail": {"code": "job_running", "message": job_id}}
        assert cancelled.status_code == 200
        assert (
            cancelled.json()["status"], cancelled.json()["ok"], cancelled.json()["error_code"]
        ) == ("done", False, "cancelled")
        types = [event_type for event_type, _ in bus.published]
        assert types[0] == "git.job.progress"
        assert types.count("git.job.done") == 1
        assert types[-2:] == ["git.job.done", "git.changed"]
        assert bus.published[-2][1] == cancelled.json()
        assert bus.published[-1] == (
            "git.changed",
            {"project_name": "proj", "toplevel": norm_path(local),
             "common_dir": norm_path(local) + "/.git"},
        )


class TestRebaseRouting:
    async def test_rebase_routes_require_auth(self, http):
        started = await http.post(_git_url("rebase"), json={"onto": "main"})
        aborted = await http.post(_git_url("rebase/abort"))

        assert (started.status_code, aborted.status_code) == (401, 401)

    @pytest.mark.parametrize(
        ("route", "body"),
        [
            ("rebase", {"onto": "main"}),
            ("rebase/continue", None),
            ("rebase/skip", None),
            ("rebase/abort", None),
        ],
    )
    async def test_unknown_session_is_404_on_both_transports(
        self, http, headers, relay, session_at, tmp_path, route, body
    ):
        session_at(str(tmp_path))

        resp = await _assert_post_parity(http, headers, relay, _git_url(route, "nope"), body)

        assert resp.status_code == 404
        assert resp.json() == {"detail": "Session 'nope' not found"}

    async def test_rebase_outside_a_repo_is_409_on_both_transports(
        self, http, headers, relay, session_at, tmp_path
    ):
        session_at(str(tmp_path))

        resp = await _assert_post_parity(
            http, headers, relay, _git_url("rebase"), {"onto": "main"}
        )

        assert resp.status_code == 409
        assert resp.json() == {"detail": {"code": "not_a_repo", "message": ""}}


async def _post_via(transport, http, headers, relay, route, body=None):
    if transport == "http":
        resp = await http.post(_git_url(route), headers=headers, json=body)
        return resp.status_code, resp.json()
    dispatched = await relay._dispatch_session_git("POST", _git_url(route), None, body)
    return dispatched["status"], dispatched["body"]


def _head(repo_dir: str, rev: str = "HEAD") -> str:
    return subprocess.run(
        [GIT_BIN, "rev-parse", rev], cwd=repo_dir, check=True, capture_output=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull},
    ).stdout.decode().strip()


@pytest.fixture
def stopped_repo(repo):
    """``other`` checked out, one commit that conflicts with ``main``."""
    make_conflict_branches(repo)
    run_git_sync(repo, "checkout", "-q", "other")
    return repo


@requires_git
class TestRebaseRoutes:
    @pytest.mark.parametrize(
        ("route", "body", "detail"),
        [
            ("rebase", {"onto": "no-such-branch"},
             {"code": "unknown_ref", "message": "no-such-branch"}),
            ("rebase", {"onto": "--root"}, {"code": "unknown_ref", "message": "--root"}),
            ("rebase/continue", None, {"code": "no_rebase", "message": "clean"}),
            ("rebase/skip", None, {"code": "no_rebase", "message": "clean"}),
            ("rebase/abort", None, {"code": "no_rebase", "message": "clean"}),
        ],
    )
    async def test_refusals_match_on_both_transports(
        self, http, headers, relay, session_at, repo, route, body, detail
    ):
        session_at(repo)

        resp = await _assert_post_parity(http, headers, relay, _git_url(route), body)

        assert resp.status_code == 409
        assert resp.json() == {"detail": detail}

    async def test_up_to_date_matches_on_both_transports(
        self, http, headers, relay, session_at, repo
    ):
        session_at(repo)

        resp = await _assert_post_parity(
            http, headers, relay, _git_url("rebase"), {"onto": "main"}
        )

        assert resp.status_code == 200
        assert resp.json() == {"outcome": "up_to_date", "rebase": None, "message": None}

    @pytest.mark.parametrize("transport", ["http", "relay"])
    async def test_start_stops_then_continue_refuses_and_skip_finishes(
        self, http, headers, relay, session_at, git_app, mock_app_state, stopped_repo,
        transport,
    ):
        session_at(stopped_repo, project_name="proj")
        bus = _FakeEventBus()
        git_app.state.event_bus = bus
        mock_app_state.event_bus = bus

        started = await _post_via(
            transport, http, headers, relay, "rebase", {"onto": "main"})
        continued = await _post_via(transport, http, headers, relay, "rebase/continue")
        skipped = await _post_via(transport, http, headers, relay, "rebase/skip")

        assert started[0] == 200
        assert started[1]["outcome"] == "stopped"
        assert started[1]["message"] is None
        assert set(started[1]["rebase"]) == {
            "branch", "onto_oid", "onto_short_oid", "onto_ref", "stopped_on", "step", "total"}
        assert (started[1]["rebase"]["branch"], started[1]["rebase"]["onto_ref"]) == (
            "other", "main")
        assert started[1]["rebase"]["stopped_on"]["subject"] == "other side"
        assert continued == (409, {"detail": {
            "code": "unmerged", "message": "unmerged paths in the index"}})
        assert skipped == (200, {"outcome": "rebased", "rebase": None, "message": None})
        assert _head(stopped_repo) == _head(stopped_repo, "main")
        changed = ("git.changed", {"project_name": "proj", "toplevel": norm_path(stopped_repo),
                                   "common_dir": norm_path(stopped_repo) + "/.git"})
        assert bus.published == [changed, changed]

    @pytest.mark.parametrize("transport", ["http", "relay"])
    async def test_abort_restores_the_branch(
        self, http, headers, relay, session_at, stopped_repo, transport
    ):
        session_at(stopped_repo)
        before = _head(stopped_repo)
        await _post_via(transport, http, headers, relay, "rebase", {"onto": "main"})

        aborted = await _post_via(transport, http, headers, relay, "rebase/abort")

        assert aborted == (200, {"outcome": "aborted", "rebase": None, "message": None})
        assert _head(stopped_repo) == before
        status = await http.get(_status_url(), headers=headers)
        assert (status.json()["repo"]["state"], status.json()["repo"]["rebase"]) == (
            "clean", None)

    @pytest.mark.parametrize("transport", ["http", "relay"])
    async def test_continue_after_a_resolution_finishes(
        self, http, headers, relay, session_at, stopped_repo, transport
    ):
        session_at(stopped_repo)
        await _post_via(transport, http, headers, relay, "rebase", {"onto": "main"})
        write_file(stopped_repo, "keep.txt", b"one\nRESOLVED\nthree\n")
        resolved = await _post_via(
            transport, http, headers, relay, "stage", {"paths": ["keep.txt"]})

        continued = await _post_via(transport, http, headers, relay, "rebase/continue")

        assert resolved == (200, {"ok": True})
        assert continued == (200, {"outcome": "rebased", "rebase": None, "message": None})
        assert _head(stopped_repo, "HEAD~1") == _head(stopped_repo, "main")

    @pytest.mark.parametrize("transport", ["http", "relay"])
    async def test_marking_a_file_with_markers_resolved_is_409(
        self, http, headers, relay, session_at, stopped_repo, transport
    ):
        session_at(stopped_repo)
        await _post_via(transport, http, headers, relay, "rebase", {"onto": "main"})

        refused = await _post_via(
            transport, http, headers, relay, "stage", {"paths": ["keep.txt"]})

        assert refused == (409, {"detail": {"code": "conflict_markers", "message": "keep.txt"}})


def _conflict_url(session_id: str = SESSION_ID) -> str:
    return _git_url("conflict", session_id)


def _read(repo_dir: str, rel: str) -> bytes:
    with open(os.path.join(repo_dir, *rel.split("/")), "rb") as fh:
        return fh.read()


@pytest.fixture
def merging_repo(repo):
    """``main`` stopped merging ``other``: ``keep.txt`` is in conflict."""
    make_conflict_branches(repo)
    run_git_sync(repo, "merge", "other", check=False)
    return repo


class TestConflictRouting:
    async def test_conflict_routes_require_auth(self, http):
        read = await http.get(_conflict_url(), params={"path": "a.txt"})
        resolved = await http.post(
            _git_url("conflict/resolve"), json={"path": "a.txt", "token": "t", "content": "x"})
        taken = await http.post(
            _git_url("conflict/take"), json={"path": "a.txt", "side": "ours"})
        restored = await http.post(_git_url("conflict/restore"), json={"path": "a.txt"})

        assert [r.status_code for r in (read, resolved, taken, restored)] == [401] * 4

    async def test_unknown_session_read_is_404_on_both_transports(
        self, http, headers, relay, session_at, tmp_path
    ):
        session_at(str(tmp_path))

        resp = await _assert_parity(
            http, headers, relay, _conflict_url("nope"), {"path": "a.txt"})

        assert resp.status_code == 404
        assert resp.json() == {"detail": "Session 'nope' not found"}

    @pytest.mark.parametrize(
        ("route", "body"),
        [
            ("conflict/resolve", {"path": "a.txt", "token": "t", "content": "x"}),
            ("conflict/take", {"path": "a.txt", "side": "ours"}),
            ("conflict/restore", {"path": "a.txt"}),
        ],
    )
    async def test_unknown_session_write_is_404_on_both_transports(
        self, http, headers, relay, session_at, tmp_path, route, body
    ):
        session_at(str(tmp_path))

        resp = await _assert_post_parity(http, headers, relay, _git_url(route, "nope"), body)

        assert resp.status_code == 404
        assert resp.json() == {"detail": "Session 'nope' not found"}

    @pytest.mark.parametrize(
        "query", [None, {}, {"path": ""}, {"path": "../a.txt"}, {"path": ".git/config"}]
    )
    async def test_a_missing_or_bad_path_is_400_on_both_transports(
        self, http, headers, relay, session_at, tmp_path, query
    ):
        session_at(str(tmp_path))

        resp = await _assert_parity(http, headers, relay, _conflict_url(), query)

        assert resp.status_code == 400
        assert resp.json() == {"detail": {"code": "bad_path"}}

    async def test_a_read_outside_a_repo_is_400_and_a_write_409(
        self, http, headers, relay, session_at, tmp_path
    ):
        session_at(str(tmp_path))

        read = await _assert_parity(http, headers, relay, _conflict_url(), {"path": "a.txt"})
        taken = await _assert_post_parity(
            http, headers, relay, _git_url("conflict/take"), {"path": "a.txt", "side": "ours"})

        assert (read.status_code, read.json()) == (400, {"detail": {"code": "not_a_repo"}})
        assert (taken.status_code, taken.json()) == (
            409, {"detail": {"code": "not_a_repo", "message": ""}})


@requires_git
class TestConflictRoutes:
    async def test_read_body_matches_on_both_transports(
        self, http, headers, relay, session_at, merging_repo
    ):
        session_at(merging_repo)

        resp = await _assert_parity(
            http, headers, relay, _conflict_url(), {"path": "keep.txt"})

        body = resp.json()
        assert resp.status_code == 200
        assert set(body) == {
            "path", "code", "state", "kind", "content", "token", "markers", "has_base",
            "ours", "theirs", "byte_limit"}
        assert (body["path"], body["code"], body["state"], body["kind"]) == (
            "keep.txt", "UU", "merging", "text")
        assert body["content"] == _read(merging_repo, "keep.txt").decode()
        assert (body["markers"], body["has_base"], body["byte_limit"]) == (1, False, 262144)
        assert body["ours"] == {"label": "main", "present": True}
        assert body["theirs"]["present"] is True
        assert body["theirs"]["label"].endswith(" other side")

    async def test_a_file_without_text_carries_nulls_on_both_transports(
        self, http, headers, relay, session_at, merging_repo
    ):
        session_at(merging_repo)
        write_file(merging_repo, "keep.txt", b"\0\1\2")

        resp = await _assert_parity(
            http, headers, relay, _conflict_url(), {"path": "keep.txt"})

        body = resp.json()
        assert (body["kind"], body["content"], body["token"]) == ("binary", None, None)

    @pytest.mark.parametrize(
        ("route", "body", "status", "detail"),
        [
            ("conflict/resolve", {"path": "README.md", "token": "t", "content": "x\n"},
             409, {"code": "not_conflicted", "message": "README.md"}),
            ("conflict/resolve", {"path": "keep.txt", "token": "stale", "content": "x\n"},
             409, {"code": "file_changed", "message": "keep.txt"}),
            ("conflict/resolve",
             {"path": "keep.txt", "token": "t", "content": "<<<<<<< a\nx\n>>>>>>> b\n"},
             409, {"code": "conflict_markers", "message": "keep.txt"}),
            ("conflict/resolve",
             {"path": "keep.txt", "token": "t", "content": "x" * (256 * 1024 + 1)},
             400, {"code": "too_large"}),
            ("conflict/resolve", {"path": "keep.txt", "token": "t", "content": "a\0b"},
             400, {"code": "bad_content"}),
            ("conflict/resolve", {"path": ".git/config", "token": "t", "content": "x"},
             400, {"code": "bad_path"}),
            ("conflict/take", {"path": "README.md", "side": "theirs"},
             409, {"code": "not_conflicted", "message": "README.md"}),
            ("conflict/take", {"path": "../x", "side": "theirs"}, 400, {"code": "bad_path"}),
            ("conflict/restore", {"path": "README.md"},
             409, {"code": "not_restorable", "message": "README.md"}),
            ("conflict/restore", {"path": "keep.txt", "token": "stale"},
             409, {"code": "file_changed", "message": "keep.txt"}),
            ("conflict/restore", {"path": "a/.git/x"}, 400, {"code": "bad_path"}),
        ],
    )
    async def test_refusals_and_bad_requests_match_on_both_transports(
        self, http, headers, relay, session_at, merging_repo, route, body, status, detail
    ):
        session_at(merging_repo)
        before = _read(merging_repo, "keep.txt")

        resp = await _assert_post_parity(http, headers, relay, _git_url(route), body)

        assert resp.status_code == status
        assert resp.json() == {"detail": detail}
        assert _read(merging_repo, "keep.txt") == before

    async def test_a_not_conflicted_read_is_409_on_both_transports(
        self, http, headers, relay, session_at, merging_repo
    ):
        session_at(merging_repo)

        resp = await _assert_parity(
            http, headers, relay, _conflict_url(), {"path": "README.md"})

        assert resp.status_code == 409
        assert resp.json() == {"detail": {"code": "not_conflicted", "message": "README.md"}}

    @pytest.mark.parametrize("transport", ["http", "relay"])
    async def test_resolve_then_restore_then_take_walks_the_file_through_the_resolver(
        self, http, headers, relay, session_at, git_app, mock_app_state, merging_repo,
        transport,
    ):
        session_at(merging_repo, project_name="proj")
        bus = _FakeEventBus()
        git_app.state.event_bus = bus
        mock_app_state.event_bus = bus
        read = await http.get(_conflict_url(), headers=headers, params={"path": "keep.txt"})

        resolved = await _post_via(transport, http, headers, relay, "conflict/resolve", {
            "path": "keep.txt", "token": read.json()["token"],
            "content": "one\nRESOLVED\nthree\n"})
        after_resolve = (await http.get(_status_url(), headers=headers)).json()
        restored = await _post_via(
            transport, http, headers, relay, "conflict/restore",
            {"path": "keep.txt", "style": "diff3"})
        after_restore = (await http.get(_status_url(), headers=headers)).json()
        reread = await http.get(_conflict_url(), headers=headers, params={"path": "keep.txt"})
        taken = await _post_via(
            transport, http, headers, relay, "conflict/take",
            {"path": "keep.txt", "side": "theirs"})

        assert resolved == (200, {"ok": True})
        assert after_resolve["resolved_paths"] == ["keep.txt"]
        assert [(e["group"], e["path"]) for e in after_resolve["entries"]] == [
            ("staged", "keep.txt")]
        assert restored == (200, {"ok": True})
        assert after_restore["resolved_paths"] == []
        assert [(e["group"], e["path"]) for e in after_restore["entries"]] == [
            ("conflicts", "keep.txt")]
        assert (reread.json()["markers"], reread.json()["has_base"]) == (1, True)
        assert taken == (200, {"ok": True})
        assert _read(merging_repo, "keep.txt") == b"one\nOTHER\nthree\n"
        changed = ("git.changed", {"project_name": "proj", "toplevel": norm_path(merging_repo),
                                   "common_dir": norm_path(merging_repo) + "/.git"})
        assert bus.published == [changed, changed, changed]

    @pytest.mark.parametrize("transport", ["http", "relay"])
    async def test_a_draft_defaults_to_apply_unless_mark_resolved_is_false(
        self, http, headers, relay, session_at, merging_repo, transport
    ):
        session_at(merging_repo)
        read = await http.get(_conflict_url(), headers=headers, params={"path": "keep.txt"})
        draft = "one\n<<<<<<< main\nhalf\n=======\nOTHER\n>>>>>>> other\nthree\n"

        saved = await _post_via(transport, http, headers, relay, "conflict/resolve", {
            "path": "keep.txt", "token": read.json()["token"], "content": draft,
            "mark_resolved": False})
        applied = await _post_via(transport, http, headers, relay, "conflict/resolve", {
            "path": "keep.txt", "token": read.json()["token"], "content": draft})

        assert saved == (200, {"ok": True})
        assert _read(merging_repo, "keep.txt") == draft.encode()
        assert await _groups(http, headers) == [("conflicts", "keep.txt")]
        assert applied == (409, {"detail": {"code": "conflict_markers", "message": "keep.txt"}})

    async def test_status_carries_resolved_paths_on_both_transports(
        self, http, headers, relay, session_at, merging_repo
    ):
        session_at(merging_repo)
        write_file(merging_repo, "keep.txt", b"one\nRESOLVED\nthree\n")
        run_git_sync(merging_repo, "add", "keep.txt")

        resp = await _assert_parity(http, headers, relay, _status_url())

        assert resp.json()["resolved_paths"] == ["keep.txt"]
