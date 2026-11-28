"""``GET /api/sessions/{id}/files`` — the ranked, capped listing behind the
chat composer's ``@`` suggestions — through ``session_files.list_session_files``
(git and walk listings, ranking, caps, the per-folder cache, errors), the
FastAPI route and the relay dispatcher, plus the ``SessionFilesResponse``
golden fixture."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from mobaide.agent.fs import session_files
from mobaide.agent.fs.models import SessionFilesResponse
from mobaide.agent.fs.service import FsListError
from mobaide.agent.fs.session_files import list_session_files
from mobaide.core.git_exec import CmdResult, reset_caches
from mobaide.relay.client import RelayClient
from mobaide.relay.envelope import CH_CONTROL, decode

from .git_repo_support import init_repo, requires_git, run_git_sync, write_file

SESSION_ID = "sess-1"

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks and file modes need POSIX"
)

_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "tests" / "protocol_fixtures" / "dto" / "agent" / "SessionFilesResponse.json"
)


@pytest.fixture(autouse=True)
def _fresh_caches():
    reset_caches()
    session_files._cache.clear()
    session_files._inflight.clear()
    yield
    session_files._cache.clear()
    session_files._inflight.clear()
    reset_caches()


@pytest.fixture
def no_git(monkeypatch):
    """Lists by walking the folder, as on a host without git."""
    monkeypatch.setattr(session_files, "git_binary", lambda: None)


class _Clock:
    """``time`` for the module under test: ``monotonic`` moves only when told."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(session_files, "time", fake)
    return fake


@pytest.fixture
def builds(monkeypatch) -> list[str]:
    """Each folder a listing was built for, in order."""
    calls: list[str] = []
    real = session_files.build_index

    def counting(root: str):
        calls.append(root)
        return real(root)

    monkeypatch.setattr(session_files, "build_index", counting)
    return calls


class _HeldBuild:
    """Stands in for ``build_index``: blocks until released, like a listing
    stuck on a slow disk, then lists for real."""

    def __init__(self, real) -> None:
        self._real = real
        self.release = threading.Event()
        self.calls: list[str] = []

    def __call__(self, root: str):
        self.calls.append(root)
        assert self.release.wait(timeout=10), "the held listing was never released"
        return self._real(root)


@pytest.fixture
def held_build(monkeypatch):
    fake = _HeldBuild(session_files.build_index)
    monkeypatch.setattr(session_files, "build_index", fake)
    try:
        yield fake
    finally:
        fake.release.set()


