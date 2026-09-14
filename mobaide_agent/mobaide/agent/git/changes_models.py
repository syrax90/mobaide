"""Pydantic wire models for the session-scoped read-only git Changes API.

``GET /api/sessions/{id}/git/status`` and ``.../git/diff`` share one parser
on the agent so the three clients render dumb, pre-shaped JSON. Every key is
always emitted (nulls included); ``unchanged: true`` carries neutral values
in the other fields and tells the client to keep its cached body.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ENTRY_LIMIT = 5000
LINE_LIMIT = 5000
BYTE_LIMIT = 1024 * 1024

# Advertised as ``capabilities.git`` on ``GET /api/version`` (both transports).
GIT_CAPABILITIES: dict[str, bool] = {"changes": True}

UnavailableReason = Literal[
    "not_a_repo",
    "bare_repository",
    "cwd_missing",
    "unsafe_directory",
    "git_too_old",
]
RepoState = Literal[
    "clean", "merging", "rebasing", "cherry_picking", "reverting", "bisecting"
]
EntryGroup = Literal["conflicts", "staged", "unstaged", "untracked"]
DiffScope = Literal["staged", "unstaged", "untracked"]
DiffKind = Literal["text", "binary", "submodule", "too_large", "missing"]
DiffChange = Literal[
    "modified", "added", "deleted", "renamed", "copied", "type_changed"
]
DiffLineType = Literal["ctx", "add", "del"]


class GitHead(BaseModel):
    oid: str | None = None
    short_oid: str | None = None
    branch: str | None = None
    detached: bool = False
    initial: bool = False


class GitUpstream(BaseModel):
    """``ahead``/``behind`` are null when the upstream ref no longer exists."""

    ref: str
    ahead: int | None = None
    behind: int | None = None


class GitRepoInfo(BaseModel):
    toplevel: str
    git_dir: str
    common_dir: str
    is_linked_worktree: bool = False
    head: GitHead
    upstream: GitUpstream | None = None
    state: RepoState = "clean"


class GitStatusEntry(BaseModel):
    """One row of the Changes list; a file that is both staged and modified
    yields one entry per group."""

    group: EntryGroup
    path: str
    orig_path: str | None = None
    code: str
    index_status: str
    worktree_status: str
    rename_score: int | None = None
    submodule: bool = False


class GitStatusCounts(BaseModel):
    conflicts: int = 0
    staged: int = 0
    unstaged: int = 0
    untracked: int = 0
    total: int = 0


class GitStatusResponse(BaseModel):
    session_id: str
    cwd: str | None = None
    etag: str
    unchanged: bool = False
    unavailable_reason: UnavailableReason | None = None
    repo: GitRepoInfo | None = None
    entries: list[GitStatusEntry] = Field(default_factory=list)
    counts: GitStatusCounts | None = None
    truncated: bool = False
    entry_limit: int = ENTRY_LIMIT


class GitDiffLine(BaseModel):
    t: DiffLineType
    old: int | None = None
    new: int | None = None
    text: str
    no_newline: bool = False


class GitDiffHunk(BaseModel):
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    heading: str = ""
    lines: list[GitDiffLine] = Field(default_factory=list)


class GitDiffResponse(BaseModel):
    path: str
    orig_path: str | None = None
    scope: DiffScope
    kind: DiffKind
    change: DiffChange | None = None
    old_mode: str | None = None
    new_mode: str | None = None
    additions: int = 0
    deletions: int = 0
    hunks: list[GitDiffHunk] = Field(default_factory=list)
    truncated: bool = False
    total_lines: int = 0
    line_limit: int = LINE_LIMIT
    byte_limit: int = BYTE_LIMIT
