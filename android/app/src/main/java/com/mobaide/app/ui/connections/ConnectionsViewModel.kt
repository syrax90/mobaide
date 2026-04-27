package com.mobaide.app.ui.connections

import androidx.lifecycle.ViewModel
import com.mobaide.app.data.auth.ConnectionStore
import com.mobaide.app.data.auth.ConnectionStore.ConnectionProfile
import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.events.AgentEventBus
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import javax.inject.Inject

data class ConnectionsUiState(
    val profiles: List<ConnectionProfile> = emptyList(),
    val activeProfileId: String? = null,
)

@HiltViewModel
class ConnectionsViewModel @Inject constructor(
    private val connectionStore: ConnectionStore,
    private val tokenStore: TokenStore,
    private val sessionManager: SessionManager,
    private val eventBus: AgentEventBus,
) : ViewModel() {

    private val _uiState = MutableStateFlow(loadState())
    val uiState: StateFlow<ConnectionsUiState> = _uiState.asStateFlow()

    fun switchProfile(profileId: String) {
        // Disconnect agent only; user identity tokens are not cleared.
        sessionManager.agentDisconnect()
        eventBus.stop()

        connectionStore.activateProfile(profileId, tokenStore)

        sessionManager.markAgentConnected()
        eventBus.start()

        _uiState.value = loadState()
    }

    fun removeProfile(profileId: String) {
        connectionStore.removeProfile(profileId)
        _uiState.value = loadState()
    }

    private fun loadState(): ConnectionsUiState = ConnectionsUiState(
        profiles = connectionStore.listProfiles(),
        activeProfileId = connectionStore.getActiveProfileId(),
    )
}
