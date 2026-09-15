"""Session-scoped git mutations — stage, unstage, discard, commit — shared by
the HTTP router and the relay dispatcher.

Every command runs through :mod:`mobaide.core.git_exec` in the repository's
toplevel, under a per-toplevel ``asyncio.Lock`` so two clients of the same
session cannot interleave index writes (git's own ``index.lock`` would turn
the loser into a spurious failure). Pathspecs travel NUL-separated on stdin
(``--pathspec-from-file=-``): no argv limit on Windows, and a path git
cannot match fails the whole call before anything is touched. Only
``git clean`` lacks that option; it can only ever delete untracked,
non-ignored files, so it takes argv in chunks.

Refusals — git declining because of the repository's state — are raised as
:class:`GitRefusedError` (``409``) with a fixed ``code`` vocabulary the three
clients render; unexpected failures stay ``git_error`` (``500``).
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from mobaide.core.git_exec import CmdResult, run_git

from .changes_jobs import job_registry
from .changes_models import (
    BYTE_LIMIT,
    MESSAGE_LIMIT,
    GitCommitAuthor,
    GitCommitContextResponse,
    GitCommitHead,
    GitCommitRequest,
    GitCommitResponse,
    GitMutationResponse,
)
from .changes_service import (
    MIN_GIT_VERSION_MUTATE,
    GitBadRequestError,
    GitCommandError,
    GitRefusedError,
    ResolvedRepo,
    _check_result,
    _fail,
    _lines,
    _lock_for,
    _mutable_repo,
    _text,
    validate_repo_path,
)
from .service import _publish_git_changed

__all__ = [
    "MIN_GIT_VERSION_MUTATE",
    "commit_session",
    "discard_session_paths",
    "get_session_commit_context",
    "stage_session_paths",
    "unstage_session_paths",
]

_QUICK_TIMEOUT = 5.0
_MUTATE_TIMEOUT = 15.0
# Hooks run inside ``git commit``; the relay gives a proxied request 30 s.
_COMMIT_TIMEOUT = 25.0
_SMALL_OUTPUT = 64 * 1024
_CLEAN_CHUNK = 100
_PATHSPEC_FROM_STDIN = ("--pathspec-from-file=-", "--pathspec-file-nul")
_IDENT_RE = re.compile(r"^(.*?) <([^>]*)> \d+ [+-]\d{4}$")


def _normalise_paths(paths: Sequence[str]) -> list[str]:
    """Validated, deduplicated, never empty: an empty pathspec file means
    "no pathspec" to git, which would turn ``add -A`` / ``reset`` into
    whole-tree operations."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in paths:
        norm = validate_repo_path(raw)
        if norm not in seen:
            seen.add(norm)
            out.append(norm)
    if not out:
        raise GitBadRequestError("bad_path")
    return out


def _pathspec_stdin(paths: Sequence[str]) -> bytes:
    return b"".join(path.encode("utf-8") + b"\0" for path in paths)


def _output(result: CmdResult) -> str:
    err = _text(result.stderr).strip()
    out = _text(result.stdout).strip()
    if err and out:
        return f"{out}\n{err}"
    return err or out


def _classify(result: CmdResult, fallback: str) -> GitRefusedError | GitCommandError:
    """Turn a non-zero git exit into the client-facing refusal vocabulary;
    anything unrecognised stays a ``git_error``."""
    stderr = _text(result.stderr)
    detail = _output(result) or fallback
    if "index.lock" in stderr:
        return GitRefusedError("index_locked", detail)
    if "did not match any" in stderr or "ignored by one of your" in stderr:
        return GitRefusedError("pathspec_mismatch", detail)
    if (
        "Author identity unknown" in stderr
        or "empty ident" in stderr
        or "unable to auto-detect" in stderr
    ):
        return GitRefusedError("identity_required", detail)
    return _fail(result, fallback)


async def _writable_repo(cwd: str | None) -> ResolvedRepo:
    """A write is refused outright while a fetch/pull/push job owns the
    repository: queuing behind its 120 s budget would only turn into a relay
    timeout, and the job publishes ``git.changed`` when it is done."""
    repo = await _mutable_repo(cwd)
    active = job_registry.active_job_id(repo.toplevel)
    if active is not None:
        raise GitRefusedError("job_running", active)
    return repo


