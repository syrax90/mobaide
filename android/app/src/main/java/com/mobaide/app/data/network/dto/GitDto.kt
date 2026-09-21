package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable

/**
 * One row in the unified git management list.
 *
 * `kind` discriminates between four shapes:
 *  - `branch_with_worktree` — local branch checked out in some worktree.
 *  - `branch_only` — local branch with no associated worktree.
 *  - `orphan_worktree` — worktree on detached HEAD or whose branch ref no longer exists.
 *  - `remote_branch` — a remote-tracking branch (`branch` is `<remote>/<name>`,
 *    `remote` names the remote), only listed when asked for with `?remote=true`.
 *
 * The server resolves the branch↔worktree relationship; the client only renders.
 * `subject` / `committed_at` describe the tip commit when the ref has one.
 */
@Serializable
data class GitItem(
    val kind: String,
    val branch: String? = null,
    val worktree_path: String? = null,
    val is_current: Boolean = false,
    val upstream: String? = null,
    val ahead: Int = 0,
    val behind: Int = 0,
    val gone: Boolean = false,
    val locked: Boolean = false,
    val locked_reason: String? = null,
    val detached: Boolean = false,
    val prunable: Boolean = false,
    val remote: String? = null,
    val subject: String? = null,
    val committed_at: String? = null,
) {
    companion object {
        const val KIND_BRANCH_WITH_WORKTREE = "branch_with_worktree"
        const val KIND_BRANCH_ONLY = "branch_only"
        const val KIND_ORPHAN_WORKTREE = "orphan_worktree"
        const val KIND_REMOTE_BRANCH = "remote_branch"
    }

    /** Stable identifier for selection state. */
    val id: String
        get() = when (kind) {
            KIND_BRANCH_WITH_WORKTREE, KIND_BRANCH_ONLY ->
                "$kind:${branch ?: worktree_path.orEmpty()}"
            KIND_ORPHAN_WORKTREE ->
                "$kind:${worktree_path.orEmpty()}"
            else -> "$kind:${branch.orEmpty()}|${worktree_path.orEmpty()}"
        }

    /**
     * False for the checked-out branch (git refuses); a remote-tracking row only
     * with [deleteRemote] (`capabilities.git.delete_remote`) — an older agent
     * answers the whole batch with a 422.
     */
    fun isDeletable(deleteRemote: Boolean): Boolean =
        !is_current && (kind != KIND_REMOTE_BRANCH || deleteRemote)

    /** *Remove worktree* keeps the branch: a worktree-backed row other than the project root's checkout. */
    val canRemoveWorktree: Boolean
        get() = !is_current && kind == KIND_BRANCH_WITH_WORKTREE && !worktree_path.isNullOrEmpty()

    /**
     * Check out is offered for a branch nothing has checked out: a `branch_only`
     * row or a remote-tracking one; never the current branch or a worktree-backed row.
     */
    val canCheckOut: Boolean
        get() = !is_current && (kind == KIND_BRANCH_ONLY || kind == KIND_REMOTE_BRANCH)
}

@Serializable
data class GitItemsResponse(val items: List<GitItem>)

@Serializable
data class GitDeleteItem(
    val kind: String,
    val branch: String? = null,
    val worktree_path: String? = null,
    val force: Boolean = false,
)

@Serializable
data class GitDeleteRequest(val items: List<GitDeleteItem>)

@Serializable
data class GitDeleteResultItem(
    val kind: String,
    val target: String,
    val ok: Boolean,
    val error: String? = null,
    val error_detail: String? = null,
)

@Serializable
data class GitDeleteResponse(val results: List<GitDeleteResultItem>)

@Serializable
data class BusyHolder(
    val kind: String,
    val session_id: String? = null,
    val project_name: String? = null,
    val cwd: String? = null,
    val worktree_path: String? = null,
)

@Serializable
data class BusyByResponse(val busy_by: List<BusyHolder>)

/** `POST /api/projects/{name}/git/branches`. */
@Serializable
data class GitBranchCreateRequest(
    val name: String,
    val start_point: String? = null,
    val checkout: Boolean = false,
)

