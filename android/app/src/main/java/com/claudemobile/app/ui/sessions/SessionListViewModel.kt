package com.claudemobile.app.ui.sessions

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
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
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class SessionListUiState(
    val sessions: List<SessionResponse> = emptyList(),
    val isLoading: Boolean = false,
    val error: String? = null,
    val scanStatus: ScanStatusResponse? = null,
)

@HiltViewModel
class SessionListViewModel @Inject constructor(
    private val sessionRepository: SessionRepository,
    private val autoScanRepository: AutoScanRepository,
    private val preferencesStore: PreferencesStore,
) : ViewModel() {

    private val _uiState = MutableStateFlow(SessionListUiState())
    val uiState: StateFlow<SessionListUiState> = _uiState.asStateFlow()

    private val _autoResumeSessionId = MutableSharedFlow<String>(extraBufferCapacity = 1)
    val autoResumeSessionId: SharedFlow<String> = _autoResumeSessionId.asSharedFlow()

    private var didTryAutoResume = false

    init {
        loadSessions()
        observeSessionList()
        loadAutoScanStatus()
    }

    private fun loadSessions() {
        viewModelScope.launch {
            _uiState.update { it.copy(isLoading = true) }
            try {
                val sessions = sessionRepository.listSessions()
                _uiState.update { it.copy(sessions = sessions, isLoading = false) }
                maybeAutoResume(sessions)
            } catch (e: Exception) {
                _uiState.update { it.copy(isLoading = false, error = e.message) }
            }
        }
    }

    private suspend fun maybeAutoResume(sessions: List<SessionResponse>) {
        if (didTryAutoResume) return
        didTryAutoResume = true
        val lastId = preferencesStore.getLastUsedSessionId() ?: return
        val match = sessions.firstOrNull { it.session_id == lastId } ?: return
        if (match.state == "running" || match.state == "starting") {
            _autoResumeSessionId.tryEmit(lastId)
        } else {
            // Stale — forget it so we don't loop on next launch.
            preferencesStore.setLastUsedSessionId(null)
        }
    }

    private fun observeSessionList() {
        viewModelScope.launch {
            sessionRepository.observeSessionList()
                .catch { /* WebSocket errors handled by reconnect */ }
                .collect { sessions ->
                    _uiState.update { it.copy(sessions = sessions) }
                }
        }
    }

    private fun loadAutoScanStatus() {
        viewModelScope.launch {
            try {
                val status = autoScanRepository.getStatus()
                _uiState.update { it.copy(scanStatus = status) }
            } catch (_: Exception) {
                // Auto scan may not be configured — ignore
            }
        }
    }

    fun refresh() {
        loadSessions()
        loadAutoScanStatus()
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
                loadAutoScanStatus()
            } catch (e: Exception) {
                _uiState.update { it.copy(error = "Failed to start scan: ${e.message}") }
            }
        }
    }

    fun stopAutoScan() {
        viewModelScope.launch {
            try {
                autoScanRepository.stopScan()
                loadAutoScanStatus()
            } catch (e: Exception) {
                _uiState.update { it.copy(error = "Failed to stop scan: ${e.message}") }
            }
        }
    }

    fun clearError() {
        _uiState.update { it.copy(error = null) }
    }

    override fun onCleared() {
        super.onCleared()
        sessionRepository.disconnectSessionList()
    }
}
