"""Session-scoped rebase — start onto a ref, then continue / skip / abort —
shared by the HTTP router and the relay dispatcher.

Each call runs one non-interactive ``git rebase`` under the per-toplevel lock
and answers when git exits: a local rebase has no network step, so it fits
the relay's 30 s budget the way a commit does, and a rebase cut off by the
timeout is left in progress for Abort to undo. The editors are disabled with
``:``, which git itself treats as "no editor", so nothing is ever spawned to
wait for a person. A call that leaves the rebase in progress and further
along — a start always; a continue, skip or abort that moved HEAD or
``REBASE_HEAD`` (the commit git stopped at), or left unmerged paths where
there were none — is a normal ``stopped`` answer carrying the progress the
status poll also reports, whatever git exited with; git declining to act
without moving anything is a ``409`` in the jobs' failure vocabulary
(``dirty_worktree``, ``unmerged``, ``hook_failed``, …). ``git.changed`` is
published whenever git ran, whatever it answered.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from mobaide.core.git_exec import CmdResult, run_git

from .changes_jobs import classify_job_failure
from .changes_models import GitRebaseResponse
from .changes_mutations import _has_unmerged, _publish, _writable_repo
from .changes_service import (
    _QUICK_TIMEOUT,
    _SMALL_OUTPUT,
    _STATUS_MAX_BYTES,
    GitRefusedError,
    _check_result,
    _lines,
    _lock_for,
    _text,
    operation_state,
    rebase_info,
)

__all__ = [
    "abort_session_rebase",
    "continue_session_rebase",
    "skip_session_rebase",
    "start_session_rebase",
]

# The relay gives a proxied request 30 s.
_REBASE_TIMEOUT = 25.0
_NO_EDITOR = {"GIT_EDITOR": ":", "GIT_SEQUENCE_EDITOR": ":"}
# ``git rebase --continue`` words a dirty worktree like unmerged paths; the
# unmerged pre-check has already ruled those out.
_CONTINUE_DIRTY = "You must edit all merge conflicts"

_Action = Literal["start", "continue", "skip", "abort"]


async def _head_oid(toplevel: str) -> str | None:
    return await _rev(toplevel, "HEAD")


async def _rev(toplevel: str, name: str) -> str | None:
    result = await run_git(
        ["rev-parse", "-q", "--verify", name],
        cwd=toplevel,
        timeout=_QUICK_TIMEOUT,
        max_bytes=_SMALL_OUTPUT,
    )
    _check_result(result)
    if result.rc != 0:
        return None
    return _text(result.stdout).strip() or None


async def _resolves_to_commit(toplevel: str, ref: str) -> bool:
    result = await run_git(
        ["rev-parse", "-q", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
        cwd=toplevel,
        timeout=_QUICK_TIMEOUT,
        max_bytes=_SMALL_OUTPUT,
    )
    _check_result(result)
    return result.rc == 0


async def _require_rebasing(toplevel: str) -> None:
    state, _ = await operation_state(toplevel)
    if state != "rebasing":
        raise GitRefusedError("no_rebase", state)


async def _progress(toplevel: str) -> tuple[str | None, str | None]:
    """``(HEAD, REBASE_HEAD)``: a continue that commits the resolved pick moves
    HEAD, a skip moves the commit git stopped at, and either means git did
    something even when it then exits non-zero on the next pick. Not the todo
    step: git reschedules a pick that failed and bumps ``msgnum`` on every
    retry, so a retry that changed nothing would read as progress."""
    return await _head_oid(toplevel), await _rev(toplevel, "REBASE_HEAD")


async def _run_rebase(toplevel: str, args: Sequence[str]) -> CmdResult:
    result = await run_git(
        ["rebase", *args],
        cwd=toplevel,
        timeout=_REBASE_TIMEOUT,
        max_bytes=_STATUS_MAX_BYTES,
        env_extra=_NO_EDITOR,
    )
    _check_result(result)
    return result


def _output_lines(data: bytes) -> list[str]:
    """git's output split into lines at ``\\r`` as well: its ``Rebasing
    (n/m)`` progress is ``\\r``-separated, and the ``error:`` line that
    explains a stop would otherwise sit behind it where no line-anchored
    rule can see it."""
    return [part for line in _lines(data) for part in line.split("\r") if part]


def _advice_free(data: bytes) -> list[str]:
    return [line for line in _output_lines(data) if not line.startswith("hint:")]


async def _outcome(
    toplevel: str,
    result: CmdResult,
    *,
    action: _Action,
    before: tuple[str | None, str | None],
    had_unmerged: bool = False,
) -> GitRebaseResponse:
    """Decided from the repository, never from git's wording, which quotes
    commit subjects and paths: a failed call that left the rebase in progress
    and further along — HEAD or ``REBASE_HEAD`` moved, or a retried pick
    left conflicts where there were none — has stopped (its message is git's
    explanation only when no unmerged path explains it); one that moved
    nothing was refused. ``hint:`` lines are dropped before classifying: git's
    advice for a rescheduled pick quotes its subject."""
    _, rebase_dir = await operation_state(toplevel)
    if result.rc != 0:
        stdout, stderr = _advice_free(result.stdout), _advice_free(result.stderr)
        code, message = classify_job_failure("rebase", err_tail=stderr, out_tail=stdout)
        unmerged = rebase_dir is not None and await _has_unmerged(toplevel)
        if rebase_dir is not None and (
            action == "start"
            or (unmerged and not had_unmerged)
            or await _progress(toplevel) != before
        ):
            return GitRebaseResponse(
                outcome="stopped",
                rebase=await rebase_info(toplevel, rebase_dir),
                message=None if unmerged else message,
            )
        if action == "continue" and code == "git_error" and any(
            _CONTINUE_DIRTY in line for line in (*stdout, *stderr)
        ):
            code, message = "dirty_worktree", "unstaged changes to tracked files"
        raise GitRefusedError(code, message)
    if rebase_dir is not None:
        return GitRebaseResponse(
            outcome="stopped", rebase=await rebase_info(toplevel, rebase_dir)
        )
    if action == "abort":
        return GitRebaseResponse(outcome="aborted")
    if action == "start" and before[0] == await _head_oid(toplevel):
        return GitRebaseResponse(outcome="up_to_date")
    return GitRebaseResponse(outcome="rebased")


async def start_session_rebase(
    *,
    cwd: str | None,
    onto: str,
    project_name: str | None,
    event_bus: Any | None,
) -> GitRebaseResponse:
    """``git rebase --no-autostash <onto>``: git decides whether the tree is
    too dirty to start, and ``rebase.autoStash`` cannot stash around that."""
    if onto.startswith("-") or "\0" in onto:
        raise GitRefusedError("unknown_ref", onto)
    repo = await _writable_repo(cwd)
    ran = False
    try:
        async with _lock_for(repo.toplevel):
            state, _ = await operation_state(repo.toplevel)
            if state != "clean":
                raise GitRefusedError("operation_in_progress", state)
            head = await _head_oid(repo.toplevel)
            if head is None:
                raise GitRefusedError("no_head", "no commit to rebase")
            if not await _resolves_to_commit(repo.toplevel, onto):
                raise GitRefusedError("unknown_ref", onto)
            ran = True
            result = await _run_rebase(
                repo.toplevel, ["--no-autostash", "--end-of-options", onto]
            )
            return await _outcome(
                repo.toplevel, result, action="start", before=(head, None)
            )
    finally:
        if ran:
            _publish(event_bus, project_name, repo)


async def _step(
    action: Literal["continue", "skip", "abort"],
    *,
    cwd: str | None,
    project_name: str | None,
    event_bus: Any | None,
) -> GitRebaseResponse:
    repo = await _writable_repo(cwd)
    ran = False
    try:
        async with _lock_for(repo.toplevel):
            await _require_rebasing(repo.toplevel)
            had_unmerged = await _has_unmerged(repo.toplevel)
            if action == "continue" and had_unmerged:
                raise GitRefusedError("unmerged", "unmerged paths in the index")
            before = await _progress(repo.toplevel)
            ran = True
            result = await _run_rebase(repo.toplevel, [f"--{action}"])
            return await _outcome(
                repo.toplevel,
                result,
                action=action,
                before=before,
                had_unmerged=had_unmerged,
            )
    finally:
        if ran:
            _publish(event_bus, project_name, repo)


async def continue_session_rebase(
    *, cwd: str | None, project_name: str | None, event_bus: Any | None
) -> GitRebaseResponse:
    """``git rebase --continue``, refused while unmerged paths remain."""
    return await _step(
        "continue", cwd=cwd, project_name=project_name, event_bus=event_bus
    )


async def skip_session_rebase(
    *, cwd: str | None, project_name: str | None, event_bus: Any | None
) -> GitRebaseResponse:
    """``git rebase --skip``: drops the commit git stopped at."""
    return await _step("skip", cwd=cwd, project_name=project_name, event_bus=event_bus)


async def abort_session_rebase(
    *, cwd: str | None, project_name: str | None, event_bus: Any | None
) -> GitRebaseResponse:
    """``git rebase --abort``: back to the branch as it was before the rebase."""
    return await _step("abort", cwd=cwd, project_name=project_name, event_bus=event_bus)
