package com.mobaide.app.ui.projects.git

import com.mobaide.app.data.network.dto.BusyHolder
import com.mobaide.app.data.network.dto.GitBranchResponse
import com.mobaide.app.data.network.dto.GitDeleteItem
import com.mobaide.app.data.network.dto.GitDeleteResultItem
import com.mobaide.app.data.network.dto.GitItem
import com.mobaide.app.data.repository.SessionGitException
import com.mobaide.app.ui.terminal.git.sessionGitErrorCopy

/** Below this many rows a filter field would take more space than it saves. */
internal const val FILTER_THRESHOLD = 8

/** Snapshot of the git management screen. */
data class GitUiState(
    val projectName: String = "",
    val items: List<GitItem> = emptyList(),
    val isLoading: Boolean = true,
    val errorMessage: String? = null,

    val selected: Set<String> = emptySet(),
    val inSelectionMode: Boolean = false,
    val deleting: Boolean = false,

    /** `capabilities.git.branches`: gates New branch and the row's Check out. */
    val branchesEnabled: Boolean = false,
    val remoteExpanded: Boolean = false,
    val filter: String = "",
    /** When non-null, the New branch sheet is open. */
    val newBranch: NewBranchSheetState? = null,
    /** When non-null, the screen renders the check-out confirmation for this row. */
    val checkoutPrompt: GitItem? = null,
    val checkingOut: Boolean = false,

    /**
     * Rows queued for deletion, hidden from the list so a swipe disappears at
     * once instead of lingering through the undo window. Every failure path
     * restores them, and a reload drops ids the server no longer lists.
     */
    val pendingDeleteIds: Set<String> = emptySet(),

    /** When non-null, the screen shows the undo snackbar for the queued batch. */
    val undoPrompt: UndoPrompt? = null,

    /** When non-null, the screen renders the busy-conflict dialog. */
    val busyDialog: List<BusyHolder>? = null,

    /** When non-null, the screen renders the force-delete confirmation. */
    val forceDeletePrompt: ForceDeletePrompt? = null,
) {
    val visibleItems: List<GitItem> get() = items.filterNot { it.id in pendingDeleteIds }

    val hasDeletableItems: Boolean get() = visibleItems.any { it.isDeletable }

    val filterable: Boolean get() = visibleItems.size >= FILTER_THRESHOLD

    private val shownItems: List<GitItem>
        get() = if (filterable) visibleItems.filter { matchesFilter(it, filter) } else visibleItems

    val localItems: List<GitItem> get() = shownItems.filterNot { it.kind == GitItem.KIND_REMOTE_BRANCH }

    val remoteItems: List<GitItem> get() = shownItems.filter { it.kind == GitItem.KIND_REMOTE_BRANCH }

    /** A non-empty filter opens the remote section: hidden matches would read as "not found". */
    val remoteOpen: Boolean get() = remoteExpanded || filter.isNotBlank()

    val rowBusy: Boolean get() = deleting || checkingOut
}

/** The New branch sheet: `creating` blocks dismissal, `error` is the inline refusal. */
data class NewBranchSheetState(
    val creating: Boolean = false,
    val error: String? = null,
)

/**
 * A queued delete: the wire item plus the [GitItem.id] it came from, so the
 * positional `results` array can be mapped back onto rows.
 */
data class PendingDelete(
    val id: String,
    val item: GitDeleteItem,
)

/** The open undo window. [id] changes per batch so the screen re-shows the snackbar. */
data class UndoPrompt(
    val id: Long,
    val count: Int,
)

/** A failure that requires the user to opt into `force=true`. */
data class ForceDeletePrompt(
    val items: List<PendingDelete>,
    val refs: List<String>,
)

/** One-shot screen events. */
sealed interface GitUiEvent {
    /** Generic snackbar text — used for delete failures. */
    data class Message(val text: String) : GitUiEvent
}

internal fun deleteFailureMessage(failures: List<GitDeleteResultItem>): String {
    val failure = failures.singleOrNull() ?: return "${failures.size} item(s) failed to delete"
    return "Couldn't delete ${failure.target}: ${deleteFailureReason(failure)}"
}

/**
 * `error_detail` is raw git stderr — first line, capped, so the single-line
 * snackbar can't turn into a wall of text.
 */
