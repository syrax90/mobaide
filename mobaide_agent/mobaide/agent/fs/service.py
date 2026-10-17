"""Bounded single-directory listing behind ``GET /api/fs/dirs``.

Shared by the FastAPI route and the relay dispatcher. Each call scans one
directory, never recurses, and stops at a fixed entry count, result count
and wall-clock budget, so a client typing a path costs the host a handful of
cheap ``scandir`` calls however large the directory is.
"""
from __future__ import annotations

import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor

from .models import FsDirsResponse

MAX_PATH_CHARS = 4096
MAX_ENTRIES_SCANNED = 5000
MAX_DIRS_RETURNED = 1000
SCAN_BUDGET_SECONDS = 0.25
SCAN_REPLY_TIMEOUT_SECONDS = 1.0

# The budget is checked only between entries, so a scan stuck in one syscall (a hung
# mount) can outlive it; the relay reader awaits requests inline, so the reply is
# capped separately at SCAN_REPLY_TIMEOUT_SECONDS. A private pool keeps stuck scans
# out of the PTY readers' default executor and bounds them to two workers.
_MAX_CONCURRENT_SCANS = 2
_scan_executor = ThreadPoolExecutor(
    max_workers=_MAX_CONCURRENT_SCANS, thread_name_prefix="mobaide-fs-dirs",
)


class FsListError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _scan(directory: str, needle: str | None) -> tuple[list[str], bool]:
    deadline = time.monotonic() + SCAN_BUDGET_SECONDS
    dirs: list[str] = []
    truncated = False
    with os.scandir(directory) as it:
        for scanned, entry in enumerate(it, start=1):
            try:
                is_dir = entry.is_dir()
            except OSError:
                is_dir = False
            if is_dir and (needle is None or needle in entry.name.casefold()):
                dirs.append(entry.name)
            if (
                len(dirs) >= MAX_DIRS_RETURNED
                or scanned >= MAX_ENTRIES_SCANNED
                or time.monotonic() >= deadline
            ):
                truncated = next(it, None) is not None
                break
    dirs.sort(key=str.casefold)
    return dirs, truncated


async def list_dirs(path: str, q: str | None = None) -> FsDirsResponse:
    """List the subdirectories of ``path`` (``~`` expanded, must be absolute).

    Raises :class:`FsListError` with the HTTP status both transports answer:
    400 for an empty, relative or malformed path, 404 when it is missing or
    not a directory, 403 when the OS denies the listing, 503 when the scan
    did not answer within ``SCAN_REPLY_TIMEOUT_SECONDS``.
    """
    if not path or len(path) > MAX_PATH_CHARS:
        raise FsListError(400, "Path must be a non-empty absolute path")
    directory = os.path.expanduser(path)
    if not os.path.isabs(directory):
        raise FsListError(400, f"Path must be absolute: {path}")
    needle = q.casefold() if q else None
    loop = asyncio.get_running_loop()
    try:
        dirs, truncated = await asyncio.wait_for(
            loop.run_in_executor(_scan_executor, _scan, directory, needle),
            timeout=SCAN_REPLY_TIMEOUT_SECONDS,
        )
    # Before OSError: TimeoutError subclasses it, and a 404 would be cached.
    except TimeoutError:
        raise FsListError(503, "Directory listing timed out") from None
    except (FileNotFoundError, NotADirectoryError):
        raise FsListError(404, f"No such directory: {directory}") from None
    except PermissionError:
        raise FsListError(403, f"Permission denied: {directory}") from None
    except ValueError:
        raise FsListError(400, "Path contains invalid characters") from None
    except OSError as exc:
        raise FsListError(404, f"Cannot list directory: {exc.filename or directory}") from None
    return FsDirsResponse(path=directory, dirs=dirs, truncated=truncated)