async def _run(
    args: Sequence[str],
    *,
    toplevel: str,
    fallback: str,
    stdin: bytes | None = None,
    timeout: float = _MUTATE_TIMEOUT,
    max_bytes: int = _SMALL_OUTPUT,
) -> CmdResult:
    result = await run_git(
        args, cwd=toplevel, timeout=timeout, max_bytes=max_bytes, stdin=stdin
    )
    _check_result(result)
    if result.rc != 0:
        raise _classify(result, fallback)
    return result


def _publish(event_bus: Any | None, project_name: str | None, repo: ResolvedRepo) -> None:
    _publish_git_changed(
        event_bus,
        project_name or "",
        toplevel=repo.toplevel,
        common_dir=repo.common_dir,
    )


async def stage_session_paths(
    *,
    cwd: str | None,
    paths: Sequence[str],
    project_name: str | None,
    event_bus: Any | None,
) -> GitMutationResponse:
    """``git add -A`` limited to ``paths``: stages modifications, deletions,
    type changes and untracked files alike."""
    rel = _normalise_paths(paths)
    repo = await _writable_repo(cwd)
    async with _lock_for(repo.toplevel):
        await _run(
            ["add", "-A", *_PATHSPEC_FROM_STDIN],
            toplevel=repo.toplevel,
            fallback="git add failed",
            stdin=_pathspec_stdin(rel),
        )
    _publish(event_bus, project_name, repo)
    return GitMutationResponse()


async def unstage_session_paths(
    *,
    cwd: str | None,
    paths: Sequence[str],
    project_name: str | None,
    event_bus: Any | None,
) -> GitMutationResponse:
    """``git reset -q -- paths``: unlike ``restore --staged`` it also works
    on a branch with no commits yet."""
    rel = _normalise_paths(paths)
    repo = await _writable_repo(cwd)
    async with _lock_for(repo.toplevel):
        await _run(
            ["reset", "-q", *_PATHSPEC_FROM_STDIN],
            toplevel=repo.toplevel,
            fallback="git reset failed",
            stdin=_pathspec_stdin(rel),
        )
    _publish(event_bus, project_name, repo)
    return GitMutationResponse()


async def discard_session_paths(
    *,
    cwd: str | None,
    paths: Sequence[str],
    scope: str | None,
    project_name: str | None,
    event_bus: Any | None,
) -> GitMutationResponse:
    """Throw away working-tree changes: ``restore --worktree`` for tracked
    paths (a staged part survives), ``clean -f`` for untracked ones."""
    if scope not in ("unstaged", "untracked"):
        raise GitBadRequestError("bad_scope")
    rel = _normalise_paths(paths)
    repo = await _writable_repo(cwd)
    async with _lock_for(repo.toplevel):
        if scope == "unstaged":
            await _run(
                ["restore", "--worktree", *_PATHSPEC_FROM_STDIN],
                toplevel=repo.toplevel,
                fallback="git restore failed",
                stdin=_pathspec_stdin(rel),
            )
        else:
            for start in range(0, len(rel), _CLEAN_CHUNK):
                await _run(
                    ["clean", "-f", "-q", "--", *rel[start:start + _CLEAN_CHUNK]],
                    toplevel=repo.toplevel,
                    fallback="git clean failed",
                )
    _publish(event_bus, project_name, repo)
    return GitMutationResponse()


async def _author_identity(toplevel: str) -> GitCommitAuthor | None:
    """``git var GIT_AUTHOR_IDENT`` applies the same strictness as ``git
    commit``: it fails where the commit would ask "who are you?"."""
    result = await run_git(
        ["var", "GIT_AUTHOR_IDENT"],
        cwd=toplevel,
        timeout=_QUICK_TIMEOUT,
        max_bytes=_SMALL_OUTPUT,
    )
    _check_result(result)
    if result.rc != 0:
        return None
    lines = _lines(result.stdout)
    match = _IDENT_RE.match(lines[0]) if lines else None
    if match is None:
        return None
    return GitCommitAuthor(name=match.group(1), email=match.group(2))


