package com.claudemobile.app.ui.settings

import androidx.lifecycle.ViewModel
import com.claudemobile.app.data.auth.TokenStore
import com.claudemobile.app.data.repository.AuthRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import javax.inject.Inject

data class SettingsUiState(
    val connectionInfo: String = "",
    val username: String = "",
    val userEmail: String = "",
)

@HiltViewModel
class SettingsViewModel @Inject constructor(
    private val authRepository: AuthRepository,
    private val tokenStore: TokenStore,
) : ViewModel() {

    private val _uiState = MutableStateFlow(
        SettingsUiState(
            connectionInfo = buildConnectionInfo(),
            username = authRepository.getUsername() ?: tokenStore.getDeviceId() ?: "",
            userEmail = authRepository.getUserEmail() ?: "",
        ),
    )
    val uiState: StateFlow<SettingsUiState> = _uiState.asStateFlow()

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
