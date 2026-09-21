"""Git management service — pure logic shared by the HTTP router and the
relay dispatcher.

Responsibilities:

* Combine ``WorktreeManager.list_branches`` and ``list_worktrees`` into the
  unified item shape returned by the API, enriched with each ref's tip
  commit and, on request, the remote-tracking branches.
* Enforce a busy-guard against active terminal sessions and auto-scan
  work items before allowing destructive operations.
* Drive per-item deletes (branch / worktree / branch+worktree) with the
  per-repo lock owned by the manager, delete remote branches with one
  ``git push`` per remote, audit each outcome, and publish ``git.changed``
  after any successful delete.
* Create branches and check them out in the project root through the
  hardened runner, with the refusal vocabulary the session mutations use.
"""
from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from mobaide.core.git_exec import CmdResult, run_git
from mobaide.linear.worktree import (
    BranchDeleteError,
    BranchInfo,
    WorktreeInfo,
    WorktreeManager,
    WorktreeRemoveError,
)

from ..logging import audit_event, get_logger
from .changes_service import (
    _QUICK_TIMEOUT,
    _SMALL_OUTPUT,
    _STATUS_MAX_BYTES,
    _STATUS_TIMEOUT,
    GitBadRequestError,
    GitChangesError,
    GitNotFoundError,
    GitRefusedError,
    GitTimeoutError,
    ResolvedRepo,
    _check_result,
    _fail,
    _lines,
    _lock_for,
    _mutable_repo,
    _ref_exists,
    _text,
    repo_state,
    resolve_repo_dirs,
)
from .models import (
    BusyHolder,
    GitBranchCreateRequest,
    GitBranchResponse,
    GitCheckoutRequest,
    GitDeleteItem,
    GitDeleteRequest,
    GitDeleteResponse,
    GitDeleteResultItem,
    GitItemRead,
    ItemKind,
)

logger = get_logger(__name__)

_LOCKED_PATTERNS = ("is locked",)
_MUTATE_TIMEOUT = 15.0
# Leaves headroom inside the relay's 30 s proxied-request budget; the local
# deletes of the same batch run alongside, not before.
_REMOTE_DELETE_TIMEOUT = 20.0
_REMOTE_DELETE_ERRORS = frozenset(
    {"auth_required", "network", "remote_not_found", "rejected", "hook_failed"}
)
_MAX_BRANCH_NAME_BYTES = 255
_REFS_HEADS = "refs/heads/"
_REFS_REMOTES = "refs/remotes/"
_TIP_FORMAT = "%(refname)%00%(symref)%00%(subject)%00%(committerdate:iso-strict)"
_DIRTY_NEEDLES = (
    "would be overwritten by",
    "would be removed by",
    "would lose untracked files",
    "Your local changes",
    "Please commit your changes",
)
_IN_PROGRESS_NEEDLES = ("cannot switch branch while", "in the middle of an am session")
_ELSEWHERE_RE = re.compile(r"is already (?:used by worktree|checked out) at '(.*)'")


class GitItemBusyError(Exception):
    """Raised when at least one delete target is held by a session or auto-scan."""

    def __init__(self, busy_by: list[BusyHolder]) -> None:
        super().__init__(f"{len(busy_by)} item(s) busy")
        self.busy_by = busy_by


def _normalize_path(path: str | None) -> str | None:
    if not path:
        return None
    try:
        return os.path.normcase(os.path.realpath(path))
    except OSError:
        return os.path.normcase(path)


def _strip_refs_heads(branch_ref: str | None) -> str | None:
    """Strip the ``refs/heads/`` prefix that ``git worktree list --porcelain`` emits."""
    if branch_ref is None:
        return None
    return branch_ref.removeprefix("refs/heads/")


