"""Resumable, verified catalog downloads over ``httpx.MockTransport``: Range
resume, a server that ignores Range, the next-URL fallback on 4xx and bad
bytes, retry with capped backoff on 5xx/429/transport errors, and
``disk_full``. No real network and no real sleeps."""
from __future__ import annotations

import errno
import hashlib
from pathlib import Path

import httpx
import pytest

from mobaide.voice.catalog import FAKE_CONTENT, FAKE_MODEL, CatalogFile
from mobaide.voice.download import DownloadError, FakeFetcher, HttpFetcher

MIRROR = "https://mirror.test/model.bin"
FALLBACK = "https://fallback.test/model.bin"
BLOB = bytes(range(256)) * 40  # 10 KiB


def _file(blob: bytes = BLOB, urls: tuple[str, ...] = (MIRROR, FALLBACK)) -> CatalogFile:
    return CatalogFile(
        name="model.bin", size=len(blob), sha256=hashlib.sha256(blob).hexdigest(), urls=urls
    )


class _Server:
    """Scripted origin: ``script[host]`` is a list of responders consumed in
    order (the last one repeats); every request is recorded."""

    def __init__(self, script: dict[str, list]) -> None:
        self.script = {host: list(steps) for host, steps in script.items()}
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        steps = self.script[request.url.host]
        step = steps.pop(0) if len(steps) > 1 else steps[0]
        return step(request)

    def hosts(self) -> list[str]:
        return [r.url.host for r in self.requests]


def _full(blob: bytes = BLOB):
    return lambda _req: httpx.Response(200, content=blob)


def _ranged(blob: bytes = BLOB):
    def respond(request: httpx.Request) -> httpx.Response:
        start = int(request.headers["range"].removeprefix("bytes=").rstrip("-"))
        return httpx.Response(
            206,
            content=blob[start:],
            headers={"content-range": f"bytes {start}-{len(blob) - 1}/{len(blob)}"},
        )

    return respond


def _status(code: int):
    return lambda _req: httpx.Response(code, content=b"nope")


def _connect_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


class _Sleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _fetcher(server: _Server, sleeps: _Sleeps, attempts: int = 6) -> HttpFetcher:
    return HttpFetcher(
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(server.handler)),
        attempts_per_url=attempts,
        sleep=sleeps,
    )


class _Progress:
    def __init__(self) -> None:
        self.reports: list[tuple[str, int]] = []

    def __call__(self, name: str, done: int) -> None:
        self.reports.append((name, done))


async def test_fresh_download_lands_verified_file_and_no_part(tmp_path):
    server = _Server({"mirror.test": [_full()]})
    progress = _Progress()

    path = await _fetcher(server, _Sleeps()).fetch(_file(), tmp_path, progress)

    assert path == tmp_path / "model.bin"
    assert path.read_bytes() == BLOB
    assert not (tmp_path / "model.bin.part").exists()
    assert progress.reports[-1] == ("model.bin", len(BLOB))
    assert "range" not in server.requests[0].headers


async def test_partial_download_resumes_with_a_range_request(tmp_path):
    (tmp_path / "model.bin.part").write_bytes(BLOB[:4000])
    server = _Server({"mirror.test": [_ranged()]})

    path = await _fetcher(server, _Sleeps()).fetch(_file(), tmp_path, _Progress())

    assert server.requests[0].headers["range"] == "bytes=4000-"
    assert path.read_bytes() == BLOB


async def test_full_200_answer_to_a_range_request_restarts_the_file(tmp_path):
    """A server that ignores Range sends the whole file; appending it to
    the part would corrupt the download."""
    (tmp_path / "model.bin.part").write_bytes(BLOB[:4000])
    server = _Server({"mirror.test": [_full()]})

    path = await _fetcher(server, _Sleeps()).fetch(_file(), tmp_path, _Progress())

    assert server.requests[0].headers["range"] == "bytes=4000-"
    assert path.read_bytes() == BLOB


async def test_part_longer_than_the_file_is_discarded(tmp_path):
    (tmp_path / "model.bin.part").write_bytes(BLOB + b"junk")
    server = _Server({"mirror.test": [_full()]})

    path = await _fetcher(server, _Sleeps()).fetch(_file(), tmp_path, _Progress())

    assert "range" not in server.requests[0].headers
    assert path.read_bytes() == BLOB


async def test_complete_verified_part_is_renamed_without_a_request(tmp_path):
    (tmp_path / "model.bin.part").write_bytes(BLOB)
    server = _Server({"mirror.test": [_full()]})

    path = await _fetcher(server, _Sleeps()).fetch(_file(), tmp_path, _Progress())

    assert server.requests == []
    assert path.read_bytes() == BLOB


async def test_verified_final_file_is_kept_without_a_request(tmp_path):
    (tmp_path / "model.bin").write_bytes(BLOB)
    server = _Server({"mirror.test": [_full()]})
    progress = _Progress()

    await _fetcher(server, _Sleeps()).fetch(_file(), tmp_path, progress)

    assert server.requests == []
    assert progress.reports == [("model.bin", len(BLOB))]


