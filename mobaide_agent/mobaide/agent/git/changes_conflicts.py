"""Session-scoped conflict resolution — read a conflicted file, write its
resolution, take one side whole, or rebuild the conflict — shared by the HTTP
router and the relay dispatcher.

Every route acts on exactly one path, and only on a path whose index entry is
unmerged *under that exact name*: a literal pathspec still matches by
directory prefix, so the stages are filtered byte for byte. That check is what
bounds the write route — it can only ever replace a file git itself reports
as conflicted — and the file is reached without following a symlink at any
level. ``ours`` is index stage 2 and ``theirs`` stage 3; during a rebase those
are the branch being rebased onto and the commit being replayed, which is why
the labels are computed here instead of being left to the word "ours".

The text travels with LF line endings and no BOM; the file's own convention
is read back from the bytes on disk when the resolution is written, so a
CRLF file stays CRLF (``core.safecrlf`` would otherwise refuse the ``add``).
``token`` is the hash of the file as the client read it: a write is refused
once the file has moved on, because Claude or the user may be editing the
same file in the session's terminal. The one exception is a file that already
holds the requested bytes — a retry of a write whose answer was lost.

``git checkout --merge`` rebuilds a conflict from the index — from the
unmerged stages, or from the resolve-undo record of a path already marked
resolved. On any other path it silently overwrites the working file with the
index copy, so the precondition in :func:`restore_session_conflict` is a
safety check, not a courtesy. Git labels the rebuilt markers ``ours`` /
``theirs``, which during a rebase reads backwards, so the restore relabels
them with the sides' names — best effort, and clients never rely on the
label text either way.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import re
import secrets
import stat as statmod
import sys
import time
from collections.abc import Generator, Sequence
from dataclasses import dataclass
from typing import Any

from mobaide.core.git_exec import run_git

from .changes_models import (
    EDIT_BYTE_LIMIT,
    GitConflictResolveRequest,
    GitConflictResponse,
    GitConflictRestoreRequest,
    GitConflictSide,
    GitConflictTakeRequest,
    GitMutationResponse,
    RepoState,
)
from .changes_mutations import (
    _MARKER_RE,
    _PATHSPEC_FROM_STDIN,
    _pathspec_stdin,
    _publish,
    _run,
    _writable_repo,
)
from .changes_service import (
    _QUICK_TIMEOUT,
    _SMALL_OUTPUT,
    GitBadRequestError,
    GitCommandError,
    GitRefusedError,
    _is_gitlink,
    _lock_for,
    _resolve_undo_raw,
    _Stage,
    _text,
    operation_state,
    rebase_info,
    resolve_repo,
    restorable_paths,
    validate_repo_path,
)

__all__ = [
    "get_session_conflict",
    "resolve_session_conflict",
    "restore_session_conflict",
    "take_session_conflict",
]

# The relay gives a proxied request 30 s; every git call of one request draws
# on this single budget.
_REQUEST_BUDGET = 25.0
_LABEL_LIMIT = 200
_SYMLINK_MODE = "120000"
_BOM = b"\xef\xbb\xbf"
_START_RE = re.compile(rb"^<{7,}(?:\s|$)")
_BASE_RE = re.compile(rb"^\|{7,}(?:\s|$)")
_REPLACE_RETRIES = 3
_REPLACE_RETRY_DELAY = 0.1
_O_BINARY = getattr(os, "O_BINARY", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
# ``os.replace`` takes ``src_dir_fd``/``dst_dir_fd`` but is never listed in
# ``os.supports_dir_fd`` — CPython registers ``renameat`` under ``os.rename``.
_DIR_FD = (
    sys.platform != "win32"
    and hasattr(os, "O_DIRECTORY")
    and {os.open, os.stat, os.unlink, os.rename} <= os.supports_dir_fd
)

# Stage sets as porcelain v2 spells them (1 = base, 2 = ours, 3 = theirs).
_CODES = {
    frozenset({1, 2, 3}): "UU",
    frozenset({2, 3}): "AA",
    frozenset({1, 3}): "DU",
    frozenset({1, 2}): "UD",
    frozenset({1}): "DD",
    frozenset({2}): "AU",
    frozenset({3}): "UA",
}


class _Deadline:
    def __init__(self, budget: float = _REQUEST_BUDGET) -> None:
        self._end = time.monotonic() + budget

    def timeout(self, ceiling: float) -> float:
        return max(0.5, min(ceiling, self._end - time.monotonic()))


@dataclass(frozen=True)
class _Target:
    """The file's parent directory, reached without following a symlink:
    an open descriptor where the platform has ``dir_fd``, a checked path
    elsewhere."""

    dir_fd: int | None
    parent: str
    leaf: str

    def _at(self, name: str) -> tuple[str, dict[str, int]]:
        if self.dir_fd is not None:
            return name, {"dir_fd": self.dir_fd}
        return os.path.join(self.parent, name), {}

    def lstat(self) -> os.stat_result:
        path, kw = self._at(self.leaf)
        return os.stat(path, follow_symlinks=False, **kw)

    def open(self, name: str, flags: int, mode: int = 0o600) -> int:
        path, kw = self._at(name)
        return os.open(path, flags | _O_BINARY | _O_NOFOLLOW | _O_CLOEXEC, mode, **kw)

    def unlink(self, name: str) -> None:
        path, kw = self._at(name)
        os.unlink(path, **kw)

    def replace(self, src: str) -> None:
        if self.dir_fd is not None:
            os.replace(src, self.leaf, src_dir_fd=self.dir_fd, dst_dir_fd=self.dir_fd)
        else:
            os.replace(os.path.join(self.parent, src), os.path.join(self.parent, self.leaf))


@dataclass(frozen=True)
class _FileRead:
    kind: str
    data: bytes | None = None
    mode: int = 0o644


def _parts(rel: str) -> list[str]:
    parts = rel.split("/")
    if any(part.casefold() == ".git" for part in parts):
        raise GitBadRequestError("bad_path")
    return parts


@contextlib.contextmanager
def _open_target(toplevel: str, rel: str) -> Generator[_Target | None, None, None]:
    """Yields None when a directory on the way is missing, a symlink or not a
    directory — git never tracks a path beyond a symlink."""
    parts = _parts(rel)
    if not _DIR_FD:
        parent = os.path.join(toplevel, *parts[:-1])
        real_root = os.path.realpath(toplevel)
        expected = os.path.join(real_root, *parts[:-1])
        if not os.path.isdir(parent) or os.path.normcase(
            os.path.realpath(parent)
        ) != os.path.normcase(expected):
            yield None
            return
        yield _Target(None, parent, parts[-1])
        return
    flags = os.O_RDONLY | os.O_DIRECTORY | _O_CLOEXEC
    try:
        fd = os.open(toplevel, flags)
    except OSError:
        yield None
        return
    try:
        for part in parts[:-1]:
            try:
                nxt = os.open(part, flags | _O_NOFOLLOW, dir_fd=fd)
            except OSError:
                yield None
                return
            os.close(fd)
            fd = nxt
        yield _Target(fd, os.path.join(toplevel, *parts[:-1]), parts[-1])
    finally:
        os.close(fd)


def _read_target(target: _Target | None) -> _FileRead:
    if target is None:
        return _FileRead("missing")
    try:
        st = target.lstat()
    except FileNotFoundError:
        return _FileRead("missing")
    except OSError:
        return _FileRead("unsupported")
    if statmod.S_ISLNK(st.st_mode):
        return _FileRead("symlink")
    if not statmod.S_ISREG(st.st_mode):
        return _FileRead("unsupported")
    if st.st_size > EDIT_BYTE_LIMIT:
        return _FileRead("too_large")
    try:
        fd = target.open(target.leaf, os.O_RDONLY)
    except OSError:
        return _FileRead("unsupported")
    with os.fdopen(fd, "rb") as fh:
        opened = os.fstat(fh.fileno())
        if not statmod.S_ISREG(opened.st_mode):
            return _FileRead("unsupported")
        data = fh.read(EDIT_BYTE_LIMIT + 1)
    if len(data) > EDIT_BYTE_LIMIT:
        return _FileRead("too_large")
    return _FileRead("text", data, statmod.S_IMODE(opened.st_mode))


def _read_file(toplevel: str, rel: str) -> _FileRead:
    with _open_target(toplevel, rel) as target:
        return _read_target(target)


def _token(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _lines_of(data: bytes) -> list[bytes]:
    return data.split(b"\n")


def _has_markers(data: bytes) -> bool:
    return any(_MARKER_RE.match(line) for line in _lines_of(data))


def _decode(data: bytes) -> str | None:
    """LF text without a BOM, or None when the bytes are not UTF-8."""
    body = data[len(_BOM):] if data.startswith(_BOM) else data
    try:
        return body.decode("utf-8").replace("\r\n", "\n")
    except UnicodeDecodeError:
        return None


def _encode_like(original: bytes, content: bytes) -> bytes:
    """``content`` (LF, no BOM) in the line-ending and BOM convention of the
    file it replaces; a file mixing both endings gets its dominant one."""
    crlf = original.count(b"\r\n")
    if crlf > original.count(b"\n") - crlf:
        content = content.replace(b"\n", b"\r\n")
    if original.startswith(_BOM):
        content = _BOM + content
    return content


def _content_bytes(content: str) -> bytes:
    try:
        data = content.replace("\r\n", "\n").encode("utf-8")
    except UnicodeEncodeError as exc:
        raise GitBadRequestError("bad_content") from exc
    if b"\0" in data:
        raise GitBadRequestError("bad_content")
    if len(data) > EDIT_BYTE_LIMIT:
        raise GitBadRequestError("too_large")
    return data


def _replace_with_retry(target: _Target, tmp: str) -> None:
    """Windows refuses to replace a file another process holds open without
    share-delete (an editor, an indexer); that usually clears within a beat."""
    for attempt in range(_REPLACE_RETRIES):
        try:
            target.replace(tmp)
            return
        except PermissionError:
            if attempt == _REPLACE_RETRIES - 1:
                raise
            time.sleep(_REPLACE_RETRY_DELAY)


def _write_resolution(toplevel: str, rel: str, token: str, content: bytes) -> None:
    """The client's LF text, in the file's own line-ending and BOM convention."""
    _replace_file(toplevel, rel, token, content, encode=True)


def _replace_file(
    toplevel: str, rel: str, token: str, content: bytes, *, encode: bool = False
) -> None:
    """Replace the conflicted file atomically, provided it is still the file
    ``token`` was taken from. The temporary sits next to it so the rename
    never crosses a filesystem, and takes over its permission bits."""
    with _open_target(toplevel, rel) as target:
        current = _read_target(target)
        if target is None or current.data is None:
            raise GitRefusedError("file_changed", rel)
        data = _encode_like(current.data, content) if encode else content
        # A retry after a lost answer or a failed ``git add`` still carries the
        # token from before its own write: the file already holds the text.
        if current.data == data:
            return
        if _token(current.data) != token:
            raise GitRefusedError("file_changed", rel)
        tmp = f".mobaide-resolve-{secrets.token_hex(6)}.tmp"
        try:
            fd = target.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                    if hasattr(os, "fchmod"):
                        os.fchmod(fh.fileno(), current.mode)
                _replace_with_retry(target, tmp)
            except BaseException:
                with contextlib.suppress(OSError):
                    target.unlink(tmp)
                raise
        except PermissionError as exc:
            raise GitRefusedError("file_busy", rel) from exc
        except OSError as exc:
            raise GitCommandError(f"could not write {rel}: {exc.strerror or exc}") from exc


def _marker_label(label: str | None) -> bytes | None:
    """A side's label as one marker-safe line, or None to keep git's word."""
    if not label:
        return None
    clean = " ".join(label.split())
    return clean.encode("utf-8") or None


def _relabel_markers(
    toplevel: str, rel: str, ours: str | None, theirs: str | None
) -> None:
    """Swap the ``ours`` / ``theirs`` labels ``git checkout --merge`` writes
    for the sides' names. Cosmetic: any failure leaves git's labels."""
    swaps = {
        b"<<<<<<< ours": _marker_label(ours),
        b">>>>>>> theirs": _marker_label(theirs),
    }
    current = _read_file(toplevel, rel)
    if current.data is None:
        return
    lines = current.data.split(b"\n")
    changed = False
    for index, line in enumerate(lines):
        body = line[:-1] if line.endswith(b"\r") else line
        label = swaps.get(body)
        if label is None:
            continue
        marker = body.split(b" ", 1)[0]
        lines[index] = marker + b" " + label + line[len(body):]
        changed = True
    if not changed:
        return
    with contextlib.suppress(GitRefusedError, GitCommandError, OSError):
        _replace_file(toplevel, rel, _token(current.data), b"\n".join(lines))


def _check_token(toplevel: str, rel: str, token: str) -> None:
    current = _read_file(toplevel, rel)
    if current.data is None or _token(current.data) != token:
        raise GitRefusedError("file_changed", rel)


async def _git(
    args: Sequence[str],
    *,
    toplevel: str,
    deadline: _Deadline,
    fallback: str,
    ceiling: float = 15.0,
    stdin: bytes | None = None,
) -> None:
    await _run(
        args,
        toplevel=toplevel,
        fallback=fallback,
        stdin=stdin,
        timeout=deadline.timeout(ceiling),
    )


async def _conflict_stages(
    toplevel: str, rel: str, deadline: _Deadline
) -> dict[int, _Stage]:
    """The unmerged index stages of exactly ``rel``; empty when the path is
    not in conflict. ``ls-files -- src`` also lists ``src/a``, hence the
    byte comparison."""
    result = await _run(
        ["ls-files", "-u", "-z", "--", rel],
        toplevel=toplevel,
        fallback="git ls-files failed",
        timeout=deadline.timeout(_QUICK_TIMEOUT),
    )
    wanted = rel.encode("utf-8")
    stages: dict[int, _Stage] = {}
    for record in result.stdout.split(b"\0"):
        meta, tab, path = record.partition(b"\t")
        fields = meta.split(b" ")
        if not tab or path != wanted or len(fields) != 3 or not fields[2].isdigit():
            continue
        stages[int(fields[2])] = _Stage(_text(fields[0]), _text(fields[1]))
    return stages


def _require_conflict(stages: dict[int, _Stage], rel: str) -> None:
    if not stages:
        raise GitRefusedError("not_conflicted", rel)


def _is_symlink(stages: dict[int, _Stage]) -> bool:
    return any(stage.mode == _SYMLINK_MODE for stage in stages.values())


async def _commit_label(toplevel: str, ref: str, deadline: _Deadline) -> str | None:
    result = await run_git(
        [
            "log", "-1", "--no-color", "--no-show-signature",
            "--format=%h %s", "--end-of-options", ref,
        ],
        cwd=toplevel,
        timeout=deadline.timeout(_QUICK_TIMEOUT),
        max_bytes=_SMALL_OUTPUT,
    )
    if result.timed_out or result.rc != 0:
        return None
    return _text(result.stdout).strip()[:_LABEL_LIMIT] or None


async def _head_label(toplevel: str, deadline: _Deadline) -> str | None:
    result = await run_git(
        ["symbolic-ref", "--short", "-q", "HEAD"],
        cwd=toplevel,
        timeout=deadline.timeout(_QUICK_TIMEOUT),
        max_bytes=_SMALL_OUTPUT,
    )
    if result.timed_out or result.rc != 0:
        return None
    return _text(result.stdout).strip()[:_LABEL_LIMIT] or None


async def _side_labels(
    toplevel: str, state: RepoState, rebase_dir: str | None, deadline: _Deadline
) -> tuple[str | None, str | None]:
    if state == "rebasing" and rebase_dir:
        info = await rebase_info(toplevel, rebase_dir)
        if info is None:
            return None, None
        theirs = (
            f"{info.stopped_on.short_oid} {info.stopped_on.subject}"[:_LABEL_LIMIT]
            if info.stopped_on
            else None
        )
        return info.onto_ref or info.onto_short_oid, theirs
    if state == "merging":
        return (
            await _head_label(toplevel, deadline),
            await _commit_label(toplevel, "MERGE_HEAD", deadline),
        )
    return None, None


async def get_session_conflict(*, cwd: str | None, path: str | None) -> GitConflictResponse:
    rel = validate_repo_path(path)
    _parts(rel)
    probe = await resolve_repo(cwd)
    if probe.repo is None:
        raise GitBadRequestError(probe.reason or "not_a_repo")
    toplevel = probe.repo.toplevel
    deadline = _Deadline()
    stages = await _conflict_stages(toplevel, rel, deadline)
    _require_conflict(stages, rel)
    state, rebase_dir = await operation_state(toplevel)
    ours_label, theirs_label = await _side_labels(toplevel, state, rebase_dir, deadline)
    response = GitConflictResponse(
        path=rel,
        code=_CODES.get(frozenset(stages), "UU"),
        state=state,
        kind="submodule",
        ours=GitConflictSide(label=ours_label, present=2 in stages),
        theirs=GitConflictSide(label=theirs_label, present=3 in stages),
    )
    if _is_gitlink(stages):
        return response
    if _is_symlink(stages):
        response.kind = "symlink"
        return response
    read = await asyncio.to_thread(_read_file, toplevel, rel)
    response.kind = read.kind
    if read.data is None:
        return response
    if b"\0" in read.data:
        response.kind = "binary"
        return response
    content = _decode(read.data)
    if content is None:
        response.kind = "encoding"
        return response
    lines = _lines_of(read.data)
    response.content = content
    response.token = _token(read.data)
    response.markers = sum(1 for line in lines if _START_RE.match(line))
    response.has_base = any(_BASE_RE.match(line) for line in lines)
    return response


async def resolve_session_conflict(
    *,
    cwd: str | None,
    request: GitConflictResolveRequest,
    project_name: str | None,
    event_bus: Any | None,
) -> GitMutationResponse:
    """Write the user's text over the conflicted file and, unless it is a
    draft, mark the path resolved. A draft may keep its markers; a resolution
    may not."""
    rel = validate_repo_path(request.path)
    _parts(rel)
    content = _content_bytes(request.content)
    if request.mark_resolved and _has_markers(content):
        raise GitRefusedError("conflict_markers", rel)
    repo = await _writable_repo(cwd)
    deadline = _Deadline()
    async with _lock_for(repo.toplevel):
        stages = await _conflict_stages(repo.toplevel, rel, deadline)
        _require_conflict(stages, rel)
        if _is_gitlink(stages) or _is_symlink(stages):
            raise GitRefusedError("unsupported", rel)
        await asyncio.to_thread(
            _write_resolution, repo.toplevel, rel, request.token, content
        )
        try:
            if request.mark_resolved:
                await _git(
                    ["add", "-A", *_PATHSPEC_FROM_STDIN],
                    toplevel=repo.toplevel,
                    deadline=deadline,
                    fallback="git add failed",
                    stdin=_pathspec_stdin([rel]),
                )
        finally:
            # the file is already rewritten, whatever ``add`` answers
            _publish(event_bus, project_name, repo)
    return GitMutationResponse()


def _parent_unsafe(toplevel: str, rel: str) -> bool:
    """A symlink or a non-directory where one of the file's parent
    directories should be. A parent that is simply missing is not unsafe:
    a side that deleted the file may have taken its directory along."""
    current = toplevel
    for part in _parts(rel)[:-1]:
        current = os.path.join(current, part)
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            return False
        except OSError:
            return True
        if not statmod.S_ISDIR(mode):
            return True
    return False


def _unwalkable(toplevel: str, rel: str) -> bool:
    """The no-follow walk refused the parent, yet the file is there by path:
    something on the way is not a plain directory this agent may enter (a
    directory junction, an unreadable directory). A parent that is simply
    missing leaves nothing at the path and is fine. Only "not found" counts
    as missing: ``os.path.lexists`` would also read a parent without search
    permission as nothing there."""
    try:
        os.lstat(os.path.join(toplevel, *_parts(rel)))
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True
    return True


def _take_blocked(toplevel: str, rel: str) -> bool:
    """Whether a whole-file pick must be refused: git would act through a
    symlinked parent, the walk cannot reach a file that exists, or a
    directory sits where the file should be."""
    if _parent_unsafe(toplevel, rel):
        return True
    with _open_target(toplevel, rel) as target:
        if target is None:
            return _unwalkable(toplevel, rel)
        try:
            return statmod.S_ISDIR(target.lstat().st_mode)
        except OSError:
            return False


def _remove_worktree_file(toplevel: str, rel: str) -> None:
    """Unlink the working-tree file through the no-follow walk. ``git rm``
    is not used for this: it unlinks by path and follows a symlinked parent
    directory out of the repository."""
    with _open_target(toplevel, rel) as target:
        if target is None:
            # never drop the index entry of a file that was left on disk
            if _unwalkable(toplevel, rel):
                raise GitRefusedError("unsupported", rel)
            return
        try:
            if statmod.S_ISDIR(target.lstat().st_mode):
                raise GitRefusedError("unsupported", rel)
            target.unlink(target.leaf)
        except FileNotFoundError:
            return
        except PermissionError as exc:
            raise GitRefusedError("file_busy", rel) from exc
        except OSError as exc:
            raise GitCommandError(f"could not remove {rel}: {exc.strerror or exc}") from exc


async def take_session_conflict(
    *,
    cwd: str | None,
    request: GitConflictTakeRequest,
    project_name: str | None,
    event_bus: Any | None,
) -> GitMutationResponse:
    """Make the path look like one side and mark it resolved. Which command
    that takes is read from the index stages: a side that deleted the file
    (or never had it) resolves by removal, since ``checkout --ours`` has no
    version to check out. The removal unlinks the file itself and only then
    drops the index entry with ``git rm --cached``, which never touches the
    working tree."""
    rel = validate_repo_path(request.path)
    _parts(rel)
    repo = await _writable_repo(cwd)
    toplevel = repo.toplevel
    deadline = _Deadline()
    async with _lock_for(toplevel):
        stages = await _conflict_stages(toplevel, rel, deadline)
        _require_conflict(stages, rel)
        if _is_gitlink(stages) or await asyncio.to_thread(_take_blocked, toplevel, rel):
            raise GitRefusedError("unsupported", rel)
        try:
            if (2 if request.side == "ours" else 3) in stages:
                await _git(
                    ["checkout", f"--{request.side}", "--", rel],
                    toplevel=toplevel,
                    deadline=deadline,
                    fallback="git checkout failed",
                )
                await _git(
                    ["add", "-A", *_PATHSPEC_FROM_STDIN],
                    toplevel=toplevel,
                    deadline=deadline,
                    fallback="git add failed",
                    stdin=_pathspec_stdin([rel]),
                )
            else:
                await asyncio.to_thread(_remove_worktree_file, toplevel, rel)
                await _git(
                    ["rm", "--cached", "-q", "-f", "--", rel],
                    toplevel=toplevel,
                    deadline=deadline,
                    fallback="git rm failed",
                )
        finally:
            _publish(event_bus, project_name, repo)
    return GitMutationResponse()


async def _restorable(
    toplevel: str, rel: str, stages: dict[int, _Stage]
) -> bool:
    if stages:
        return 2 in stages and 3 in stages and not _is_gitlink(stages)
    state, _ = await operation_state(toplevel)
    if state == "clean":
        return False
    raw = await _resolve_undo_raw(toplevel)
    return rel in await restorable_paths(toplevel, raw, unmerged=set())


async def restore_session_conflict(
    *,
    cwd: str | None,
    request: GitConflictRestoreRequest,
    project_name: str | None,
    event_bus: Any | None,
) -> GitMutationResponse:
    """``git checkout --merge`` for one path: rebuilds the markers of a file
    still in conflict (discarding edits made to it) or of one resolved during
    the operation in progress. ``--conflict`` is always explicit so a
    configured ``merge.conflictStyle`` never decides whether the original
    lines appear."""
    rel = validate_repo_path(request.path)
    _parts(rel)
    repo = await _writable_repo(cwd)
    toplevel = repo.toplevel
    deadline = _Deadline()
    async with _lock_for(toplevel):
        stages = await _conflict_stages(toplevel, rel, deadline)
        if not await _restorable(toplevel, rel, stages):
            raise GitRefusedError("not_restorable", rel)
        if request.token is not None:
            await asyncio.to_thread(_check_token, toplevel, rel, request.token)
        try:
            await _git(
                ["checkout", "--merge", f"--conflict={request.style}", "--", rel],
                toplevel=toplevel,
                deadline=deadline,
                fallback="git checkout --merge failed",
            )
            # The conflict is rebuilt; naming its sides must not turn that
            # into a failure.
            with contextlib.suppress(Exception):
                state, rebase_dir = await operation_state(toplevel)
                ours, theirs = await _side_labels(toplevel, state, rebase_dir, deadline)
                await asyncio.to_thread(_relabel_markers, toplevel, rel, ours, theirs)
        finally:
            _publish(event_bus, project_name, repo)
    return GitMutationResponse()
