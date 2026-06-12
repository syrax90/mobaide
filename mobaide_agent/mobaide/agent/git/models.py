"""Pydantic wire models for the git management API.

The unified ``items`` array carries three row kinds, distinguished by the
``kind`` field:

* ``branch_with_worktree`` — local branch checked out in some worktree.
* ``branch_only`` — local branch with no associated worktree (lingering
  auto-scan branches whose worktree was already cleaned up).
* ``orphan_worktree`` — worktree on detached HEAD or whose branch ref no
  longer exists.

Server resolves the relationship between branches and worktrees so the
client never has to.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ItemKind = Literal["branch_with_worktree", "branch_only", "orphan_worktree"]


class GitItemRead(BaseModel):
    """One row in the unified git management list.

    ``branch`` is set for ``branch_*`` kinds; ``worktree_path`` is set for
    ``branch_with_worktree`` and ``orphan_worktree``. Status fields apply
    where relevant; clients should ignore irrelevant fields.
    """

    kind: ItemKind
    branch: str | None = None
    worktree_path: str | None = None
    is_current: bool = False
    upstream: str | None = None
    ahead: int = 0
    behind: int = 0
    gone: bool = False
    locked: bool = False
    locked_reason: str | None = None
    detached: bool = False
    prunable: bool = False


class GitItemsResponse(BaseModel):
    items: list[GitItemRead]


class GitDeleteItem(BaseModel):
    """One item in a delete request.

    The wire shape mirrors :class:`GitItemRead`'s discriminator — clients
    echo back the ``kind`` and the appropriate identifier (``branch`` for
    branch kinds, ``worktree_path`` for worktree kinds; both for combined
    deletions). ``force`` upgrades a branch delete from ``-d`` to ``-D`` and
    adds ``--force`` to a worktree removal so a worktree holding
    modified/untracked files is deleted instead of refused.
    """

    kind: ItemKind
    branch: str | None = None
    worktree_path: str | None = None
    force: bool = False


class GitDeleteRequest(BaseModel):
    items: list[GitDeleteItem] = Field(default_factory=list)


class GitDeleteResultItem(BaseModel):
    """Per-item outcome.

    ``error`` codes:
        * ``force_required`` — branch is unmerged, or worktree holds
          modified/untracked files; retry with ``force=True``.
        * ``locked`` — worktree is locked.
        * ``not_found`` — branch or worktree no longer exists.
        * ``invalid`` — request payload missing the required identifier.
        * ``error`` — other git failure (full message in ``error_detail``).
    """

    kind: ItemKind
    target: str
    ok: bool
    error: str | None = None
    error_detail: str | None = None


class GitDeleteResponse(BaseModel):
    results: list[GitDeleteResultItem]


class BusyHolder(BaseModel):
    """One reason why a delete was refused with 409."""

    kind: Literal["session", "auto_scan"]
    session_id: str | None = None
    project_name: str | None = None
    cwd: str | None = None
    worktree_path: str | None = None


class BusyByResponse(BaseModel):
    busy_by: list[BusyHolder]
