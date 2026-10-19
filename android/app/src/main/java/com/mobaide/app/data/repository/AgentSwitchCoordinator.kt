package com.mobaide.app.data.repository

import com.mobaide.app.BuildConfig
import com.mobaide.app.data.auth.ConnectionStore
import com.mobaide.app.data.auth.DeviceIdentityStore
import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.dto.AgentSummaryResponse
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.launch
import javax.inject.Inject
import javax.inject.Singleton

/** The user signed out while the agent tokens were being minted. */
class SignedOutException : IllegalStateException("Signed out while connecting")

/**
 * Connects the app to a different agent. One install talks to one agent at
 * a time, so a switch tears the current connection down before the new
 * credentials are minted; the auth state cycles FullyConnected → UserOnly →
 * FullyConnected and `AppNavigation` resets the back stack on the way.
 *
 * Runs on its own app-scoped [scope], never a ViewModel's: that stack reset
 * destroys the screen that asked for the switch, and cancelling the job
 * mid-way would leave the user signed in with no agent at all.
 */
@Singleton
class AgentSwitchCoordinator @Inject constructor(
    private val agentRepository: AgentRepository,
    private val connectionStore: ConnectionStore,
    private val tokenStore: TokenStore,
    private val sessionManager: SessionManager,
    private val agentEventBus: AgentEventBus,
    private val deviceIdentityStore: DeviceIdentityStore,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)

    private val _switchingAgentId = MutableStateFlow<String?>(null)

    /** The agent a switch is in flight for, or null when idle. */
    val switchingAgentId: StateFlow<String?> = _switchingAgentId.asStateFlow()

    private val _pendingSessionsList = MutableStateFlow(false)

    /** True after a [switchAndOpenSessions] succeeded, until `AppNavigation`
     *  consumes it by opening the new agent's sessions list. */
    val pendingSessionsList: StateFlow<Boolean> = _pendingSessionsList.asStateFlow()

    private val _errors = Channel<String>(Channel.BUFFERED)

    /** Failures of [switchAndOpenSessions], surfaced by the agent list the
     *  user lands on (the switch disconnects before it can fail). */
    val errors: Flow<String> = _errors.receiveAsFlow()

    fun consumePendingSessionsList() {
        _pendingSessionsList.value = false
    }

    /**
     * Switches to [agent] and, on success, asks `AppNavigation` to open its
     * sessions list. No-op while another switch is in flight.
     */
    fun switchAndOpenSessions(agent: AgentSummaryResponse) {
        if (_switchingAgentId.value != null) return
        scope.launch {
            try {
                _pendingSessionsList.value = true
                switchTo(agent)
            } catch (e: SignedOutException) {
                // The user is on the login screen; a message buffered for the
                // next sign-in would read as an error on a session that had none.
                _pendingSessionsList.value = false
            } catch (e: Exception) {
                _pendingSessionsList.value = false
                // Same rule when the mint itself failed because the central
                // session expired (401 → forced sign-out): no message for the
                // next sign-in to find.
                if (sessionManager.isUserAuthenticated()) {
                    _errors.trySend(e.message ?: "Failed to connect to ${agent.name}")
                }
            }
        }
    }

    /**
     * The connect sequence itself. Throws on failure; the caller owns any
     * navigation and error surface. Not reentrant: a second call while one is
     * running throws rather than racing the credential rewrite.
     */
    suspend fun switchTo(agent: AgentSummaryResponse) {
        check(_switchingAgentId.compareAndSet(null, agent.agent_id)) { "Already connecting to an agent" }
        try {
            if (sessionManager.isLoggedIn()) {
                agentEventBus.stop()
                sessionManager.agentDisconnect()
            }

            val deviceId = deviceIdentityStore.getOrCreateDeviceId()
            val deviceName = deviceIdentityStore.getDeviceName()
            val tokens = agentRepository.getMobileTokensForAgent(
                agent.agent_id,
                mobileDeviceId = deviceId,
                mobileDeviceName = deviceName,
            )

            // This scope outlives a sign-out. A logout (manual, or a forced 401)
            // that landed while the mint was in flight must win: persisting the
            // relay credentials now would resurrect FullyConnected with no user.
            val connected = sessionManager.tryMarkAgentConnected {
                tokenStore.saveRelayCredentials(
                    serverUrl = BuildConfig.CENTRAL_SERVER_URL,
                    agentId = agent.agent_id,
                    pairingId = agent.agent_id,
                    deviceId = deviceId,
                    accessToken = tokens.access_token,
                    refreshToken = tokens.refresh_token,
                )
            }
            if (!connected) throw SignedOutException()

            val existingProfile = connectionStore.listProfiles()
                .find { it.agentId == agent.agent_id && it.mode == "relay" }
            if (existingProfile != null) {
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

            agentEventBus.start()
        } finally {
            _switchingAgentId.value = null
        }
    }
}