def _build_items(
    branches: list[BranchInfo],
    worktrees: list[WorktreeInfo],
) -> list[GitItemRead]:
    """Merge branches and worktrees into the unified API shape."""
    branch_to_wt: dict[str, WorktreeInfo] = {}
    for wt in worktrees:
        short = _strip_refs_heads(wt.branch)
        if short and not wt.detached:
            branch_to_wt[short] = wt

    branch_names = {b.name for b in branches}
    items: list[GitItemRead] = []

    for branch in branches:
        wt = branch_to_wt.get(branch.name)
        if wt is not None:
            items.append(
                GitItemRead(
                    kind="branch_with_worktree",
                    branch=branch.name,
                    worktree_path=wt.path,
                    is_current=branch.is_current,
                    upstream=branch.upstream,
                    ahead=branch.ahead,
                    behind=branch.behind,
                    gone=branch.gone,
                    locked=wt.locked,
                    locked_reason=wt.locked_reason,
                    prunable=wt.prunable,
                )
            )
        else:
            items.append(
                GitItemRead(
                    kind="branch_only",
                    branch=branch.name,
                    is_current=False,
                    upstream=branch.upstream,
                    ahead=branch.ahead,
                    behind=branch.behind,
                    gone=branch.gone,
                )
            )

    for wt in worktrees:
        short = _strip_refs_heads(wt.branch)
        if wt.detached or short is None or short not in branch_names:
            items.append(
                GitItemRead(
                    kind="orphan_worktree",
                    worktree_path=wt.path,
                    branch=short,
                    detached=wt.detached,
                    locked=wt.locked,
                    locked_reason=wt.locked_reason,
                    prunable=wt.prunable,
                )
            )

    return items


@dataclass(frozen=True)
class _Tip:
    subject: str | None
    committed_at: str | None


async def _quick(cwd: str, args: Sequence[str]) -> CmdResult:
    result = await run_git(
        list(args), cwd=cwd, timeout=_QUICK_TIMEOUT, max_bytes=_SMALL_OUTPUT
    )
    _check_result(result)
    return result


async def _ref_tips(project_root: str) -> tuple[dict[str, _Tip], list[str]]:
    """``refname -> tip`` for every local and remote-tracking branch, plus
    the remote-tracking refnames that are real branches (``<remote>/HEAD``
    is a symref and is left out)."""
    result = await run_git(
        ["for-each-ref", f"--format={_TIP_FORMAT}", _REFS_HEADS, _REFS_REMOTES],
        cwd=project_root,
        timeout=_STATUS_TIMEOUT,
        max_bytes=_STATUS_MAX_BYTES,
    )
    _check_result(result)
    if result.rc != 0 and not result.truncated:
        raise _fail(result, "git for-each-ref failed")
    lines = _text(result.stdout).split("\n")
    if result.truncated or (lines and lines[-1] == ""):
        lines.pop()
    tips: dict[str, _Tip] = {}
    remote_refs: list[str] = []
    for line in lines:
        parts = line.split("\0")
        if len(parts) != 4:
            continue
        refname, symref, subject, committed_at = parts
        tips[refname] = _Tip(subject or None, committed_at or None)
        if refname.startswith(_REFS_REMOTES) and not symref:
            remote_refs.append(refname)
    return tips, remote_refs


async def _remote_names(project_root: str) -> list[str]:
    result = await _quick(project_root, ["remote"])
    if result.rc != 0:
        raise _fail(result, "git remote failed")
    return [line for line in _lines(result.stdout) if line]


def _split_remote_ref(short: str, remotes: Iterable[str]) -> tuple[str, str] | None:
    """``("origin", "feature/x")`` for ``origin/feature/x``; the longest
    configured remote wins because remote names may contain ``/`` too."""
    best: str | None = None
    for remote in remotes:
        prefix = remote + "/"
        if short.startswith(prefix) and len(short) > len(prefix):
            if best is None or len(remote) > len(best):
                best = remote
    if best is None:
        return None
    return best, short[len(best) + 1:]


