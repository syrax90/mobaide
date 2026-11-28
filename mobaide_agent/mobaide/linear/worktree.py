from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import NoReturn

from mobaide.core.git_exec import NO_PROMPT_ENV, git_binary, git_env, run_capped

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

# A local command (``worktree add`` on a large repo included) finishes well
# inside _GIT_TIMEOUT. A fetch is best-effort and runs while the relay waits
# for the create, so it gets a short bound that leaves room for the rest of
# the create inside the central server's 30 s proxy budget.
_GIT_TIMEOUT = 60.0
_FETCH_TIMEOUT = 10.0
_GIT_MAX_BYTES = 32 * 1024 * 1024
_TIMEOUT_RC = 124
# After a fetch times out, the same fetch in the same repo is skipped for a
# while: creates queue on the repo lock, and each one waiting the full fetch
# timeout again would push the later ones past the proxy budget.
_FETCH_BACKOFF = 60.0
_fetch_timed_out_at: dict[tuple[str, tuple[str, ...]], float] = {}
# The base-branch picker refetches on every open and window focus; one
# network fetch per repo in this window serves all of them.
_REMOTES_FRESH_FOR = 30.0
# A listing waits out the whole fetch bound: nothing tells an open picker
# that refs changed later. The margin covers the thread hand-off.
_REMOTES_WAIT = _FETCH_TIMEOUT + 1.0


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
    real git stderr instead of silently landing in the workspace root.
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
    """Raised when ``git worktree remove`` fails.

    ``dirty`` is set when stderr matches git's "contains modified or untracked
    files" wording, which the API maps to ``error="force_required"`` so the
    client can prompt the user to retry with ``force=True``.
    """

    def __init__(self, *, path: str, stderr: str, dirty: bool = False) -> None:
        super().__init__(f"Failed to remove worktree at {path}: {stderr}")
        self.path = path
        self.stderr = stderr
        self.dirty = dirty


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


async def create_standard_worktree(
    mgr: WorktreeManager,
    workspace_root: str,
    branch_name: str | None,
    *,
    fallback_prefix: str = "claude-session",
    base_branch: str | None = None,
) -> WorktreeResult:
    """Create a user-named worktree for Standard (Claude) or Terminal mode.

    Shared by ``ClaudeLauncher`` (Claude Standard mode) and ``SessionManager``
    (Terminal mode). A blank/``None`` ``branch_name`` gets a unique
    ``{fallback_prefix}-<hex>`` branch so two worktree-on sessions never
    collide on the same worktree directory; a supplied name is used verbatim.
    A blank ``base_branch`` keeps the default start point.
    """
    branch = branch_name or f"{fallback_prefix}-{uuid.uuid4().hex[:8]}"
    slug = convert_to_worktree_slug("", branch)
    return await mgr.create_worktree(
        slug,
        workspace_root,
        branch_name=branch,
        base_branch=(base_branch or "").strip() or None,
    )


async def _run_git(
    *args: str, cwd: str, timeout: float = _GIT_TIMEOUT,
) -> tuple[int, str, str]:
    """Run a git command and return (returncode, stdout, stderr).

    Goes through :func:`mobaide.core.git_exec.run_capped`, which kills the
    whole process tree on timeout (a stalled ``ssh`` or ``git-remote-https``
    child would otherwise keep the pipes open), with the no-prompt
    environment, so nothing waits on a sign-in dialog nobody sees. A timeout
    returns ``_TIMEOUT_RC`` with a "timed out" stderr instead of raising.
    """
    if not os.path.isdir(cwd):
        return (1, "", f"Directory does not exist: {cwd}")
    binary = git_binary()
    if binary is None:
        return (127, "", "git is not installed or not on PATH")
    try:
        result = await asyncio.to_thread(
            run_capped,
            [binary, *args],
            cwd=cwd,
            timeout=timeout,
            max_bytes=_GIT_MAX_BYTES,
            env=git_env(NO_PROMPT_ENV),
        )
    except OSError as exc:
        return (1, "", str(exc))
    stdout = result.stdout.decode(errors="replace")
    stderr = result.stderr.decode(errors="replace")
    command = args[0] if args else "git"
    if result.timed_out:
        return (_TIMEOUT_RC, stdout, f"git {command} timed out after {timeout:.0f} s")
    if result.truncated:
        return (1, stdout, f"git {command} printed more than {_GIT_MAX_BYTES} bytes")
    return (result.rc, stdout, stderr)


