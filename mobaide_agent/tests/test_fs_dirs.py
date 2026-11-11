"""``GET /api/fs/dirs`` — the bounded single-directory listing behind the
clients' path suggestions — through ``service.list_dirs``, the FastAPI route
and the relay ``_dispatch_proxy`` branch, plus ``capabilities.fs`` on both
``/api/version`` builders and the ``FsDirsResponse`` golden fixture."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from mobaide.agent.fs import service
from mobaide.agent.fs.models import FsDirsResponse
from mobaide.agent.fs.service import FsListError, list_dirs
from mobaide.relay.client import RelayClient

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks and file modes need POSIX"
)

_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "tests" / "protocol_fixtures" / "dto" / "agent" / "FsDirsResponse.json"
)


_FS_CAPABILITIES = {"dirs": True, "windows_paths": sys.platform == "win32"}


def _tree(root: Path, dirs: list[str], files: tuple[str, ...] | list[str] = ()) -> str:
    for name in dirs:
        (root / name).mkdir()
    for name in files:
        (root / name).write_text("x")
    return str(root)


async def _status_of(path: str, q: str | None = None) -> int:
    with pytest.raises(FsListError) as exc:
        await list_dirs(path, q)
    return exc.value.status


class _HungScan:
    """Stands in for ``service._scan``: blocks inside the scan for ``hung_dir``
    until released (like a syscall stuck on a dead mount), scans anything else
    for real, and records every directory a worker actually entered."""

    def __init__(self, real, hung_dir: str) -> None:
        self._real = real
        self._hung_dir = hung_dir
        self._release = threading.Event()
        self._exited = threading.Semaphore(0)
        self._lock = threading.Lock()
        self._drained = 0
        self.entered: list[str] = []

    def __call__(self, directory: str, needle: str | None):
        with self._lock:
            self.entered.append(directory)
        if directory != self._hung_dir:
            return self._real(directory, needle)
        try:
            self._release.wait(timeout=10)
            return [], False
        finally:
            self._exited.release()

    def drain(self) -> None:
        self._release.set()
        while self._drained < self.entered.count(self._hung_dir):
            assert self._exited.acquire(timeout=10), "a hung scan never returned"
            self._drained += 1


@pytest.fixture
def hung_scan(tmp_path, monkeypatch):
    hung_dir = tmp_path / "hung"
    hung_dir.mkdir()
    fake = _HungScan(service._scan, str(hung_dir))
    monkeypatch.setattr(service, "_scan", fake)
    monkeypatch.setattr(service, "SCAN_REPLY_TIMEOUT_SECONDS", 0.05)
    try:
        yield fake
    finally:
        fake.drain()


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


class TestListing:
    async def test_lists_only_subdirectories_sorted_case_insensitively(self, tmp_path):
        root = _tree(tmp_path, ["beta", "Alpha", ".git", "gamma"], ["alpha.txt", "Zfile"])

        listing = await list_dirs(root)

        assert listing == FsDirsResponse(
            path=root, dirs=[".git", "Alpha", "beta", "gamma"], truncated=False
        )


    @posix_only
    async def test_symlink_to_a_directory_counts_and_a_dangling_one_does_not(self, tmp_path):
        target = tmp_path / "real"
        target.mkdir()
        listed = tmp_path / "listed"
        listed.mkdir()
        (listed / "link").symlink_to(target)
        (listed / "dangling").symlink_to(tmp_path / "gone")

        listing = await list_dirs(str(listed))

        assert listing.dirs == ["link"]

    async def test_q_keeps_names_containing_it_case_insensitively(self, tmp_path):
        root = _tree(tmp_path, ["project", "MyProj", "docs"], ["proj.txt"])

        listing = await list_dirs(root, "PROJ")

        assert listing.dirs == ["MyProj", "project"]
        assert listing.truncated is False


    @posix_only
    async def test_tilde_is_expanded_and_echoed_absolute(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        _tree(tmp_path, ["code"])

        listing = await list_dirs("~/")

        assert listing.path == f"{tmp_path}/"
        assert listing.dirs == ["code"]

    @posix_only
    async def test_windows_host_echoes_backslash_separators(self, tmp_path, monkeypatch):
        monkeypatch.setattr(service.os, "sep", "\\")
        _tree(tmp_path, ["code"])

        listing = await list_dirs(f"{tmp_path}/")

        assert listing.path == str(tmp_path).replace("/", "\\") + "\\"
        assert listing.dirs == ["code"]


class TestCaps:
    async def test_entry_cap_truncates_only_when_more_entries_remain(self, tmp_path, monkeypatch):
        monkeypatch.setattr(service, "MAX_ENTRIES_SCANNED", 3)
        (tmp_path / "exact").mkdir()
        (tmp_path / "over").mkdir()
        exact = _tree(tmp_path / "exact", ["a", "b"], ["f"])
        over = _tree(tmp_path / "over", ["a", "b", "c"], ["f", "g"])

        at_cap = await list_dirs(exact)
        past_cap = await list_dirs(over)

        assert at_cap == FsDirsResponse(path=exact, dirs=["a", "b"], truncated=False)
        assert past_cap.truncated is True
        assert len(past_cap.dirs) <= 3

    async def test_result_cap_truncates(self, tmp_path, monkeypatch):
        monkeypatch.setattr(service, "MAX_DIRS_RETURNED", 2)
        root = _tree(tmp_path, ["a", "b", "c", "d"])

        listing = await list_dirs(root)

        assert listing.truncated is True
        assert len(listing.dirs) == 2

    async def test_time_budget_truncates(self, tmp_path, monkeypatch):
        # A zero budget is spent by the first entry, without any sleeping.
        monkeypatch.setattr(service, "SCAN_BUDGET_SECONDS", 0)
        root = _tree(tmp_path, ["a", "b", "c"])

        listing = await list_dirs(root)

        assert listing.truncated is True
        assert len(listing.dirs) == 1

    async def test_a_hung_scan_answers_503_not_404(self, hung_scan, tmp_path):
        assert await _status_of(str(tmp_path / "hung")) == 503

    async def test_a_call_queued_behind_two_hung_workers_times_out_unstarted(
        self, hung_scan, tmp_path, monkeypatch
    ):
        hung = str(tmp_path / "hung")
        (tmp_path / "healthy").mkdir()
        healthy = _tree(tmp_path / "healthy", ["a"])
        for _ in range(2):
            assert await _status_of(hung) == 503

        assert await _status_of(healthy) == 503
        # Let the cancellation reach the pool before a worker frees up.
        for _ in range(3):
            await asyncio.sleep(0)
        hung_scan.drain()

        assert hung_scan.entered == [hung, hung]
        monkeypatch.setattr(service, "SCAN_REPLY_TIMEOUT_SECONDS", 5)
        assert (await list_dirs(healthy)).dirs == ["a"]
        assert hung_scan.entered == [hung, hung, healthy]

    async def test_a_failed_scan_gives_its_slot_back(self, tmp_path):
        for _ in range(3):
            assert await _status_of(str(tmp_path / "missing")) == 404

        assert (await list_dirs(str(tmp_path))).truncated is False


class TestErrors:
    @pytest.mark.parametrize("path", ["", "relative/dir", "~nosuchuser_mobaide/x"])
    async def test_empty_or_relative_path_is_400(self, path):
        assert await _status_of(path) == 400

    async def test_overlong_path_is_400(self):
        assert await _status_of("/" + "a" * service.MAX_PATH_CHARS) == 400

    async def test_nul_byte_is_400(self, tmp_path):
        assert await _status_of(f"{tmp_path}/a\x00b") == 400

    async def test_missing_directory_is_404(self, tmp_path):
        assert await _status_of(str(tmp_path / "nope")) == 404

    async def test_a_file_is_404(self, tmp_path):
        target = tmp_path / "file.txt"
        target.write_text("x")

        assert await _status_of(str(target)) == 404

    @posix_only
    @pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                        reason="root reads any directory")
    async def test_unreadable_directory_is_403(self, tmp_path):
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0)
        try:
            assert await _status_of(str(locked)) == 403
        finally:
            locked.chmod(0o755)


class TestTransports:
    async def test_route_requires_auth(self, client, tmp_path):
        resp = await client.get("/api/fs/dirs", params={"path": str(tmp_path)})

        assert resp.status_code == 401

    @pytest.mark.parametrize(
        ("query", "status"),
        [
            ({"q": "Pro"}, 200),
            ({}, 200),
            ({"path": "relative"}, 400),
            ({"path": "missing"}, 404),
        ],
    )
    async def test_both_transports_answer_identically(
        self, client, auth_headers, relay, tmp_path, query, status
    ):
        root = _tree(tmp_path, ["project", "docs", ".hidden"])
        params = {"path": root, **query}
        if params["path"] == "missing":
            params["path"] = str(tmp_path / "missing")

        resp = await client.get("/api/fs/dirs", headers=auth_headers, params=params)
        dispatched = await relay._dispatch_proxy(
            "GET", "/api/fs/dirs", None, params, AsyncMock()
        )

        assert resp.status_code == status
        assert dispatched == {"status": resp.status_code, "body": resp.json()}

    async def test_a_hung_scan_is_503_on_both_transports(
        self, client, auth_headers, relay, hung_scan, tmp_path
    ):
        params = {"path": str(tmp_path / "hung")}

        resp = await client.get("/api/fs/dirs", headers=auth_headers, params=params)
        dispatched = await relay._dispatch_proxy(
            "GET", "/api/fs/dirs", None, params, AsyncMock()
        )

        assert resp.status_code == 503
        assert dispatched == {"status": 503, "body": resp.json()}

    async def test_direct_version_advertises_fs_dirs(self, client, auth_headers):
        resp = await client.get("/api/version", headers=auth_headers)

        assert resp.json()["capabilities"]["fs"] == _FS_CAPABILITIES


def _fixture_cases():
    doc = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert doc["type"] == "FsDirsResponse"
    assert doc["cases"], "the golden would assert nothing"
    return [pytest.param(case, id=case["name"]) for case in doc["cases"]]


@pytest.mark.parametrize("case", _fixture_cases())
def test_golden_fixture_round_trips_through_model(case):
    decoded = FsDirsResponse.model_validate(case["wire"])

    assert decoded.model_dump(mode="json") == case["wire"]
    assert "decoded_canonical" not in case
