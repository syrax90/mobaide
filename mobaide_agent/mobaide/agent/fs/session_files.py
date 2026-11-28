"""Ranked file and folder suggestions for ``@`` mentions in the chat
composer: ``GET /api/sessions/{session_id}/files``.

The listing runs in the session's own folder: ``git ls-files`` in a git work
tree, else a breadth-first ``scandir`` walk. Both stop at a fixed count, byte
size or time; a listing is cached per folder, so each keystroke only ranks.
"""
from __future__ import annotations

import asyncio
import functools
import heapq
import os
import time
from collections import OrderedDict, deque
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from mobaide.core.git_exec import GIT_ARGV_PREFIX, CmdResult, git_binary, git_env, run_capped

from .models import SessionFileEntry, SessionFilesResponse
from .service import FsListError

LIMIT_DEFAULT = 20
LIMIT_MAX = 50
QUERY_MAX_CHARS = 256
MAX_ENTRIES = 40_000
GIT_TIMEOUT_SECONDS = 2.5
GIT_MAX_BYTES = 8 * 1024 * 1024
WALK_BUDGET_SECONDS = 1.0
CACHE_TTL_SECONDS = 30.0
CACHE_MAX_FOLDERS = 4
REPLY_TIMEOUT_SECONDS = 4.0

# ``core.fsmonitor`` names a program that git runs; a repository's own config
# must not get to start one through a listing.
_LS_FILES_ARGS = (
    "-c", "core.fsmonitor=false",
    "ls-files", "-z", "--cached", "--others", "--exclude-standard",
)
_LS_DELETED_ARGS = ("-c", "core.fsmonitor=false", "ls-files", "-z", "--deleted")
_DELETED_MIN_SECONDS = 0.2
_SKIPPED_DIRS = frozenset({"node_modules", "__pycache__"})

# Its own pool: a listing stuck on a hung mount must not hold the PTY readers'
# default executor, and the reply is capped at REPLY_TIMEOUT_SECONDS anyway.
# Ranking has a pool of its own, so stuck listings cannot starve folders that
# are already cached.
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mobaide-session-files")
_rank_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mobaide-session-files-rank")

# (path, is_dir, casefolded path, offset of the name in the casefolded path)
_Entry = tuple[str, bool, str, int]


@dataclass(frozen=True)
class _Index:
    entries: tuple[_Entry, ...]
    truncated: bool
    built_at: float


# Touched only on the event loop: lookups in list_session_files, stores in
# the done callback of the listing future.
_cache: OrderedDict[str, _Index] = OrderedDict()
_inflight: dict[str, asyncio.Future[_Index]] = {}


async def list_session_files(
    cwd: str | None, q: str | None = None, limit: int | None = None
) -> SessionFilesResponse:
    """The ``limit`` best matches for ``q`` in the session folder ``cwd``.

    Raises :class:`FsListError`: 404 when the folder is gone, 403 when the
    OS denies it, 503 when the listing did not answer in time.
    """
    if not cwd:
        raise FsListError(404, "This session has no folder")
    needle = (q or "").strip().replace("\\", "/")[:QUERY_MAX_CHARS].casefold()
    count = min(max(limit if limit is not None else LIMIT_DEFAULT, 1), LIMIT_MAX)
    try:
        index = await asyncio.wait_for(_index_for(cwd), timeout=REPLY_TIMEOUT_SECONDS)
        loop = asyncio.get_running_loop()
        entries = await asyncio.wait_for(
            loop.run_in_executor(_rank_executor, rank, index.entries, needle, count),
            timeout=REPLY_TIMEOUT_SECONDS,
        )
    # Before OSError: TimeoutError subclasses it.
    except TimeoutError:
        raise FsListError(503, "Listing the session's files timed out") from None
    except (FileNotFoundError, NotADirectoryError):
        raise FsListError(404, "The session folder no longer exists") from None
    except PermissionError:
        raise FsListError(403, "Permission denied for the session folder") from None
    except OSError as exc:
        raise FsListError(404, f"Cannot list the session folder: {exc}") from None
    return SessionFilesResponse(
        entries=[SessionFileEntry(path=path, dir=is_dir) for path, is_dir in entries],
        truncated=index.truncated,
    )


async def _index_for(cwd: str) -> _Index:
    now = time.monotonic()
    for key in [key for key, index in _cache.items() if now - index.built_at > CACHE_TTL_SECONDS]:
        del _cache[key]
    hit = _cache.get(cwd)
    if hit is not None:
        _cache.move_to_end(cwd)
        return hit
    pending = _inflight.get(cwd)
    if pending is None:
        pending = asyncio.get_running_loop().run_in_executor(_executor, build_index, cwd)
        _inflight[cwd] = pending
        pending.add_done_callback(functools.partial(_store, cwd))
    # Shielded: a caller that times out leaves the listing to finish and fill
    # the cache for the next keystroke.
    return await asyncio.shield(pending)


def _store(cwd: str, done: asyncio.Future[_Index]) -> None:
    if _inflight.get(cwd) is done:
        del _inflight[cwd]
    if done.cancelled() or done.exception() is not None:
        return
    _cache[cwd] = done.result()
    _cache.move_to_end(cwd)
    while len(_cache) > CACHE_MAX_FOLDERS:
        _cache.popitem(last=False)


def build_index(root: str) -> _Index:
    """Blocking: the folder's files and folders, capped."""
    listed = _git_files(root)
    if listed is None:
        files, dirs, truncated = _walk(root)
    else:
        files, nested, truncated = listed
        dirs = list(set(_parent_dirs([*files, *nested])) | nested)
    entries: list[_Entry] = []
    for paths, is_dir in ((files, False), (dirs, True)):
        for path in paths:
            folded = path.casefold()
            if folded == path:
                folded = path
            entries.append((path, is_dir, folded, folded.rfind("/") + 1))
    return _Index(tuple(entries), truncated, time.monotonic())