async def list_git_items(
    project_root: str,
    manager: WorktreeManager,
    *,
    remote: bool = False,
) -> list[GitItemRead]:
    """List the unified git items for ``project_root``.

    Tip-commit enrichment is best-effort for the local rows; with
    ``remote`` the same ``for-each-ref`` call is what lists the remote
    branches, so its failure surfaces instead of silently hiding them.
    """
    branches = await manager.list_branches(project_root)
    worktrees = await manager.list_worktrees(project_root)
    items = _build_items(branches, worktrees)
    try:
        tips, remote_refs = await _ref_tips(project_root)
    except Exception:
        if remote:
            raise
        logger.warning("git.ref_tips_failed", exc_info=True)
        return items
    for item in items:
        tip = tips.get(_REFS_HEADS + item.branch) if item.branch else None
        if tip is not None:
            item.subject = tip.subject
            item.committed_at = tip.committed_at
    if not remote:
        return items
    remotes = await _remote_names(project_root)
    for refname in remote_refs:
        short = refname[len(_REFS_REMOTES):]
        split = _split_remote_ref(short, remotes)
        # A ref under a name that matches no configured remote (an ad-hoc
        # fetch, an interrupted remote rename) cannot be checked out with
        # tracking, so it is not offered.
        if split is None:
            continue
        tip = tips[refname]
        items.append(
            GitItemRead(
                kind="remote_branch",
                branch=short,
                remote=split[0],
                subject=tip.subject,
                committed_at=tip.committed_at,
            )
        )
    return items


@dataclass
class _RepoContext:
    """All collaborators a delete batch or a ref mutation needs."""

    project_name: str
    project_root: str
    manager: WorktreeManager
    session_manager: Any | None
    autoscan_manager: Any | None
    event_bus: Any | None
    actor: str | None


def _affected_paths(items: list[GitDeleteItem]) -> set[str]:
    paths: set[str] = set()
    for item in items:
        norm = _normalize_path(item.worktree_path)
        if norm is not None:
            paths.add(norm)
    return paths


async def _holders(
    session_manager: Any | None,
    autoscan_manager: Any | None,
    matches: Callable[[str], bool],
) -> list[BusyHolder]:
    """Every session / auto-scan work item whose normalised path satisfies
    ``matches``."""
    holders: list[BusyHolder] = []

    if session_manager is not None:
        try:
            sessions = session_manager.list_sessions()
        except Exception:
            sessions = []
        for info in sessions:
            cwd = getattr(info, "cwd", None)
            norm = _normalize_path(cwd)
            if norm is not None and matches(norm):
                holders.append(
                    BusyHolder(
                        kind="session",
                        session_id=getattr(info, "session_id", None),
                        cwd=cwd,
                    )
                )

    if autoscan_manager is not None:
        try:
            workitems_by_project = await autoscan_manager.list_all_workitems()
        except Exception:
            workitems_by_project = {}
        for project_name, items in workitems_by_project.items():
            for item in items:
                norm = _normalize_path(getattr(item, "worktree_path", None))
                if norm is not None and matches(norm):
                    holders.append(
                        BusyHolder(
                            kind="auto_scan",
                            project_name=project_name,
                            worktree_path=getattr(item, "worktree_path", None),
                        )
                    )

    return holders


async def _busy_holders(
    affected: set[str],
    session_manager: Any | None,
    autoscan_manager: Any | None,
) -> list[BusyHolder]:
    """Return every session / auto-scan work item whose path is in ``affected``."""
    if not affected:
        return []
    return await _holders(session_manager, autoscan_manager, affected.__contains__)


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _root_matcher(project_root: str, worktree_paths: Iterable[str]) -> Callable[[str], bool]:
    """Holder predicate for a checkout in the project root: anything at or
    below the root counts, except what lives inside a linked worktree
    (which keeps its own HEAD). Only worktrees strictly inside the root can
    shadow a holder — a project registered at a linked worktree nested in
    the main one must not have its own root hidden by the main worktree's
    path."""
    root = _normalize_path(project_root)
    linked = [
        norm
        for norm in (_normalize_path(path) for path in worktree_paths)
        if norm is not None and root is not None and norm != root and _within(norm, root)
    ]

    def matches(norm: str) -> bool:
        if root is None or not _within(norm, root):
            return False
        return not any(_within(norm, wt) for wt in linked)

    return matches


def _result(
    *,
    kind: ItemKind,
    target: str,
    ok: bool,
    error: str | None = None,
    error_detail: str | None = None,
) -> GitDeleteResultItem:
    return GitDeleteResultItem(
        kind=kind, target=target, ok=ok, error=error, error_detail=error_detail,
    )


def _worktree_error_code(exc: WorktreeRemoveError) -> str:
    """Map a worktree-remove failure to a wire error code.

    A dirty worktree (modified/untracked files) becomes ``force_required`` so
    the client offers a force-delete confirmation, mirroring the unmerged-branch
    flow; a locked worktree stays ``locked``; anything else is ``error``.
    """
    if exc.dirty:
        return "force_required"
    if any(p in exc.stderr for p in _LOCKED_PATTERNS):
        return "locked"
    return "error"


