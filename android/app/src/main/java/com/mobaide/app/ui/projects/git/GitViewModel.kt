package com.mobaide.app.ui.projects.git

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.network.dto.GitBranchCreateRequest
import com.mobaide.app.data.network.dto.GitDeleteResultItem
import com.mobaide.app.data.network.dto.GitDeleteItem
import com.mobaide.app.data.network.dto.GitItem
import com.mobaide.app.data.repository.GitBusyException
import com.mobaide.app.data.repository.GitRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.delay
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
 *    re-fetches the list. The screen never mutates [GitUiState.items] locally.
 *  - Rows are deleted by swipe, or in bulk from selection mode (entered by
 *    long-press or the "Select" action; a back press exits it).
 *  - A delete hides its rows at once and opens a 5s undo window owned here, not
 *    by the snackbar: a delete raised while the window is open joins the pending
 *    batch instead of replacing it. Undo cancels the window entirely — no
 *    compensating create, because no destructive action ever reached the server.
 *  - `force_required` results re-prompt and re-send with `force=true`; a 409
 *    restores every row, because the server rejects the batch whole.
 *  - New branch and Check out are pessimistic: a typed or confirmed request
 *    whose outcome the list shows through the same `git.changed` reload.
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

    /** The not-yet-sent batch, in request order. */
    private var pendingBatch: List<PendingDelete> = emptyList()

    /**
     * The undo countdown only — never the in-flight request. The agent
     * publishes `git.changed` *before* it replies, so a reload can land while a
     * delete is still awaiting its response; cancelling that job would abort
     * response handling and leave failed rows hidden with no force prompt.
     */
    private var undoWindow: Job? = null
    private var undoSequence = 0L

    init {
        viewModelScope.launch { reload() }
        viewModelScope.launch {
            gitRepository.changed.filter { it == projectName }.collect { reload() }
        }
        viewModelScope.launch { loadCapabilities() }
    }

    private suspend fun loadCapabilities() {
        try {
            val capabilities = gitRepository.gitCapabilities()
            _uiState.update { it.copy(branchesEnabled = capabilities.branches) }
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            // An unreadable probe keeps the delete-only screen older agents get.
        }
    }

    fun refresh() {
        viewModelScope.launch { reload() }
    }

    private suspend fun reload() {
        _uiState.update { it.copy(isLoading = true, errorMessage = null) }
        try {
            val items = gitRepository.listGitItems(projectName)
            val surviving = items.map(GitItem::id).toSet()
            // Rows the server already dropped stay dropped; anything still
            // listed and no longer queued comes back into view.
            pendingBatch = pendingBatch.filter { it.id in surviving }
            if (pendingBatch.isEmpty()) {
                undoWindow?.cancel()
                undoWindow = null
            }
            _uiState.update {
                val hidden = it.pendingDeleteIds intersect surviving
                it.copy(
                    items = items,
                    isLoading = false,
                    errorMessage = null,
                    pendingDeleteIds = hidden,
                    selected = it.selected intersect (surviving - hidden),
                    undoPrompt = if (pendingBatch.isEmpty()) null else it.undoPrompt,
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
            if (it.visibleItems.firstOrNull { item -> item.id == itemId }?.isDeletable != true) it
            else it.copy(inSelectionMode = true, selected = it.selected + itemId)
        }
    }

    /** Enter selection mode from the top bar, with nothing selected yet. */
    fun enterSelectionMode() {
        _uiState.update { if (it.hasDeletableItems) it.copy(inSelectionMode = true) else it }
    }

    fun toggle(itemId: String) {
        _uiState.update { state ->
            val item = state.visibleItems.firstOrNull { it.id == itemId } ?: return@update state
            if (!item.isDeletable) return@update state
            val newSelected = if (itemId in state.selected) {
                state.selected - itemId
            } else {
                state.selected + itemId
            }
            state.copy(selected = newSelected)
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

    /** Single-row delete from a swipe. */
    fun deleteItem(itemId: String) {
        val item = _uiState.value.items.firstOrNull { it.id == itemId } ?: return
        enqueue(listOf(item))
    }

    /** Batch delete of the current selection. */
    fun requestDelete() {
        val state = _uiState.value
        val targets = state.visibleItems.filter { it.id in state.selected && it.isDeletable }
        _uiState.update { it.copy(inSelectionMode = false, selected = emptySet()) }
        enqueue(targets)
    }

    private fun enqueue(targets: List<GitItem>) {
        val hidden = _uiState.value.pendingDeleteIds
        val queued = targets.filter { it.isDeletable && it.id !in hidden }
        if (queued.isEmpty()) return
        pendingBatch = pendingBatch + queued.map { PendingDelete(it.id, toDeleteItem(it)) }
        undoSequence += 1
        val queuedIds = queued.map(GitItem::id).toSet()
        _uiState.update {
            it.copy(
                pendingDeleteIds = it.pendingDeleteIds + queuedIds,
                selected = it.selected - queuedIds,
                undoPrompt = UndoPrompt(id = undoSequence, count = pendingBatch.size),
            )
        }
        // Restarting the window is safe: the payload lives in `pendingBatch`,
        // not in the cancelled job, so a second delete extends the batch
        // instead of discarding the first one.
        undoWindow?.cancel()
        undoWindow = viewModelScope.launch {
            delay(UNDO_WINDOW_MS)
            commitPendingBatch()
        }
    }

    /** The undo snackbar was dismissed by hand: send without waiting out the window. */
    fun commitNow() {
        undoWindow?.cancel()
        undoWindow = null
        // Deliberately not held in `undoWindow`: on `Dispatchers.Main.immediate`
        // the body runs before `launch` returns, so storing it would re-expose
        // the request to reload's cancellation.
        viewModelScope.launch { commitPendingBatch() }
    }

    private suspend fun commitPendingBatch() {
        val batch = pendingBatch
        pendingBatch = emptyList()
        // Detach from the countdown before awaiting: from here on this job is
        // the request, and a concurrent reload must not cancel it.
        undoWindow = null
        _uiState.update { it.copy(undoPrompt = null) }
        sendDelete(batch)
    }

    fun undo() {
        undoWindow?.cancel()
        undoWindow = null
        val queuedIds = pendingBatch.map(PendingDelete::id).toSet()
        pendingBatch = emptyList()
        _uiState.update {
            it.copy(undoPrompt = null, pendingDeleteIds = it.pendingDeleteIds - queuedIds)
        }
    }

    /** Re-issue a delete with `force=true` for the items the server flagged unmerged. */
    fun confirmForceDelete() {
        val prompt = _uiState.value.forceDeletePrompt ?: return
        val forced = prompt.items.map { PendingDelete(it.id, it.item.copy(force = true)) }
        _uiState.update {
            it.copy(
                forceDeletePrompt = null,
                pendingDeleteIds = it.pendingDeleteIds + forced.map(PendingDelete::id),
            )
        }
        viewModelScope.launch { sendDelete(forced) }
    }

    private suspend fun sendDelete(batch: List<PendingDelete>) {
        if (batch.isEmpty()) return
        _uiState.update { it.copy(deleting = true) }
        try {
            val response = gitRepository.deleteGitItems(projectName, batch.map(PendingDelete::item))
            val unmerged = mutableListOf<PendingDelete>()
            val failures = mutableListOf<GitDeleteResultItem>()
            val restore = mutableSetOf<String>()
            response.results.forEachIndexed { index, result ->
                val pending = batch.getOrNull(index) ?: return@forEachIndexed
                when {
                    result.error == "force_required" -> {
                        // Nothing was deleted, so the row comes back while the
                        // prompt names it; confirming re-hides it.
                        unmerged += pending
                        restore += pending.id
                    }
                    !result.ok -> {
                        failures += result
                        restore += pending.id
                    }
                }
            }
            // A short `results` array leaves those items unreported — treat them
            // as un-deleted so their rows come back.
            if (response.results.size < batch.size) {
                restore += batch.drop(response.results.size).map(PendingDelete::id)
            }
            _uiState.update {
                it.copy(
                    deleting = false,
                    pendingDeleteIds = it.pendingDeleteIds - restore,
                    forceDeletePrompt = if (unmerged.isEmpty()) {
                        it.forceDeletePrompt
                    } else {
                        ForceDeletePrompt(
                            items = unmerged,
                            refs = unmerged.mapNotNull { p -> p.item.branch ?: p.item.worktree_path },
                        )
                    },
                )
            }
            if (failures.isNotEmpty()) {
                _events.tryEmit(GitUiEvent.Message(deleteFailureMessage(failures)))
            }
        } catch (e: GitBusyException) {
            // The busy guard is all-or-nothing: nothing was processed.
            _uiState.update {
                it.copy(
                    deleting = false,
                    busyDialog = e.busyBy,
                    pendingDeleteIds = it.pendingDeleteIds - batch.map(PendingDelete::id),
                )
            }
        } catch (e: CancellationException) {
            // The scope is going away — never report that as a delete failure.
            throw e
        } catch (e: Exception) {
            _uiState.update {
                it.copy(
                    deleting = false,
                    pendingDeleteIds = it.pendingDeleteIds - batch.map(PendingDelete::id),
                )
            }
            _events.tryEmit(GitUiEvent.Message("Delete failed: ${e.message ?: "unknown error"}"))
        }
    }

    fun toggleRemote() {
        _uiState.update { it.copy(remoteExpanded = !it.remoteExpanded) }
    }

    fun setFilter(text: String) {
        _uiState.update { it.copy(filter = text) }
    }

    fun openNewBranch() {
        _uiState.update { if (it.branchesEnabled) it.copy(newBranch = NewBranchSheetState()) else it }
    }

    fun dismissNewBranch() {
        _uiState.update { if (it.newBranch?.creating == true) it else it.copy(newBranch = null) }
    }

    /** `startPoint` null means the agent's default, `HEAD` of the project root. */
    fun createBranch(name: String, startPoint: String?, checkout: Boolean) {
        val trimmed = name.trim()
        val sheet = _uiState.value.newBranch ?: return
        if (sheet.creating || trimmed.isEmpty()) return
        _uiState.update { it.copy(newBranch = NewBranchSheetState(creating = true)) }
        viewModelScope.launch {
            try {
                val result = gitRepository.createBranch(
                    projectName,
                    GitBranchCreateRequest(name = trimmed, start_point = startPoint, checkout = checkout),
                )
                _uiState.update { it.copy(newBranch = null) }
                _events.tryEmit(GitUiEvent.Message(branchCreatedMessage(result)))
            } catch (e: GitBusyException) {
                _uiState.update { it.copy(newBranch = null, busyDialog = e.busyBy) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(newBranch = NewBranchSheetState(error = createBranchErrorCopy(e, trimmed)))
                }
            }
        }
    }

    fun requestCheckout(itemId: String) {
        _uiState.update { state ->
            val item = state.visibleItems.firstOrNull { it.id == itemId } ?: return@update state
            if (!state.branchesEnabled || !item.canCheckOut || state.rowBusy) state
            else state.copy(checkoutPrompt = item)
        }
    }

    fun dismissCheckoutPrompt() {
        _uiState.update { it.copy(checkoutPrompt = null) }
    }

    fun confirmCheckout() {
        val item = _uiState.value.checkoutPrompt ?: return
        val ref = item.branch ?: return
        // Set before launching: on Main.immediate a second tap could otherwise
        // land between the launch and the first suspension.
        _uiState.update { it.copy(checkoutPrompt = null, checkingOut = true) }
        viewModelScope.launch {
            try {
                val result = gitRepository.checkout(projectName, ref)
                _events.tryEmit(GitUiEvent.Message(checkedOutMessage(result)))
            } catch (e: GitBusyException) {
                _uiState.update { it.copy(busyDialog = e.busyBy) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _events.tryEmit(GitUiEvent.Message(checkoutErrorCopy(e, primaryLabel(item))))
            } finally {
                _uiState.update { it.copy(checkingOut = false) }
            }
        }
    }

    private fun toDeleteItem(item: GitItem) = GitDeleteItem(
        kind = item.kind,
        branch = item.branch,
        worktree_path = item.worktree_path,
        force = false,
    )

    private companion object {
        const val UNDO_WINDOW_MS = 5_000L
    }
}