def _git_files(root: str) -> tuple[list[str], set[str], bool] | None:
    """``(files, folders, truncated)``; the folders are nested repositories,
    which git lists with a trailing ``/``. ``None`` outside a git work tree,
    or when git cannot answer at all."""
    binary = git_binary()
    if binary is None:
        return None
    started = time.monotonic()
    try:
        result = _ls_files(binary, root, _LS_FILES_ARGS, GIT_TIMEOUT_SECONDS)
    except OSError:
        return None
    truncated = result.truncated or result.timed_out
    if not truncated and result.rc != 0:
        return None
    if truncated and not result.stdout:
        return None
    names = result.stdout.split(b"\0")
    # The last name is cut short when a cap stopped the output.
    names.pop()
    seen: set[str] = set()
    files: list[str] = []
    nested: set[str] = set()
    for raw in names:
        if not raw:
            continue
        path = raw.decode("utf-8", errors="replace")
        if path.endswith("/"):
            nested.add(path.rstrip("/"))
            continue
        # An unmerged path is listed once per merge stage.
        if path in seen:
            continue
        seen.add(path)
        files.append(path)
        if len(files) >= MAX_ENTRIES:
            truncated = True
            break
    if not files and not nested and not truncated:
        # Everything under the folder is ignored (a dotfiles repo in the home
        # folder that ignores "*"): the walk lists what the user works on.
        return None
    # Both git calls share one budget, so a build stays under the reply cap;
    # a list that is cut short anyway is not checked for deleted files.
    remaining = GIT_TIMEOUT_SECONDS - (time.monotonic() - started)
    if not truncated and remaining >= _DELETED_MIN_SECONDS:
        files = _without_deleted(binary, root, files, remaining)
    return files, nested, truncated


def _ls_files(binary: str, root: str, args: tuple[str, ...], timeout: float) -> CmdResult:
    return run_capped(
        [binary, *GIT_ARGV_PREFIX, *args],
        cwd=root,
        timeout=timeout,
        max_bytes=GIT_MAX_BYTES,
        env=git_env(),
    )


def _without_deleted(binary: str, root: str, files: list[str], timeout: float) -> list[str]:
    """``--cached`` still lists a tracked file deleted from the work tree, and
    a mention of it would be dropped; the list as it is when git cannot say."""
    try:
        result = _ls_files(binary, root, _LS_DELETED_ARGS, timeout)
    except OSError:
        return files
    if result.rc != 0 or result.truncated or result.timed_out or not result.stdout:
        return files
    deleted = {raw.decode("utf-8", errors="replace") for raw in result.stdout.split(b"\0") if raw}
    return [path for path in files if path not in deleted]


def _walk(root: str) -> tuple[list[str], list[str], bool]:
    deadline = time.monotonic() + WALK_BUDGET_SECONDS
    files: list[str] = []
    dirs: list[str] = []
    pending: deque[str] = deque([""])
    while pending:
        rel = pending.popleft()
        try:
            with os.scandir(os.path.join(root, rel) if rel else root) as it:
                for entry in it:
                    name = entry.name
                    path = f"{rel}/{name}" if rel else name
                    try:
                        path.encode("utf-8")
                    except UnicodeEncodeError:
                        continue
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        is_dir = False
                    if not is_dir:
                        files.append(path)
                    elif not name.startswith(".") and name not in _SKIPPED_DIRS:
                        dirs.append(path)
                        pending.append(path)
                    if len(files) + len(dirs) >= MAX_ENTRIES or time.monotonic() >= deadline:
                        return files, dirs, True
        except OSError:
            if not rel:
                raise
    return files, dirs, False


def _parent_dirs(files: list[str]) -> list[str]:
    dirs: set[str] = set()
    for path in files:
        cut = path.rfind("/")
        while cut > 0:
            parent = path[:cut]
            if parent in dirs:
                break
            dirs.add(parent)
            cut = path.rfind("/", 0, cut)
    return list(dirs)


def rank(entries: tuple[_Entry, ...], needle: str, limit: int) -> list[tuple[str, bool]]:
    """Best first. With no query: shallow paths first, dot names last, then
    by name. With one:
    name prefix, name substring, path prefix, path substring, then the query's
    characters in order anywhere in the path; shorter paths win ties."""
    if not needle:
        best = heapq.nsmallest(
            limit, entries, key=lambda e: (e[2].count("/"), e[2].startswith(".", e[3]), e[2])
        )
        return [(path, is_dir) for path, is_dir, _, _ in best]
    ranked = heapq.nsmallest(limit, _matches(entries, needle))
    return [(path, is_dir) for _, _, _, path, is_dir in ranked]


def _matches(entries: tuple[_Entry, ...], needle: str) -> Iterator[tuple[int, int, str, str, bool]]:
    for path, is_dir, folded, name_at in entries:
        tier = _tier(folded, name_at, needle)
        if tier is not None:
            yield tier, len(folded), folded, path, is_dir


def _tier(folded: str, name_at: int, needle: str) -> int | None:
    if folded.startswith(needle, name_at):
        return 0
    if folded.find(needle, name_at) >= 0:
        return 1
    if folded.startswith(needle):
        return 2
    if needle in folded:
        return 3
    pos = 0
    for ch in needle:
        pos = folded.find(ch, pos)
        if pos < 0:
            return None
        pos += 1
    return 4
