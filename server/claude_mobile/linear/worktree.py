from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class WorktreeResult:
    """Result of a worktree creation attempt."""

    path: str
    is_new: bool
    branch: str


def convert_to_worktree_slug(identifier: str, title: str) -> str:
    """Convert a Linear issue identifier and title into a safe worktree directory name.

    Produces a lowercase, dash-separated slug. Non-alphanumeric characters
    (except dashes) are replaced with dashes. Limited to 10 tokens to keep
    paths manageable on Windows.

    Mirrors PS1 ConvertTo-WorktreeSlug (lines 340-369).
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
    """Creates and manages git worktrees for auto-scan issues.

    Reimplements PS1 New-GitWorktree (lines 270-338).
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

    async def create_worktree(
        self, slug: str, project_root: str
    ) -> WorktreeResult | None:
        """Create a git worktree with a tracking branch for the given slug.

        Returns WorktreeResult on success, None on failure.
        """
        branch_name = f"{self._branch_prefix}/{slug}" if self._branch_prefix else slug
        resolved_base = os.path.join(project_root, self._worktree_base)
        wt_dir = os.path.join(resolved_base, slug)

        os.makedirs(resolved_base, exist_ok=True)

        if os.path.exists(wt_dir):
            logger.warning("Worktree already exists at %s, reusing it", wt_dir)
            return WorktreeResult(path=wt_dir, is_new=False, branch=branch_name)

        # Enable long paths (Windows)
        rc, _, _ = await _run_git("config", "core.longpaths", "true", cwd=project_root)
        if rc == 0:
            logger.debug("Enabled core.longpaths")

        # Fetch from origin
        logger.debug("Fetching from origin...")
        rc, _, stderr = await _run_git("fetch", "origin", cwd=project_root)
        if rc != 0:
            logger.warning(
                "git fetch failed, worktree will branch from local state: %s",
                stderr.strip(),
            )

        start_point = f"origin/{self._main_branch}"

        # Attempt 1: create new branch
        logger.info(
            "Creating worktree at %s with branch %s from %s",
            wt_dir,
            branch_name,
            start_point,
        )
        rc, _, stderr = await _run_git(
            "worktree", "add", wt_dir, "-b", branch_name, start_point,
            cwd=project_root,
        )
        if rc == 0:
            logger.info("Worktree created successfully")
            return WorktreeResult(path=wt_dir, is_new=True, branch=branch_name)

        # Attempt 2: branch may already exist — retry without -b
        logger.warning("Branch may already exist, retrying without -b: %s", stderr.strip())
        rc, _, stderr = await _run_git(
            "worktree", "add", wt_dir, branch_name,
            cwd=project_root,
        )
        if rc == 0:
            logger.info("Worktree created successfully (existing branch)")
            return WorktreeResult(path=wt_dir, is_new=True, branch=branch_name)

        logger.error(
            "Failed to create worktree: %s. Falling back to project root.",
            stderr.strip(),
        )
        return None

    async def cleanup_worktree(self, path: str, project_root: str) -> bool:
        """Remove a git worktree."""
        rc, _, stderr = await _run_git(
            "worktree", "remove", path, "--force", cwd=project_root
        )
        if rc != 0:
            logger.error("Failed to remove worktree %s: %s", path, stderr.strip())
            return False
        logger.info("Worktree removed: %s", path)
        return True
