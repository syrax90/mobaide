package com.mobaide.app.ui.workspaces

import com.mobaide.app.data.network.dto.WorkspaceInfo

data class WorkspacesUiState(
    val workspaces: List<WorkspaceInfo> = emptyList(),
    val busyWorkspaceNames: Set<String> = emptySet(),
    val form: WorkspaceFormState? = null,  // null = sheet closed
    val pendingDelete: WorkspaceInfo? = null,  // null = no pending confirmation
    val isLoading: Boolean = true,
)

/** One-shot events emitted by [WorkspacesViewModel] for the screen layer. */
sealed interface WorkspacesUiEvent {
    /** Snackbar with UNDO action (5s). */
    data class DeletedWithUndo(val snapshot: WorkspaceInfo) : WorkspacesUiEvent
    /** Informational snackbar reminding the user to re-enter the Linear API key. */
    data object LinearKeyReminder : WorkspacesUiEvent
    /** Plain snackbar (errors, reorder failures, etc.). */
    data class Message(val text: String) : WorkspacesUiEvent
    /** Close the bottom sheet after a successful save. */
    data object CloseSheet : WorkspacesUiEvent
}
