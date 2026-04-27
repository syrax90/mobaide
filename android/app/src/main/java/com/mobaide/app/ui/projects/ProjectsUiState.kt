package com.mobaide.app.ui.projects

import com.mobaide.app.data.network.dto.ProjectInfo

data class ProjectsUiState(
    val projects: List<ProjectInfo> = emptyList(),
    val busyProjectNames: Set<String> = emptySet(),
    val form: ProjectFormState? = null,  // null = sheet closed
    val pendingDelete: ProjectInfo? = null,  // null = no pending confirmation
    val isLoading: Boolean = true,
)

/** One-shot events emitted by [ProjectsViewModel] for the screen layer. */
sealed interface ProjectsUiEvent {
    /** Snackbar with UNDO action (5s). */
    data class DeletedWithUndo(val snapshot: ProjectInfo) : ProjectsUiEvent
    /** Informational snackbar reminding the user to re-enter the Linear API key. */
    data object LinearKeyReminder : ProjectsUiEvent
    /** Plain snackbar (errors, reorder failures, etc.). */
    data class Message(val text: String) : ProjectsUiEvent
    /** Close the bottom sheet after a successful save. */
    data object CloseSheet : ProjectsUiEvent
}
