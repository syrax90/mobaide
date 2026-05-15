from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass

# Suppress the transient ``git`` console window the Scheduled-Task-spawned
# agent (no parent console under ``pythonw.exe``) would otherwise pop on
# every worktree / branch listing call. ``CREATE_NO_WINDOW`` keeps
# ``capture_output=True``'s stdio inheritance intact.
_WINDOWS_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

try:
    from mobaide.agent.logging import audit_event, get_logger, log_call
except Exception:  # pragma: no cover
    import logging
    def get_logger(name): return logging.getLogger(name)
    def log_call(*args, **kwargs):
        def deco(fn): return fn
        return deco
    def audit_event(*args, **kwargs): pass

logger = get_logger(__name__)


@dataclass
class WorktreeResult:
    """Result of a worktree creation attempt."""

    path: str
    is_new: bool
    branch: str


@dataclass
class BranchInfo:
    """One row from ``git for-each-ref refs/heads/``."""

    name: str
    sha: str
    upstream: str | None
    ahead: int
    behind: int
    gone: bool
    is_current: bool


@dataclass
class WorktreeInfo:
    """One stanza from ``git worktree list --porcelain``."""

    path: str
    head: str | None
    branch: str | None
    detached: bool
    locked: bool
    locked_reason: str | None
    prunable: bool
    prunable_reason: str | None


class WorktreeCreationError(RuntimeError):
    """Raised when ``git worktree add`` cannot produce a usable worktree.

    The launcher converts this into an HTTP 400 so the mobile client sees the
    real git stderr instead of silently landing in the project root.
    """

    def __init__(self, *, branch: str, path: str, stderr: str) -> None:
        super().__init__(
            f"Failed to create worktree at {path} on branch {branch}: {stderr}"
        )
        self.branch = branch
        self.path = path
        self.stderr = stderr


class BranchDeleteError(RuntimeError):
    """Raised when ``git branch -d/-D`` fails.

    ``unmerged`` is set when stderr matches the "not fully merged" pattern,
    which the API maps to ``error="force_required"`` so the client can prompt
    the user to retry with ``force=True``.
    """

    def __init__(self, *, name: str, stderr: str, unmerged: bool) -> None:
        super().__init__(f"Failed to delete branch {name}: {stderr}")
        self.name = name
        self.stderr = stderr
        self.unmerged = unmerged


class WorktreeRemoveError(RuntimeError):
    """Raised when ``git worktree remove`` fails."""

    def __init__(self, *, path: str, stderr: str) -> None:
        super().__init__(f"Failed to remove worktree at {path}: {stderr}")
        self.path = path
        self.stderr = stderr


