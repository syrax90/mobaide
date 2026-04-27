package com.mobaide.app.ui.projects.git

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.network.dto.GitDeleteItem
import com.mobaide.app.data.network.dto.GitItem
import com.mobaide.app.data.repository.GitBusyException
import com.mobaide.app.data.repository.GitRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.filter
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * Backing ViewModel for the git management screen.
 *
 *  - Server-authoritative: every successful delete triggers a server-side
 *    `git.changed` event that flows back through [GitRepository.changed] and
 *    re-fetches the list. The screen never mutates [items] locally.
 *  - Selection mode is entered on long-press; a back press exits it.
 *  - Safe deletes (`force=false`) emit a [GitUiEvent.DeletedWithUndo]; the
 *    network call only fires after the snackbar dismisses. Tapping Undo
 *    cancels the call entirely (no compensating create — no destructive
 *    action ever reached the server).
 */
@HiltViewModel
class GitViewModel @Inject constructor(
    private val gitRepository: GitRepository,
    savedStateHandle: SavedStateHandle,
) : ViewModel() {

    private val projectName: String = savedStateHandle.get<String>("projectName").orEmpty()

    private val _uiState = MutableStateFlow(GitUiState(projectName = projectName))
    val uiState: StateFlow<GitUiState> = _uiState.asStateFlow()

    private val _events = MutableSharedFlow<GitUiEvent>(
        replay = 0,
        extraBufferCapacity = 4,
        onBufferOverflow = BufferOverflow.DROP_OLDEST,
    )
    val events: SharedFlow<GitUiEvent> = _events.asSharedFlow()

    init {
        viewModelScope.launch { reload() }
        viewModelScope.launch {
            gitRepository.changed.filter { it == projectName }.collect { reload() }
        }
    }

    fun refresh() {
        viewModelScope.launch { reload() }
    }

    private suspend fun reload() {
        _uiState.update { it.copy(isLoading = true, errorMessage = null) }
        try {
            val items = gitRepository.listGitItems(projectName)
            _uiState.update {
                it.copy(
                    items = items,
                    isLoading = false,
                    errorMessage = null,
                    selected = it.selected.intersect(items.map(GitItem::id).toSet()),
                )
            }
        } catch (e: Exception) {
            _uiState.update {
                it.copy(
                    isLoading = false,
                    errorMessage = e.message ?: "Failed to load git items",
                )
            }
        }
    }

    fun enterSelection(itemId: String) {
        _uiState.update {
            if (it.items.firstOrNull { item -> item.id == itemId }?.isDeletable != true) it
            else it.copy(inSelectionMode = true, selected = it.selected + itemId)
        }
    }

    fun toggle(itemId: String) {
        _uiState.update { state ->
            val item = state.items.firstOrNull { it.id == itemId } ?: return@update state
            if (!item.isDeletable) return@update state
            val newSelected = if (itemId in state.selected) {
                state.selected - itemId
            } else {
                state.selected + itemId
            }
            state.copy(
                selected = newSelected,
                inSelectionMode = newSelected.isNotEmpty(),
            )
        }
    }

    fun exitSelection() {
        _uiState.update { it.copy(inSelectionMode = false, selected = emptySet()) }
    }

    fun dismissBusyDialog() {
        _uiState.update { it.copy(busyDialog = null) }
    }

    fun dismissForceDeletePrompt() {
        _uiState.update { it.copy(forceDeletePrompt = null) }
    }

    /** Begin a soft delete: announce undo snackbar; deletion fires on dismiss. */
    fun requestDelete() {
        val state = _uiState.value
        val targets = state.items.filter { it.id in state.selected && it.isDeletable }
        if (targets.isEmpty()) return
        val request = targets.map(::toDeleteItem)
        _events.tryEmit(GitUiEvent.DeletedWithUndo(request))
        _uiState.update { it.copy(inSelectionMode = false, selected = emptySet()) }
    }

    /** Perform the actual deletion after the undo snackbar dismissed. */
    fun confirmDelete(items: List<GitDeleteItem>) {
        viewModelScope.launch { sendDelete(items) }
    }

    /** Re-issue a delete with `force=true` for the items the server flagged unmerged. */
    fun confirmForceDelete() {
        val prompt = _uiState.value.forceDeletePrompt ?: return
        _uiState.update { it.copy(forceDeletePrompt = null) }
        val forced = prompt.items.map { it.copy(force = true) }
        viewModelScope.launch { sendDelete(forced) }
    }

    private suspend fun sendDelete(items: List<GitDeleteItem>) {
        if (items.isEmpty()) return
        _uiState.update { it.copy(deleting = true) }
        try {
            val response = gitRepository.deleteGitItems(projectName, items)
            _uiState.update { it.copy(deleting = false) }
            _events.tryEmit(GitUiEvent.DeleteResults(response.results))
            val unmerged = response.results
                .mapIndexedNotNull { index, result ->
                    if (result.error == "force_required") items.getOrNull(index) else null
                }
            if (unmerged.isNotEmpty()) {
                _uiState.update {
                    it.copy(
                        forceDeletePrompt = ForceDeletePrompt(
                            items = unmerged,
                            refs = unmerged.mapNotNull { it.branch ?: it.worktree_path },
                        ),
                    )
                }
            }
        } catch (e: GitBusyException) {
            _uiState.update { it.copy(deleting = false, busyDialog = e.busyBy) }
        } catch (e: Exception) {
            _uiState.update { it.copy(deleting = false) }
            _events.tryEmit(GitUiEvent.Message("Delete failed: ${e.message ?: "unknown error"}"))
        }
    }

    private fun toDeleteItem(item: GitItem) = GitDeleteItem(
        kind = item.kind,
        branch = item.branch,
        worktree_path = item.worktree_path,
        force = false,
    )
}