/** `POST /api/projects/{name}/git/checkout`; `ref` is a row's `branch` verbatim. */
@Serializable
data class GitCheckoutRequest(val ref: String)

@Serializable
data class GitBranchResponse(
    val branch: String,
    val checked_out: Boolean,
    val created: Boolean,
)

// The agent always emits every key, nulls included; the defaults below mirror
// the agent's pydantic models so the null-dropped fixture shape decodes too.
// Wire enums stay `String` (plus the constants) so a future agent value never
// fails the decode.

@Serializable
data class GitHeadInfo(
    val oid: String? = null,
    val short_oid: String? = null,
    val branch: String? = null,
    val detached: Boolean = false,
    val initial: Boolean = false,
)

@Serializable
data class GitUpstreamInfo(
    val ref: String,
    val ahead: Int? = null,
    val behind: Int? = null,
)

@Serializable
data class GitRebaseCommit(
    val oid: String,
    val short_oid: String,
    val subject: String,
)

/**
 * The rebase in progress: `branch` is null for a detached HEAD, `onto_ref` a
 * branch pointing at the base or null, `stopped_on` the commit git stopped at.
 * Every key can be null — a rebase git was killed in need not have written
 * every state file.
 */
@Serializable
data class GitRebaseInfo(
    val branch: String? = null,
    val onto_oid: String? = null,
    val onto_short_oid: String? = null,
    val onto_ref: String? = null,
    val stopped_on: GitRebaseCommit? = null,
    val step: Int? = null,
    val total: Int? = null,
)

@Serializable
data class GitRepoInfo(
    val toplevel: String,
    val git_dir: String,
    val common_dir: String,
    val is_linked_worktree: Boolean = false,
    val head: GitHeadInfo,
    val upstream: GitUpstreamInfo? = null,
    val state: String = STATE_CLEAN,
    /** Set only while `state` is `rebasing`, by an agent with `capabilities.git.rebase`. */
    val rebase: GitRebaseInfo? = null,
) {
    companion object {
        const val STATE_CLEAN = "clean"
        const val STATE_MERGING = "merging"
        const val STATE_REBASING = "rebasing"
        const val STATE_CHERRY_PICKING = "cherry_picking"
        const val STATE_REVERTING = "reverting"
        const val STATE_BISECTING = "bisecting"
    }
}

@Serializable
data class GitStatusEntry(
    val group: String,
    val path: String,
    val orig_path: String? = null,
    val code: String,
    val index_status: String,
    val worktree_status: String,
    val rename_score: Int? = null,
    val submodule: Boolean = false,
) {
    companion object {
        const val GROUP_CONFLICTS = "conflicts"
        const val GROUP_STAGED = "staged"
        const val GROUP_UNSTAGED = "unstaged"
        const val GROUP_UNTRACKED = "untracked"
    }

    val fileName: String
        get() = gitPathFileName(path)

    val directory: String?
        get() = gitPathDirectory(path)
}

/**
 * The last path segment. Git lists an untracked nested repository or worktree
 * as `dir/`, so a trailing slash stays on the name instead of leaving it empty.
 */
fun gitPathFileName(path: String): String =
    path.substring(path.removeSuffix("/").lastIndexOf('/') + 1)

/** Everything before [gitPathFileName], or null for a top-level entry. */
fun gitPathDirectory(path: String): String? =
    path.removeSuffix("/").substringBeforeLast('/', "").takeIf { it.isNotEmpty() }

@Serializable
data class GitStatusCounts(
    val conflicts: Int = 0,
    val staged: Int = 0,
    val unstaged: Int = 0,
    val untracked: Int = 0,
    val total: Int = 0,
)

@Serializable
data class GitStatusResponse(
    val session_id: String,
    val cwd: String? = null,
    val etag: String,
    val unchanged: Boolean = false,
    val unavailable_reason: String? = null,
    val repo: GitRepoInfo? = null,
    val entries: List<GitStatusEntry> = emptyList(),
    val counts: GitStatusCounts? = null,
    val truncated: Boolean = false,
    val entry_limit: Int = 5000,
    /**
     * Paths marked resolved during the operation in progress whose conflict
     * the agent can still rebuild. Not entries: a resolution equal to HEAD has
     * no status row at all.
     */
    val resolved_paths: List<String> = emptyList(),
) {
    companion object {
        const val UNAVAILABLE_NOT_A_REPO = "not_a_repo"
        const val UNAVAILABLE_BARE_REPOSITORY = "bare_repository"
        const val UNAVAILABLE_CWD_MISSING = "cwd_missing"
        const val UNAVAILABLE_UNSAFE_DIRECTORY = "unsafe_directory"
        const val UNAVAILABLE_GIT_TOO_OLD = "git_too_old"
    }
}

