package com.mobaide.app.ui.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_DEFAULT
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_MAX
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_MIN
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.data.repository.AuthRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class SettingsUiState(
    val connectionInfo: String = "",
    val username: String = "",
    val userEmail: String = "",
    val terminalDisplayMode: TerminalDisplayMode = TerminalDisplayMode.Full,
    val terminalStaticColumns: Int = TERMINAL_STATIC_COLUMNS_DEFAULT,
)

@HiltViewModel
class SettingsViewModel @Inject constructor(
    private val authRepository: AuthRepository,
    private val tokenStore: TokenStore,
    private val preferencesStore: PreferencesStore,
) : ViewModel() {

    private val _uiState = MutableStateFlow(
        SettingsUiState(
            connectionInfo = buildConnectionInfo(),
            username = authRepository.getUsername() ?: tokenStore.getDeviceId() ?: "",
            userEmail = authRepository.getUserEmail() ?: "",
        ),
    )
    val uiState: StateFlow<SettingsUiState> = _uiState.asStateFlow()

    init {
        viewModelScope.launch {
            preferencesStore.terminalDisplayMode.collect { mode ->
                _uiState.update { it.copy(terminalDisplayMode = mode) }
            }
        }
        viewModelScope.launch {
            preferencesStore.terminalStaticColumns.collect { cols ->
                _uiState.update { it.copy(terminalStaticColumns = cols) }
            }
        }
    }

    fun setTerminalDisplayMode(mode: TerminalDisplayMode) {
        viewModelScope.launch { preferencesStore.setTerminalDisplayMode(mode) }
    }

    fun setTerminalStaticColumns(columns: Int) {
        val clamped = columns.coerceIn(TERMINAL_STATIC_COLUMNS_MIN, TERMINAL_STATIC_COLUMNS_MAX)
        viewModelScope.launch { preferencesStore.setTerminalStaticColumns(clamped) }
    }

    fun logout() {
        authRepository.userLogout()
    }

    private fun buildConnectionInfo(): String = when (tokenStore.getConnectionMode()) {
        TokenStore.ConnectionMode.DIRECT -> {
            "Direct: ${authRepository.getAgentUrl() ?: "unknown"}"
        }
        TokenStore.ConnectionMode.RELAY -> {
            val server = tokenStore.getRelayServerUrl() ?: "unknown"
            val agentId = tokenStore.getAgentId() ?: "unknown"
            "Relay: $server (agent $agentId)"
        }
    }
}