async def _fetch(*args: str, cwd: str) -> None:
    """Best-effort fetch: on failure or timeout the cached refs are used."""
    key = (os.path.normcase(os.path.realpath(cwd)), tuple(a for a in args if a != "--"))
    timed_out_at = _fetch_timed_out_at.get(key)
    if timed_out_at is not None and time.monotonic() - timed_out_at < _FETCH_BACKOFF:
        logger.info(
            "worktree.fetch_skipped args=%s: timed out %.0f s ago",
            " ".join(args), time.monotonic() - timed_out_at,
        )
        return
    rc, _, stderr = await _run_git("fetch", *args, cwd=cwd, timeout=_FETCH_TIMEOUT)
    if rc == _TIMEOUT_RC:
        _fetch_timed_out_at[key] = time.monotonic()
    else:
        _fetch_timed_out_at.pop(key, None)
    if rc == _TIMEOUT_RC:
        logger.warning(
            "worktree.fetch_timeout args=%s: branching from the last fetched state",
            " ".join(args),
        )
    elif rc != 0:
        logger.info("worktree.fetch_failed args=%s: %s", " ".join(args), stderr.strip())


_TRACK_PATTERN = re.compile(r"\[(?:ahead (\d+))?(?:, )?(?:behind (\d+))?\]")


