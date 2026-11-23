package com.mobaide.app.data.repository

import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.events.AgentEvent
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.AgentSettingsResponse
import com.mobaide.app.data.network.dto.keepAwakeCapability
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import java.util.concurrent.atomic.AtomicLong

/**
 * The connected agent's own settings. Reconciled by `agent_settings.changed`,
 * a full snapshot the agent also republishes on every events-channel connect,
 * so an agent switch needs no REST refresh; leaving the connected state
 * clears it.
 */
class AgentSettingsRepository(
    private val restApi: RestApi,
    eventBus: AgentEventBus,
    sessionManager: SessionManager,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val _settings = MutableStateFlow<AgentSettingsResponse?>(null)
    val settings: StateFlow<AgentSettingsResponse?> = _settings.asStateFlow()

    /** Bumped by every `agent_settings.changed`, so an older answer never overwrites one. */
    private val events = AtomicLong()

    init {
        scope.launch {
            eventBus.stream<AgentEvent.AgentSettingsChanged>().collect {
                events.incrementAndGet()
                _settings.value = it.settings
            }
        }
        scope.launch {
            sessionManager.authState.collect { state ->
                if (state != SessionManager.AuthState.FullyConnected) _settings.value = null
            }
        }
    }

    /** `capabilities.agent_settings.keep_awake` of the connected agent. */
    suspend fun keepAwakeSupported(): Boolean = restApi.getAgentVersion().keepAwakeCapability()

    /** A snapshot that arrived while this request was in flight is newer and wins. */
    suspend fun refresh(): AgentSettingsResponse {
        val before = events.get()
        val answer = restApi.getAgentSettings()
        if (events.get() == before) _settings.value = answer
        return answer
    }

    /** Applied on the agent before it answers, for every client of it. */
    suspend fun setKeepAwake(enabled: Boolean): AgentSettingsResponse {
        val before = events.get()
        val answer = restApi.setKeepAwake(enabled)
        if (events.get() == before) _settings.value = answer
        return answer
    }
}