@Serializable
data class GitDiffLine(
    val t: String,
    val old: Int? = null,
    val new: Int? = null,
    val text: String,
    val no_newline: Boolean = false,
) {
    companion object {
        const val T_CTX = "ctx"
        const val T_ADD = "add"
        const val T_DEL = "del"
    }
}

@Serializable
data class GitDiffHunk(
    val old_start: Int,
    val old_lines: Int,
    val new_start: Int,
    val new_lines: Int,
    val heading: String = "",
    val lines: List<GitDiffLine> = emptyList(),
)

@Serializable
data class GitDiffResponse(
    val path: String,
    val orig_path: String? = null,
    val scope: String,
    val kind: String,
    val change: String? = null,
    val old_mode: String? = null,
    val new_mode: String? = null,
    val additions: Int = 0,
    val deletions: Int = 0,
    val hunks: List<GitDiffHunk> = emptyList(),
    val truncated: Boolean = false,
    val total_lines: Int = 0,
    val line_limit: Int = 5000,
    val byte_limit: Int = 1_048_576,
) {
    companion object {
        const val SCOPE_STAGED = "staged"
        const val SCOPE_UNSTAGED = "unstaged"
        const val SCOPE_UNTRACKED = "untracked"

        const val KIND_TEXT = "text"
        const val KIND_BINARY = "binary"
        const val KIND_SUBMODULE = "submodule"
        const val KIND_TOO_LARGE = "too_large"
        const val KIND_MISSING = "missing"

        const val CHANGE_MODIFIED = "modified"
        const val CHANGE_ADDED = "added"
        const val CHANGE_DELETED = "deleted"
        const val CHANGE_RENAMED = "renamed"
        const val CHANGE_COPIED = "copied"
        const val CHANGE_TYPE_CHANGED = "type_changed"
    }
}

/** Repo-relative paths as `status` reported them; a renamed entry lists both `path` and `orig_path`. */
@Serializable
data class GitPathsRequest(val paths: List<String>)

@Serializable
data class GitDiscardRequest(val paths: List<String>, val scope: String) {
    companion object {
        const val SCOPE_UNSTAGED = "unstaged"
        const val SCOPE_UNTRACKED = "untracked"
    }
}

@Serializable
data class GitMutationResponse(val ok: Boolean = true)

@Serializable
data class GitCommitRequest(
    val message: String,
    val amend: Boolean = false,
    val no_verify: Boolean = false,
)

@Serializable
data class GitCommitResponse(
    val oid: String,
    val short_oid: String,
    val subject: String,
)

@Serializable
data class GitCommitAuthor(val name: String, val email: String)

@Serializable
data class GitCommitHead(
    val oid: String,
    val short_oid: String,
    val subject: String,
    val body: String = "",
)

/**
 * What the commit sheet needs before the user types: `author` is null when git
 * cannot determine an identity (the commit would be refused with
 * `identity_required`), `head` is null on an unborn branch (nothing to amend).
 */
@Serializable
data class GitCommitContextResponse(
    val author: GitCommitAuthor? = null,
    val head: GitCommitHead? = null,
)

@Serializable
data class GitPullRequest(val mode: String = MODE_FF_ONLY) {
    companion object {
        const val MODE_FF_ONLY = "ff_only"
        const val MODE_REBASE = "rebase"
        const val MODE_MERGE = "merge"
    }
}

@Serializable
data class GitPushRequest(val force: Boolean = false)

@Serializable
data class GitRebaseRequest(val onto: String)

/** One side of a conflict; `present` is false when that side deleted the file or never had it. */
@Serializable
data class GitConflictSide(
    val label: String? = null,
    val present: Boolean = false,
)