def _parse_for_each_ref(stdout: str) -> list[BranchInfo]:
    """Parse the NUL-delimited output of ``git for-each-ref refs/heads/``.

    Format: ``%(refname:lstrip=2)%00%(upstream:short)%00%(upstream:track)%00%(objectname)%00%(HEAD)``
    Entries separated by newline; fields within an entry separated by NUL.
    ``lstrip=2``, not ``short``: ``short`` turns a branch that shares its name
    with a tag into ``heads/<name>``, which no ``refs/heads/`` lookup matches.
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

_WORKTREE_DIRTY_PATTERNS = (
    "contains modified or untracked files",
)


def _same_dir(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


# Marks which repository owns a workspace folder under the worktrees folder, so
# two repositories with the same folder name never share one. The file name is
# stored on disk, so it keeps the old "project" name.
WORKSPACE_OWNER_FILE = ".mobaide-project"

_UNSAFE_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def _safe_dir_name(name: str) -> str:
    """``name`` as one folder name that is valid on every OS."""
    cleaned = _UNSAFE_NAME_CHARS.sub("_", name).rstrip(" .")
    if not cleaned:
        return "project"
    if cleaned.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        return f"{cleaned}_"
    return cleaned


def _canonical_root(workspace_root: str) -> str:
    return os.path.normcase(os.path.realpath(workspace_root))


def _read_owner(folder: str) -> str | None:
    """The owner recorded in ``folder``; ``None`` when there is none."""
    try:
        with open(
            os.path.join(folder, WORKSPACE_OWNER_FILE),
            encoding="utf-8", errors="surrogateescape",
        ) as fh:
            return fh.read().strip() or None
    except OSError:
        return None


# Serializes claims of all repositories in this process; the per-repo locks
# do not cover two repositories that want the same folder name.
_claim_lock = threading.Lock()


def _claim(folder: str, owner: str) -> bool:
    """Make ``owner`` the owner of ``folder`` if nobody owns it yet.

    The owner file is written in full beside ``folder`` and then hard-linked
    into place. A link fails when the file already exists, so a claim never
    overwrites another owner and nobody reads a half-written file.
    """
    marker = os.path.join(folder, WORKSPACE_OWNER_FILE)
    tmp = os.path.join(
        os.path.dirname(folder), f".{WORKSPACE_OWNER_FILE}-{uuid.uuid4().hex}.tmp",
    )
    with _claim_lock:
        try:
            os.makedirs(folder, exist_ok=True)
            if any(entry != WORKSPACE_OWNER_FILE for entry in os.listdir(folder)):
                return False
            with open(tmp, "w", encoding="utf-8", errors="surrogateescape") as fh:
                fh.write(owner + "\n")
            try:
                os.link(tmp, marker)
            except FileExistsError:
                pass
            except OSError:
                # A file system without hard links (FAT, some network shares).
                if not os.path.exists(marker):
                    os.replace(tmp, marker)
        except OSError:
            return False
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        return _read_owner(folder) == owner


class WorktreeManager:
    """Create and manage git worktrees and local branches for a workspace.

    Mutating operations acquire a per-repo ``asyncio.Lock`` keyed by the
    realpath-normalized workspace root, so concurrent ``create_worktree`` /
    ``cleanup_worktree`` / ``delete_branch`` / ``remove_worktree`` calls
    against the same repo are serialized while different repos remain
    independent. Listings (``list_branches``, ``list_worktrees``) do not
    acquire the lock — they are read-only and safe to interleave.
    """

    def __init__(
        self,
        *,
        worktrees_dir: str,
        branch_prefix: str,
        main_branch: str = "main",
        legacy_worktree_base: str | None = None,
    ) -> None:
        """``worktrees_dir`` holds new worktrees at
        ``<worktrees_dir>/<workspace folder name>/<slug>``.
        ``legacy_worktree_base`` is the folder inside the workspace root where
        earlier versions put worktrees; one found there is reused."""
        self._worktrees_dir = worktrees_dir
        self._legacy_worktree_base = legacy_worktree_base
        self._branch_prefix = branch_prefix
        self._main_branch = main_branch
        self._repo_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._refresh_tasks: dict[str, asyncio.Task[None]] = {}
        self._remotes_fetched_at: dict[str, float] = {}

    def _lock_for(self, workspace_root: str) -> asyncio.Lock:
        try:
            key = os.path.realpath(workspace_root)
        except OSError:
            key = workspace_root
        return self._repo_locks[key]

    def workspace_worktrees_dir(self, workspace_root: str) -> str:
        """The folder that holds the worktrees of ``workspace_root``.

        Its name is the workspace folder name. When another repository already
        owns that name, a short hash of the workspace path is appended.
        """
        owner = _canonical_root(workspace_root)
        name = _safe_dir_name(os.path.basename(os.path.realpath(workspace_root)))
        digest = hashlib.sha256(os.fsencode(owner)).hexdigest()[:8]
        candidates = (name, f"{name}-{digest}")
        for candidate in candidates:
            folder = os.path.join(self._worktrees_dir, candidate)
            current = _read_owner(folder)
            if current == owner or (current is None and _claim(folder, owner)):
                return folder
        logger.warning(
            "Could not claim worktree folder %s for %s, using it anyway",
            candidates[-1], owner,
        )
        return os.path.join(self._worktrees_dir, candidates[-1])

    def _legacy_worktree_dir(self, workspace_root: str, slug: str) -> str | None:
        base = self._legacy_worktree_base
        if not base:
            return None
        # An absolute base replaces the workspace root, as it did before.
        path = os.path.join(workspace_root, base, slug)
        # Only a linked worktree counts. When the old and the new folder are
        # the same, ``<base>/<slug>`` can also be the folder of a workspace.
        if not os.path.exists(os.path.join(path, ".git")) or os.path.exists(
            os.path.join(path, WORKSPACE_OWNER_FILE)
        ):
            return None
        return path

    async def _ref_resolves(self, workspace_root: str, ref: str) -> bool:
        rc, _, _ = await _run_git(
            "rev-parse", "--verify", "--quiet", "--end-of-options",
            f"{ref}^{{commit}}",
            cwd=workspace_root,
        )
        return rc == 0

    async def default_base(self, workspace_root: str) -> str | None:
        """The branch a worktree starts from when the client names none.

        ``origin/HEAD`` (the remote's default branch, set by ``git clone`` or
        ``git remote set-head``) wins, then ``origin/<main_branch>``, then the
        local ``<main_branch>``. ``None`` means "branch from ``HEAD``". Reads
        local refs only, so it never touches the network.
        """
        rc, stdout, _ = await _run_git(
            "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD",
            cwd=workspace_root,
        )
        origin_head = stdout.strip()
        if rc == 0 and origin_head and await self._ref_resolves(
            workspace_root, f"refs/remotes/{origin_head}",
        ):
            return origin_head
        if await self._ref_resolves(
            workspace_root, f"refs/remotes/origin/{self._main_branch}",
        ):
            return f"origin/{self._main_branch}"
        if await self._ref_resolves(
            workspace_root, f"refs/heads/{self._main_branch}",
        ):
            return self._main_branch
        return None

    async def refresh_remotes(self, workspace_root: str) -> None:
        """Best-effort ``fetch --all --prune`` of the remote-tracking refs.

        Callers share one fetch, reused for ``_REMOTES_FRESH_FOR``, and wait
        at most ``_REMOTES_WAIT``. Takes no repo lock, so a running create
        never delays a listing; offline or a timeout keeps the cached refs.
        """
        key = _canonical_root(workspace_root)
        task = self._refresh_tasks.get(key)
        if task is None:
            fetched_at = self._remotes_fetched_at.get(key)
            if fetched_at is not None and time.monotonic() - fetched_at < _REMOTES_FRESH_FOR:
                return
            task = asyncio.create_task(
                self._fetch_remotes(key, workspace_root),
                name="worktree-refresh-remotes",
            )
            self._refresh_tasks[key] = task
        await asyncio.wait({task}, timeout=_REMOTES_WAIT)

    async def _fetch_remotes(self, key: str, workspace_root: str) -> None:
        try:
            await _fetch("--all", "--prune", cwd=workspace_root)
        except Exception:
            logger.warning("worktree.refresh_remotes_failed", exc_info=True)
        finally:
            self._remotes_fetched_at[key] = time.monotonic()
            self._refresh_tasks.pop(key, None)

    async def _remote_of(self, workspace_root: str, ref: str) -> str | None:
        """The configured remote ``ref`` (``<remote>/<branch>``) lives under;
        the longest name wins because remote names may contain ``/``."""
        rc, stdout, _ = await _run_git("remote", cwd=workspace_root)
        if rc != 0:
            return None
        best: str | None = None
        for remote in (line.strip() for line in stdout.splitlines()):
            if remote and ref.startswith(remote + "/") and len(ref) > len(remote) + 1:
                if best is None or len(remote) > len(best):
                    best = remote
        return best

    async def _resolve_base(
        self, workspace_root: str, base_branch: str,
    ) -> str | None:
        """Full refname of a client-chosen base, local branch first.

        A remote-tracking base is fetched from its remote first (best-effort,
        offline keeps the cached ref) so the worktree starts at the remote's
        current tip. Full refnames keep a same-named tag from winning.
        """
        local = f"refs/heads/{base_branch}"
        if await self._ref_resolves(workspace_root, local):
            return local
        remote = await self._remote_of(workspace_root, base_branch)
        if remote is None:
            return None
        await _fetch("--", remote, cwd=workspace_root)
        tracking = f"refs/remotes/{base_branch}"
        if await self._ref_resolves(workspace_root, tracking):
            return tracking
        return None

    async def _resolve_start_point(self, workspace_root: str) -> str:
        """Pick a commit-ish to branch from that actually exists in this repo.

        Fetches ``origin`` first, then takes :meth:`default_base`, falling
        back to ``HEAD`` so a worktree can still be created in a repo with
        no ``origin`` and no ``<main_branch>``.
        """
        await _fetch("origin", cwd=workspace_root)
        base = await self.default_base(workspace_root)
        if base is not None:
            return base
        logger.warning(
            "No origin/HEAD, origin/%s or local %s found, branching from HEAD",
            self._main_branch, self._main_branch,
        )
        return "HEAD"

    @log_call(
        name="worktree.create_worktree",
        log_args=("slug", "branch_name", "base_branch"),
    )
    async def create_worktree(
        self,
        slug: str,
        workspace_root: str,
        *,
        branch_name: str | None = None,
        base_branch: str | None = None,
    ) -> WorktreeResult:
        """Create a git worktree with an independent branch.

        When ``branch_name`` is None (Auto Scan path), the branch is derived
        from ``{branch_prefix}/{slug}`` — the existing auto-scan convention.
        When ``branch_name`` is provided (Standard-mode path), it is used
        verbatim, with no prefix applied, so the user's entered branch name
        actually becomes the git branch name.

        A branch already checked out in another linked worktree reopens that
        worktree (``is_new=False``) rather than failing git's one-checkout
        rule.

        ``base_branch`` names the local or remote-tracking branch a new
        branch starts from; ``None`` takes the default start point. It is
        ignored when an existing worktree or branch is reused.

        Raises ``WorktreeCreationError`` if the worktree cannot be created.
        The launcher surfaces this as HTTP 400 so the user sees the real
        git stderr instead of silently landing in the workspace root.
        """
        async with self._lock_for(workspace_root):
            return await self._create_worktree_unlocked(
                slug, workspace_root, branch_name=branch_name,
                base_branch=base_branch,
            )

    async def _create_worktree_unlocked(
        self,
        slug: str,
        workspace_root: str,
        *,
        branch_name: str | None,
        base_branch: str | None = None,
    ) -> WorktreeResult:
        if branch_name is None:
            branch_name = (
                f"{self._branch_prefix}/{slug}" if self._branch_prefix else slug
            )
        # Git refs cannot begin with '-'; reject early so a client-supplied
        # branch name can never be parsed as an option by ``git worktree add``
        # (defense-in-depth against argument injection).
        if branch_name.startswith("-"):
            raise WorktreeCreationError(
                branch=branch_name,
                path=slug,
                stderr="branch name must not start with '-'",
            )
        if base_branch is not None and base_branch.startswith("-"):
            raise WorktreeCreationError(
                branch=branch_name,
                path=slug,
                stderr="base branch must not start with '-'",
            )

        legacy_dir = self._legacy_worktree_dir(workspace_root, slug)
        if legacy_dir is not None:
            logger.info("Reusing worktree %s from the earlier location", legacy_dir)
            return WorktreeResult(path=legacy_dir, is_new=False, branch=branch_name)

        workspace_dir = await asyncio.to_thread(self.workspace_worktrees_dir, workspace_root)
        wt_dir = os.path.join(workspace_dir, slug)

        if os.path.exists(wt_dir):
            logger.warning("Worktree already exists at %s, reusing it", wt_dir)
            return WorktreeResult(path=wt_dir, is_new=False, branch=branch_name)

        # git refuses to check a branch out twice, so a branch already living in
        # a worktree outside ``wt_dir`` (an earlier worktrees folder, or made by
        # hand) reopens it; a registration whose directory is gone is pruned.
        existing = await self._find_branch_worktree(workspace_root, branch_name)
        if existing is not None:
            if not existing.prunable and os.path.isdir(existing.path):
                path = os.path.normpath(existing.path)
                logger.info(
                    "Branch %s is already checked out at %s, reusing it",
                    branch_name, path,
                )
                return WorktreeResult(path=path, is_new=False, branch=branch_name)
            if existing.prunable and not existing.locked:
                logger.info(
                    "Pruning stale worktree %s that still holds branch %s",
                    existing.path, branch_name,
                )
                await _run_git("worktree", "prune", cwd=workspace_root)

        await _run_git("config", "core.longpaths", "true", cwd=workspace_root)

        # An existing branch keeps its own history, so a chosen base is moot
        # and must not block reopening it (the base may be deleted by now).
        reuse_branch = base_branch is not None and await self._ref_resolves(
            workspace_root, f"refs/heads/{branch_name}",
        )
        if reuse_branch:
            first_err = ""
        else:
            if base_branch is None:
                start_point = await self._resolve_start_point(workspace_root)
            else:
                resolved = await self._resolve_base(workspace_root, base_branch)
                if resolved is None:
                    raise WorktreeCreationError(
                        branch=branch_name,
                        path=wt_dir,
                        stderr=f"base branch '{base_branch}' not found",
                    )
                start_point = resolved

            branch_existed = await self._ref_resolves(
                workspace_root, f"refs/heads/{branch_name}",
            )
            logger.info(
                "Creating worktree at %s with branch %s from %s",
                wt_dir,
                branch_name,
                start_point,
            )
            rc, _, stderr = await _run_git(
                "worktree", "add", "--no-track", wt_dir, "-b", branch_name, start_point,
                cwd=workspace_root,
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

            if rc == _TIMEOUT_RC:
                await self._discard_partial_worktree(
                    workspace_root, wt_dir,
                    created_branch=None if branch_existed else branch_name,
                )
                self._raise_create_failed(branch_name, wt_dir, stderr.strip())
            first_err = stderr.strip()
            logger.warning("Branch may already exist, retrying without -b: %s", first_err)

        rc, _, stderr = await _run_git(
            "worktree", "add", "--", wt_dir, branch_name,
            cwd=workspace_root,
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

        if rc == _TIMEOUT_RC:
            await self._discard_partial_worktree(workspace_root, wt_dir, created_branch=None)
        self._raise_create_failed(branch_name, wt_dir, stderr.strip() or first_err)

    async def _discard_partial_worktree(
        self, workspace_root: str, wt_dir: str, *, created_branch: str | None,
    ) -> None:
        """Undo a ``worktree add`` killed on timeout. SIGKILL skips git's own
        cleanup, and a leftover ``wt_dir`` would be reused half checked out by
        the next create. ``wt_dir`` did not exist before this create."""
        # git marks a worktree it is still checking out as locked
        # ("initializing"); a locked entry survives remove --force and prune.
        await _run_git("worktree", "unlock", "--", wt_dir, cwd=workspace_root)
        await _run_git("worktree", "remove", "--force", "--", wt_dir, cwd=workspace_root)
        if os.path.exists(wt_dir):
            await asyncio.to_thread(shutil.rmtree, wt_dir, ignore_errors=True)
        await _run_git("worktree", "prune", cwd=workspace_root)
        if created_branch is not None:
            await _run_git("branch", "-D", created_branch, cwd=workspace_root)

    @staticmethod
    def _raise_create_failed(branch_name: str, wt_dir: str, stderr: str) -> NoReturn:
        logger.error("Failed to create worktree at %s: %s", wt_dir, stderr)
        audit_event(
            "audit.worktree.create_failed",
            target=wt_dir,
            outcome="error",
            branch=branch_name,
            error=stderr,
        )
        raise WorktreeCreationError(branch=branch_name, path=wt_dir, stderr=stderr)

    async def _find_branch_worktree(
        self, workspace_root: str, branch_name: str,
    ) -> WorktreeInfo | None:
        """Return the linked worktree that has ``branch_name`` checked out.

        The main worktree and ``workspace_root`` itself are skipped: a worktree
        session must never land in the workspace's own checkout. A failed
        listing yields ``None`` so creation proceeds and git reports the error.
        """
        try:
            worktrees = await self.list_worktrees(workspace_root)
        except RuntimeError as exc:
            logger.warning("Could not list worktrees of %s: %s", workspace_root, exc)
            return None
        ref = f"refs/heads/{branch_name}"
        for wt in worktrees[1:]:
            if wt.branch == ref and not _same_dir(wt.path, workspace_root):
                return wt
        return None

    @log_call(name="worktree.cleanup_worktree", log_args=("path",))
    async def cleanup_worktree(
        self, path: str, workspace_root: str, *, force: bool = True,
    ) -> bool:
        """Remove a git worktree.

        Uses ``--force`` unless ``force`` is false (git then keeps a worktree
        with uncommitted changes), and silently swallows failures by design —
        auto-scan relies on this contract for retry-on-next-cycle semantics.
        New callers that need failure propagation should use ``remove_worktree``.
        """
        args = ["worktree", "remove", path, *(["--force"] if force else [])]
        async with self._lock_for(workspace_root):
            rc, _, stderr = await _run_git(*args, cwd=workspace_root)
            if rc != 0:
                logger.error("Failed to remove worktree %s: %s", path, stderr.strip())
                return False
            logger.info("Worktree removed: %s", path)
            audit_event("audit.worktree.removed", target=path, outcome="ok")
            return True

    async def list_branches(self, workspace_root: str) -> list[BranchInfo]:
        """Enumerate local branches via ``git for-each-ref refs/heads/``."""
        fmt = "%(refname:lstrip=2)%00%(upstream:short)%00%(upstream:track)%00%(objectname)%00%(HEAD)"
        rc, stdout, stderr = await _run_git(
            "for-each-ref", f"--format={fmt}", "refs/heads/",
            cwd=workspace_root,
        )
        if rc != 0:
            raise RuntimeError(f"git for-each-ref failed: {stderr.strip()}")
        return _parse_for_each_ref(stdout)

    async def list_worktrees(self, workspace_root: str) -> list[WorktreeInfo]:
        """Enumerate worktrees via ``git worktree list --porcelain``."""
        rc, stdout, stderr = await _run_git(
            "worktree", "list", "--porcelain",
            cwd=workspace_root,
        )
        if rc != 0:
            raise RuntimeError(f"git worktree list failed: {stderr.strip()}")
        return _parse_worktree_porcelain(stdout)

    async def linked_worktree_branch(self, workspace_root: str, path: str) -> str | None:
        """The local branch checked out in the linked worktree at ``path``.

        ``None`` when ``path`` is the main checkout, is not a worktree of
        ``workspace_root``, or has a detached HEAD.
        """
        if _same_dir(path, workspace_root):
            return None
        worktrees = await self.list_worktrees(workspace_root)
        # The first stanza is always the main checkout.
        for info in worktrees[1:]:
            if _same_dir(info.path, path):
                if info.detached or not info.branch:
                    return None
                return info.branch.removeprefix("refs/heads/")
        return None

    @log_call(name="worktree.delete_branch", log_args=("name", "force"))
    async def delete_branch(
        self,
        workspace_root: str,
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
        async with self._lock_for(workspace_root):
            rc, _, stderr = await _run_git(
                "branch", flag, name, cwd=workspace_root,
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

    @log_call(name="worktree.remove_worktree", log_args=("path", "force"))
    async def remove_worktree(
        self,
        workspace_root: str,
        path: str,
        *,
        force: bool = False,
    ) -> None:
        """Remove a git worktree via ``git worktree remove`` and prune.

        Unlike :meth:`cleanup_worktree`, failures are propagated as
        :class:`WorktreeRemoveError` so the API can surface them to the user.
        Without ``force`` a worktree holding modified/untracked files is left
        intact and :attr:`WorktreeRemoveError.dirty` is set so the caller can
        map to ``force_required`` and prompt the user; ``force=True`` passes
        ``--force`` to discard that work.
        """
        extra = ("--force",) if force else ()
        async with self._lock_for(workspace_root):
            rc, _, stderr = await _run_git(
                "worktree", "remove", path, *extra, cwd=workspace_root,
            )
            if rc != 0:
                err = stderr.strip()
                dirty = (not force) and any(
                    p in err for p in _WORKTREE_DIRTY_PATTERNS
                )
                logger.warning(
                    "Failed to remove worktree %s (force=%s): %s", path, force, err,
                )
                audit_event(
                    "audit.git.worktree.remove_failed",
                    target=path,
                    outcome="error",
                    force=force,
                    error=err,
                )
                raise WorktreeRemoveError(path=path, stderr=err, dirty=dirty)
            await _run_git("worktree", "prune", cwd=workspace_root)
            logger.info("Worktree removed and pruned: %s", path)
            audit_event(
                "audit.git.worktree.removed",
                target=path,
                outcome="ok",
            )
