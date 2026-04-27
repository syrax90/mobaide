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
