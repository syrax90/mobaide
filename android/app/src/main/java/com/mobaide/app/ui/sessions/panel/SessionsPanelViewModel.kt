package com.mobaide.app.ui.sessions.panel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.dto.AgentSummaryResponse
import com.mobaide.app.data.network.dto.CustomAgentTypeDto
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.repository.AgentRepository
import com.mobaide.app.data.repository.AgentSwitchCoordinator
import com.mobaide.app.data.repository.CustomAgentTypesRepository
import com.mobaide.app.data.repository.SessionRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

data class SessionsPanelUiState(
    val sessions: List<SessionResponse> = emptyList(),
    /** False until the connected agent has pushed its snapshot; the panel
     *  shows "Reconnecting…" instead of an empty list meanwhile. */
    val synced: Boolean = false,
    val customTypeNames: Map<String, String> = emptyMap(),
    val agents: List<AgentSummaryResponse> = emptyList(),
    val connectedAgentId: String? = null,
    val switchingAgentId: String? = null,
    /** The session a panel-started stop is waiting on, if any; every row's
     *  stop control is disabled while it is set. */
    val stoppingSessionId: String? = null,
) {
    val connectedAgent: AgentSummaryResponse?
        get() = agents.firstOrNull { it.agent_id == connectedAgentId }

    val otherAgents: List<AgentSummaryResponse>
        get() = agents.filter { it.agent_id != connectedAgentId }
}

sealed interface SessionsPanelEvent {
    /** [nextSessionId] is the open session to show instead when the stopped
     *  one is on screen, or null when none is left. */
    data class Stopped(val sessionId: String, val nextSessionId: String?) : SessionsPanelEvent
    data class StopFailed(val message: String) : SessionsPanelEvent
}

/**
 * State for the terminal's sessions drawer: the connected agent's live
 * session list plus the other agents the account owns. Every source is a
 * bus-fed repository cache, so the drawer needs no fetch of its own beyond a
 * best-effort agents refresh at creation.
 */
@HiltViewModel
class SessionsPanelViewModel @Inject constructor(
    private val sessionRepository: SessionRepository,
    customAgentTypesRepository: CustomAgentTypesRepository,
    private val agentRepository: AgentRepository,
    tokenStore: TokenStore,
    agentEventBus: AgentEventBus,
    private val agentSwitchCoordinator: AgentSwitchCoordinator,
) : ViewModel() {

    private val connectedAgentId = tokenStore.getAgentId()

    private val stoppingSessionId = MutableStateFlow<String?>(null)

    private val _events = Channel<SessionsPanelEvent>(Channel.BUFFERED)
    val events: Flow<SessionsPanelEvent> = _events.receiveAsFlow()

    // A snapshot from a dropped tunnel is not agent-authoritative (see
    // `SessionRepository`), so the list reads as synced only while the bus is up.
    private fun isSynced(synced: Boolean, connection: AgentEventBus.ConnState) =
        synced && connection == AgentEventBus.ConnState.Connected

    private val liveSessions = combine(
        sessionRepository.observeSessionList(),
        sessionRepository.observeSessionsSynced(),
        agentEventBus.connection,
    ) { sessions, synced, connection ->
        sessions to isSynced(synced, connection)
    }

    private fun panelState(
        sessions: List<SessionResponse>,
        synced: Boolean,
        types: List<CustomAgentTypeDto>,
        agents: List<AgentSummaryResponse>,
        switching: String?,
        stopping: String?,
    ) = SessionsPanelUiState(
        sessions = sessions,
        synced = synced,
        customTypeNames = types.associate { "custom_${it.id}" to it.name },
        agents = agents,
        connectedAgentId = connectedAgentId,
        switchingAgentId = switching,
        stoppingSessionId = stopping,
    )

    // Seeded from the caches rather than empty: a terminal that opens with the
    // drawer already up (a stop hand-off) must not flash "Reconnecting…" or
    // fade every row in on its first frame.
    val uiState: StateFlow<SessionsPanelUiState> = combine(
        liveSessions,
        customAgentTypesRepository.observeTypes(),
        agentRepository.observeAgents(),
        agentSwitchCoordinator.switchingAgentId,
        stoppingSessionId,
    ) { (sessions, synced), types, agents, switching, stopping ->
        panelState(sessions, synced, types, agents, switching, stopping)
    }.stateIn(
        viewModelScope,
        SharingStarted.WhileSubscribed(5_000),
        panelState(
            sessions = sessionRepository.observeSessionList().value,
            synced = isSynced(
                sessionRepository.observeSessionsSynced().value,
                agentEventBus.connection.value,
            ),
            types = customAgentTypesRepository.observeTypes().value,
            agents = agentRepository.observeAgents().value,
            switching = agentSwitchCoordinator.switchingAgentId.value,
            stopping = null,
        ),
    )

    init {
        viewModelScope.launch {
            try {
                agentRepository.refreshAgents()
            } catch (_: Exception) {
                // The cached list from the agents screen still renders.
            }
        }
    }

    /** The row disappears through the agent's `sessions.changed` push, so a
     *  success needs no refetch here. One stop at a time: the panel disables
     *  every stop control while one is in flight. */
    fun stopSession(sessionId: String, force: Boolean) {
        if (stoppingSessionId.value != null) return
        stoppingSessionId.value = sessionId
        // Positions are read before the request: the agent's push can drop the
        // stopped row before the stop resolves.
        val snapshot = sessionRepository.observeSessionList().value
        fun stopped() = SessionsPanelEvent.Stopped(
            sessionId,
            nearestOpenSessionId(snapshot, sessionRepository.observeSessionList().value, sessionId),
        )
        viewModelScope.launch {
            try {
                sessionRepository.deleteSession(sessionId, force)
                _events.send(stopped())
            } catch (e: ApiException) {
                // A 404 means the session is already gone, so the stop the user
                // asked for has happened.
                _events.send(
                    if (e.code == 404) stopped()
                    else SessionsPanelEvent.StopFailed("Failed to stop session: ${e.message}"),
                )
            } catch (e: Exception) {
                _events.send(SessionsPanelEvent.StopFailed("Failed to stop session: ${e.message}"))
            } finally {
                stoppingSessionId.value = null
            }
        }
    }

    fun switchAgent(agent: AgentSummaryResponse) {
        agentSwitchCoordinator.switchAndOpenSessions(agent)
    }
}

/**
 * The closest open session to the stopped row — below first, then above,
 * widening outward. Positions come from [snapshot], the list as it was when the
 * stop was confirmed; a candidate must still be open in [current].
 */
internal fun nearestOpenSessionId(
    snapshot: List<SessionResponse>,
    current: List<SessionResponse>,
    stoppedId: String,
): String? {
    val index = snapshot.indexOfFirst { it.session_id == stoppedId }
    if (index < 0) return null
    val open = current
        .filter { it.state == "running" || it.state == "starting" }
        .mapTo(HashSet()) { it.session_id }
    for (distance in 1 until snapshot.size) {
        for (candidate in listOf(snapshot.getOrNull(index + distance), snapshot.getOrNull(index - distance))) {
            if (candidate != null && candidate.session_id in open) return candidate.session_id
        }
    }
    return null
}
