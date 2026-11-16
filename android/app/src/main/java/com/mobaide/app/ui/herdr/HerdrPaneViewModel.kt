package com.mobaide.app.ui.herdr

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.local.HerdrSeenStore
import com.mobaide.app.data.repository.HerdrException
import com.mobaide.app.data.repository.HerdrRepository
import com.mobaide.app.notifications.AttentionNotifier
import com.mobaide.app.notifications.ForegroundSessionTracker
import com.mobaide.app.notifications.herdrPaneKey
import com.mobaide.app.ui.terminal.TerminalViewModel
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

/** What the pane screen knows about its pane beyond the terminal stream. */
data class HerdrPaneUiState(
    val title: String = "Herdr pane",
    val canControl: Boolean = false,
    val typeInWatch: Boolean = true,
    /** A mode change is on its way. */
    val controlBusy: Boolean = false,
    val history: HerdrHistoryState? = null,
)

/** The history sheet: the pane's earlier output as text. */
sealed interface HerdrHistoryState {
    data object Loading : HerdrHistoryState
    data class Loaded(val text: String, val truncated: Boolean) : HerdrHistoryState
    data class Failed(val message: String) : HerdrHistoryState
}

sealed interface HerdrPaneEvent {
    data class ShowMessage(val text: String) : HerdrPaneEvent

    /** The agent made the pane view again: the terminal attaches again. */
    data object Reattach : HerdrPaneEvent

    /** The pane is closed, or its herdr server stopped. */
    data object PaneGone : HerdrPaneEvent
}

/**
 * The Herdr side of one pane screen: its title and features from the
 * dashboard state, the watch/control switch, the history, and "seen". The
 * terminal stream itself belongs to [TerminalViewModel].
 */
@HiltViewModel
class HerdrPaneViewModel @Inject constructor(
    savedStateHandle: SavedStateHandle,
    private val repository: HerdrRepository,
    private val seenStore: HerdrSeenStore,
    private val foregroundSessionTracker: ForegroundSessionTracker,
    private val attentionNotifier: AttentionNotifier,
) : ViewModel() {

    val serverId: String = savedStateHandle[ARG_SERVER] ?: throw IllegalArgumentException("herdrServer required")
    val paneId: String = savedStateHandle[TerminalViewModel.ARG_HERDR_PANE]
        ?: throw IllegalArgumentException("herdrPane required")

    private val controlBusy = MutableStateFlow(false)
    private val history = MutableStateFlow<HerdrHistoryState?>(null)

    val uiState: StateFlow<HerdrPaneUiState> =
        combine(repository.snapshot, controlBusy, history) { snapshot, busy, history ->
            val found = snapshot.findPane(serverId, paneId)
            HerdrPaneUiState(
                title = found?.second?.let(::herdrPaneTitle) ?: "Herdr pane",
                canControl = found?.first?.features?.control ?: false,
                typeInWatch = found?.first?.features?.type_in_watch ?: true,
                controlBusy = busy,
                history = history,
            )
        }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), HerdrPaneUiState())

    private val _events = Channel<HerdrPaneEvent>(Channel.BUFFERED)
    val events = _events.receiveAsFlow()

    /** Whether the pane is in control mode now; read when the screen goes away. */
    private var controlling = false

    /** Set while a re-open runs, so one ended view asks for one new one. */
    private var reopening = false

    init {
        // A cold open (a tapped notification) has no dashboard state yet.
        if (repository.snapshot.value == null) viewModelScope.launch { repository.refresh() }
        // The pane is on screen: a finished turn it shows is seen.
        viewModelScope.launch {
            repository.snapshot.collect { snapshot ->
                val pane = snapshot.findPane(serverId, paneId)?.second ?: return@collect
                val turn = pane.turn
                if (pane.status == "done" && turn != null) {
                    seenStore.markSeen(HerdrSeenStore.key(repository.agentId(), serverId, paneId), turn)
                }
            }
        }
    }

    fun markScreenVisible() {
        foregroundSessionTracker.setVisibleHerdrPane(herdrPaneKey(serverId, paneId))
        // What the alert announced is on screen now.
        attentionNotifier.cancelHerdr(serverId, paneId)
    }

    fun markScreenHidden() {
        foregroundSessionTracker.clearVisibleHerdrPane(herdrPaneKey(serverId, paneId))
    }

    fun onModeChanged(isControl: Boolean) {
        controlling = isControl
        controlBusy.value = false
    }

    fun setControl(control: Boolean) {
        if (controlBusy.value) return
        controlBusy.value = true
        viewModelScope.launch {
            try {
                repository.setControl(serverId, paneId, control)
            } catch (e: HerdrException) {
                _events.send(HerdrPaneEvent.ShowMessage(e.message ?: "Herdr did not answer. Try again."))
            } finally {
                // With the answer, not with the mode change: a control that the
                // agent ends at once shows no change, and the button would stay off.
                controlBusy.value = false
            }
        }
    }

    /**
     * The terminal reported `stopped`. The agent ends a pane view nobody
     * watched for a while, and one whose herdr server restarted: ask for it
     * again once. A pane that is really closed answers "gone".
     */
    fun onSessionStopped() {
        if (reopening) return
        reopening = true
        viewModelScope.launch {
            try {
                repository.openPane(serverId, paneId)
                _events.send(HerdrPaneEvent.Reattach)
            } catch (e: HerdrException) {
                _events.send(
                    if (e.isGone) HerdrPaneEvent.PaneGone
                    else HerdrPaneEvent.ShowMessage(e.message ?: "Could not open the pane."),
                )
            }
        }
    }

    fun onSessionRunning() {
        reopening = false
    }

    fun openHistory() {
        history.value = HerdrHistoryState.Loading
        viewModelScope.launch {
            history.value = try {
                val read = repository.paneText(serverId, paneId, HISTORY_LINES)
                HerdrHistoryState.Loaded(read.text, read.truncated)
            } catch (e: HerdrException) {
                HerdrHistoryState.Failed(e.message ?: "Could not read the pane.")
            }
        }
    }

    fun closeHistory() {
        history.value = null
    }

    override fun onCleared() {
        // Leaving gives the pane back at once, without waiting for the socket to close.
        if (controlling) repository.releaseControlDetached(serverId, paneId)
        super.onCleared()
    }

    companion object {
        const val ARG_SERVER = "herdrServer"
        private const val HISTORY_LINES = 1000
    }
}
