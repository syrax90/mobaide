package com.claudemobile.app.ui.agents

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.claudemobile.app.BuildConfig
import com.claudemobile.app.data.auth.ConnectionStore
import com.claudemobile.app.data.auth.SessionManager
import com.claudemobile.app.data.auth.TokenStore
import com.claudemobile.app.data.auth.UserIdentityStore
import com.claudemobile.app.data.events.AgentEventBus
import com.claudemobile.app.data.network.dto.AgentSummaryResponse
import com.claudemobile.app.data.repository.AgentRepository
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

data class AgentListUiState(
    val agents: List<AgentSummaryResponse> = emptyList(),
    val isLoading: Boolean = false,
    val error: String? = null,
    val addDialogState: AddAgentDialogState = AddAgentDialogState.Hidden,
    val reRegisterDialogState: ReRegisterDialogState = ReRegisterDialogState.Hidden,
    val deleteConfirmAgent: AgentSummaryResponse? = null,
    val isConnecting: Boolean = false,
    val connectedAgentId: String? = null,
)

@HiltViewModel
class AgentListViewModel @Inject constructor(
    private val agentRepository: AgentRepository,
    private val connectionStore: ConnectionStore,
    private val tokenStore: TokenStore,
    private val sessionManager: SessionManager,
    private val agentEventBus: AgentEventBus,
    private val userIdentityStore: UserIdentityStore,
) : ViewModel() {

    private val _uiState = MutableStateFlow(
        AgentListUiState(connectedAgentId = tokenStore.getAgentId()),
    )
    val uiState: StateFlow<AgentListUiState> = _uiState.asStateFlow()

    private val _navigateToSessions = MutableSharedFlow<Unit>(extraBufferCapacity = 1)
    val navigateToSessions: SharedFlow<Unit> = _navigateToSessions.asSharedFlow()

    init {
        viewModelScope.launch {
            agentRepository.observeAgents().collect { agents ->
                _uiState.update { it.copy(agents = agents) }
            }
        }
        refresh()
    }

    fun refresh() {
        viewModelScope.launch {
            _uiState.update { it.copy(isLoading = true, error = null) }
            try {
                agentRepository.refreshAgents()
            } catch (e: Exception) {
                _uiState.update { it.copy(error = e.message ?: "Failed to load agents") }
            } finally {
                _uiState.update { it.copy(isLoading = false) }
            }
        }
    }

    fun clearError() {
        _uiState.update { it.copy(error = null) }
    }

    // -- Add agent -----------------------------------------------------------

    fun showAddDialog() {
        _uiState.update { it.copy(addDialogState = AddAgentDialogState.Input) }
    }

    fun dismissAddDialog() {
        _uiState.update { it.copy(addDialogState = AddAgentDialogState.Hidden) }
    }

    fun createAgent(name: String) {
        viewModelScope.launch {
            _uiState.update { it.copy(addDialogState = AddAgentDialogState.Loading) }
            try {
                val result = agentRepository.createAgent(name)
                _uiState.update {
                    it.copy(
                        addDialogState = AddAgentDialogState.ShowingCredentials(
                            agentId = result.agent_id,
                            token = result.token,
                        ),
                    )
                }
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(
                        addDialogState = AddAgentDialogState.Hidden,
                        error = e.message ?: "Failed to create agent",
                    )
                }
            }
        }
    }

    // -- Re-register ---------------------------------------------------------

    fun showReRegisterDialog(agent: AgentSummaryResponse) {
        _uiState.update {
            it.copy(
                reRegisterDialogState = ReRegisterDialogState.Confirming(
                    agentId = agent.agent_id,
                    agentName = agent.name,
                ),
            )
        }
    }

    fun dismissReRegisterDialog() {
        _uiState.update { it.copy(reRegisterDialogState = ReRegisterDialogState.Hidden) }
    }

    fun confirmReRegister(agentId: String) {
        viewModelScope.launch {
            _uiState.update {
                it.copy(reRegisterDialogState = ReRegisterDialogState.Loading(agentId))
            }
            try {
                val result = agentRepository.reregisterAgent(agentId)
                _uiState.update {
                    it.copy(
                        reRegisterDialogState = ReRegisterDialogState.ShowingCredentials(
                            agentId = result.agent_id,
                            token = result.token,
                        ),
                    )
                }
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(
                        reRegisterDialogState = ReRegisterDialogState.Hidden,
                        error = e.message ?: "Failed to re-register agent",
                    )
                }
            }
        }
    }

    // -- Delete --------------------------------------------------------------

    fun showDeleteConfirm(agent: AgentSummaryResponse) {
        _uiState.update { it.copy(deleteConfirmAgent = agent) }
    }

    fun dismissDeleteConfirm() {
        _uiState.update { it.copy(deleteConfirmAgent = null) }
    }

    fun confirmDelete(agentId: String) {
        viewModelScope.launch {
            _uiState.update { it.copy(deleteConfirmAgent = null) }
            try {
                // If deleting the currently connected agent, disconnect first
                if (agentId == _uiState.value.connectedAgentId) {
                    agentEventBus.stop()
                    sessionManager.agentDisconnect()
                    _uiState.update { it.copy(connectedAgentId = null) }
                }
                agentRepository.deleteAgent(agentId)
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(error = e.message ?: "Failed to delete agent")
                }
            }
        }
    }

    // -- Connect to agent ----------------------------------------------------

    fun connectToAgent(agent: AgentSummaryResponse) {
        if (_uiState.value.isConnecting) return
        if (agent.agent_id == _uiState.value.connectedAgentId) {
            // Already connected — just navigate
            _navigateToSessions.tryEmit(Unit)
            return
        }
        viewModelScope.launch {
            _uiState.update { it.copy(isConnecting = true, error = null) }
            try {
                // 1. Disconnect current agent if connected
                if (sessionManager.isLoggedIn()) {
                    agentEventBus.stop()
                    sessionManager.agentDisconnect()
                }

                // 2. Get mobile tokens for the target agent
                val tokens = agentRepository.getMobileTokensForAgent(agent.agent_id)
                val userId = userIdentityStore.getUserId() ?: agent.agent_id

                // 3. Save relay credentials to TokenStore
                tokenStore.saveRelayCredentials(
                    serverUrl = BuildConfig.CENTRAL_SERVER_URL,
                    agentId = agent.agent_id,
                    pairingId = userId,
                    deviceId = userId,
                    accessToken = tokens.access_token,
                    refreshToken = tokens.refresh_token,
                )

                // 4. Mark agent as connected
                sessionManager.markAgentConnected()

                // 5. Save connection profile (update existing or create new)
                val existingProfile = connectionStore.listProfiles()
                    .find { it.agentId == agent.agent_id && it.mode == "relay" }
                if (existingProfile != null) {
                    // Update existing profile with fresh tokens
                    connectionStore.saveProfile(
                        existingProfile.copy(
                            accessToken = tokens.access_token,
                            refreshToken = tokens.refresh_token,
                        ),
                    )
                    connectionStore.setActiveProfileId(existingProfile.id)
                } else {
                    connectionStore.captureActiveProfile(
                        tokenStore,
                        label = "Agent: ${agent.name}",
                    )
                }

                // 6. Start event bus for new agent
                agentEventBus.start()

                _uiState.update {
                    it.copy(
                        isConnecting = false,
                        connectedAgentId = agent.agent_id,
                    )
                }

                // 7. Navigate to sessions
                _navigateToSessions.tryEmit(Unit)
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(
                        isConnecting = false,
                        error = e.message ?: "Failed to connect to agent",
                    )
                }
            }
        }
    }
}
