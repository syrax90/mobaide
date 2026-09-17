"""Session-scoped rebase — start onto a ref, then continue / skip / abort —
shared by the HTTP router and the relay dispatcher.

Each call runs one non-interactive ``git rebase`` under the per-toplevel lock
and answers when git exits: a local rebase has no network step, so it fits
the relay's 30 s budget the way a commit does, and a rebase cut off by the
timeout is left in progress for Abort to undo. The editors are disabled with
``:``, which git itself treats as "no editor", so nothing is ever spawned to
wait for a person. A rebase that stops on conflicts is a normal ``stopped``
answer carrying the progress the status poll also reports; git declining to
act is a ``409`` in the jobs' failure vocabulary (``dirty_worktree``,
``unmerged``, ``hook_failed``, …). ``git.changed`` is published whenever
git ran, whatever it answered.
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
    result = await run_git(
        ["rev-parse", "-q", "--verify", "HEAD"],
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


async def _outcome(
    toplevel: str,
    result: CmdResult,
    *,
    action: _Action,
    head_before: str | None = None,
) -> GitRebaseResponse:
    """A rebase still in progress after git exits has stopped; one that is
    not either finished or was refused, and a refusal to continue or skip
    leaves the stop as it was."""
    _, rebase_dir = await operation_state(toplevel)
    if result.rc != 0:
        stdout, stderr = _output_lines(result.stdout), _output_lines(result.stderr)
        code, message = classify_job_failure("rebase", err_tail=stderr, out_tail=stdout)
        if action == "continue" and code == "git_error" and any(
            _CONTINUE_DIRTY in line for line in (*stdout, *stderr)
        ):
            code, message = "dirty_worktree", "unstaged changes to tracked files"
        if rebase_dir is not None and (code == "conflicts" or action == "start"):
            return GitRebaseResponse(
                outcome="stopped",
                rebase=await rebase_info(toplevel, rebase_dir),
                message=None if code == "conflicts" else message,
            )
        raise GitRefusedError(code, message)
    if rebase_dir is not None:
        return GitRebaseResponse(
            outcome="stopped", rebase=await rebase_info(toplevel, rebase_dir)
        )
    if action == "abort":
        return GitRebaseResponse(outcome="aborted")
    if action == "start" and head_before == await _head_oid(toplevel):
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
                repo.toplevel, result, action="start", head_before=head
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
            if action == "continue" and await _has_unmerged(repo.toplevel):
                raise GitRefusedError("unmerged", "unmerged paths in the index")
            ran = True
            result = await _run_rebase(repo.toplevel, [f"--{action}"])
            return await _outcome(repo.toplevel, result, action=action)
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
