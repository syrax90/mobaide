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
published whenever git ran, whatever it answered. A skip at a pick git
rescheduled removes that pick from the todo first, because ``--skip`` alone
would retry it.
"""
from __future__ import annotations

import contextlib
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from mobaide.core.git_exec import CmdResult, git_version, run_git

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
# Not squash/fixup: git counts them into its squash-message state before the
# blocked merge and only unwinds that for a stop it wrote ``amend`` for, so
# dropping one would corrupt the combined commit's message.
_PICK_COMMANDS = frozenset({"pick", "p", "reword", "r", "edit", "e"})
# Before 2.43 git wrote ``stopped-sha`` for a rescheduled pick too.
_RESCHEDULE_WITHOUT_STOPPED_SHA = (2, 43, 0)

_Action = Literal["start", "continue", "skip", "abort"]


async def _head_oid(toplevel: str) -> str | None:
    return await _rev(toplevel, "HEAD")


async def _rev(toplevel: str, name: str) -> str | None:
    result = await run_git(
        ["rev-parse", "-q", "--verify", "--end-of-options", name],
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


async def _require_rebasing(toplevel: str) -> str:
    state, rebase_dir = await operation_state(toplevel)
    if state != "rebasing" or rebase_dir is None:
        raise GitRefusedError("no_rebase", state)
    return rebase_dir


async def _progress(toplevel: str) -> tuple[str | None, str | None]:
    """``(HEAD, REBASE_HEAD)``: a continue that commits the resolved pick moves
    HEAD, a skip moves the commit git stopped at, and either means git did
    something even when it then exits non-zero on the next pick. Not the todo
    step: git reschedules a pick that failed and bumps ``msgnum`` on every
    retry, so a retry that changed nothing would read as progress."""
    return await _head_oid(toplevel), await _rev(toplevel, "REBASE_HEAD")


@dataclass(frozen=True)
class _DroppedPick:
    path: str
    original: bytes
    written: bytes


def _first_todo_pick(todo: bytes) -> tuple[int, int, str] | None:
    """Byte span and commit of the todo's first command, when that command
    is one Skip may drop. Lines end at ``\\n`` only, as git reads them."""
    start = 0
    while start < len(todo):
        newline = todo.find(b"\n", start)
        end = len(todo) if newline < 0 else newline + 1
        words = _text(todo[start:end]).split()
        if not words or words[0].startswith("#"):
            start = end
            continue
        if words[0] not in _PICK_COMMANDS or len(words) < 2:
            return None
        return start, end, words[1]
    return None


def _replace_todo(path: str, data: bytes) -> None:
    """Written the way git writes it: into an exclusively created ``.lock``,
    then renamed over, so a git process holding the lock is never raced."""
    lock = path + ".lock"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(lock, flags, 0o666)
    except FileExistsError as exc:
        raise GitRefusedError("index_locked", os.path.basename(lock)) from exc
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(lock, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(lock)
        raise


async def _drop_rescheduled_pick(toplevel: str, rebase_dir: str) -> _DroppedPick | None:
    """Removes a pick an untracked file blocked, which git put back at the head
    of the todo and ``--skip`` would retry. A stop on the pick itself has
    already moved it to ``done``; from 2.43 ``stopped-sha`` also tells that
    stop apart from a todo edited to repeat the commit."""
    if os.path.basename(rebase_dir) != "rebase-merge":
        return None
    version = await git_version()
    if (
        version is not None
        and version >= _RESCHEDULE_WITHOUT_STOPPED_SHA
        and os.path.exists(os.path.join(rebase_dir, "stopped-sha"))
    ):
        return None
    path = os.path.join(rebase_dir, "git-rebase-todo")
    try:
        with open(path, "rb") as fh:
            original = fh.read()
    except OSError:
        return None
    pick = _first_todo_pick(original)
    if pick is None:
        return None
    start, end, commit = pick
    stopped_at = await _rev(toplevel, "REBASE_HEAD")
    if stopped_at is None or await _rev(toplevel, f"{commit}^{{commit}}") != stopped_at:
        return None
    written = original[:start] + original[end:]
    _replace_todo(path, written)
    return _DroppedPick(path, original, written)


def _restore_todo(dropped: _DroppedPick) -> None:
    """Puts the pick back after a skip that failed before git touched the
    todo, so a refused skip leaves the stop as it was."""
    with contextlib.suppress(OSError, GitRefusedError):
        with open(dropped.path, "rb") as fh:
            if fh.read() != dropped.written:
                return
        _replace_todo(dropped.path, dropped.original)


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
            rebase_dir = await _require_rebasing(repo.toplevel)
            had_unmerged = await _has_unmerged(repo.toplevel)
            if action == "continue" and had_unmerged:
                raise GitRefusedError("unmerged", "unmerged paths in the index")
            before = await _progress(repo.toplevel)
            dropped = (
                await _drop_rescheduled_pick(repo.toplevel, rebase_dir)
                if action == "skip"
                else None
            )
            ran = True
            try:
                result = await _run_rebase(repo.toplevel, [f"--{action}"])
                return await _outcome(
                    repo.toplevel,
                    result,
                    action=action,
                    before=before,
                    had_unmerged=had_unmerged,
                )
            # Not on cancellation: git keeps running in its thread, and
            # restoring would race its own read of the todo.
            except Exception:
                if dropped is not None:
                    _restore_todo(dropped)
                raise
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
    """``git rebase --skip``: drops the commit git stopped at, including one git
    rescheduled."""
    return await _step("skip", cwd=cwd, project_name=project_name, event_bus=event_bus)


async def abort_session_rebase(
    *, cwd: str | None, project_name: str | None, event_bus: Any | None
) -> GitRebaseResponse:
    """``git rebase --abort``: back to the branch as it was before the rebase."""
    return await _step("abort", cwd=cwd, project_name=project_name, event_bus=event_bus)