async def _delete_one(
    item: GitDeleteItem,
    ctx: _RepoContext,
) -> tuple[GitDeleteResultItem, bool]:
    """Process a single delete item and return ``(result, did_change)``."""
    if item.kind == "branch_only":
        if not item.branch:
            return _result(kind=item.kind, target="", ok=False, error="invalid"), False
        try:
            await ctx.manager.delete_branch(
                ctx.project_root, item.branch, force=item.force,
            )
        except BranchDeleteError as exc:
            err_code = "force_required" if exc.unmerged else "error"
            return _result(
                kind=item.kind, target=item.branch, ok=False,
                error=err_code, error_detail=exc.stderr,
            ), False
        audit_event(
            "audit.git.delete",
            actor=ctx.actor,
            target=item.branch,
            outcome="ok",
            kind=item.kind,
            project=ctx.project_name,
            force=item.force,
        )
        return _result(kind=item.kind, target=item.branch, ok=True), True

    if item.kind == "orphan_worktree":
        if not item.worktree_path:
            return _result(kind=item.kind, target="", ok=False, error="invalid"), False
        try:
            await ctx.manager.remove_worktree(
                ctx.project_root, item.worktree_path, force=item.force,
            )
        except WorktreeRemoveError as exc:
            return _result(
                kind=item.kind, target=item.worktree_path, ok=False,
                error=_worktree_error_code(exc), error_detail=exc.stderr,
            ), False
        audit_event(
            "audit.git.delete",
            actor=ctx.actor,
            target=item.worktree_path,
            outcome="ok",
            kind=item.kind,
            project=ctx.project_name,
        )
        return _result(kind=item.kind, target=item.worktree_path, ok=True), True

    if item.kind == "branch_with_worktree":
        if not item.branch or not item.worktree_path:
            target = item.branch or item.worktree_path or ""
            return _result(kind=item.kind, target=target, ok=False, error="invalid"), False
        worktree_already_gone = not os.path.exists(item.worktree_path)
        if not worktree_already_gone:
            try:
                await ctx.manager.remove_worktree(
                    ctx.project_root, item.worktree_path, force=item.force,
                )
            except WorktreeRemoveError as exc:
                return _result(
                    kind=item.kind, target=item.worktree_path, ok=False,
                    error=_worktree_error_code(exc), error_detail=exc.stderr,
                ), False
        try:
            await ctx.manager.delete_branch(
                ctx.project_root, item.branch, force=item.force,
            )
        except BranchDeleteError as exc:
            err_code = "force_required" if exc.unmerged else "error"
            return _result(
                kind=item.kind, target=item.branch, ok=False,
                error=err_code, error_detail=exc.stderr,
            ), True
        audit_event(
            "audit.git.delete",
            actor=ctx.actor,
            target=f"{item.branch}@{item.worktree_path}",
            outcome="ok",
            kind=item.kind,
            project=ctx.project_name,
            force=item.force,
        )
        return _result(kind=item.kind, target=item.branch, ok=True), True

    return _result(kind=item.kind, target="", ok=False, error="invalid"), False


def _tracking_source(tracking_ref: str, refspecs: Iterable[str]) -> str | None:
    """The remote branch ``tracking_ref`` is fetched from, read back through
    the remote's fetch refspecs; ``None`` unless exactly one ``refs/heads/``
    source maps to it — a guess from the name alone would delete the wrong
    branch under a non-default refspec."""
    sources: set[str] = set()
    for raw in refspecs:
        spec = raw.strip().removeprefix("+")
        if spec.startswith("^") or spec.count(":") != 1:
            continue
        src, dst = spec.split(":")
        if "*" not in dst:
            if dst == tracking_ref:
                sources.add(src)
            continue
        if dst.count("*") != 1 or src.count("*") != 1:
            continue
        head, tail = dst.split("*")
        if (
            len(tracking_ref) > len(head) + len(tail)
            and tracking_ref.startswith(head)
            and tracking_ref.endswith(tail)
        ):
            sources.add(src.replace("*", tracking_ref[len(head):len(tracking_ref) - len(tail)]))
    if len(sources) != 1:
        return None
    source = sources.pop()
    return source if source.startswith(_REFS_HEADS) and "*" not in source else None


