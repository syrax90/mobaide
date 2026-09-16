"""Pydantic wire models for the session-scoped git Changes API (status/diff
reads, the stage/unstage/discard/commit mutations and the fetch/pull/push
jobs).

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
# Clients render the Changes viewer on ``changes`` and gate every mutating
# control on its own key, so an agent without a key degrades to read-only.
# ``branches`` covers the project-scoped ``GET /items?remote=true``,
# ``POST /branches`` and ``POST /checkout`` routes.
GIT_CAPABILITIES: dict[str, bool] = {
    "changes": True,
    "stage": True,
    "discard": True,
    "commit": True,
    "fetch": True,
    "pull": True,
    "push": True,
    "branches": True,
}
MESSAGE_LIMIT = 64 * 1024
JOB_LINE_LIMIT = 200
JOB_MESSAGE_LIMIT = 500

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
DiscardScope = Literal["unstaged", "untracked"]
DiffKind = Literal["text", "binary", "submodule", "too_large", "missing"]
DiffChange = Literal[
    "modified", "added", "deleted", "renamed", "copied", "type_changed"
]
DiffLineType = Literal["ctx", "add", "del"]
GitJobOp = Literal["fetch", "pull", "push"]
GitJobStatus = Literal["running", "done"]
GitPullMode = Literal["ff_only", "rebase", "merge"]


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


class GitPathsRequest(BaseModel):
    """Repo-relative paths as ``status`` reported them; a renamed entry lists
    both ``path`` and ``orig_path``."""

    paths: list[str] = Field(min_length=1, max_length=ENTRY_LIMIT)


class GitDiscardRequest(GitPathsRequest):
    scope: DiscardScope


class GitMutationResponse(BaseModel):
    ok: Literal[True] = True


class GitCommitRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MESSAGE_LIMIT)
    amend: bool = False
    no_verify: bool = False


class GitCommitResponse(BaseModel):
    oid: str
    short_oid: str
    subject: str


class GitCommitAuthor(BaseModel):
    name: str
    email: str


class GitCommitHead(BaseModel):
    oid: str
    short_oid: str
    subject: str
    body: str = ""


class GitCommitContextResponse(BaseModel):
    """What the commit sheet needs before the user types: ``author`` is null
    when git cannot determine an identity (the commit would be refused with
    ``identity_required``), ``head`` is null on an unborn branch (no amend)."""

    author: GitCommitAuthor | None = None
    head: GitCommitHead | None = None


class GitPullRequest(BaseModel):
    mode: GitPullMode = "ff_only"


class GitPushRequest(BaseModel):
    force: bool = False


class GitJobResponse(BaseModel):
    """One fetch/pull/push job: the body every job route answers and the
    payload of both ``git.job.progress`` and ``git.job.done``. Every key is
    always present; ``phase``/``percent``/``line`` mirror git's own
    ``--progress`` output, ``ok``/``error_code``/``message`` are null until
    ``status`` is ``done``. ``error_code`` is an open vocabulary (clients
    render unknown codes from ``message``): ``cancelled`` | ``git_timeout``
    | ``auth_required`` | ``network`` | ``remote_not_found`` | ``rejected``
    | ``not_fast_forward`` | ``conflicts`` | ``dirty_worktree`` |
    ``unmerged`` | ``operation_in_progress`` | ``no_upstream`` |
    ``no_remote`` | ``detached_head`` | ``no_head`` | ``hook_failed`` |
    ``index_locked`` | ``git_error``."""

    job_id: str
    session_id: str
    op: GitJobOp
    status: GitJobStatus = "running"
    phase: str | None = None
    percent: int | None = None
    line: str | None = None
    ok: bool | None = None
    error_code: str | None = None
    message: str | None = None
