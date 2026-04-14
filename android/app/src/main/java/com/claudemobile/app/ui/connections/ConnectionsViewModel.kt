package com.claudemobile.app.ui.connections

import androidx.lifecycle.ViewModel
import com.claudemobile.app.data.auth.ConnectionStore
import com.claudemobile.app.data.auth.ConnectionStore.ConnectionProfile
import com.claudemobile.app.data.auth.SessionManager
import com.claudemobile.app.data.auth.TokenStore
import com.claudemobile.app.data.events.AgentEventBus
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
        // Tear down current connection
        sessionManager.forceLogout()
        eventBus.stop()

        // Load new profile into TokenStore
        connectionStore.activateProfile(profileId, tokenStore)

        // Re-authenticate
        sessionManager.markLoggedIn()
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
