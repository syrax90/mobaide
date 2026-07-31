package com.mobaide.app.ui.projects.git

import com.mobaide.app.data.network.dto.BusyHolder
import com.mobaide.app.data.network.dto.GitDeleteItem
import com.mobaide.app.data.network.dto.GitDeleteResultItem
import com.mobaide.app.data.network.dto.GitItem

/** Snapshot of the git management screen. */
data class GitUiState(
    val projectName: String = "",
    val items: List<GitItem> = emptyList(),
    val isLoading: Boolean = true,
    val errorMessage: String? = null,

    val selected: Set<String> = emptySet(),
    val inSelectionMode: Boolean = false,
    val deleting: Boolean = false,

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
}

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
