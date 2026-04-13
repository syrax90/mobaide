package com.claudemobile.app.ui.sessions

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.claudemobile.app.data.events.AgentEventBus
import com.claudemobile.app.data.local.PreferencesStore
import com.claudemobile.app.data.network.dto.AutoScanInstanceDto
import com.claudemobile.app.data.network.dto.SessionResponse
import com.claudemobile.app.data.repository.AutoScanRepository
import com.claudemobile.app.data.repository.SessionRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class SessionListUiState(
    val sessions: List<SessionResponse> = emptyList(),
    val isReconnecting: Boolean = false,
    val error: String? = null,
    val activeScans: List<AutoScanInstanceDto> = emptyList(),
)

@HiltViewModel
class SessionListViewModel @Inject constructor(
    private val sessionRepository: SessionRepository,
    private val autoScanRepository: AutoScanRepository,
    private val preferencesStore: PreferencesStore,
    private val agentEventBus: AgentEventBus,
) : ViewModel() {

    private val _uiState = MutableStateFlow(SessionListUiState())
    val uiState: StateFlow<SessionListUiState> = _uiState.asStateFlow()

    private val _autoResumeSessionId = MutableSharedFlow<String>(extraBufferCapacity = 1)
    val autoResumeSessionId: SharedFlow<String> = _autoResumeSessionId.asSharedFlow()

    private var didTryAutoResume = false

    init {
        agentEventBus.start()

        viewModelScope.launch {
            sessionRepository.observeSessionList().collect { sessions ->
                _uiState.update { it.copy(sessions = sessions) }
                maybeAutoResume(sessions)
            }
        }

        // Reactive multi-scan list from the bus-backed repository.
        viewModelScope.launch {
            autoScanRepository.observeList().collect { list ->
                _uiState.update {
                    it.copy(activeScans = list.scans)
                }
            }
        }

        // Initial REST seed for the multi-scan list.
        viewModelScope.launch {
            try {
                autoScanRepository.refresh()
            } catch (_: Exception) {
                // Auto scan may not be configured — ignore.
            }
        }

        viewModelScope.launch {
            agentEventBus.connection.collect { state ->
                _uiState.update {
                    it.copy(isReconnecting = state == AgentEventBus.ConnState.Connecting)
                }
            }
        }
    }

    private suspend fun maybeAutoResume(sessions: List<SessionResponse>) {
        if (didTryAutoResume || sessions.isEmpty()) return
        didTryAutoResume = true
        val lastId = preferencesStore.getLastUsedSessionId() ?: return
        val match = sessions.firstOrNull { it.session_id == lastId } ?: return
        if (match.state == "running" || match.state == "starting") {
            _autoResumeSessionId.tryEmit(lastId)
        } else {
            preferencesStore.setLastUsedSessionId(null)
        }
    }

    fun refresh() {
        agentEventBus.forceReconnect()
        viewModelScope.launch {
            try {
                autoScanRepository.refresh()
            } catch (_: Exception) {
            }
        }
    }

    fun deleteSession(sessionId: String) {
        viewModelScope.launch {
            try {
                sessionRepository.deleteSession(sessionId)
            } catch (e: Exception) {
                _uiState.update { it.copy(error = "Failed to stop session: ${e.message}") }
            }
        }
    }

    fun stopAutoScan(projectName: String) {
        viewModelScope.launch {
            try {
                autoScanRepository.stopScan(projectName)
            } catch (e: Exception) {
                _uiState.update { it.copy(error = "Failed to stop scan: ${e.message}") }
            }
        }
    }

    fun clearError() {
        _uiState.update { it.copy(error = null) }
    }
}