/**
 * A conflicted path as the resolver shows it. `ours` is index stage 2 and
 * `theirs` stage 3 — during a rebase, the branch being rebased onto and the
 * commit being replayed. `kind` is an open vocabulary; only [KIND_TEXT] carries
 * `content` (LF line endings, no BOM) and `token`, which `resolve` sends back.
 */
@Serializable
data class GitConflictResponse(
    val path: String,
    val code: String,
    val state: String = GitRepoInfo.STATE_CLEAN,
    val kind: String,
    val content: String? = null,
    val token: String? = null,
    val markers: Int = 0,
    val has_base: Boolean = false,
    val ours: GitConflictSide = GitConflictSide(),
    val theirs: GitConflictSide = GitConflictSide(),
    val byte_limit: Int = 262144,
) {
    companion object {
        const val KIND_TEXT = "text"
        const val KIND_TOO_LARGE = "too_large"
        const val KIND_BINARY = "binary"
        const val KIND_ENCODING = "encoding"
        const val KIND_MISSING = "missing"
        const val KIND_SYMLINK = "symlink"
        const val KIND_SUBMODULE = "submodule"
        const val SIDE_OURS = "ours"
        const val SIDE_THEIRS = "theirs"
    }
}

/** `mark_resolved = false` saves a draft: the file is written, markers and all, and stays unmerged. */
@Serializable
data class GitConflictResolveRequest(
    val path: String,
    val token: String,
    val content: String,
    val mark_resolved: Boolean = true,
)

@Serializable
data class GitConflictTakeRequest(val path: String, val side: String)

@Serializable
data class GitConflictRestoreRequest(
    val path: String,
    val style: String = STYLE_MERGE,
    val token: String? = null,
) {
    companion object {
        const val STYLE_MERGE = "merge"
        const val STYLE_DIFF3 = "diff3"
    }
}

/**
 * What a rebase route did. `outcome` is an open vocabulary; `rebase` is set only
 * when `stopped`, and `message` explains a stop that is not a conflict.
 */
@Serializable
data class GitRebaseResponse(
    val outcome: String,
    val rebase: GitRebaseInfo? = null,
    val message: String? = null,
) {
    companion object {
        const val OUTCOME_REBASED = "rebased"
        const val OUTCOME_UP_TO_DATE = "up_to_date"
        const val OUTCOME_STOPPED = "stopped"
        const val OUTCOME_ABORTED = "aborted"
    }
}

/**
 * One fetch / pull / push job: the body every job route answers and the
 * payload of `git.job.progress` / `git.job.done`. `phase` / `percent` /
 * `line` mirror git's own `--progress` output; `ok` / `error_code` /
 * `message` stay null until `status` is `done`. `error_code` is an open
 * vocabulary — an unknown code renders from `message`.
 */
@Serializable
data class GitJobResponse(
    val job_id: String,
    val session_id: String,
    val op: String,
    val status: String = STATUS_RUNNING,
    val phase: String? = null,
    val percent: Int? = null,
    val line: String? = null,
    val ok: Boolean? = null,
    val error_code: String? = null,
    val message: String? = null,
) {
    val running: Boolean get() = status != STATUS_DONE

    companion object {
        const val OP_FETCH = "fetch"
        const val OP_PULL = "pull"
        const val OP_PUSH = "push"
        const val STATUS_RUNNING = "running"
        const val STATUS_DONE = "done"
        const val CODE_CANCELLED = "cancelled"
        const val CODE_AUTH_REQUIRED = "auth_required"
        const val CODE_NETWORK = "network"
        const val CODE_REMOTE_NOT_FOUND = "remote_not_found"
        const val CODE_REJECTED = "rejected"
        const val CODE_NOT_FAST_FORWARD = "not_fast_forward"
        const val CODE_CONFLICTS = "conflicts"
        const val CODE_DIRTY_WORKTREE = "dirty_worktree"
        const val CODE_OPERATION_IN_PROGRESS = "operation_in_progress"
        const val CODE_NO_UPSTREAM = "no_upstream"
        const val CODE_NO_REMOTE = "no_remote"
        const val CODE_DETACHED_HEAD = "detached_head"
    }
}
