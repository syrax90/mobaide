package com.mobaide.app.ui.terminal.git

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.network.dto.GitStatusEntry
import com.mobaide.app.data.network.dto.GitStatusResponse
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.repository.GitRepository
import com.mobaide.app.data.repository.SessionGitException
import com.mobaide.app.data.repository.SessionGitStatusLoad
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.notifications.AppForegroundTracker
import com.mobaide.app.notifications.ForegroundSessionTracker
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class GitChangesViewModel @Inject constructor(
    savedStateHandle: SavedStateHandle,
    gitRepository: GitRepository,
    sessionRepository: SessionRepository,
    appForegroundTracker: AppForegroundTracker,
    private val foregroundSessionTracker: ForegroundSessionTracker,
) : ViewModel() {

    val sessionId: String = savedStateHandle.get<String>("sessionId").orEmpty()

    private val sessionRow: StateFlow<SessionResponse?> =
        sessionRepository.observeSessionList()
            .map { sessions -> sessions.firstOrNull { it.session_id == sessionId } }
            .stateIn(viewModelScope, SharingStarted.Eagerly, null)

    private val refreshRequests = MutableSharedFlow<Unit>(
        extraBufferCapacity = 1,
        onBufferOverflow = BufferOverflow.DROP_OLDEST,
    )
    private val manualRefreshing = MutableStateFlow(false)

    /** Re-seeds the poll's etag cache each time the screen resubscribes. */
    private var lastStatus: GitStatusResponse? = null

    private val loads: Flow<SessionGitStatusLoad> =
        gitRepository.observeSessionGitStatus(
            sessionId = sessionId,
            projectName = { sessionRow.value?.project_name },
            foreground = appForegroundTracker.isForeground,
            refreshRequests = refreshRequests,
            initial = { lastStatus },
        ).onEach { load ->
            load.cached?.let { lastStatus = it }
            manualRefreshing.value = false
        }

    val uiState: StateFlow<GitChangesUiState> =
        combine(loads, manualRefreshing) { load, refreshing -> toUiState(load, refreshing) }
            .stateIn(
                viewModelScope,
                SharingStarted.WhileSubscribed(5_000),
                GitChangesUiState.Loading,
            )

    private val _events = Channel<GitChangesEvent>(Channel.BUFFERED)
    val events: Flow<GitChangesEvent> = _events.receiveAsFlow()

    fun refresh() {
        manualRefreshing.value = true
        refreshRequests.tryEmit(Unit)
    }

    fun onEntryClick(entry: GitStatusEntry) {
        viewModelScope.launch {
            _events.send(GitChangesEvent.OpenDiff(sessionId, entry, diffScopeFor(entry)))
        }
    }

    fun holdWatch() = foregroundSessionTracker.holdWatch(sessionId)

    fun releaseWatch() = foregroundSessionTracker.releaseWatch(sessionId)

    private fun toUiState(load: SessionGitStatusLoad, refreshing: Boolean): GitChangesUiState =
        when (load) {
            is SessionGitStatusLoad.Loaded -> fromStatus(load.status, refreshing, staleError = null)
            is SessionGitStatusLoad.Failed -> when (val error = load.error) {
                is SessionGitException.AgentOutdated -> GitChangesUiState.AgentOutdated
                is SessionGitException.SessionGone -> GitChangesUiState.SessionGone
                else -> {
                    val cached = load.cached
                    when {
                        // Retry with nothing cached: show progress rather than the
                        // unchanged Error, which StateFlow would dedupe into silence.
                        cached == null && refreshing -> GitChangesUiState.Loading
                        cached == null -> GitChangesUiState.Error(sessionGitErrorCopy(error))
                        else -> fromStatus(
                            cached,
                            refreshing = refreshing,
                            staleError = sessionGitErrorCopy(error),
                        )
                    }
                }
            }
        }

    private fun fromStatus(
        status: GitStatusResponse,
        refreshing: Boolean,
        staleError: String?,
    ): GitChangesUiState {
        val repo = status.repo
        val reason = status.unavailable_reason
        return if (repo == null || reason != null) {
            GitChangesUiState.Unavailable(
                reason ?: GitStatusResponse.UNAVAILABLE_NOT_A_REPO,
                status.cwd,
            )
        } else {
            GitChangesUiState.Ready(status, repo, sectionsFor(status), refreshing, staleError)
        }
    }
}