def _push_outcomes(stdout: bytes) -> dict[str, tuple[str, str]]:
    """``remote ref -> (flag, summary)`` from ``git push --porcelain``, whose
    ref lines read ``<flag>\t<from>:<to>\t<summary>``."""
    outcomes: dict[str, tuple[str, str]] = {}
    for line in _lines(stdout):
        parts = line.split("\t")
        if len(parts) != 3 or len(parts[0]) != 1 or ":" not in parts[1]:
            continue
        outcomes[parts[1].split(":", 1)[1]] = (parts[0], parts[2])
    return outcomes


def _remote_failure(item: GitDeleteItem, code: str, detail: str | None) -> GitDeleteResultItem:
    return _result(
        kind="remote_branch", target=item.branch or "", ok=False,
        error=code, error_detail=detail,
    )


def _remote_failures(
    targets: list[tuple[int, GitDeleteItem]], exc: Exception
) -> list[tuple[int, GitDeleteResultItem]]:
    logger.warning("git.remote_delete_failed", exc_info=True)
    code = "git_timeout" if isinstance(exc, GitTimeoutError) else "error"
    detail = getattr(exc, "message", None) or str(exc) or None
    return [(index, _remote_failure(item, code, detail)) for index, item in targets]


async def _fetch_refspecs(project_root: str, remote: str) -> list[str]:
    result = await _quick(project_root, ["config", "--get-all", f"remote.{remote}.fetch"])
    return _lines(result.stdout) if result.rc == 0 else []


async def _push_remote_deletes(
    ctx: _RepoContext,
    remote: str,
    targets: list[tuple[int, GitDeleteItem]],
) -> list[tuple[int, GitDeleteResultItem]]:
    """Delete ``targets`` (all tracking ``remote``) with one ``git push``."""
    from .changes_jobs import _JOB_ENV, classify_job_failure

    refspecs = await _fetch_refspecs(ctx.project_root, remote)
    done: list[tuple[int, GitDeleteResultItem]] = []
    pending: list[tuple[int, GitDeleteItem, str]] = []
    for index, item in targets:
        source = _tracking_source(_REFS_REMOTES + (item.branch or ""), refspecs)
        if source is None:
            done.append((index, _remote_failure(
                item, "error",
                f"the fetch refspecs of '{remote}' do not map {item.branch} to exactly one branch",
            )))
        else:
            pending.append((index, item, source))
    if not pending:
        return done

    result = await run_git(
        ["push", "--porcelain", "--", remote, *(":" + source for _, _, source in pending)],
        cwd=ctx.project_root,
        timeout=_REMOTE_DELETE_TIMEOUT,
        max_bytes=_STATUS_MAX_BYTES,
        env_extra=_JOB_ENV,
    )
    if result.timed_out:
        return done + [
            (index, _remote_failure(item, "git_timeout", None)) for index, item, _ in pending
        ]
    outcomes = _push_outcomes(result.stdout)
    failure: tuple[str, str] | None = None
    for index, item, source in pending:
        flag, summary = outcomes.get(source, ("", ""))
        if flag == "-":
            audit_event(
                "audit.git.delete",
                actor=ctx.actor,
                target=item.branch,
                outcome="ok",
                kind=item.kind,
                project=ctx.project_name,
                remote=remote,
            )
            done.append((index, _result(kind=item.kind, target=item.branch or "", ok=True)))
        elif flag == "!":
            code = "hook_failed" if "hook declined" in summary else "rejected"
            done.append((index, _remote_failure(item, code, summary)))
        else:
            if failure is None:
                code, message = classify_job_failure(
                    "push", err_tail=_lines(result.stderr), out_tail=_lines(result.stdout),
                )
                failure = (code if code in _REMOTE_DELETE_ERRORS else "error", message)
            done.append((index, _remote_failure(item, *failure)))
    return done


async def _delete_remote_group(
    ctx: _RepoContext,
    remote: str,
    targets: list[tuple[int, GitDeleteItem]],
) -> list[tuple[int, GitDeleteResultItem]]:
    """:func:`_push_remote_deletes` that never raises: the batch's local
    deletes may already have happened, so a failure must stay per item."""
    try:
        return await _push_remote_deletes(ctx, remote, targets)
    except Exception as exc:
        return _remote_failures(targets, exc)