def _files(root: Path, *paths: str) -> str:
    for rel in paths:
        target = root.joinpath(*rel.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")
    return str(root)


def _pairs(listing: SessionFilesResponse) -> list[tuple[str, bool]]:
    return [(entry.path, entry.dir) for entry in listing.entries]


async def _status_of(cwd: str | None, q: str | None = None) -> int:
    with pytest.raises(FsListError) as exc:
        await list_session_files(cwd, q)
    return exc.value.status


@requires_git
class TestGitListing:
    async def test_tracked_and_untracked_files_and_their_folders_but_not_ignored_ones(
        self, tmp_path
    ):
        repo = init_repo(str(tmp_path / "repo"), initial_commit=False)
        write_file(repo, ".gitignore", b"build/\n*.log\n")
        write_file(repo, "src/app.py", b"x")
        run_git_sync(repo, "add", "-A")
        run_git_sync(repo, "commit", "-m", "initial")
        write_file(repo, "docs/guide/intro.md", b"x")
        write_file(repo, "build/out.js", b"x")
        write_file(repo, "debug.log", b"x")

        listing = await list_session_files(repo, None, 50)

        assert set(_pairs(listing)) == {
            (".gitignore", False),
            ("src/app.py", False),
            ("docs/guide/intro.md", False),
            ("src", True),
            ("docs", True),
            ("docs/guide", True),
        }
        assert listing.truncated is False

    async def test_the_entry_cap_marks_the_listing_truncated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(session_files, "MAX_ENTRIES", 2)
        repo = init_repo(str(tmp_path / "repo"), initial_commit=False)
        for name in ("a.txt", "b.txt", "c.txt"):
            write_file(repo, name, b"x")

        listing = await list_session_files(repo, None, 50)

        assert listing.truncated is True
        assert len(listing.entries) == 2
        assert {path for path, _ in _pairs(listing)} <= {"a.txt", "b.txt", "c.txt"}

    async def test_a_nested_repository_is_a_folder_entry(self, tmp_path):
        # git lists an untracked nested repository once, as "vendor/lib/".
        repo = init_repo(str(tmp_path / "repo"), initial_commit=False)
        write_file(repo, "a.txt", b"x")
        nested = init_repo(os.path.join(repo, "vendor", "lib"), initial_commit=False)
        write_file(nested, "inner.py", b"x")

        listing = await list_session_files(repo, None, 50)

        assert set(_pairs(listing)) == {("a.txt", False), ("vendor", True), ("vendor/lib", True)}

    async def test_a_tracked_file_deleted_from_the_work_tree_is_not_listed(self, tmp_path):
        # "--cached" still names it, and the agent would drop a mention of it.
        repo = init_repo(str(tmp_path / "repo"), initial_commit=False)
        write_file(repo, "keep.txt", b"x")
        write_file(repo, "old/gone.txt", b"x")
        run_git_sync(repo, "add", "-A")
        run_git_sync(repo, "commit", "-m", "initial")
        os.remove(os.path.join(repo, "old", "gone.txt"))
        os.rmdir(os.path.join(repo, "old"))

        listing = await list_session_files(repo, None, 50)

        assert set(_pairs(listing)) == {("keep.txt", False)}


class _SlowGit:
    """Stands in for ``_ls_files``: names ``listed`` once ``release`` is set,
    like a cold git on the first call after the agent starts. Keeps the time
    limit it was given."""

    def __init__(self, listed: bytes) -> None:
        self._listed = listed
        self.release = threading.Event()
        self.timeouts: list[float] = []

    def __call__(self, binary: str, root: str, args: tuple[str, ...], timeout: float) -> CmdResult:
        self.timeouts.append(timeout)
        assert self.release.wait(timeout=10), "the slow git was never released"
        return CmdResult(rc=0, stdout=self._listed, stderr=b"")


class TestSlowGit:
    async def test_a_git_slower_than_the_reply_answers_503_then_fills_the_cache_without_the_walk(
        self, tmp_path, monkeypatch
    ):
        # The walk would list what .gitignore hides, and keep it for 30 s.
        root = _files(tmp_path, "keep.txt", "build/out.js", "debug.log")
        git = _SlowGit(b"keep.txt\0")
        monkeypatch.setattr(session_files, "git_binary", lambda: "git")
        monkeypatch.setattr(session_files, "_ls_files", git)
        monkeypatch.setattr(session_files, "REPLY_TIMEOUT_SECONDS", 0.05)

        try:
            assert await _status_of(root) == 503
        finally:
            git.release.set()
        await _until(lambda: root in session_files._cache)
        listing = await list_session_files(root, None, 50)

        assert _pairs(listing) == [("keep.txt", False)]
        assert git.timeouts == [session_files.GIT_TIMEOUT_SECONDS]
        assert session_files.GIT_TIMEOUT_SECONDS > session_files.REPLY_TIMEOUT_SECONDS


@requires_git
class TestOnDiskCheck:
    async def test_a_file_deleted_after_the_listing_was_kept_is_left_out(self, tmp_path):
        root = _files(tmp_path, "notes.txt", "notes-old.txt")
        await list_session_files(root, "notes", 50)
        os.remove(os.path.join(root, "notes-old.txt"))

        listing = await list_session_files(root, "notes", 50)

        assert _pairs(listing) == [("notes.txt", False)]

    @posix_only
    @pytest.mark.parametrize("lister", ["git", "walk"])
    async def test_a_symlink_that_leads_out_of_the_folder_is_left_out(
        self, tmp_path, monkeypatch, lister
    ):
        outside = _files(tmp_path / "outside", "secret.txt")
        repo = init_repo(str(tmp_path / "repo"), initial_commit=False)
        write_file(repo, "src/app.py", b"x")
        os.symlink(outside, os.path.join(repo, "link_out"))
        os.symlink(os.path.join(outside, "secret.txt"), os.path.join(repo, "link_file"))
        os.symlink(os.path.join(repo, "src", "app.py"), os.path.join(repo, "link_in"))
        if lister == "walk":
            monkeypatch.setattr(session_files, "git_binary", lambda: None)

        listing = await list_session_files(repo, None, 50)

        assert {path for path, _ in _pairs(listing)} == {"src", "src/app.py", "link_in"}


@pytest.mark.usefixtures("no_git")
class TestWalkListing:
    async def test_skips_dot_folders_node_modules_and_pycache_but_keeps_dot_files(self, tmp_path):
        root = _files(
            tmp_path,
            ".env",
            "src/app.py",
            ".cache/blob",
            "node_modules/pkg/index.js",
            "__pycache__/m.pyc",
        )

        listing = await list_session_files(root, None, 50)

        assert set(_pairs(listing)) == {(".env", False), ("src", True), ("src/app.py", False)}
        assert listing.truncated is False

    async def test_the_time_budget_marks_the_listing_truncated(self, tmp_path, monkeypatch):
        # A zero budget is spent by the first entry, without any sleeping.
        monkeypatch.setattr(session_files, "WALK_BUDGET_SECONDS", 0)
        root = _files(tmp_path, "a.txt", "b.txt", "c.txt")

        listing = await list_session_files(root, None, 50)

        assert listing.truncated is True
        assert len(listing.entries) == 1


@pytest.mark.usefixtures("no_git")
class TestRanking:
    async def test_name_prefix_then_name_substring_then_path_prefix_then_path_substring_then_subsequence(
        self, tmp_path
    ):
        root = _files(
            tmp_path,
            "app/notes.txt",
            "src/app.py",
            "src/application/main.py",
            "tests/test_app.py",
            "docs/alpha-plan.pdf",
            "README.md",
        )

        listing = await list_session_files(root, "APP", 50)

        assert _pairs(listing) == [
            ("app", True),
            ("src/app.py", False),
            ("src/application", True),
            ("tests/test_app.py", False),
            ("app/notes.txt", False),
            ("src/application/main.py", False),
            ("docs/alpha-plan.pdf", False),
        ]

    @pytest.mark.parametrize("q", [None, "   "], ids=["none", "blank"])
    async def test_no_query_lists_shallow_entries_first_and_dot_names_last(self, tmp_path, q):
        root = _files(tmp_path, ".env", "zeta.txt", "Alpha.md", "src/main.py", "src/.hidden")

        listing = await list_session_files(root, q, 50)

        assert _pairs(listing) == [
            ("Alpha.md", False),
            ("src", True),
            ("zeta.txt", False),
            (".env", False),
            ("src/main.py", False),
            ("src/.hidden", False),
        ]

    async def test_a_backslash_in_the_query_is_a_slash_on_windows_only(self, tmp_path):
        root = _files(tmp_path, "src/utils/helper.py")
        if sys.platform != "win32":
            _files(tmp_path, "back\\slash.txt")

        slash = await list_session_files(root, "src/ut", 50)
        backslash = await list_session_files(root, "src\\ut", 50)

        if sys.platform == "win32":
            assert _pairs(backslash) == _pairs(slash) == [
                ("src/utils", True),
                ("src/utils/helper.py", False),
            ]
        else:
            assert _pairs(backslash) == []
            named = await list_session_files(root, "back\\sl", 50)
            assert _pairs(named) == [("back\\slash.txt", False)]

    @pytest.mark.parametrize(
        ("limit", "count"),
        [(0, 1), (500, session_files.LIMIT_MAX)],
        ids=["zero", "over-max"],
    )
    async def test_limit_is_clamped(self, tmp_path, limit, count):
        root = _files(tmp_path, *(f"file{n:02}.txt" for n in range(60)))

        listing = await list_session_files(root, "file", limit)

        assert len(listing.entries) == count


@pytest.mark.usefixtures("no_git")
class TestCache:
    async def test_a_listing_is_reused_until_it_expires(self, tmp_path, clock, builds):
        root = _files(tmp_path, "old.txt")
        await list_session_files(root, "txt")
        _files(tmp_path, "new.txt")

        clock.now += session_files.CACHE_TTL_SECONDS - 1
        cached = await list_session_files(root, "txt")
        clock.now += 2
        fresh = await list_session_files(root, "txt")

        assert _pairs(cached) == [("old.txt", False)]
        assert _pairs(fresh) == [("new.txt", False), ("old.txt", False)]
        assert builds == [root, root]

    async def test_calls_during_a_listing_share_it(self, tmp_path, held_build):
        root = _files(tmp_path, "a.txt", "b.txt")

        first = asyncio.ensure_future(list_session_files(root, "a"))
        second = asyncio.ensure_future(list_session_files(root, "b"))
        # Both calls reach the listing before it can end.
        for _ in range(3):
            await asyncio.sleep(0)
        assert not first.done() and not second.done()
        held_build.release.set()
        a, b = await asyncio.gather(first, second)

        assert held_build.calls == [root]
        assert _pairs(a) == [("a.txt", False)]
        assert _pairs(b) == [("b.txt", False)]

    async def test_a_listing_that_outlives_its_reply_still_fills_the_cache(
        self, tmp_path, held_build, monkeypatch
    ):
        monkeypatch.setattr(session_files, "REPLY_TIMEOUT_SECONDS", 0.05)
        root = _files(tmp_path, "a.txt")

        assert await _status_of(root) == 503
        held_build.release.set()
        monkeypatch.setattr(session_files, "REPLY_TIMEOUT_SECONDS", 5)
        listing = await list_session_files(root, "a")

        assert _pairs(listing) == [("a.txt", False)]
        assert held_build.calls == [root]

    async def test_a_cached_folder_answers_while_every_listing_worker_is_stuck(
        self, tmp_path, monkeypatch
    ):
        cached = _files(tmp_path / "cached", "a.txt")
        await list_session_files(cached, "a")
        held = _HeldBuild(session_files.build_index)
        monkeypatch.setattr(session_files, "build_index", held)
        stuck_roots = [
            _files(tmp_path / f"stuck{n}", "b.txt")
            for n in range(session_files._executor._max_workers)
        ]
        stuck = [asyncio.ensure_future(list_session_files(root, "b")) for root in stuck_roots]
        try:
            await _until(lambda: len(held.calls) == len(stuck_roots))

            listing = await list_session_files(cached, "a")
        finally:
            held.release.set()
            await asyncio.gather(*stuck, return_exceptions=True)

        assert _pairs(listing) == [("a.txt", False)]

    async def test_a_failed_listing_is_not_kept(self, tmp_path):
        root = tmp_path / "later"

        assert await _status_of(str(root)) == 404
        _files(root, "a.txt")

        assert _pairs(await list_session_files(str(root), "a")) == [("a.txt", False)]

    async def test_the_least_recently_used_folder_is_dropped_past_the_cap(
        self, tmp_path, clock, builds
    ):
        roots = [_files(tmp_path / f"f{n}", "a.txt") for n in range(session_files.CACHE_MAX_FOLDERS + 1)]
        for root in roots:
            await list_session_files(root, "a")

        await list_session_files(roots[-1], "a")
        await list_session_files(roots[0], "a")

        assert builds == [*roots, roots[0]]


class TestErrors:
    async def test_a_missing_folder_is_404(self, tmp_path):
        assert await _status_of(str(tmp_path / "gone")) == 404

    @posix_only
    @pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                        reason="root reads any directory")
    @pytest.mark.usefixtures("no_git")
    async def test_an_unreadable_folder_is_403(self, tmp_path):
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0)
        try:
            assert await _status_of(str(locked)) == 403
        finally:
            locked.chmod(0o755)

    async def test_a_listing_that_does_not_answer_in_time_is_503(
        self, tmp_path, held_build, monkeypatch
    ):
        monkeypatch.setattr(session_files, "REPLY_TIMEOUT_SECONDS", 0.05)

        assert await _status_of(str(tmp_path)) == 503


