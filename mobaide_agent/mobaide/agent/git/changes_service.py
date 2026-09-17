"""Session-scoped read-only git status/diff — pure logic shared by the HTTP
router and the relay dispatcher.

Runs every git command through :mod:`mobaide.core.git_exec` (timeouts, byte
caps, scrubbed env, ``--literal-pathspecs``) and never takes a lock:
``GIT_OPTIONAL_LOCKS=0`` is what keeps a status poll from racing the user's
own git in the PTY. Errors are raised as exceptions and mapped to HTTP by
:func:`http_error_for` so both transports answer byte-identically; the
mutating routes in :mod:`.changes_mutations` share the same vocabulary.
"""
from __future__ import annotations

import asyncio
import hashlib
import heapq
import os
import posixpath
import re
import stat as statmod
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from mobaide.core.git_exec import (
    MIN_GIT_VERSION,
    CmdResult,
    GitCwdMissingError,
    GitUnavailableError,
    git_version,
    run_git,
)

from .changes_models import (
    BYTE_LIMIT,
    ENTRY_LIMIT,
    LINE_LIMIT,
    DiffChange,
    DiffScope,
    GitDiffHunk,
    GitDiffLine,
    GitDiffResponse,
    GitHead,
    GitRebaseCommit,
    GitRebaseInfo,
    GitRepoInfo,
    GitStatusCounts,
    GitStatusEntry,
    GitStatusResponse,
    GitUpstream,
    RepoState,
    UnavailableReason,
)

_QUICK_TIMEOUT = 5.0
_STATUS_TIMEOUT = 15.0
_DIFF_TIMEOUT = 15.0
_STATUS_MAX_BYTES = 8 * 1024 * 1024
_SMALL_OUTPUT = 64 * 1024
_BINARY_SNIFF = 8000
_MAX_ERROR_DETAIL = 500
_CONTEXT_DEFAULT = 3
# At LINE_LIMIT context a file with more than LINE_LIMIT lines on either side
# already overflows the line cap, so an untruncated diff at this ceiling is
# always the whole file as one hunk — the clients' "Full file" view.
_CONTEXT_MAX = LINE_LIMIT

_DIFF_SCOPES: tuple[str, ...] = ("staged", "unstaged", "untracked")
_GROUP_RANK = {"conflicts": 0, "staged": 1, "unstaged": 2, "untracked": 3}
_HUNK_RE = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: (.*))?$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class GitChangesError(Exception):
    """Base for conditions the transports map to non-200 answers."""


class GitTimeoutError(GitChangesError):
    pass