async def test_corrupt_final_file_is_downloaded_again(tmp_path):
    (tmp_path / "model.bin").write_bytes(b"x" * len(BLOB))
    server = _Server({"mirror.test": [_full()]})

    path = await _fetcher(server, _Sleeps()).fetch(_file(), tmp_path, _Progress())

    assert len(server.requests) == 1
    assert path.read_bytes() == BLOB


async def test_wrong_bytes_are_discarded_and_the_next_url_is_tried(tmp_path):
    """A captive portal (or a broken mirror) answering 200 with the right
    length but the wrong bytes must never be accepted."""
    portal = b"<html>" + b"x" * (len(BLOB) - 6)
    server = _Server({"mirror.test": [_full(portal)], "fallback.test": [_full()]})
    sleeps = _Sleeps()

    path = await _fetcher(server, sleeps).fetch(_file(), tmp_path, _Progress())

    assert server.hosts() == ["mirror.test", "fallback.test"]
    assert "range" not in server.requests[1].headers
    assert path.read_bytes() == BLOB
    assert sleeps.delays == []


async def test_oversized_body_is_a_size_mismatch_and_the_next_url_is_tried(tmp_path):
    server = _Server({"mirror.test": [_full(BLOB + b"extra")], "fallback.test": [_full()]})

    path = await _fetcher(server, _Sleeps()).fetch(_file(), tmp_path, _Progress())

    assert server.hosts() == ["mirror.test", "fallback.test"]
    assert path.read_bytes() == BLOB


async def test_client_error_moves_to_the_next_url_without_retrying(tmp_path):
    server = _Server({"mirror.test": [_status(404)], "fallback.test": [_full()]})
    sleeps = _Sleeps()

    path = await _fetcher(server, sleeps).fetch(_file(), tmp_path, _Progress())

    assert server.hosts() == ["mirror.test", "fallback.test"]
    assert sleeps.delays == []
    assert path.read_bytes() == BLOB


@pytest.mark.parametrize("first", [_status(503), _status(429), _connect_error], ids=["503", "429", "connect"])
async def test_transient_failure_is_retried_on_the_same_url(tmp_path, first):
    server = _Server({"mirror.test": [first, _full()], "fallback.test": [_full()]})
    sleeps = _Sleeps()

    path = await _fetcher(server, sleeps).fetch(_file(), tmp_path, _Progress())

    assert server.hosts() == ["mirror.test", "mirror.test"]
    assert sleeps.delays == [1.0]
    assert path.read_bytes() == BLOB


async def test_connection_closed_early_resumes_from_what_arrived(tmp_path):
    server = _Server({"mirror.test": [_full(BLOB[:3000]), _ranged()]})

    path = await _fetcher(server, _Sleeps()).fetch(_file(), tmp_path, _Progress())

    assert server.requests[1].headers["range"] == "bytes=3000-"
    assert path.read_bytes() == BLOB


async def test_backoff_doubles_and_is_capped(tmp_path):
    server = _Server({"mirror.test": [_status(500)], "fallback.test": [_full()]})
    sleeps = _Sleeps()

    await _fetcher(server, sleeps, attempts=8).fetch(_file(), tmp_path, _Progress())

    assert sleeps.delays == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
    assert server.hosts().count("mirror.test") == 8


async def test_every_source_failing_raises_the_last_error(tmp_path):
    server = _Server({"mirror.test": [_status(503)], "fallback.test": [_status(403)]})

    with pytest.raises(DownloadError) as excinfo:
        await _fetcher(server, _Sleeps(), attempts=2).fetch(_file(), tmp_path, _Progress())

    assert excinfo.value.code == "http_error"
    assert "403" in excinfo.value.message
    assert not (tmp_path / "model.bin").exists()


async def test_full_disk_aborts_with_disk_full_and_tries_no_other_source(tmp_path, monkeypatch):
    class _FullDisk:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def write(self, _data):
            raise OSError(errno.ENOSPC, "No space left on device")

    real_open = Path.open

    def open_full(self, mode="r", *args, **kwargs):
        if self.name.endswith(".part") and ("w" in mode or "a" in mode):
            return _FullDisk()
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_full)
    server = _Server({"mirror.test": [_full()], "fallback.test": [_full()]})

    with pytest.raises(DownloadError) as excinfo:
        await _fetcher(server, _Sleeps()).fetch(_file(), tmp_path, _Progress())

    assert excinfo.value.code == "disk_full"
    assert server.hosts() == ["mirror.test"]


class TestFakeFetcher:
    async def test_serves_verified_fake_model_files_with_progress(self, tmp_path):
        progress = _Progress()
        fetcher = FakeFetcher(FAKE_CONTENT, delay=0)

        for file in FAKE_MODEL.files:
            path = await fetcher.fetch(file, tmp_path, progress)
            assert path.read_bytes() == FAKE_CONTENT[file.name]

        assert progress.reports[-1] == (FAKE_MODEL.files[-1].name, FAKE_MODEL.files[-1].size)
        assert not list(tmp_path.glob("*.part"))

    async def test_unknown_or_mismatching_content_is_a_hash_mismatch(self, tmp_path):
        fetcher = FakeFetcher({"model.bin": b"other bytes"}, delay=0)

        with pytest.raises(DownloadError) as excinfo:
            await fetcher.fetch(_file(), tmp_path, _Progress())

        assert excinfo.value.code == "hash_mismatch"