@pytest.fixture
def relay(relay_config, mock_session_manager, mock_event_bus, mock_broadcaster,
          mock_app_state):
    mock_app_state.cli_launchers = {}
    return RelayClient(
        config=relay_config,
        session_manager=mock_session_manager,
        event_bus=mock_event_bus,
        broadcaster=mock_broadcaster,
        app_state=mock_app_state,
    )


@pytest.fixture
def session_at(app, mock_session_manager, monkeypatch):
    """Register one session whose ``to_info().cwd`` is the given folder on
    both the app's session manager and the relay's stub."""

    def install(cwd: str | None) -> None:
        info = types.SimpleNamespace(cwd=cwd)
        session = types.SimpleNamespace(to_info=lambda: info)

        async def get_session(session_id: str):
            return session if session_id == SESSION_ID else None

        monkeypatch.setattr(app.state.session_manager, "get_session", get_session)
        mock_session_manager.get_session.side_effect = get_session

    return install


def _files_url(session_id: str = SESSION_ID) -> str:
    return f"/api/sessions/{session_id}/files"


def _proxy(request_id: str, path: str, query: dict | None = None) -> bytes:
    return json.dumps({
        "type": "proxy_request", "request_id": request_id,
        "method": "GET", "path": path, "query": query,
    }).encode()


