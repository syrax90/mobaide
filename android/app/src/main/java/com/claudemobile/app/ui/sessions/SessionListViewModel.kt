package com.claudemobile.app.ui.sessions

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.claudemobile.app.data.events.ServerEventBus
import com.claudemobile.app.data.local.PreferencesStore
import com.claudemobile.app.data.network.dto.AutoScanInstanceDto
import com.claudemobile.app.data.network.dto.SessionResponse
import com.claudemobile.app.data.repository.AutoScanAlreadyRunningException
import com.claudemobile.app.data.repository.AutoScanCapacityException
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
    val autoScanCapacity: Int = 0,
    val autoScanMax: Int = 5,
    val autoScanAvailableProjects: List<String> = emptyList(),
)

@HiltViewModel
class SessionListViewModel @Inject constructor(
    private val sessionRepository: SessionRepository,
    private val autoScanRepository: AutoScanRepository,
    private val preferencesStore: PreferencesStore,
    private val serverEventBus: ServerEventBus,
) : ViewModel() {

    private val _uiState = MutableStateFlow(SessionListUiState())
    val uiState: StateFlow<SessionListUiState> = _uiState.asStateFlow()

    private val _autoResumeSessionId = MutableSharedFlow<String>(extraBufferCapacity = 1)
    val autoResumeSessionId: SharedFlow<String> = _autoResumeSessionId.asSharedFlow()

    private var didTryAutoResume = false

    init {
        serverEventBus.start()

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
                    it.copy(
                        activeScans = list.scans,
                        autoScanCapacity = list.capacity,
                        autoScanMax = list.max_scans,
                        autoScanAvailableProjects = list.available_projects,
                    )
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
            serverEventBus.connection.collect { state ->
                _uiState.update {
                    it.copy(isReconnecting = state == ServerEventBus.ConnState.Connecting)
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
        serverEventBus.forceReconnect()
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

    fun startAutoScan(projectName: String) {
        viewModelScope.launch {
            try {
                autoScanRepository.startScan(projectName)
            } catch (e: AutoScanAlreadyRunningException) {
                _uiState.update { it.copy(error = "$projectName is already scanning") }
            } catch (e: AutoScanCapacityException) {
                _uiState.update {
                    it.copy(error = "Maximum ${e.maxScans} concurrent auto scans reached")
                }
            } catch (e: Exception) {
                _uiState.update { it.copy(error = "Failed to start scan: ${e.message}") }
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