async def _head_commit(toplevel: str) -> GitCommitHead | None:
    probe = await run_git(
        ["rev-parse", "-q", "--verify", "HEAD"],
        cwd=toplevel,
        timeout=_QUICK_TIMEOUT,
        max_bytes=_SMALL_OUTPUT,
    )
    _check_result(probe)
    if probe.rc != 0:
        return None
    # A body past the cap is cut, not fatal: oid, short oid and subject sit in
    # the first bytes, and the prefill is clipped below anyway.
    result = await run_git(
        [
            "log",
            "-1",
            "--no-color",
            "--no-show-signature",
            "--format=%H%x00%h%x00%s%x00%b",
        ],
        cwd=toplevel,
        timeout=_MUTATE_TIMEOUT,
        max_bytes=BYTE_LIMIT,
    )
    _check_result(result)
    if result.rc != 0 and not result.truncated:
        raise _classify(result, "git log failed")
    parts = _text(result.stdout).split("\0", 3)
    if len(parts) != 4:
        raise GitCommandError("unexpected git log output")
    oid, short_oid, subject, body = parts
    # A generated changelog commit can carry a body far past what the sheet
    # could ever send back; the prefill needs no more than the message cap.
    return GitCommitHead(
        oid=oid.strip(),
        short_oid=short_oid.strip(),
        subject=subject,
        body=body.rstrip("\n")[:MESSAGE_LIMIT],
    )


async def get_session_commit_context(*, cwd: str | None) -> GitCommitContextResponse:
    repo = await _mutable_repo(cwd)
    return GitCommitContextResponse(
        author=await _author_identity(repo.toplevel),
        head=await _head_commit(repo.toplevel),
    )


async def _has_unmerged(toplevel: str) -> bool:
    # Any output at all means unmerged, so a listing cut at the cap is a
    # complete answer rather than a failure.
    result = await run_git(
        ["ls-files", "-u", "-z"],
        cwd=toplevel,
        timeout=_QUICK_TIMEOUT,
        max_bytes=_SMALL_OUTPUT,
    )
    _check_result(result)
    if result.truncated:
        return True
    if result.rc != 0:
        raise _classify(result, "git ls-files failed")
    return bool(result.stdout.strip(b"\0"))


async def _has_staged(toplevel: str) -> bool:
    result = await run_git(
        ["diff", "--cached", "--quiet", "--no-ext-diff"],
        cwd=toplevel,
        timeout=_QUICK_TIMEOUT,
        max_bytes=_SMALL_OUTPUT,
    )
    _check_result(result)
    if result.rc == 1:
        return True
    if result.rc != 0:
        raise _classify(result, "git diff --cached failed")
    return False


async def _run_commit(args: Sequence[str], *, toplevel: str, message: bytes) -> None:
    result = await run_git(
        args,
        cwd=toplevel,
        timeout=_COMMIT_TIMEOUT,
        max_bytes=BYTE_LIMIT,
        stdin=message,
    )
    _check_result(result)
    if result.rc == 0:
        return
    refusal = _classify(result, "git commit failed")
    if isinstance(refusal, GitRefusedError):
        raise refusal
    # after the pre-checks, exit 1 is a hook saying no (its output is the
    # only explanation the user gets)
    if result.rc == 1:
        raise GitRefusedError("hook_failed", _output(result) or "a commit hook rejected the commit")
    raise refusal


async def commit_session(
    *,
    cwd: str | None,
    request: GitCommitRequest,
    project_name: str | None,
    event_bus: Any | None,
) -> GitCommitResponse:
    """``git commit -F -`` of the staged set (never ``-a``); the message
    arrives on stdin and is cleaned with ``whitespace`` so a leading ``#``
    survives. Hooks run unless ``no_verify`` asks otherwise."""
    if not request.message.strip():
        raise GitBadRequestError("empty_message")
    repo = await _writable_repo(cwd)
    toplevel = repo.toplevel
    async with _lock_for(toplevel):
        if await _author_identity(toplevel) is None:
            raise GitRefusedError(
                "identity_required",
                "git has no user.name / user.email for this repository",
            )
        if await _has_unmerged(toplevel):
            raise GitRefusedError("unmerged", "unmerged paths in the index")
        if request.amend:
            if await _head_commit(toplevel) is None:
                raise GitRefusedError("no_head", "no commit to amend")
        elif not await _has_staged(toplevel):
            raise GitRefusedError("nothing_to_commit", "no changes added to commit")
        args = ["commit", "-q", "--cleanup=whitespace", "-F", "-"]
        if request.amend:
            args.append("--amend")
        if request.no_verify:
            args.append("--no-verify")
        await _run_commit(args, toplevel=toplevel, message=request.message.encode("utf-8"))
        head = await _head_commit(toplevel)
    if head is None:
        raise GitCommandError("commit succeeded but HEAD is unreadable")
    _publish(event_bus, project_name, repo)
    return GitCommitResponse(oid=head.oid, short_oid=head.short_oid, subject=head.subject)
