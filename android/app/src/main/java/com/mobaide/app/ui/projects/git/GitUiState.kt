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

    /** When non-null, the screen renders the busy-conflict dialog. */
    val busyDialog: List<BusyHolder>? = null,

    /** When non-null, the screen renders the force-delete confirmation. */
    val forceDeletePrompt: ForceDeletePrompt? = null,
)

/** A failure that requires the user to opt into `force=true`. */
data class ForceDeletePrompt(
    val items: List<GitDeleteItem>,
    val refs: List<String>,
)

/** One-shot screen events. */
sealed interface GitUiEvent {
    /** Generic snackbar text — used for unexpected failures. */
    data class Message(val text: String) : GitUiEvent

    /** Pending undo snackbar — `requestItems` are sent only on dismiss, never on Undo. */
    data class DeletedWithUndo(val requestItems: List<GitDeleteItem>) : GitUiEvent

    /** Per-item delete results landed; the screen surfaces failures inline. */
    data class DeleteResults(val results: List<GitDeleteResultItem>) : GitUiEvent
}