async def _delete_remote_items(
    entries: list[tuple[int, GitDeleteItem]],
    ctx: _RepoContext,
) -> list[tuple[int, GitDeleteResultItem]]:
    """Resolve each ``remote_branch`` item against the refs the listing
    offers, then push the deletions, one ``git push`` per remote, the
    remotes in parallel."""
    try:
        remotes = await _remote_names(ctx.project_root)
        _, remote_refs = await _ref_tips(ctx.project_root)
    except Exception as exc:
        return _remote_failures(entries, exc)
    listed = set(remote_refs)
    done: list[tuple[int, GitDeleteResultItem]] = []
    groups: dict[str, list[tuple[int, GitDeleteItem]]] = {}
    for index, item in entries:
        branch = item.branch
        if not branch or "\0" in branch:
            done.append((index, _remote_failure(item, "invalid", None)))
            continue
        split = _split_remote_ref(branch, remotes)
        if split is None or _REFS_REMOTES + branch not in listed:
            done.append((index, _remote_failure(item, "not_found", None)))
            continue
        groups.setdefault(split[0], []).append((index, item))
    for group in await asyncio.gather(
        *(_delete_remote_group(ctx, remote, targets) for remote, targets in groups.items())
    ):
        done.extend(group)
    return done


def _publish_git_changed(
    event_bus: Any | None,
    project_name: str,
    *,
    toplevel: str | None = None,
    common_dir: str | None = None,
) -> None:
    if event_bus is None:
        return
    try:
        event_bus.publish(
            "git.changed",
            {
                "project_name": project_name,
                "toplevel": toplevel,
                "common_dir": common_dir,
            },
        )
    except Exception:
        logger.exception("git.publish_failed")


async def delete_git_items(
    request: GitDeleteRequest,
    *,
    project_name: str,
    project_root: str,
    manager: WorktreeManager,
    session_manager: Any | None,
    autoscan_manager: Any | None,
    event_bus: Any | None,
    actor: str | None,
) -> GitDeleteResponse:
    """Process a delete batch atomically with respect to busy-guard.

    Raises :class:`GitItemBusyError` if any worktree path in the batch is
    held by a session or auto-scan work item — the entire batch is rejected
    and the caller maps to HTTP 409. Otherwise each local item is processed
    in order under the manager's per-repo lock while the ``remote_branch``
    items are pushed alongside; per-item failures are recorded in the
    response, at the item's position, without aborting the batch.
    """
    affected = _affected_paths(request.items)
    busy = await _busy_holders(affected, session_manager, autoscan_manager)
    if busy:
        raise GitItemBusyError(busy_by=busy)

    ctx = _RepoContext(
        project_name=project_name,
        project_root=project_root,
        manager=manager,
        session_manager=session_manager,
        autoscan_manager=autoscan_manager,
        event_bus=event_bus,
        actor=actor,
    )
    local = [(i, item) for i, item in enumerate(request.items) if item.kind != "remote_branch"]
    remote = [(i, item) for i, item in enumerate(request.items) if item.kind == "remote_branch"]
    slots: list[GitDeleteResultItem | None] = [None] * len(request.items)

    async def delete_local() -> bool:
        changed = False
        for index, item in local:
            result, did_change = await _delete_one(item, ctx)
            slots[index] = result
            changed = changed or did_change
        return changed

    async def delete_remote() -> bool:
        if not remote:
            return False
        outcomes = await _delete_remote_items(remote, ctx)
        for index, result in outcomes:
            slots[index] = result
        return any(result.ok for _, result in outcomes)

    local_changed, remote_changed = await asyncio.gather(delete_local(), delete_remote())
    results = [slot for slot in slots if slot is not None]

    if local_changed or remote_changed:
        toplevel, common_dir = await resolve_repo_dirs(project_root)
        _publish_git_changed(
            event_bus, project_name, toplevel=toplevel, common_dir=common_dir
        )

    return GitDeleteResponse(results=results)


