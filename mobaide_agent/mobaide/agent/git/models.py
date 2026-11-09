"""Pydantic wire models for the git management API.

The unified ``items`` array carries four row kinds, distinguished by the
``kind`` field:

* ``branch_with_worktree`` — local branch checked out in some worktree.
* ``branch_only`` — local branch with no associated worktree (lingering
  auto-scan branches whose worktree was already cleaned up).
* ``orphan_worktree`` — worktree on detached HEAD or whose branch ref no
  longer exists.
* ``remote_branch`` — remote-tracking branch, only when the client asked for
  it with ``?remote=true``; clients that predate the kind never receive a
  row they would offer for delete, and newer ones offer it only behind
  ``capabilities.git.delete_remote``.

Server resolves the relationship between branches and worktrees so the
client never has to.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ItemKind = Literal[
    "branch_with_worktree", "branch_only", "orphan_worktree", "remote_branch"
]


class GitItemRead(BaseModel):
    """One row in the unified git management list.

    ``branch`` is set for ``branch_*`` kinds and, as ``<remote>/<name>``,
    for ``remote_branch``; ``worktree_path`` is set for
    ``branch_with_worktree`` and ``orphan_worktree``; ``remote`` only for
    ``remote_branch`` (remote and branch names may both contain ``/``, so
    the client never splits ``branch`` itself). ``subject`` and
    ``committed_at`` (committer date, ISO 8601) describe the tip commit
    when the ref resolves to one. Status fields apply where relevant;
    clients should ignore irrelevant fields.
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
    remote: str | None = None
    subject: str | None = None
    committed_at: str | None = None


class GitItemsResponse(BaseModel):
    items: list[GitItemRead]


class GitItemsQuery(BaseModel):
    """Query string of ``GET /items``; parsed here so the relay accepts the
    same boolean spellings as FastAPI and answers the same 422."""

    remote: bool = False


class GitDeleteItem(BaseModel):
    """One item in a delete request.

    The wire shape mirrors :class:`GitItemRead`'s discriminator — clients
    echo back the ``kind`` and the appropriate identifier (``branch`` for
    branch kinds, ``worktree_path`` for worktree kinds; both for combined
    deletions). ``force`` upgrades a branch delete from ``-d`` to ``-D`` and
    adds ``--force`` to a worktree removal so a worktree holding
    modified/untracked files is deleted instead of refused.

    ``orphan_worktree`` removes only the worktree at ``worktree_path``,
    whatever row it came from: sent for a ``branch_with_worktree`` row it
    keeps the branch. ``remote_branch`` carries the row's ``branch``
    (``<remote>/<name>``) verbatim and deletes that branch on the remote;
    ``force`` does not apply to it.
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
        * ``auth_required`` / ``network`` / ``remote_not_found`` — a
          ``remote_branch`` push could not reach or log in to the remote.
        * ``rejected`` / ``hook_failed`` — the remote, or a hook, refused
          the deletion.
        * ``git_timeout`` — the push outlasted its budget.
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


class GitBranchCreateRequest(BaseModel):
    """``POST /branches``: create ``name`` at ``start_point`` (``HEAD`` of the
    project root when omitted); ``checkout`` switches the project root to
    it in the same call and is subject to the checkout refusals; ``force``
    skips the busy-holder refusal after the user confirmed it."""

    name: str
    start_point: str | None = None
    checkout: bool = False
    force: bool = False


class GitCheckoutRequest(BaseModel):
    """``POST /checkout``: ``ref`` is a local branch name or a
    ``<remote>/<name>`` remote-tracking branch as listed by ``GET /items``;
    a remote ref without a local branch of that name gets one created,
    tracking it. ``force`` skips the busy-holder refusal after the user
    confirmed it; every other checkout refusal still applies."""

    ref: str
    force: bool = False


class GitBranchResponse(BaseModel):
    """Outcome of ``POST /branches`` and ``POST /checkout``: the local
    branch touched, whether the project root now has it checked out and
    whether the call created it."""

    branch: str
    checked_out: bool
    created: bool


class WorktreeBaseRef(BaseModel):
    """One branch a new worktree can start from. ``ref`` is what the client
    sends back as ``base_branch``: a local branch name, or
    ``<remote>/<name>`` for a remote-tracking branch."""

    ref: str
    kind: Literal["local", "remote"]
    subject: str | None = None
    committed_at: str | None = None


class WorktreeBasesResponse(BaseModel):
    """``GET /worktree-bases``: the base-branch picker of the New Session
    screen. ``default`` is the repository's default branch (``origin/HEAD``
    first) and ``last_used`` the base of the last worktree created in this
    project; both are ``null`` or a ``ref`` present in ``refs``. Clients
    preselect ``last_used``, then ``default``."""

    default: str | None
    last_used: str | None
    refs: list[WorktreeBaseRef]
