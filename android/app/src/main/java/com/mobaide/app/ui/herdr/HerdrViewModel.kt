package com.mobaide.app.ui.herdr

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.local.HerdrSeenStore
import com.mobaide.app.data.network.dto.HerdrCreateRequest
import com.mobaide.app.data.network.dto.ProjectInfo
import com.mobaide.app.data.repository.HerdrException
import com.mobaide.app.notifications.herdrPaneKey
import com.mobaide.app.data.repository.HerdrRepository
import com.mobaide.app.data.repository.ProjectsRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * The Herdr dashboard. Its state is the repository's snapshot, which
 * `herdr.changed` keeps current, so an action needs no refetch: the event that
 * follows it redraws the tree.
 */
@HiltViewModel
class HerdrViewModel @Inject constructor(
    private val savedStateHandle: SavedStateHandle,
    private val repository: HerdrRepository,
    private val seenStore: HerdrSeenStore,
    projectsRepository: ProjectsRepository,
) : ViewModel() {

    /** The first read finished, so a missing snapshot means "nothing", not "not yet". */
    private val loaded = MutableStateFlow(false)
    private val busy = MutableStateFlow(false)

    /** Why the action of the open dialog failed; see [HerdrUiState.Success.actionError]. */
    private val actionError = MutableStateFlow<String?>(null)

    /**
     * The row key of the pane that is being opened. The open is a round trip
     * to the agent: the row shows progress, and a second tap starts no second
     * call.
     */
    private val openingKey = MutableStateFlow<String?>(null)

    /** The three flags of a running action, as one flow: `combine` takes five. */
    private val actionState = combine(busy, actionError, openingKey) { busy, error, opening ->
        Triple(busy, error, opening)
    }

    val uiState: StateFlow<HerdrUiState> =
        combine(repository.snapshot, seenStore.seen, loaded, actionState) { snapshot, seen, loaded, action ->
            val (busy, actionError, openingKey) = action
            when {
                snapshot != null && snapshot.servers.isNotEmpty() -> {
                    val rows = herdrPaneRows(repository.agentId(), snapshot, seen)
                    HerdrUiState.Success(
                        servers = snapshot.servers,
                        rows = rows,
                        agents = herdrAgentRows(rows),
                        summary = herdrSummary(rows),
                        busy = busy,
                        actionError = actionError,
                        openingKey = openingKey,
                    )
                }
                loaded -> HerdrUiState.Empty
                else -> HerdrUiState.Loading
            }
        }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), HerdrUiState.Loading)

    /** The agent's projects: the folder choices of "New workspace". */
    val projects: StateFlow<List<ProjectInfo>> = projectsRepository.observeProjects()

    private val _events = Channel<HerdrEvent>(Channel.BUFFERED)
    val events = _events.receiveAsFlow()

    init {
        refresh()
        // A tapped alert names its pane: open it once, also after process death.
        val server = savedStateHandle.get<String>(ARG_SERVER)
        val pane = savedStateHandle.get<String>(ARG_PANE)
        if (server != null && pane != null && savedStateHandle.get<Boolean>(KEY_OPENED) != true) {
            savedStateHandle[KEY_OPENED] = true
            openPane(server, pane)
        }
        viewModelScope.launch {
            // The bus snapshot may not have arrived on a cold open of this screen.
            if (projects.value.isEmpty()) runCatching { projectsRepository.listProjects() }
        }
    }

    fun refresh() {
        viewModelScope.launch {
            repository.refresh()
            loaded.value = true
        }
    }

    fun openPane(serverId: String, paneId: String) {
        if (openingKey.value != null) return
        openingKey.value = herdrPaneKey(serverId, paneId)
        viewModelScope.launch {
            try {
                val sessionId = repository.openPane(serverId, paneId)
                _events.send(HerdrEvent.OpenPane(sessionId, serverId, paneId))
            } catch (e: HerdrException) {
                _events.send(HerdrEvent.ShowMessage(e.message ?: "Could not open the pane."))
            } finally {
                openingKey.value = null
            }
        }
    }

    /** A dialog opened or closed: the error of the action before it is over. */
    fun clearActionError() {
        actionError.value = null
    }

    /**
     * Create a workspace, a tab or a split, then open the new pane: that is why
     * it was made. [fromDialog] is false for a split, which has no dialog to
     * show a failure in.
     */
    fun create(request: HerdrCreateRequest, fromDialog: Boolean = true) = runAction(fromDialog) {
        val created = repository.create(request)
        // The terminal exists though its agent did not start: say so, and open it.
        created.agent_error?.let { _events.send(HerdrEvent.ShowMessage(it)) }
        val sessionId = repository.openPane(request.server_id, created.pane_id)
        _events.send(HerdrEvent.OpenPane(sessionId, request.server_id, created.pane_id))
    }

    fun startAgent(serverId: String, paneId: String, agent: String) = runAction {
        repository.startAgent(serverId, paneId, agent)
    }

    fun rename(serverId: String, target: String, id: String, label: String) = runAction {
        repository.rename(serverId, target, id, label.trim())
    }

    /** Herdr closes at once and cannot undo it; the screen asks before it calls this. */
    fun close(serverId: String, target: String, id: String) = runAction {
        repository.close(serverId, target, id)
    }

    fun markSeen(row: HerdrPaneRow) {
        val turn = row.pane.turn ?: return
        seenStore.markSeen(
            HerdrSeenStore.key(repository.agentId(), row.server.server_id, row.pane.pane_id),
            turn,
        )
    }

    /**
     * One action at a time: a second tap while one runs is dropped. A failure
     * of a dialog's action stays in that dialog; a snackbar would be under it.
     */
    private fun runAction(fromDialog: Boolean = true, block: suspend () -> Unit) {
        if (busy.value) return
        busy.value = true
        actionError.value = null
        viewModelScope.launch {
            try {
                block()
                _events.send(HerdrEvent.ActionDone)
            } catch (e: HerdrException) {
                val message = e.message ?: "Herdr did not answer. Try again."
                if (fromDialog) actionError.value = message else _events.send(HerdrEvent.ShowMessage(message))
            } finally {
                busy.value = false
            }
        }
    }

    private companion object {
        const val ARG_SERVER = "server"
        const val ARG_PANE = "pane"
        const val KEY_OPENED = "herdr_deep_link_opened"
    }
}