def _validate_branch_name(name: str) -> None:
    """The cheap rejections ``check-ref-format --branch`` cannot make: a
    leading ``-`` would read as an option, ``@{-1}`` and ``@`` are
    revision syntax it expands rather than refuses, ``HEAD`` is reserved."""
    if (
        not name
        or len(name.encode("utf-8")) > _MAX_BRANCH_NAME_BYTES
        or "\0" in name
        or name.startswith("-")
        or name in ("HEAD", "@")
        or "@{" in name
    ):
        raise GitBadRequestError("bad_branch_name")


async def _check_branch_name(project_root: str, name: str) -> None:
    _validate_branch_name(name)
    result = await _quick(project_root, ["check-ref-format", "--branch", name])
    if result.rc != 0:
        raise GitBadRequestError("bad_branch_name")


async def _resolve_start_point(project_root: str, start_point: str | None) -> str:
    """The start point as git should read it: a name that is a local or
    remote-tracking branch is qualified first, because a tag of the same name
    would make the bare name ambiguous and ``git branch`` refuses those."""
    start = start_point or "HEAD"
    if start.startswith("-") or "\0" in start:
        raise GitRefusedError("unknown_start_point", start)
    qualified = start
    if start_point:
        for prefix in (_REFS_HEADS, _REFS_REMOTES):
            if await _ref_exists(project_root, prefix + start):
                qualified = prefix + start
                break
    result = await _quick(
        project_root,
        ["rev-parse", "-q", "--verify", "--end-of-options", f"{qualified}^{{commit}}"],
    )
    if result.rc != 0:
        raise GitRefusedError("unknown_start_point", start)
    return qualified


async def _resolve_checkout(project_root: str, ref: str) -> tuple[str, str | None]:
    """``(local branch, start point)``: ``start point`` is the remote-tracking
    ref to create the branch from, ``None`` when the branch already exists."""
    if await _ref_exists(project_root, _REFS_HEADS + ref):
        return ref, None
    if not await _ref_exists(project_root, _REFS_REMOTES + ref):
        raise GitNotFoundError("ref_not_found")
    split = _split_remote_ref(ref, await _remote_names(project_root))
    if split is None:
        raise GitNotFoundError("ref_not_found")
    short = split[1]
    if await _ref_exists(project_root, _REFS_HEADS + short):
        return short, None
    return short, ref


def _refuse_running_job(toplevel: str) -> None:
    """A fetch/pull/push job holds the toplevel lock for its whole run, so
    this must be asked *before* queuing on that lock: waiting would only
    turn into a relay timeout with the mutation landing afterwards."""
    from .changes_jobs import job_registry

    active = job_registry.active_job_id(toplevel)
    if active is not None:
        raise GitRefusedError("job_running", active)


async def _guard_checkout(ctx: _RepoContext, repo: ResolvedRepo) -> None:
    """Refuse to move the project root's HEAD under a session or auto-scan
    item working in it, while a fetch/pull/push job owns the repository, or
    in the middle of a merge/rebase/cherry-pick/revert/bisect."""
    worktrees = await ctx.manager.list_worktrees(ctx.project_root)
    matches = _root_matcher(ctx.project_root, [wt.path for wt in worktrees])
    busy = await _holders(ctx.session_manager, ctx.autoscan_manager, matches)
    if busy:
        raise GitItemBusyError(busy_by=busy)
    _refuse_running_job(repo.toplevel)
    state = await repo_state(repo.toplevel)
    if state != "clean":
        raise GitRefusedError("operation_in_progress", state)


def _classify_ref_failure(result: CmdResult, fallback: str) -> GitChangesError:
    """Turn a failed ``git branch`` / ``git switch`` into the refusal
    vocabulary; ``git switch`` decides for itself whether local changes
    survive the move, so ``dirty`` is only ever what it refused."""
    stderr = _text(result.stderr)
    detail = stderr.strip() or _text(result.stdout).strip() or fallback
    elsewhere = _ELSEWHERE_RE.search(stderr)
    if elsewhere is not None:
        return GitRefusedError("checked_out_elsewhere", elsewhere.group(1))
    if any(needle in stderr for needle in _DIRTY_NEEDLES):
        return GitRefusedError("dirty", detail)
    if any(needle in stderr for needle in _IN_PROGRESS_NEEDLES):
        return GitRefusedError("operation_in_progress", detail)
    if "is not a valid branch name" in stderr:
        return GitBadRequestError("bad_branch_name")
    if "already exists" in stderr:
        return GitRefusedError("exists", detail)
    if "index.lock" in stderr:
        return GitRefusedError("index_locked", detail)
    return _fail(result, fallback)