class GitCommandError(GitChangesError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message[:_MAX_ERROR_DETAIL]


class GitBadRequestError(GitChangesError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class GitRefusedError(GitChangesError):
    """Git declined a mutation because of the repository's state (``409``):
    ``code`` is the client-facing vocabulary, ``message`` the trimmed output."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.message = message[:_MAX_ERROR_DETAIL]


class GitNotFoundError(GitChangesError):
    """A job id nobody knows (``404`` with a coded detail, unlike the string
    detail a missing session answers)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def http_error_for(exc: BaseException) -> tuple[int, dict[str, Any]] | None:
    """Map a service exception to ``(status, body)``; ``None`` if unrelated."""
    if isinstance(exc, GitUnavailableError):
        return 503, {"detail": {"code": "git_unavailable"}}
    if isinstance(exc, GitCwdMissingError):
        return 400, {"detail": {"code": "cwd_missing"}}
    if isinstance(exc, GitTimeoutError):
        return 504, {"detail": {"code": "git_timeout"}}
    if isinstance(exc, GitBadRequestError):
        return 400, {"detail": {"code": exc.code}}
    if isinstance(exc, GitNotFoundError):
        return 404, {"detail": {"code": exc.code}}
    if isinstance(exc, GitRefusedError):
        return 409, {"detail": {"code": exc.code, "message": exc.message}}
    if isinstance(exc, GitCommandError):
        return 500, {"detail": {"code": "git_error", "message": exc.message}}
    return None


@dataclass(frozen=True)
class ResolvedRepo:
    toplevel: str
    git_dir: str
    common_dir: str
    is_linked_worktree: bool


@dataclass(frozen=True)
class RepoProbe:
    reason: UnavailableReason | None
    repo: ResolvedRepo | None


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _lines(data: bytes) -> list[str]:
    """Split git output on ``\\n`` only: ``str.splitlines`` would also break
    on ``\\r``, ``\\x0b``, U+0085, U+2028 and friends, all legal path bytes."""
    lines = _text(data).split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _fs_path(base: str, raw: str) -> str:
    """Absolute, symlink-resolved path in the form git itself prints for
    ``--show-toplevel`` (forward slashes on Windows), so ``git_dir`` /
    ``common_dir`` — which git may print relative to the cwd — compare equal
    to it. On POSIX a backslash is an ordinary filename byte and is kept."""
    resolved = os.path.realpath(os.path.join(base, raw))
    if sys.platform == "win32":
        resolved = resolved.replace("\\", "/")
    return resolved


def _check_result(result: CmdResult) -> None:
    if result.timed_out:
        raise GitTimeoutError()


def _fail(result: CmdResult, fallback: str) -> GitCommandError:
    return GitCommandError(_text(result.stderr).strip() or fallback)


async def resolve_repo(cwd: str | None) -> RepoProbe:
    """Classify ``cwd`` and locate its repository without touching the index."""
    if not cwd or not os.path.isdir(cwd):
        return RepoProbe("cwd_missing", None)
    version = await git_version()
    if version is None:
        raise GitCommandError("could not determine git version")
    if version < MIN_GIT_VERSION:
        return RepoProbe("git_too_old", None)
    try:
        result = await run_git(
            [
                "rev-parse",
                "--is-bare-repository",
                "--is-inside-work-tree",
                "--show-toplevel",
                "--git-dir",
                "--git-common-dir",
            ],
            cwd=cwd,
            timeout=_QUICK_TIMEOUT,
            max_bytes=_SMALL_OUTPUT,
        )
    except GitCwdMissingError:
        return RepoProbe("cwd_missing", None)
    _check_result(result)
    lines = _lines(result.stdout)
    stderr = _text(result.stderr)
    if result.rc != 0:
        if "dubious ownership" in stderr:
            return RepoProbe("unsafe_directory", None)
        if lines and lines[0].strip() == "true":
            return RepoProbe("bare_repository", None)
        if "not a git repository" in stderr:
            return RepoProbe("not_a_repo", None)
        # inside ``.git`` itself: rev-parse prints ``false`` for the work-tree
        # probe and then fails on ``--show-toplevel``
        if len(lines) >= 2 and lines[1].strip() == "false":
            return RepoProbe("not_a_repo", None)
        raise _fail(result, "git rev-parse failed")
    if len(lines) != 5:
        # a path containing a newline would shift the frame: fail closed
        raise GitCommandError("unexpected git rev-parse output")
    # only the two boolean lines are trimmed: a path line may legitimately end
    # in whitespace and must survive byte-for-byte
    bare, inside = lines[0].strip(), lines[1].strip()
    toplevel, git_dir, common_dir = lines[2:5]
    if bare == "true":
        return RepoProbe("bare_repository", None)
    if inside != "true":
        return RepoProbe("not_a_repo", None)
    git_dir_abs = _fs_path(cwd, git_dir)
    common_abs = _fs_path(cwd, common_dir)
    return RepoProbe(
        None,
        ResolvedRepo(
            toplevel=_fs_path(cwd, toplevel),
            git_dir=git_dir_abs,
            common_dir=common_abs,
            is_linked_worktree=(
                os.path.normcase(git_dir_abs) != os.path.normcase(common_abs)
            ),
        ),
    )


# ``--pathspec-from-file`` reached ``reset`` in 2.26 (``add``/``restore``/
# ``commit`` had it since 2.25); the read routes keep their 2.15 floor and
# the fetch/pull/push jobs share this one.
MIN_GIT_VERSION_MUTATE: tuple[int, int, int] = (2, 26, 0)

_repo_locks: dict[str, asyncio.Lock] = {}


def _lock_for(toplevel: str) -> asyncio.Lock:
    """Per-toplevel lock shared by the mutations and the jobs so index and
    worktree writes never interleave (git's own ``index.lock`` would turn
    the loser into a spurious failure)."""
    key = os.path.normcase(toplevel)
    lock = _repo_locks.get(key)
    if lock is None:
        lock = _repo_locks[key] = asyncio.Lock()
    return lock


async def _mutable_repo(cwd: str | None) -> ResolvedRepo:
    """Resolve ``cwd`` for a write: repo conditions become ``409`` refusals
    (there is no partial answer to give) and the git floor is the mutation
    one."""
    probe = await resolve_repo(cwd)
    if probe.repo is None:
        reason = probe.reason or "not_a_repo"
        if reason == "cwd_missing":
            raise GitCwdMissingError(cwd or "")
        raise GitRefusedError(reason)
    version = await git_version()
    if version is None or version < MIN_GIT_VERSION_MUTATE:
        floor = ".".join(str(part) for part in MIN_GIT_VERSION_MUTATE)
        raise GitRefusedError("git_too_old", f"git {floor} or newer is required")
    return probe.repo


async def resolve_repo_dirs(cwd: str | None) -> tuple[str | None, str | None]:
    """Best-effort ``(toplevel, common_dir)`` for event payloads; never raises."""
    try:
        probe = await resolve_repo(cwd)
    except Exception:
        return None, None
    if probe.repo is None:
        return None, None
    return probe.repo.toplevel, probe.repo.common_dir


async def _ref_exists(toplevel: str, ref: str) -> bool:
    result = await run_git(
        ["rev-parse", "-q", "--verify", ref],
        cwd=toplevel,
        timeout=_QUICK_TIMEOUT,
        max_bytes=_SMALL_OUTPUT,
    )
    _check_result(result)
    return result.rc == 0


async def repo_state(toplevel: str) -> RepoState:
    """Per-worktree operation in progress. ``--git-path`` answers are relative
    to the cwd in the main worktree and absolute in linked ones, so they are
    joined against ``toplevel``; ``CHERRY_PICK_HEAD``/``REVERT_HEAD`` are
    checked as refs because reftable repos keep them out of the filesystem."""
    state, _ = await operation_state(toplevel)
    return state


async def operation_state(toplevel: str) -> tuple[RepoState, str | None]:
    """:func:`repo_state` plus the rebase state directory, set only while
    ``rebasing``."""
    result = await run_git(
        [
            "rev-parse",
            "--git-path", "MERGE_HEAD",
            "--git-path", "BISECT_LOG",
            "--git-path", "rebase-merge",
            "--git-path", "rebase-apply",
        ],
        cwd=toplevel,
        timeout=_QUICK_TIMEOUT,
        max_bytes=_SMALL_OUTPUT,
    )
    _check_result(result)
    if result.rc != 0:
        raise _fail(result, "git rev-parse --git-path failed")
    lines = _lines(result.stdout)
    if len(lines) != 4:
        raise GitCommandError("unexpected git rev-parse --git-path output")
    merge, bisect, rebase_merge, rebase_apply = (
        os.path.join(toplevel, p) for p in lines
    )
    for rebase_dir in (rebase_merge, rebase_apply):
        if os.path.exists(rebase_dir):
            return "rebasing", rebase_dir
    if os.path.exists(merge):
        return "merging", None
    if await _ref_exists(toplevel, "CHERRY_PICK_HEAD"):
        return "cherry_picking", None
    if await _ref_exists(toplevel, "REVERT_HEAD"):
        return "reverting", None
    if os.path.exists(bisect):
        return "bisecting", None
    return "clean", None


def _read_state_file(rebase_dir: str, name: str) -> str | None:
    try:
        with open(os.path.join(rebase_dir, name), "rb") as fh:
            value = _text(fh.read(_SMALL_OUTPUT)).strip()
    except OSError:
        return None
    return value or None


def _state_int(rebase_dir: str, name: str) -> int | None:
    value = _read_state_file(rebase_dir, name)
    return int(value) if value is not None and value.isdigit() else None


async def rebase_info(toplevel: str, rebase_dir: str) -> GitRebaseInfo | None:
    """Progress of the rebase whose state lives in ``rebase_dir``
    (``rebase-merge`` or ``rebase-apply``), or None for a ``git am`` session,
    which shares ``rebase-apply``. ``REBASE_HEAD`` is git's own record of
    the commit it stopped at; everything unreadable stays null."""
    merge_backend = os.path.basename(rebase_dir) == "rebase-merge"
    if not merge_backend and os.path.exists(os.path.join(rebase_dir, "applying")):
        return None
    head_name = _read_state_file(rebase_dir, "head-name")
    onto_oid = _read_state_file(rebase_dir, "onto")
    info = GitRebaseInfo(
        branch=head_name.removeprefix("refs/heads/")
        if head_name and head_name.startswith("refs/heads/")
        else None,
        onto_oid=onto_oid,
        step=_state_int(rebase_dir, "msgnum" if merge_backend else "next"),
        total=_state_int(rebase_dir, "end" if merge_backend else "last"),
    )
    revs = [onto_oid] if onto_oid else []
    commits = await run_git(
        [
            "log",
            "--no-walk=unsorted",
            "--ignore-missing",
            "--no-color",
            "--no-show-signature",
            "--format=%H%x00%h%x00%s",
            "--end-of-options",
            *revs,
            "REBASE_HEAD",
        ],
        cwd=toplevel,
        timeout=_QUICK_TIMEOUT,
        max_bytes=_SMALL_OUTPUT,
    )
    if commits.timed_out or commits.rc != 0:
        return info
    for line in _lines(commits.stdout):
        parts = line.split("\0", 2)
        if len(parts) != 3:
            continue
        oid, short_oid, subject = parts
        if oid == onto_oid and info.onto_short_oid is None:
            info.onto_short_oid = short_oid
        else:
            info.stopped_on = GitRebaseCommit(oid=oid, short_oid=short_oid, subject=subject)
    if onto_oid:
        info.onto_ref = await _ref_pointing_at(toplevel, onto_oid)
    return info


async def _ref_pointing_at(toplevel: str, oid: str) -> str | None:
    """A branch name for ``oid``: a local branch first, then a remote-tracking
    one (never a ``<remote>/HEAD`` symref)."""
    result = await run_git(
        [
            "for-each-ref",
            f"--points-at={oid}",
            "--format=%(refname)%00%(symref)",
            "refs/heads",
            "refs/remotes",
        ],
        cwd=toplevel,
        timeout=_QUICK_TIMEOUT,
        max_bytes=_SMALL_OUTPUT,
    )
    if result.timed_out or (result.rc != 0 and not result.truncated):
        return None
    lines = _text(result.stdout).split("\n")
    lines.pop()
    remote: str | None = None
    for line in lines:
        refname, _, symref = line.partition("\0")
        if symref:
            continue
        if refname.startswith("refs/heads/"):
            return refname.removeprefix("refs/heads/")
        if remote is None and refname.startswith("refs/remotes/"):
            remote = refname.removeprefix("refs/remotes/")
    return remote


@dataclass
class ParsedStatus:
    oid: str | None = None
    branch: str | None = None
    detached: bool = False
    initial: bool = False
    upstream: str | None = None
    ahead: int | None = None
    behind: int | None = None
    entries: list[GitStatusEntry] = field(default_factory=list)
    counts: GitStatusCounts = field(default_factory=GitStatusCounts)


class _RawEntry(NamedTuple):
    group: str
    path: str
    code: str
    index_status: str
    worktree_status: str
    orig_path: str | None = None
    rename_score: int | None = None
    submodule: bool = False

    def to_entry(self) -> GitStatusEntry:
        return GitStatusEntry(**self._asdict())


def _entry_rank(entry: _RawEntry) -> tuple[int, str]:
    return _GROUP_RANK[entry.group], entry.path


def _ordinary_entries(
    xy: str,
    sub: str,
    path: str,
    orig: str | None,
    score: int | None,
) -> list[_RawEntry]:
    if len(xy) < 2:
        return []
    x, y = xy[0], xy[1]
    submodule = sub.startswith("S")
    out: list[_RawEntry] = []
    if x != ".":
        renamed = x in "RC"
        out.append(
            _RawEntry(
                group="staged",
                path=path,
                orig_path=orig if renamed else None,
                code=x,
                index_status=x,
                worktree_status=y,
                rename_score=score if renamed else None,
                submodule=submodule,
            )
        )
    if y != ".":
        renamed = y in "RC"
        out.append(
            _RawEntry(
                group="unstaged",
                path=path,
                orig_path=orig if renamed else None,
                code=y,
                index_status=x,
                worktree_status=y,
                rename_score=score if renamed else None,
                submodule=submodule,
            )
        )
    return out


def _parse_ab(value: str) -> tuple[int | None, int | None]:
    match = re.match(r"^\+(\d+) -(\d+)$", value.strip())
    if match is None:
        return None, None
    return int(match.group(1)), int(match.group(2))


def parse_porcelain_v2(
    data: bytes, *, truncated: bool = False, limit: int | None = None
) -> ParsedStatus:
    """Parse ``git status --porcelain=v2 --branch -z`` output.

    Records are NUL-terminated; only ``2`` (rename/copy) records span two
    NUL fields (``<path>NUL<origPath>``), which is why this walks tokens
    with an index instead of mapping over ``split``. A truncated stream
    drops its partial tail — including a ``2`` record whose origin token
    was cut off. ``counts`` cover every entry; ``limit`` keeps only the
    smallest (group rank, path) entries through a bounded heap, because a
    ``_STATUS_MAX_BYTES`` status can hold hundreds of thousands of untracked
    paths and only ``ENTRY_LIMIT`` are ever sent.
    """
    parsed = ParsedStatus()
    tally = {group: 0 for group in _GROUP_RANK}
    raw_entries = _walk_porcelain_v2(data, truncated, parsed, tally)
    if limit is None:
        parsed.entries = [entry.to_entry() for entry in raw_entries]
    else:
        selected = heapq.nsmallest(limit, raw_entries, key=_entry_rank)
        # nsmallest(0, …) pulls nothing; the header and tally live on the walk
        for _ in raw_entries:
            pass
        parsed.entries = [entry.to_entry() for entry in selected]
    parsed.counts = GitStatusCounts(**tally, total=sum(tally.values()))
    return parsed


def _walk_porcelain_v2(
    data: bytes, truncated: bool, parsed: ParsedStatus, tally: dict[str, int]
) -> Iterator[_RawEntry]:
    tokens = data.split(b"\0")
    if tokens and tokens[-1] == b"":
        tokens.pop()
    elif truncated and tokens:
        tokens.pop()
    i = 0
    while i < len(tokens):
        line = _text(tokens[i])
        i += 1
        raw_entries: list[_RawEntry]
        if line.startswith("# "):
            key, _, value = line[2:].partition(" ")
            if key == "branch.oid":
                if value == "(initial)":
                    parsed.initial = True
                else:
                    parsed.oid = value
            elif key == "branch.head":
                if value == "(detached)":
                    parsed.detached = True
                else:
                    parsed.branch = value
            elif key == "branch.upstream":
                parsed.upstream = value
            elif key == "branch.ab":
                parsed.ahead, parsed.behind = _parse_ab(value)
            continue
        elif line.startswith("1 "):
            parts = line.split(" ", 8)
            if len(parts) < 9:
                continue
            raw_entries = _ordinary_entries(parts[1], parts[2], parts[8], None, None)
        elif line.startswith("2 "):
            parts = line.split(" ", 9)
            if len(parts) < 10:
                continue
            if i >= len(tokens):
                break
            orig = _text(tokens[i])
            i += 1
            score_field = parts[8]
            digits = score_field[1:]
            score = int(digits) if digits.isdigit() else None
            raw_entries = _ordinary_entries(parts[1], parts[2], parts[9], orig, score)
        elif line.startswith("u "):
            parts = line.split(" ", 10)
            if len(parts) < 11 or len(parts[1]) < 2:
                continue
            xy = parts[1]
            raw_entries = [
                _RawEntry(
                    group="conflicts",
                    path=parts[10],
                    code=xy,
                    index_status=xy[0],
                    worktree_status=xy[1],
                    submodule=parts[2].startswith("S"),
                )
            ]
        elif line.startswith("? "):
            raw_entries = [
                _RawEntry(
                    group="untracked",
                    path=line[2:],
                    code="?",
                    index_status="?",
                    worktree_status="?",
                )
            ]
        else:
            continue
        for entry in raw_entries:
            tally[entry.group] += 1
            yield entry


def _etag(*parts: bytes | str) -> str:
    digest = hashlib.sha1()
    for part in parts:
        digest.update(part if isinstance(part, bytes) else part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _unchanged(session_id: str, cwd: str | None, etag: str) -> GitStatusResponse:
    return GitStatusResponse(session_id=session_id, cwd=cwd, etag=etag, unchanged=True)


def _unavailable(
    session_id: str,
    cwd: str | None,
    reason: UnavailableReason,
    etag: str | None,
) -> GitStatusResponse:
    tag = _etag("unavailable", reason, cwd or "")
    if etag and etag == tag:
        return _unchanged(session_id, cwd, tag)
    return GitStatusResponse(
        session_id=session_id, cwd=cwd, etag=tag, unavailable_reason=reason
    )


async def get_session_git_status(
    *,
    session_id: str,
    cwd: str | None,
    etag: str | None,
) -> GitStatusResponse:
    probe = await resolve_repo(cwd)
    if probe.repo is None:
        return _unavailable(session_id, cwd, probe.reason or "not_a_repo", etag)

    repo = probe.repo
    try:
        state, rebase_dir = await operation_state(repo.toplevel)
        rebase = await rebase_info(repo.toplevel, rebase_dir) if rebase_dir else None
        result = await run_git(
            ["status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all"],
            cwd=repo.toplevel,
            timeout=_STATUS_TIMEOUT,
            max_bytes=_STATUS_MAX_BYTES,
        )
    except GitCwdMissingError:
        return _unavailable(session_id, cwd, "cwd_missing", etag)
    _check_result(result)
    if result.rc != 0 and not result.truncated:
        raise _fail(result, "git status failed")
    # Skipping a commit that conflicted in the same file as the next one
    # leaves HEAD and the porcelain identical, so the stop point is hashed too.
    tag = _etag(
        result.stdout,
        repo.toplevel,
        repo.git_dir,
        state,
        rebase.model_dump_json() if rebase else "",
    )
    if etag and etag == tag:
        return _unchanged(session_id, cwd, tag)

    parsed = await asyncio.to_thread(
        parse_porcelain_v2, result.stdout, truncated=result.truncated, limit=ENTRY_LIMIT
    )
    head = GitHead(
        oid=parsed.oid,
        short_oid=parsed.oid[:7] if parsed.oid else None,
        branch=parsed.branch,
        detached=parsed.detached,
        initial=parsed.initial,
    )
    upstream = (
        GitUpstream(ref=parsed.upstream, ahead=parsed.ahead, behind=parsed.behind)
        if parsed.upstream
        else None
    )
    return GitStatusResponse(
        session_id=session_id,
        cwd=cwd,
        etag=tag,
        repo=GitRepoInfo(
            toplevel=repo.toplevel,
            git_dir=repo.git_dir,
            common_dir=repo.common_dir,
            is_linked_worktree=repo.is_linked_worktree,
            head=head,
            upstream=upstream,
            state=state,
            rebase=rebase,
        ),
        entries=parsed.entries,
        counts=parsed.counts,
        truncated=result.truncated or parsed.counts.total > ENTRY_LIMIT,
    )


def validate_repo_path(raw: str | None) -> str:
    """Accept only a normalised repo-relative POSIX path (no escape, no NUL)."""
    if not raw or "\0" in raw:
        raise GitBadRequestError("bad_path")
    norm = posixpath.normpath(raw)
    if (
        norm.startswith("/")
        or norm in (".", "..")
        or ".." in norm.split("/")
        or _DRIVE_RE.match(norm)
    ):
        raise GitBadRequestError("bad_path")
    return norm


def clamp_context(raw: Any) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = _CONTEXT_DEFAULT
    return max(0, min(_CONTEXT_MAX, value))


@dataclass
class ParsedDiff:
    old_mode: str | None = None
    new_mode: str | None = None
    added: bool = False
    deleted: bool = False
    renamed: bool = False
    copied: bool = False
    binary: bool = False
    submodule: bool = False
    hunks: list[GitDiffHunk] = field(default_factory=list)
    total_lines: int = 0
    truncated: bool = False
    saw_header: bool = False

    def change(self) -> DiffChange:
        if self.added and self.deleted:
            return "type_changed"
        if self.renamed:
            return "renamed"
        if self.copied:
            return "copied"
        if self.added:
            return "added"
        if self.deleted:
            return "deleted"
        if (
            self.old_mode
            and self.new_mode
            and self.old_mode != self.new_mode
            and (
                self.old_mode[:3] != self.new_mode[:3]
            )
        ):
            return "type_changed"
        return "modified"


def _mode_from_index(line: bytes) -> str | None:
    parts = line.split()
    if len(parts) >= 3 and parts[-1].isdigit():
        return _text(parts[-1])
    return None


def parse_unified_diff(data: bytes, *, truncated: bool = False) -> ParsedDiff:
    """Parse ``git diff`` output for one path into hunks/lines.

    Paths are never read from the headers (spaces are unquoted there); the
    request path is echoed instead. A type change arrives as two
    consecutive ``diff --git`` blocks (delete + add) and is merged. A
    combined diff (``diff --cc``) is refused — conflicted files are diffed
    against ``HEAD`` by the caller before reaching here.
    """
    lines = data.split(b"\n")
    if truncated:
        lines.pop()
    elif lines and lines[-1] == b"":
        lines.pop()
    parsed = ParsedDiff(truncated=truncated)
    hunk: GitDiffHunk | None = None
    old_no = new_no = 0
    last_kept = False
    # Past the line cap, later hunks are still parsed so total_lines keeps
    # counting, but they are not retained: a sparse diff would otherwise ship
    # thousands of header-only hunks after the preview.
    capped = False
    for raw in lines:
        if raw.startswith(b"diff --cc ") or raw.startswith(b"diff --combined "):
            raise GitCommandError("combined diff is not supported")
        if raw.startswith(b"diff --git "):
            parsed.saw_header = True
            hunk = None
            continue
        if hunk is None:
            if raw.startswith(b"old mode "):
                parsed.old_mode = _text(raw[9:]).strip()
            elif raw.startswith(b"new mode "):
                parsed.new_mode = _text(raw[9:]).strip()
            elif raw.startswith(b"deleted file mode "):
                parsed.deleted = True
                parsed.old_mode = _text(raw[18:]).strip()
            elif raw.startswith(b"new file mode "):
                parsed.added = True
                parsed.new_mode = _text(raw[14:]).strip()
            elif raw.startswith(b"rename from "):
                parsed.renamed = True
            elif raw.startswith(b"copy from "):
                parsed.copied = True
            elif raw.startswith(b"index "):
                mode = _mode_from_index(raw)
                if mode:
                    parsed.old_mode = parsed.old_mode or mode
                    parsed.new_mode = parsed.new_mode or mode
            elif raw.startswith(b"Binary files "):
                parsed.binary = True
            elif raw.startswith(b"@@ "):
                hunk, old_no, new_no = _start_hunk(raw, parsed, retain=not capped)
            continue
        if raw.startswith(b"@@ "):
            hunk, old_no, new_no = _start_hunk(raw, parsed, retain=not capped)
            continue
        if raw.startswith(b"\\ "):
            # the marker belongs to the line just before it, which may have
            # been dropped by LINE_LIMIT
            if last_kept and hunk.lines:
                hunk.lines[-1].no_newline = True
            continue
        marker = raw[:1]
        if marker == b"+":
            kind, text = "add", raw[1:]
            if raw.startswith(b"+Subproject commit "):
                parsed.submodule = True
        elif marker == b"-":
            kind, text = "del", raw[1:]
            if raw.startswith(b"-Subproject commit "):
                parsed.submodule = True
        else:
            kind, text = "ctx", raw[1:] if marker == b" " else raw
        parsed.total_lines += 1
        if parsed.total_lines > LINE_LIMIT:
            parsed.truncated = True
            capped = True
            last_kept = False
            continue
        last_kept = True
        if kind == "ctx":
            line = GitDiffLine(t="ctx", old=old_no, new=new_no, text=_text(text))
            old_no += 1
            new_no += 1
        elif kind == "add":
            line = GitDiffLine(t="add", old=None, new=new_no, text=_text(text))
            new_no += 1
        else:
            line = GitDiffLine(t="del", old=old_no, new=None, text=_text(text))
            old_no += 1
        hunk.lines.append(line)
    # A cap that lands exactly on a hunk boundary (or a byte cut right after a
    # header) leaves one retained hunk with no lines; git never emits one.
    if parsed.truncated and parsed.hunks and not parsed.hunks[-1].lines:
        parsed.hunks.pop()
    return parsed


def _start_hunk(
    raw: bytes, parsed: ParsedDiff, *, retain: bool = True
) -> tuple[GitDiffHunk, int, int]:
    match = _HUNK_RE.match(raw)
    if match is None:
        raise GitCommandError("unrecognised hunk header")
    old_start = int(match.group(1))
    old_lines = int(match.group(2)) if match.group(2) is not None else 1
    new_start = int(match.group(3))
    new_lines = int(match.group(4)) if match.group(4) is not None else 1
    hunk = GitDiffHunk(
        old_start=old_start,
        old_lines=old_lines,
        new_start=new_start,
        new_lines=new_lines,
        heading=_text(match.group(5) or b""),
    )
    if retain:
        parsed.hunks.append(hunk)
    return hunk, old_start, new_start


def _missing(path: str, orig_path: str | None, scope: DiffScope) -> GitDiffResponse:
    return GitDiffResponse(path=path, orig_path=orig_path, scope=scope, kind="missing")


async def _numstat(
    base: list[str], pathspec: list[str], toplevel: str
) -> list[str]:
    result = await run_git(
        [*base, "--numstat", "--no-ext-diff", "--no-textconv", "-M", *pathspec],
        cwd=toplevel,
        timeout=_QUICK_TIMEOUT,
        max_bytes=_SMALL_OUTPUT,
    )
    _check_result(result)
    if result.rc != 0:
        raise _fail(result, "git diff --numstat failed")
    return [ln for ln in _lines(result.stdout) if ln]


async def _is_unmerged(toplevel: str, path: str) -> bool:
    result = await run_git(
        ["ls-files", "-u", "-z", "--", path],
        cwd=toplevel,
        timeout=_QUICK_TIMEOUT,
        max_bytes=_SMALL_OUTPUT,
    )
    _check_result(result)
    return result.rc == 0 and bool(result.stdout.strip(b"\0"))


def _counts_from_numstat(line: str) -> tuple[int, int, bool]:
    parts = line.split("\t", 2)
    if len(parts) < 2:
        return 0, 0, False
    if parts[0] == "-" or parts[1] == "-":
        return 0, 0, True
    try:
        return int(parts[0]), int(parts[1]), False
    except ValueError:
        return 0, 0, False


async def get_session_git_diff(
    *,
    cwd: str | None,
    path: str | None,
    scope: str | None,
    context: Any = _CONTEXT_DEFAULT,
    orig_path: str | None = None,
) -> GitDiffResponse:
    if scope not in _DIFF_SCOPES:
        raise GitBadRequestError("bad_scope")
    rel = validate_repo_path(path)
    orig = validate_repo_path(orig_path) if orig_path else None
    ctx = clamp_context(context)

    probe = await resolve_repo(cwd)
    if probe.repo is None:
        raise GitBadRequestError(probe.reason or "not_a_repo")
    toplevel = probe.repo.toplevel

    if scope == "untracked":
        return await _untracked_diff(toplevel, rel)

    base = ["diff", "--cached"] if scope == "staged" else ["diff"]
    pathspec = ["--", rel] + ([orig] if orig else [])
    numstat = await _numstat(base, pathspec, toplevel)
    if not numstat:
        return _missing(rel, orig, scope)
    if await _is_unmerged(toplevel, rel):
        if scope == "staged":
            return _missing(rel, orig, scope)
        base = ["diff", "HEAD"]
        numstat = await _numstat(base, pathspec, toplevel)
        if not numstat:
            # modify/delete conflict: the working-tree file *is* HEAD's
            # version, so the conflict has no textual diff — still a real
            # entry unless the file is gone from the worktree too
            if not os.path.lexists(os.path.join(toplevel, *rel.split("/"))):
                return _missing(rel, orig, scope)
            return GitDiffResponse(
                path=rel, orig_path=orig, scope=scope, kind="text", change="modified"
            )
    additions, deletions, numstat_binary = _counts_from_numstat(numstat[0])

    patch = await run_git(
        [
            *base,
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "--submodule=short",
            f"--unified={ctx}",
            "-M",
            *pathspec,
        ],
        cwd=toplevel,
        timeout=_DIFF_TIMEOUT,
        max_bytes=BYTE_LIMIT,
    )
    _check_result(patch)
    if patch.rc != 0 and not patch.truncated:
        raise _fail(patch, "git diff failed")
    parsed = parse_unified_diff(patch.stdout, truncated=patch.truncated)
    if not parsed.saw_header:
        return _missing(rel, orig, scope)
    if parsed.binary or numstat_binary:
        kind = "binary"
    elif parsed.submodule:
        kind = "submodule"
    else:
        kind = "text"
    return GitDiffResponse(
        path=rel,
        orig_path=orig,
        scope=scope,
        kind=kind,
        change=parsed.change(),
        old_mode=parsed.old_mode,
        new_mode=parsed.new_mode,
        additions=additions,
        deletions=deletions,
        hunks=parsed.hunks,
        truncated=parsed.truncated,
        total_lines=parsed.total_lines,
    )


def _contained(toplevel: str, full: str) -> bool:
    root = os.path.normcase(os.path.realpath(toplevel))
    parent = os.path.normcase(os.path.realpath(os.path.dirname(full)))
    return parent == root or parent.startswith(root.rstrip(os.sep) + os.sep)


async def _untracked_diff(toplevel: str, rel: str) -> GitDiffResponse:
    """``git diff --no-index -- /dev/null <file>`` is broken on Git for Windows,
    so the add-everything diff is built here. Only a path git itself lists as
    untracked-and-not-ignored is served; the leaf is never followed — a
    symlink diffs as its target text with mode ``120000``, as git shows it."""
    listed = await run_git(
        ["ls-files", "--others", "--exclude-standard", "-z", "--", rel],
        cwd=toplevel,
        timeout=_QUICK_TIMEOUT,
        max_bytes=_SMALL_OUTPUT,
    )
    _check_result(listed)
    if listed.rc != 0 or listed.stdout.rstrip(b"\0") != os.fsencode(rel):
        return _missing(rel, None, "untracked")

    full = os.path.join(toplevel, *rel.split("/"))
    if not _contained(toplevel, full):
        raise GitBadRequestError("bad_path")
    try:
        st = os.lstat(full)
        if statmod.S_ISLNK(st.st_mode):
            content = os.fsencode(os.readlink(full))
            mode = "120000"
        elif statmod.S_ISREG(st.st_mode):
            if st.st_size > BYTE_LIMIT:
                return GitDiffResponse(
                    path=rel, scope="untracked", kind="too_large", change="added",
                    new_mode="100644",
                )
            with open(full, "rb") as fh:
                content = fh.read(BYTE_LIMIT + 1)
            if len(content) > BYTE_LIMIT:
                return GitDiffResponse(
                    path=rel, scope="untracked", kind="too_large", change="added",
                    new_mode="100644",
                )
            mode = "100755" if st.st_mode & 0o111 else "100644"
        else:
            return _missing(rel, None, "untracked")
    except OSError:
        return _missing(rel, None, "untracked")

    if b"\0" in content[:_BINARY_SNIFF]:
        return GitDiffResponse(
            path=rel, scope="untracked", kind="binary", change="added", new_mode=mode
        )

    parts = content.split(b"\n")
    no_newline = parts[-1] != b""
    if not no_newline:
        parts.pop()
    total = len(parts)
    kept = parts[:LINE_LIMIT]
    lines = [
        GitDiffLine(
            t="add",
            old=None,
            new=index + 1,
            text=_text(part),
            no_newline=no_newline and index == total - 1,
        )
        for index, part in enumerate(kept)
    ]
    hunks = (
        [
            GitDiffHunk(
                old_start=0, old_lines=0, new_start=1, new_lines=total, lines=lines
            )
        ]
        if total
        else []
    )
    return GitDiffResponse(
        path=rel,
        scope="untracked",
        kind="text",
        change="added",
        new_mode=mode,
        additions=total,
        hunks=hunks,
        truncated=total > LINE_LIMIT,
        total_lines=total,
    )