def _responses(ws) -> list[dict]:
    out = []
    for call in ws.send.call_args_list:
        channel, _sid, payload = decode(call[0][0])
        if channel == CH_CONTROL:
            out.append(json.loads(payload))
    return out


async def _until(predicate) -> None:
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached in time")


@pytest.mark.usefixtures("no_git")
class TestTransports:
    async def test_route_requires_auth(self, client):
        resp = await client.get(_files_url())

        assert resp.status_code == 401

    @pytest.mark.parametrize(
        ("session_id", "query", "status"),
        [
            (SESSION_ID, {"q": "app", "limit": "2"}, 200),
            ("nope", {}, 404),
        ],
        ids=["listing", "unknown-session"],
    )
    async def test_both_transports_answer_identically(
        self, client, auth_headers, relay, session_at, tmp_path, session_id, query, status
    ):
        session_at(_files(tmp_path, "src/app.py", "app.json", "docs/readme.md"))
        url = _files_url(session_id)

        resp = await client.get(url, headers=auth_headers, params=query)
        dispatched = await relay._dispatch_proxy("GET", url, None, query, AsyncMock())

        assert resp.status_code == status
        assert dispatched == {"status": resp.status_code, "body": resp.json()}

    async def test_a_session_without_a_folder_is_404_on_both_transports(
        self, client, auth_headers, relay, session_at
    ):
        session_at(None)

        resp = await client.get(_files_url(), headers=auth_headers)
        dispatched = await relay._dispatch_proxy("GET", _files_url(), None, None, AsyncMock())

        assert resp.status_code == 404
        assert dispatched == {"status": 404, "body": resp.json()}

    async def test_a_limit_that_is_no_integer_is_422_on_both_transports(
        self, client, auth_headers, relay, session_at, tmp_path
    ):
        session_at(str(tmp_path))
        query = {"limit": "many"}

        resp = await client.get(_files_url(), headers=auth_headers, params=query)
        dispatched = await relay._dispatch_proxy("GET", _files_url(), None, query, AsyncMock())

        assert resp.status_code == 422
        assert dispatched == {"status": 422, "body": {"detail": "limit must be an integer"}}

    async def test_the_relay_lists_aside_from_the_tunnel_reader(
        self, relay, session_at, tmp_path, monkeypatch
    ):
        session_at(_files(tmp_path, "a.txt"))
        gate = asyncio.Event()
        real = session_files.list_session_files

        async def held(*args, **kwargs):
            await gate.wait()
            return await real(*args, **kwargs)

        monkeypatch.setattr("mobaide.relay.client.list_session_files", held)
        ws = AsyncMock()
        relay._ws = ws
        try:
            await asyncio.wait_for(
                relay._handle_control(_proxy("r-files", _files_url(), {"q": "a"}), ws), 1
            )
            assert _responses(ws) == []

            gate.set()
            await _until(lambda: len(_responses(ws)) == 1)
        finally:
            gate.set()
            await relay.stop()

        reply = _responses(ws)[0]
        assert (reply["request_id"], reply["status"]) == ("r-files", 200)
        assert reply["body"] == {"entries": [{"path": "a.txt", "dir": False}], "truncated": False}

    async def test_listings_past_the_cap_answer_503(self, relay, session_at, tmp_path, monkeypatch):
        monkeypatch.setattr("mobaide.relay.client.MAX_FILES_PROXY_TASKS", 1)
        session_at(str(tmp_path))
        gate = asyncio.Event()

        async def held(*args, **kwargs):
            await gate.wait()
            return SessionFilesResponse(entries=[], truncated=False)

        monkeypatch.setattr("mobaide.relay.client.list_session_files", held)
        ws = AsyncMock()
        relay._ws = ws
        try:
            await relay._handle_control(_proxy("r-1", _files_url()), ws)
            await relay._handle_control(_proxy("r-2", _files_url()), ws)
            await _until(lambda: len(_responses(ws)) == 1)

            busy = _responses(ws)[0]
            assert (busy["request_id"], busy["status"]) == ("r-2", 503)

            gate.set()
            await _until(lambda: len(_responses(ws)) == 2)
        finally:
            gate.set()
            await relay.stop()

        assert (_responses(ws)[1]["request_id"], _responses(ws)[1]["status"]) == ("r-1", 200)


def _fixture_cases():
    doc = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert doc["type"] == "SessionFilesResponse"
    assert doc["cases"], "the golden would assert nothing"
    return [pytest.param(case, id=case["name"]) for case in doc["cases"]]


@pytest.mark.parametrize("case", _fixture_cases())
def test_golden_fixture_round_trips_through_model(case):
    decoded = SessionFilesResponse.model_validate(case["wire"])

    assert decoded.model_dump(mode="json") == case["wire"]
    assert "decoded_canonical" not in case