async def _run_ref_mutation(
    ctx: _RepoContext, repo: ResolvedRepo, args: Sequence[str], fallback: str
) -> None:
    result = await run_git(
        list(args),
        cwd=ctx.project_root,
        timeout=_MUTATE_TIMEOUT,
        max_bytes=_SMALL_OUTPUT,
    )
    _check_result(result)
    if result.rc != 0:
        raise _classify_ref_failure(result, fallback)


async def create_branch(
    request: GitBranchCreateRequest,
    *,
    project_name: str,
    project_root: str,
    manager: WorktreeManager,
    session_manager: Any | None,
    autoscan_manager: Any | None,
    event_bus: Any | None,
    actor: str | None,
) -> GitBranchResponse:
    """Create ``request.name`` at ``request.start_point`` in the project's
    repository; with ``checkout`` the project root switches to it in the
    same ``git switch -c``, under the checkout guards."""
    repo = await _mutable_repo(project_root)
    _refuse_running_job(repo.toplevel)
    ctx = _RepoContext(
        project_name=project_name,
        project_root=project_root,
        manager=manager,
        session_manager=session_manager,
        autoscan_manager=autoscan_manager,
        event_bus=event_bus,
        actor=actor,
    )
    name = request.name
    async with manager._lock_for(project_root):
        async with _lock_for(repo.toplevel):
            await _check_branch_name(project_root, name)
            start = await _resolve_start_point(project_root, request.start_point)
            if await _ref_exists(project_root, _REFS_HEADS + name):
                raise GitRefusedError("exists", name)
            if request.checkout:
                await _guard_checkout(ctx, repo)
                args = ["switch", "--no-guess", "-c", name, "--", start]
            else:
                args = ["branch", "--", name, start]
            await _run_ref_mutation(ctx, repo, args, "git branch failed")
    audit_event(
        "audit.git.branch.created",
        actor=actor,
        target=name,
        outcome="ok",
        project=project_name,
        start_point=start,
        checkout=request.checkout,
    )
    _publish_git_changed(
        event_bus, project_name, toplevel=repo.toplevel, common_dir=repo.common_dir
    )
    return GitBranchResponse(branch=name, checked_out=request.checkout, created=True)


async def checkout_ref(
    request: GitCheckoutRequest,
    *,
    project_name: str,
    project_root: str,
    manager: WorktreeManager,
    session_manager: Any | None,
    autoscan_manager: Any | None,
    event_bus: Any | None,
    actor: str | None,
) -> GitBranchResponse:
    """Switch the project root to ``request.ref``: a local branch as is, a
    remote-tracking branch through the local branch of the same name,
    created tracking it when missing."""
    repo = await _mutable_repo(project_root)
    _refuse_running_job(repo.toplevel)
    ref = request.ref
    if not ref or ref.startswith("-") or "\0" in ref:
        raise GitBadRequestError("bad_ref")
    ctx = _RepoContext(
        project_name=project_name,
        project_root=project_root,
        manager=manager,
        session_manager=session_manager,
        autoscan_manager=autoscan_manager,
        event_bus=event_bus,
        actor=actor,
    )
    async with manager._lock_for(project_root):
        async with _lock_for(repo.toplevel):
            await _guard_checkout(ctx, repo)
            branch, start = await _resolve_checkout(project_root, ref)
            if start is None:
                args = ["switch", "--no-guess", "--", branch]
            else:
                args = ["switch", "--no-guess", "--track", "-c", branch, "--", start]
            await _run_ref_mutation(ctx, repo, args, "git switch failed")
    audit_event(
        "audit.git.checked_out",
        actor=actor,
        target=branch,
        outcome="ok",
        project=project_name,
        ref=ref,
        created=start is not None,
    )
    _publish_git_changed(
        event_bus, project_name, toplevel=repo.toplevel, common_dir=repo.common_dir
    )
    return GitBranchResponse(branch=branch, checked_out=True, created=start is not None)
