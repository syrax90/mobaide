package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable

/**
 * One row in the unified git management list.
 *
 * `kind` discriminates between three shapes:
 *  - `branch_with_worktree` — local branch checked out in some worktree.
 *  - `branch_only` — local branch with no associated worktree.
 *  - `orphan_worktree` — worktree on detached HEAD or whose branch ref no longer exists.
 *
 * The server resolves the branch↔worktree relationship; the client only renders.
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
) {
    companion object {
        const val KIND_BRANCH_WITH_WORKTREE = "branch_with_worktree"
        const val KIND_BRANCH_ONLY = "branch_only"
        const val KIND_ORPHAN_WORKTREE = "orphan_worktree"
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

    /** True when the row is checked out somewhere — git refuses to delete it. */
    val isDeletable: Boolean
        get() = !is_current
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
data class GitRepoInfo(
    val toplevel: String,
    val git_dir: String,
    val common_dir: String,
    val is_linked_worktree: Boolean = false,
    val head: GitHeadInfo,
    val upstream: GitUpstreamInfo? = null,
    val state: String = STATE_CLEAN,
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
        get() = path.substringAfterLast('/')

    val directory: String?
        get() = path.substringBeforeLast('/', "").takeIf { it.isNotEmpty() }
}

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
