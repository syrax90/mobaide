"""Pydantic wire models for the session-scoped git Changes API (status/diff
reads, the stage/unstage/discard/commit mutations, the fetch/pull/push
jobs and the rebase routes).

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
# ``POST /branches`` and ``POST /checkout`` routes. ``rebase`` covers the four
# ``POST .../git/rebase`` routes and ``repo.rebase`` on status; ``resolve``
# certifies that ``stage`` refuses an unmerged path still holding conflict
# markers, which is what makes a "Mark resolved" row action safe to offer.
# ``delete_remote`` lets ``POST /items:delete`` take ``remote_branch`` items.
GIT_CAPABILITIES: dict[str, bool] = {
    "changes": True,
    "stage": True,
    "discard": True,
    "commit": True,
    "fetch": True,
    "pull": True,
    "push": True,
    "branches": True,
    "rebase": True,
    "resolve": True,
    "delete_remote": True,
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


class GitRebaseCommit(BaseModel):
    oid: str
    short_oid: str
    subject: str


class GitRebaseInfo(BaseModel):
    """The rebase in progress, read from git's state directory: ``branch`` is
    null for a detached HEAD, ``onto_ref`` is a branch pointing at the base
    (local first) or null, ``stopped_on`` is the commit git stopped at, and
    ``step``/``total`` count the todo list. Any key can be null — a rebase
    git was killed in need not have written every file."""

    branch: str | None = None
    onto_oid: str | None = None
    onto_short_oid: str | None = None
    onto_ref: str | None = None
    stopped_on: GitRebaseCommit | None = None
    step: int | None = None
    total: int | None = None


class GitRepoInfo(BaseModel):
    toplevel: str
    git_dir: str
    common_dir: str
    is_linked_worktree: bool = False
    head: GitHead
    upstream: GitUpstream | None = None
    state: RepoState = "clean"
    rebase: GitRebaseInfo | None = None


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


class GitRebaseRequest(BaseModel):
    onto: str = Field(min_length=1)


class GitRebaseResponse(BaseModel):
    """Outcome of a rebase route. ``outcome`` is an open vocabulary:
    ``rebased`` | ``up_to_date`` | ``stopped`` | ``aborted``. ``rebase`` is set
    only when ``stopped``; ``message`` is git's explanation of a stop that is
    not a conflict (an untracked file in the way, an ``edit`` stop)."""

    outcome: str
    rebase: GitRebaseInfo | None = None
    message: str | None = None


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
