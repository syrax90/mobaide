package com.claudemobile.app.ui.projects

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.claudemobile.app.data.network.dto.ProjectInfo
import com.claudemobile.app.data.repository.AutoScanRepository
import com.claudemobile.app.data.repository.ProjectConflictException
import com.claudemobile.app.data.repository.ProjectsRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.launchIn
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * Projects management screen state holder.
 *
 *  - Server-authoritative: create/update/delete never mutate the projects list
 *    locally. The repository's bus collector applies the server's
 *    `projects.changed` snapshot. The sheet closes on HTTP 2xx.
 *  - Optimistic reorder only: while a PATCH is in flight we hold a
 *    `pendingOrder` override so the drag feels instantaneous. It clears on
 *    the next event (success) or on exception (reverts to server order).
 *  - Busy detection: a project is "busy" iff an auto-scan is running for it.
 *    Swipe-to-delete a busy project opens a confirm dialog (server will 409);
 *    swipe-to-delete a free project triggers an optimistic UNDO snackbar.
 */
@HiltViewModel
class ProjectsViewModel @Inject constructor(
    private val projectsRepository: ProjectsRepository,
    private val autoScanRepository: AutoScanRepository,
) : ViewModel() {

    private val _uiState = MutableStateFlow(ProjectsUiState())
    val uiState: StateFlow<ProjectsUiState> = _uiState.asStateFlow()

    private val _events = MutableSharedFlow<ProjectsUiEvent>(
        replay = 0,
        extraBufferCapacity = 4,
        onBufferOverflow = BufferOverflow.DROP_OLDEST,
    )
    val events: SharedFlow<ProjectsUiEvent> = _events.asSharedFlow()

    /** While non-null, the UI renders this order instead of the repo's. */
    private val _pendingOrder = MutableStateFlow<List<String>?>(null)

    init {
        // `distinctUntilChanged` prevents UI flicker when scan status transitions churn.
        autoScanRepository.observeScans()
            .onEach { scans ->
                val busy = scans.map { it.project_name }.toSet()
                _uiState.update { it.copy(busyProjectNames = busy) }
            }
            .launchIn(viewModelScope)

        // Pending override clears once the repo's order matches it (server's projects.changed landed).
        combine(projectsRepository.observeProjects(), _pendingOrder) { projects, pending ->
            val repoOrder = projects.sortedBy { it.order }
            val display = if (pending != null) {
                projects.sortedBy { p ->
                    val idx = pending.indexOf(p.name)
                    if (idx >= 0) idx else Int.MAX_VALUE
                }
            } else repoOrder
            Triple(display, pending, repoOrder.map { it.name })
        }
            .distinctUntilChanged()
            .onEach { (display, pending, repoOrderNames) ->
                _uiState.update { it.copy(projects = display, isLoading = false) }
                if (pending != null && repoOrderNames == pending) {
                    _pendingOrder.value = null
                }
            }
            .launchIn(viewModelScope)

        // Fallback REST seed if the bus snapshot hasn't arrived yet.
        viewModelScope.launch {
            try {
                val list = projectsRepository.listProjects()
                if (_uiState.value.projects.isEmpty()) {
                    _uiState.update { it.copy(projects = list.sortedBy { p -> p.order }, isLoading = false) }
                } else {
                    _uiState.update { it.copy(isLoading = false) }
                }
            } catch (e: Exception) {
                _uiState.update { it.copy(isLoading = false) }
                _events.tryEmit(ProjectsUiEvent.Message("Failed to load projects: ${e.message}"))
            }
        }
    }

    fun openCreateSheet() {
        _uiState.update { it.copy(form = ProjectFormState.forCreate()) }
    }

    fun openEditSheet(project: ProjectInfo) {
        _uiState.update { it.copy(form = ProjectFormState.forEdit(project)) }
    }

    fun closeSheet() {
        _uiState.update { it.copy(form = null) }
    }

    private fun updateForm(mutator: (ProjectFormState) -> ProjectFormState) {
        _uiState.update { state ->
            state.form?.let { state.copy(form = mutator(it)) } ?: state
        }
    }

    fun onNameChange(value: String) = updateForm {
        it.copy(name = value, nameServerError = null)
    }

    fun onPathChange(value: String) = updateForm { it.copy(path = value) }

    fun onCondaChange(value: String) = updateForm { it.copy(condaEnv = value) }

    fun onLinearKeyChange(value: String) = updateForm {
        // Typing any non-empty value cancels a pending "clear" instruction.
        it.copy(linearApiKey = value, clearLinearKey = false)
    }

    fun toggleLinearKeyVisible() = updateForm { it.copy(linearKeyVisible = !it.linearKeyVisible) }

    fun requestClearLinearKey() = updateForm {
        it.copy(linearApiKey = "", clearLinearKey = true, linearKeyVisible = false)
    }

    fun touch(field: ProjectFormState.Field) = updateForm {
        it.copy(touched = it.touched + field)
    }

    fun addFlag() = updateForm {
        it.copy(
            flags = it.flags + FlagEntry(),
            flagsDirty = true,
            touched = it.touched + ProjectFormState.Field.FLAG,
        )
    }

    fun removeFlag(index: Int) = updateForm {
        if (index !in it.flags.indices) it
        else it.copy(
            flags = it.flags.toMutableList().apply { removeAt(index) },
            flagsDirty = true,
        )
    }

    fun updateFlag(index: Int, updated: FlagEntry) = updateForm {
        if (index !in it.flags.indices) it
        else it.copy(
            flags = it.flags.toMutableList().apply { this[index] = updated },
            flagsDirty = true,
        )
    }

    fun save() {
        val form = _uiState.value.form ?: return
        val attempted = form.copy(submitAttempted = true)
        if (!attempted.canSave) {
            updateForm { attempted }
            return
        }
        updateForm { attempted.copy(submitting = true) }

        viewModelScope.launch {
            try {
                if (form.isEdit) {
                    projectsRepository.updateProject(form.editing!!.name, attempted.toUpdateBody())
                } else {
                    projectsRepository.createProject(attempted.toCreateBody())
                }
                // Success → close sheet; list refresh arrives via `projects.changed` event.
                _uiState.update { it.copy(form = null) }
                _events.tryEmit(ProjectsUiEvent.CloseSheet)
            } catch (e: ProjectConflictException) {
                // 409 on save: server reports rename collision or "auto-scan active" —
                // surface inline under the Name field.
                updateForm { it.copy(submitting = false, nameServerError = e.detail) }
            } catch (e: Exception) {
                updateForm { it.copy(submitting = false) }
                _events.tryEmit(ProjectsUiEvent.Message("Save failed: ${e.message ?: "unknown error"}"))
            }
        }
    }

    fun onSwipeDelete(project: ProjectInfo) {
        if (_uiState.value.busyProjectNames.contains(project.name)) {
            _uiState.update { it.copy(pendingDelete = project) }
            return
        }
        performDelete(project, announceUndo = true)
    }

    fun confirmBusyDelete() {
        val target = _uiState.value.pendingDelete ?: return
        _uiState.update { it.copy(pendingDelete = null) }
        performDelete(target, announceUndo = false)
    }

    fun dismissBusyDelete() {
        _uiState.update { it.copy(pendingDelete = null) }
    }

    private fun performDelete(project: ProjectInfo, announceUndo: Boolean) {
        viewModelScope.launch {
            try {
                projectsRepository.deleteProject(project.name)
                if (announceUndo) _events.tryEmit(ProjectsUiEvent.DeletedWithUndo(project))
            } catch (e: ProjectConflictException) {
                _events.tryEmit(ProjectsUiEvent.Message(e.detail))
            } catch (e: Exception) {
                _events.tryEmit(ProjectsUiEvent.Message("Delete failed: ${e.message ?: "unknown error"}"))
            }
        }
    }

    fun undoDelete(snapshot: ProjectInfo) {
        viewModelScope.launch {
            try {
                projectsRepository.createProject(snapshot.toUndoCreateBody())
                if (snapshot.has_linear_key) _events.tryEmit(ProjectsUiEvent.LinearKeyReminder)
            } catch (e: Exception) {
                _events.tryEmit(ProjectsUiEvent.Message("Undo failed: ${e.message ?: "unknown error"}"))
            }
        }
    }

    /**
     * Build a POST body to re-create a previously-deleted project. The
     * server never echoes `linear_api_key` back, so UNDO cannot restore it —
     * the UI emits a [ProjectsUiEvent.LinearKeyReminder] when the snapshot
     * had one.
     */
    private fun ProjectInfo.toUndoCreateBody() =
        com.claudemobile.app.data.network.dto.ProjectWrite(
            name = name,
            path = path,
            linear_api_key = null,
            conda_env = conda_env,
            default_flags = default_flags,
        )

    fun reorder(newNameOrder: List<String>) {
        _pendingOrder.value = newNameOrder
        viewModelScope.launch {
            try {
                projectsRepository.reorderProjects(newNameOrder)
                // Success: wait for `projects.changed` to clear _pendingOrder.
            } catch (e: Exception) {
                _pendingOrder.value = null
                _events.tryEmit(ProjectsUiEvent.Message("Reorder failed — reverted."))
            }
        }
    }

}
