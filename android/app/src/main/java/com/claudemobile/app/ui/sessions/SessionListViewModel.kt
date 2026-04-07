package com.claudemobile.app.ui.sessions

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.claudemobile.app.data.events.ServerEventBus
import com.claudemobile.app.data.local.PreferencesStore
import com.claudemobile.app.data.network.dto.ScanStatusResponse
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
    val scanStatus: ScanStatusResponse? = null,
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
        // Make sure the bus is connected; idempotent.
        serverEventBus.start()

        // Reactive: sessions list flows from the bus-backed repository.
        viewModelScope.launch {
            sessionRepository.observeSessionList().collect { sessions ->
                _uiState.update { it.copy(sessions = sessions) }
                maybeAutoResume(sessions)
            }
        }

        // Reactive: autoscan status from the bus-backed repository.
        viewModelScope.launch {
            autoScanRepository.observeStatus().collect { status ->
                _uiState.update { it.copy(scanStatus = status) }
            }
        }

        // Initial REST seed for autoscan status if the bus snapshot hasn't
        // arrived yet (e.g. cold start before /ws/events connects).
        viewModelScope.launch {
            try {
                val status = autoScanRepository.getStatus()
                _uiState.update {
                    if (it.scanStatus == null) it.copy(scanStatus = status) else it
                }
            } catch (_: Exception) {
                // Auto scan may not be configured — ignore.
            }
        }

        // Reflect connection status as "reconnecting".
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

    /** Manual escape hatch: tear down the WebSocket and resync from a fresh snapshot. */
    fun refresh() {
        serverEventBus.forceReconnect()
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
            } catch (e: Exception) {
                _uiState.update { it.copy(error = "Failed to start scan: ${e.message}") }
            }
        }
    }

    fun stopAutoScan() {
        viewModelScope.launch {
            try {
                autoScanRepository.stopScan()
            } catch (e: Exception) {
                _uiState.update { it.copy(error = "Failed to stop scan: ${e.message}") }
            }
        }
    }

    fun clearError() {
        _uiState.update { it.copy(error = null) }
    }
}