def convert_to_worktree_slug(identifier: str, title: str) -> str:
    """Convert a Linear issue identifier and title into a safe worktree directory name.

    Produces a lowercase, dash-separated slug. Non-alphanumeric characters
    (except dashes) are replaced with dashes. Limited to 10 tokens to keep
    paths manageable on Windows.
    """
    slug = f"{identifier} {title}".lower()
    slug = re.sub(r"[^a-z0-9-]", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    slug = slug.strip("-")

    parts = slug.split("-")
    if len(parts) > 10:
        slug = "-".join(parts[:10])

    if not slug:
        slug = re.sub(r"[^a-z0-9-]", "-", identifier.lower())

    return slug


async def _run_git(
    *args: str, cwd: str
) -> tuple[int, str, str]:
    """Run a git command and return (returncode, stdout, stderr).

    Uses ``asyncio.to_thread(subprocess.run, ...)`` rather than
    ``asyncio.create_subprocess_exec``: the latter raises
    ``NotImplementedError`` under ``WindowsSelectorEventLoop`` (the policy
    the agent installs on Windows to work around python-websockets'
    Proactor recv hangs — see ``mobaide.__main__.main`` for the rationale).
    Wrapping the blocking ``subprocess.run`` in a thread keeps the
    coroutine API unchanged for callers while staying loop-agnostic on
    every platform.
    """
    if not os.path.isdir(cwd):
        return (1, "", f"Directory does not exist: {cwd}")
    completed = await asyncio.to_thread(
        subprocess.run,
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        creationflags=_WINDOWS_NO_WINDOW,
    )
    return (
        completed.returncode or 0,
        completed.stdout.decode(errors="replace"),
        completed.stderr.decode(errors="replace"),
    )


_TRACK_PATTERN = re.compile(r"\[(?:ahead (\d+))?(?:, )?(?:behind (\d+))?\]")


def _parse_for_each_ref(stdout: str) -> list[BranchInfo]:
    """Parse the NUL-delimited output of ``git for-each-ref refs/heads/``.

    Format: ``%(refname:short)%00%(upstream:short)%00%(upstream:track)%00%(objectname)%00%(HEAD)``
    Entries separated by newline; fields within an entry separated by NUL.
    """
    branches: list[BranchInfo] = []
    for line in stdout.split("\n"):
        if not line.strip():
            continue
        parts = line.split("\x00")
        if len(parts) < 5:
            continue
        name, upstream, track, sha, head = parts[:5]
        gone = "[gone]" in track
        ahead = 0
        behind = 0
        if not gone:
            match = _TRACK_PATTERN.search(track)
            if match:
                if match.group(1):
                    ahead = int(match.group(1))
                if match.group(2):
                    behind = int(match.group(2))
        branches.append(
            BranchInfo(
                name=name,
                sha=sha,
                upstream=upstream or None,
                ahead=ahead,
                behind=behind,
                gone=gone,
                is_current=(head == "*"),
            )
        )
    return branches


def _parse_worktree_porcelain(stdout: str) -> list[WorktreeInfo]:
    """Parse ``git worktree list --porcelain`` output.

    Each worktree stanza is a sequence of key-value lines terminated by a
    blank line. Recognized keys: ``worktree``, ``HEAD``, ``branch``,
    ``bare``, ``detached``, ``locked`` (optional reason), ``prunable``
    (optional reason).
    """
    worktrees: list[WorktreeInfo] = []
    path: str | None = None
    head: str | None = None
    branch: str | None = None
    detached = False
    locked = False
    locked_reason: str | None = None
    prunable = False
    prunable_reason: str | None = None

    def flush() -> None:
        nonlocal path, head, branch, detached, locked, locked_reason, prunable, prunable_reason
        if path is not None:
            worktrees.append(
                WorktreeInfo(
                    path=path,
                    head=head,
                    branch=branch,
                    detached=detached,
                    locked=locked,
                    locked_reason=locked_reason,
                    prunable=prunable,
                    prunable_reason=prunable_reason,
                )
            )
        path = None
        head = None
        branch = None
        detached = False
        locked = False
        locked_reason = None
        prunable = False
        prunable_reason = None

    for raw_line in stdout.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            flush()
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            path = value
        elif key == "HEAD":
            head = value
        elif key == "branch":
            branch = value
        elif key == "detached":
            detached = True
        elif key == "locked":
            locked = True
            locked_reason = value or None
        elif key == "prunable":
            prunable = True
            prunable_reason = value or None
    flush()
    return worktrees


_UNMERGED_PATTERNS = (
    "is not fully merged",
    "not fully merged",
)


class WorktreeManager:
    """Create and manage git worktrees and local branches for a project.

    Mutating operations acquire a per-repo ``asyncio.Lock`` keyed by the
    realpath-normalized project root, so concurrent ``create_worktree`` /
    ``cleanup_worktree`` / ``delete_branch`` / ``remove_worktree`` calls
    against the same repo are serialized while different repos remain
    independent. Listings (``list_branches``, ``list_worktrees``) do not
    acquire the lock — they are read-only and safe to interleave.
    """

    def __init__(
        self,
        worktree_base: str,
        branch_prefix: str,
        main_branch: str = "main",
    ) -> None:
        self._worktree_base = worktree_base
        self._branch_prefix = branch_prefix
        self._main_branch = main_branch
        self._repo_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def _lock_for(self, project_root: str) -> asyncio.Lock:
        try:
            key = os.path.realpath(project_root)
        except OSError:
            key = project_root
        return self._repo_locks[key]

    async def _resolve_start_point(self, project_root: str) -> str:
        """Pick a commit-ish to branch from that actually exists in this repo.

        The historical behavior — always branching from ``origin/<main>`` —
        silently failed in common environments (repos using ``master``, no
        ``origin`` remote, offline or auth-broken fetch). We now fall back
        through origin → local → ``HEAD`` so a worktree can still be created.
        """
        await _run_git("fetch", "origin", cwd=project_root)
        origin_ref = f"origin/{self._main_branch}"
        rc, _, _ = await _run_git(
            "rev-parse", "--verify", "--quiet", origin_ref, cwd=project_root
        )
        if rc == 0:
            return origin_ref

        local_ref = f"refs/heads/{self._main_branch}"
        rc, _, _ = await _run_git(
            "rev-parse", "--verify", "--quiet", local_ref, cwd=project_root
        )
        if rc == 0:
            return self._main_branch

        logger.warning(
            "No origin/%s or local %s found, branching from HEAD",
            self._main_branch, self._main_branch,
        )
        return "HEAD"

    @log_call(name="worktree.create_worktree", log_args=("slug", "branch_name"))
    async def create_worktree(
        self,
        slug: str,
        project_root: str,
        *,
        branch_name: str | None = None,
    ) -> WorktreeResult:
        """Create a git worktree with an independent branch.

        When ``branch_name`` is None (Auto Scan path), the branch is derived
        from ``{branch_prefix}/{slug}`` — the existing auto-scan convention.
        When ``branch_name`` is provided (Standard-mode path), it is used
        verbatim, with no prefix applied, so the user's entered branch name
        actually becomes the git branch name.

        Raises ``WorktreeCreationError`` if the worktree cannot be created.
        The launcher surfaces this as HTTP 400 so the user sees the real
        git stderr instead of silently landing in the project root.
        """
        async with self._lock_for(project_root):
            return await self._create_worktree_unlocked(
                slug, project_root, branch_name=branch_name,
            )

    async def _create_worktree_unlocked(
        self,
        slug: str,
        project_root: str,
        *,
        branch_name: str | None,
    ) -> WorktreeResult:
        if branch_name is None:
            branch_name = (
                f"{self._branch_prefix}/{slug}" if self._branch_prefix else slug
            )
        resolved_base = os.path.join(project_root, self._worktree_base)
        wt_dir = os.path.join(resolved_base, slug)

        os.makedirs(resolved_base, exist_ok=True)

        if os.path.exists(wt_dir):
            logger.warning("Worktree already exists at %s, reusing it", wt_dir)
            return WorktreeResult(path=wt_dir, is_new=False, branch=branch_name)

        await _run_git("config", "core.longpaths", "true", cwd=project_root)

        start_point = await self._resolve_start_point(project_root)

        logger.info(
            "Creating worktree at %s with branch %s from %s",
            wt_dir,
            branch_name,
            start_point,
        )
        rc, _, stderr = await _run_git(
            "worktree", "add", "--no-track", wt_dir, "-b", branch_name, start_point,
            cwd=project_root,
        )
        if rc == 0:
            logger.info("Worktree created successfully")
            audit_event(
                "audit.worktree.created",
                target=wt_dir,
                outcome="ok",
                branch=branch_name,
            )
            return WorktreeResult(path=wt_dir, is_new=True, branch=branch_name)

        first_err = stderr.strip()
        logger.warning("Branch may already exist, retrying without -b: %s", first_err)
        rc, _, stderr = await _run_git(
            "worktree", "add", wt_dir, branch_name,
            cwd=project_root,
        )
        if rc == 0:
            logger.info("Worktree created successfully (existing branch)")
            audit_event(
                "audit.worktree.created",
                target=wt_dir,
                outcome="ok",
                branch=branch_name,
                existing_branch=True,
            )
            return WorktreeResult(path=wt_dir, is_new=True, branch=branch_name)

        combined_stderr = stderr.strip() or first_err
        logger.error(
            "Failed to create worktree at %s: %s",
            wt_dir, combined_stderr,
        )
        audit_event(
            "audit.worktree.create_failed",
            target=wt_dir,
            outcome="error",
            branch=branch_name,
            error=combined_stderr,
        )
        raise WorktreeCreationError(
            branch=branch_name, path=wt_dir, stderr=combined_stderr,
        )

    @log_call(name="worktree.cleanup_worktree", log_args=("path",))
    async def cleanup_worktree(self, path: str, project_root: str) -> bool:
        """Remove a git worktree.

        Uses ``--force`` and silently swallows failures by design — auto-scan
        relies on this contract for retry-on-next-cycle semantics. New
        callers that need failure propagation should use ``remove_worktree``.
        """
        async with self._lock_for(project_root):
            rc, _, stderr = await _run_git(
                "worktree", "remove", path, "--force", cwd=project_root
            )
            if rc != 0:
                logger.error("Failed to remove worktree %s: %s", path, stderr.strip())
                return False
            logger.info("Worktree removed: %s", path)
            audit_event("audit.worktree.removed", target=path, outcome="ok")
            return True

    async def list_branches(self, project_root: str) -> list[BranchInfo]:
        """Enumerate local branches via ``git for-each-ref refs/heads/``."""
        fmt = "%(refname:short)%00%(upstream:short)%00%(upstream:track)%00%(objectname)%00%(HEAD)"
        rc, stdout, stderr = await _run_git(
            "for-each-ref", f"--format={fmt}", "refs/heads/",
            cwd=project_root,
        )
        if rc != 0:
            raise RuntimeError(f"git for-each-ref failed: {stderr.strip()}")
        return _parse_for_each_ref(stdout)

    async def list_worktrees(self, project_root: str) -> list[WorktreeInfo]:
        """Enumerate worktrees via ``git worktree list --porcelain``."""
        rc, stdout, stderr = await _run_git(
            "worktree", "list", "--porcelain",
            cwd=project_root,
        )
        if rc != 0:
            raise RuntimeError(f"git worktree list failed: {stderr.strip()}")
        return _parse_worktree_porcelain(stdout)

    @log_call(name="worktree.delete_branch", log_args=("name", "force"))
    async def delete_branch(
        self,
        project_root: str,
        name: str,
        *,
        force: bool = False,
    ) -> None:
        """Delete a local branch via ``git branch -d`` (or ``-D`` when forced).

        Raises :class:`BranchDeleteError` on failure. ``unmerged=True`` is
        set when stderr matches git's "not fully merged" wording so the
        caller can map to ``force_required``.
        """
        flag = "-D" if force else "-d"
        async with self._lock_for(project_root):
            rc, _, stderr = await _run_git(
                "branch", flag, name, cwd=project_root,
            )
            if rc != 0:
                err = stderr.strip()
                unmerged = (not force) and any(p in err for p in _UNMERGED_PATTERNS)
                logger.warning(
                    "Failed to delete branch %s (force=%s): %s", name, force, err,
                )
                audit_event(
                    "audit.git.branch.delete_failed",
                    target=name,
                    outcome="error",
                    force=force,
                    error=err,
                )
                raise BranchDeleteError(name=name, stderr=err, unmerged=unmerged)
            logger.info("Branch deleted: %s (force=%s)", name, force)
            audit_event(
                "audit.git.branch.deleted",
                target=name,
                outcome="ok",
                force=force,
            )

    @log_call(name="worktree.remove_worktree", log_args=("path",))
    async def remove_worktree(self, project_root: str, path: str) -> None:
        """Remove a git worktree via ``git worktree remove`` and prune.

        Unlike :meth:`cleanup_worktree`, failures are propagated as
        :class:`WorktreeRemoveError` so the API can surface them to the user.
        Refuses to use ``--force`` — locked or dirty worktrees must be
        unlocked/cleaned in the user's terminal first.
        """
        async with self._lock_for(project_root):
            rc, _, stderr = await _run_git(
                "worktree", "remove", path, cwd=project_root,
            )
            if rc != 0:
                err = stderr.strip()
                logger.warning("Failed to remove worktree %s: %s", path, err)
                audit_event(
                    "audit.git.worktree.remove_failed",
                    target=path,
                    outcome="error",
                    error=err,
                )
                raise WorktreeRemoveError(path=path, stderr=err)
            await _run_git("worktree", "prune", cwd=project_root)
            logger.info("Worktree removed and pruned: %s", path)
            audit_event(
                "audit.git.worktree.removed",
                target=path,
                outcome="ok",
            )
