package com.claudemobile.app.ui.sessions

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.claudemobile.app.data.network.dto.CreateSessionRequest
import com.claudemobile.app.data.network.dto.SessionResponse
import com.claudemobile.app.data.repository.AutoScanRepository
import com.claudemobile.app.data.repository.SessionRepository
import com.claudemobile.app.data.network.dto.ScanStatusResponse
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
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
    val showCreateDialog: Boolean = false,
)

@HiltViewModel
class SessionListViewModel @Inject constructor(
    private val sessionRepository: SessionRepository,
    private val autoScanRepository: AutoScanRepository,
) : ViewModel() {

    private val _uiState = MutableStateFlow(SessionListUiState())
    val uiState: StateFlow<SessionListUiState> = _uiState.asStateFlow()

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
            } catch (e: Exception) {
                _uiState.update { it.copy(isLoading = false, error = e.message) }
            }
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

    fun showCreateDialog() {
        _uiState.update { it.copy(showCreateDialog = true) }
    }

    fun hideCreateDialog() {
        _uiState.update { it.copy(showCreateDialog = false) }
    }

    fun createSession(command: String?, cwd: String?, rows: Int = 24, cols: Int = 80) {
        viewModelScope.launch {
            try {
                val cmdList = command?.takeIf { it.isNotBlank() }?.split(" ")
                sessionRepository.createSession(
                    CreateSessionRequest(
                        command = cmdList,
                        cwd = cwd?.takeIf { it.isNotBlank() },
                        rows = rows,
                        cols = cols,
                    )
                )
                _uiState.update { it.copy(showCreateDialog = false) }
            } catch (e: Exception) {
                _uiState.update { it.copy(error = "Failed to create session: ${e.message}") }
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
