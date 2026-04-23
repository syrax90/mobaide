from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass

try:
    from claude_mobile.agent.logging import audit_event, get_logger, log_call
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
    """Run a git command and return (returncode, stdout, stderr)."""
    if not os.path.isdir(cwd):
        return (1, "", f"Directory does not exist: {cwd}")
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return (
        proc.returncode or 0,
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
    )


class WorktreeManager:
    """Create and manage git worktrees for auto-scan issues."""

    def __init__(
        self,
        worktree_base: str,
        branch_prefix: str,
        main_branch: str = "main",
    ) -> None:
        self._worktree_base = worktree_base
        self._branch_prefix = branch_prefix
        self._main_branch = main_branch

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
        """Remove a git worktree."""
        rc, _, stderr = await _run_git(
            "worktree", "remove", path, "--force", cwd=project_root
        )
        if rc != 0:
            logger.error("Failed to remove worktree %s: %s", path, stderr.strip())
            return False
        logger.info("Worktree removed: %s", path)
        audit_event("audit.worktree.removed", target=path, outcome="ok")
        return True