internal fun deleteFailureReason(failure: GitDeleteResultItem): String {
    val detail = failure.error_detail?.lineSequence()?.firstOrNull()?.trim().orEmpty()
    if (detail.isNotEmpty()) return detail.take(120)
    return when (failure.error) {
        "locked" -> "the worktree is locked"
        "not_found" -> "it no longer exists"
        "invalid" -> "the request was invalid"
        else -> "unknown error"
    }
}

internal fun primaryLabel(item: GitItem): String = when (item.kind) {
    GitItem.KIND_BRANCH_WITH_WORKTREE, GitItem.KIND_BRANCH_ONLY, GitItem.KIND_REMOTE_BRANCH ->
        item.branch.orEmpty()
    GitItem.KIND_ORPHAN_WORKTREE ->
        item.worktree_path.orEmpty()
    else -> item.branch ?: item.worktree_path.orEmpty()
}

/**
 * `YYYY-MM-DD` from the agent's `iso-strict` committer date — a prefix, not a
 * parse, so the day stays the committer's own on all three clients.
 */
internal fun shortDate(iso: String?): String? {
    if (iso.isNullOrEmpty()) return null
    return if (iso.length > 10) iso.substring(0, 10) else iso
}

/** The tip commit's subject and day, as the agent reported them. */
internal fun tipLine(item: GitItem): String? {
    val subject = item.subject?.trim().orEmpty()
    val date = shortDate(item.committed_at)
    return when {
        subject.isEmpty() && date == null -> null
        subject.isEmpty() -> date
        date == null -> subject
        else -> "$subject · $date"
    }
}

/** A case-insensitive substring match on the branch and the worktree path. */
internal fun matchesFilter(item: GitItem, filter: String): Boolean {
    val needle = filter.trim().lowercase()
    if (needle.isEmpty()) return true
    return primaryLabel(item).lowercase().contains(needle) ||
        item.worktree_path.orEmpty().lowercase().contains(needle)
}

internal fun checkoutConfirmMessage(branch: String, projectName: String): String =
    "Check out $branch in the project folder of $projectName? " +
        "Sessions started in that folder afterwards use this branch."

internal fun checkedOutMessage(result: GitBranchResponse): String = "Checked out ${result.branch}"

internal fun branchCreatedMessage(result: GitBranchResponse): String =
    if (result.checked_out) "Created and checked out ${result.branch}" else "Created ${result.branch}"

/** The checkout refusals, word for word what iOS and the web show. */
internal fun checkoutErrorCopy(error: Throwable, branch: String): String {
    if (error is SessionGitException.GitError) {
        when (error.code) {
            CODE_DIRTY -> return "The project folder has local changes this checkout would overwrite. " +
                "Commit or discard them from a session's Changes screen first."
            CODE_CHECKED_OUT_ELSEWHERE -> {
                val where = error.detailMessage
                return if (where.isNullOrEmpty()) {
                    "$branch is already checked out in another worktree."
                } else {
                    "$branch is already checked out in another worktree ($where)."
                }
            }
            CODE_OPERATION_IN_PROGRESS -> return "A merge, rebase or cherry-pick is in progress in the " +
                "project folder. Finish or abort it first."
            SessionGitException.CODE_JOB_RUNNING ->
                return "A fetch, pull or push is still running in the project folder."
            CODE_REF_NOT_FOUND -> return "Branch not found — refresh the list."
        }
    }
    return sessionGitErrorCopy(error)
}

/** The create refusals shown inline in the New branch sheet. */
internal fun createBranchErrorCopy(error: Throwable, name: String): String {
    if (error is SessionGitException.GitError) {
        when (error.code) {
            CODE_EXISTS -> return "A branch named $name already exists."
            CODE_BAD_BRANCH_NAME -> return "Not a valid branch name."
            CODE_UNKNOWN_START_POINT -> {
                val start = error.detailMessage
                return if (start.isNullOrEmpty()) {
                    "The start point was not found."
                } else {
                    "Start point $start was not found."
                }
            }
        }
    }
    return checkoutErrorCopy(error, name)
}

/** The coded refusals of the branch routes (PROTOCOL.md), on top of the session vocabulary. */
internal const val CODE_EXISTS = "exists"
internal const val CODE_BAD_BRANCH_NAME = "bad_branch_name"
internal const val CODE_UNKNOWN_START_POINT = "unknown_start_point"
internal const val CODE_DIRTY = "dirty"
internal const val CODE_CHECKED_OUT_ELSEWHERE = "checked_out_elsewhere"
internal const val CODE_OPERATION_IN_PROGRESS = "operation_in_progress"
internal const val CODE_REF_NOT_FOUND = "ref_not_found"
