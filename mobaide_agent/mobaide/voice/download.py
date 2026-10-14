"""Resumable, verified downloads of catalog files.

Each file streams into ``<name>.part`` and is renamed into place only after
its size and SHA-256 match the catalog. A retry resumes with an HTTP Range
request from whatever the ``.part`` already holds; the prefix is re-hashed
from disk first, so a partial file left by a crash, an agent restart or a
laptop that slept mid-download resumes safely.
"""
from __future__ import annotations

import asyncio
import errno
import hashlib
import logging
import os
import ssl
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import certifi
import httpx

from mobaide._http import USER_AGENT

from .catalog import CatalogFile
from .store import PART_SUFFIX

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str, int], None]

ATTEMPTS_PER_URL = 6
BACKOFF_CAP_SECONDS = 30.0
CONNECT_TIMEOUT_SECONDS = 15.0
# A stalled transfer (a sleeping laptop, a dead proxy) surfaces as a read
# timeout and is retried with a Range request.
READ_TIMEOUT_SECONDS = 60.0
_WRITE_BATCH = 1024 * 1024
_HASH_CHUNK = 1024 * 1024


class DownloadError(Exception):
    """``code``: ``network`` | ``http_error`` | ``hash_mismatch`` |
    ``size_mismatch`` | ``disk_full`` | ``io_error``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class _Retry(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _NextSource(Exception):
    def __init__(self, error: DownloadError) -> None:
        super().__init__(error.message)
        self.error = error


class Fetcher(Protocol):
    async def fetch(self, file: CatalogFile, directory: Path, on_progress: ProgressFn) -> Path:
        """Place a verified ``file`` in ``directory``; report bytes present."""
        ...


def default_client() -> httpx.AsyncClient:
    # trust_env honours HTTPS_PROXY / HTTP_PROXY / NO_PROXY; trust is pinned
    # to certifi like every other outbound call of the agent.
    return httpx.AsyncClient(
        verify=ssl.create_default_context(cafile=certifi.where()),
        trust_env=True,
        follow_redirects=True,
        timeout=httpx.Timeout(READ_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
        headers={"User-Agent": USER_AGENT},
    )


async def _hash_file(path: Path, size: int) -> "hashlib._Hash":
    """SHA-256 of the first ``size`` bytes, in a thread that stops early when
    the awaiting task is cancelled."""
    stop = threading.Event()

    def work() -> "hashlib._Hash":
        digest = hashlib.sha256()
        remaining = size
        with path.open("rb") as fh:
            while remaining and not stop.is_set():
                block = fh.read(min(_HASH_CHUNK, remaining))
                if not block:
                    break
                digest.update(block)
                remaining -= len(block)
        return digest

    try:
        return await asyncio.to_thread(work)
    except asyncio.CancelledError:
        stop.set()
        raise


def _content_range_start(value: str | None) -> int | None:
    # "bytes 1000-1999/5000"
    if not value or not value.startswith("bytes "):
        return None
    try:
        return int(value[6:].split("-", 1)[0])
    except ValueError:
        return None


def _host(url: str) -> str:
    return urlsplit(url).hostname or url


def _io_error(exc: OSError) -> DownloadError:
    if exc.errno == errno.ENOSPC:
        return DownloadError("disk_full", "The disk is full.")
    return DownloadError("io_error", f"Could not write the download: {exc.strerror or exc}")


class HttpFetcher:
    def __init__(
        self,
        *,
        client_factory: Callable[[], httpx.AsyncClient] = default_client,
        attempts_per_url: int = ATTEMPTS_PER_URL,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client_factory = client_factory
        self._attempts = attempts_per_url
        self._sleep = sleep

    async def fetch(self, file: CatalogFile, directory: Path, on_progress: ProgressFn) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        final = directory / file.name
        part = directory / (file.name + PART_SUFFIX)
        if await self._already_present(final, file):
            on_progress(file.name, file.size)
            return final
        error = DownloadError("network", "No download source is configured.")
        async with self._client_factory() as client:
            for url in file.urls:
                try:
                    await self._from_source(client, url, file, part, on_progress)
                except _NextSource as exc:
                    error = exc.error
                    logger.warning(
                        "voice.download_source_failed file=%s host=%s code=%s",
                        file.name, _host(url), error.code,
                    )
                    continue
                try:
                    os.replace(part, final)
                except OSError as exc:
                    raise _io_error(exc) from exc
                on_progress(file.name, file.size)
                return final
        raise error

    async def _already_present(self, final: Path, file: CatalogFile) -> bool:
        try:
            size = final.stat().st_size
        except OSError:
            return False
        if size == file.size and (await _hash_file(final, size)).hexdigest() == file.sha256:
            return True
        final.unlink(missing_ok=True)
        return False

    async def _from_source(
        self,
        client: httpx.AsyncClient,
        url: str,
        file: CatalogFile,
        part: Path,
        on_progress: ProgressFn,
    ) -> None:
        attempt = 0
        while True:
            try:
                await self._attempt(client, url, file, part, on_progress)
                return
            except _Retry as exc:
                attempt += 1
                if attempt >= self._attempts:
                    raise _NextSource(DownloadError(exc.code, exc.message)) from exc
                delay = min(BACKOFF_CAP_SECONDS, 2.0 ** (attempt - 1))
                logger.info(
                    "voice.download_retry file=%s host=%s attempt=%d delay=%.0fs reason=%s",
                    file.name, _host(url), attempt, delay, exc.code,
                )
                await self._sleep(delay)

    async def _attempt(
        self,
        client: httpx.AsyncClient,
        url: str,
        file: CatalogFile,
        part: Path,
        on_progress: ProgressFn,
    ) -> None:
        try:
            have = part.stat().st_size
        except OSError:
            have = 0
        if have > file.size:
            part.unlink(missing_ok=True)
            have = 0
        digest = await _hash_file(part, have) if have else hashlib.sha256()
        if have == file.size:
            self._check_digest(digest, file, part, url)
            return
        on_progress(file.name, have)

        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            async with client.stream("GET", url, headers=headers) as resp:
                status = resp.status_code
                if status == 416:
                    part.unlink(missing_ok=True)
                    raise _Retry("http_error", "HTTP 416")
                if status == 429 or status >= 500:
                    raise _Retry("http_error", f"HTTP {status} from {_host(url)}")
                if status not in (200, 206):
                    raise _NextSource(
                        DownloadError("http_error", f"HTTP {status} from {_host(url)}")
                    )
                if status == 200 or _content_range_start(resp.headers.get("content-range")) != have:
                    have = 0
                    digest = hashlib.sha256()
                with part.open("ab" if have else "wb") as fh:
                    pending = bytearray()
                    async for chunk in resp.aiter_bytes():
                        if have + len(pending) + len(chunk) > file.size:
                            fh.close()
                            part.unlink(missing_ok=True)
                            raise _NextSource(
                                DownloadError(
                                    "size_mismatch",
                                    f"{_host(url)} sent more data than expected for {file.name}.",
                                )
                            )
                        pending += chunk
                        digest.update(chunk)
                        if len(pending) >= _WRITE_BATCH:
                            await asyncio.to_thread(fh.write, bytes(pending))
                            have += len(pending)
                            pending.clear()
                            on_progress(file.name, have)
                    if pending:
                        await asyncio.to_thread(fh.write, bytes(pending))
                        have += len(pending)
                        on_progress(file.name, have)
        except httpx.TransportError as exc:
            raise _Retry("network", f"{type(exc).__name__} from {_host(url)}") from exc
        except OSError as exc:
            raise _io_error(exc) from exc
        if have < file.size:
            raise _Retry("network", f"{_host(url)} closed the connection early")
        self._check_digest(digest, file, part, url)

    @staticmethod
    def _check_digest(digest: "hashlib._Hash", file: CatalogFile, part: Path, url: str) -> None:
        if digest.hexdigest() == file.sha256:
            return
        part.unlink(missing_ok=True)
        raise _NextSource(
            DownloadError(
                "hash_mismatch",
                f"{file.name} from {_host(url)} failed verification.",
            )
        )


class FakeFetcher:
    """Serves the fake model's bytes from memory, slowly enough that clients
    see ``downloading`` progress (``MOBAIDE_VOICE_FAKE=1``)."""

    def __init__(
        self,
        content: dict[str, bytes],
        *,
        chunk: int = 128 * 1024,
        delay: float = 0.03,
    ) -> None:
        self._content = content
        self._chunk = chunk
        self._delay = delay

    async def fetch(self, file: CatalogFile, directory: Path, on_progress: ProgressFn) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        data = self._content.get(file.name)
        if data is None or hashlib.sha256(data).hexdigest() != file.sha256:
            raise DownloadError("hash_mismatch", f"{file.name} failed verification.")
        final = directory / file.name
        part = directory / (file.name + PART_SUFFIX)
        try:
            with part.open("wb") as fh:
                for offset in range(0, len(data), self._chunk):
                    fh.write(data[offset : offset + self._chunk])
                    on_progress(file.name, min(offset + self._chunk, len(data)))
                    await asyncio.sleep(self._delay)
            os.replace(part, final)
        except OSError as exc:
            raise _io_error(exc) from exc
        return final
