package com.mobaide.app.data.events

import android.util.Log
import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WebSocketManager
import com.mobaide.app.data.network.WsEvent
import com.mobaide.app.data.network.dto.TemplateDeletedPayload
import com.mobaide.app.data.network.dto.TemplateDto
import com.mobaide.app.data.repository.AgentRepository
import com.mobaide.app.data.repository.TemplatesRepository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import javax.inject.Inject
import javax.inject.Singleton

/**
 * App-scoped sink for **user-scoped** events, sister to [AgentEventBus].
 *
 * Unlike [AgentEventBus] — which connects to `/relay/ws/events/{agentId}`
 * and only receives events for the paired agent — this bus connects to
 * `ws/user-events`, authenticated with the user's central-server JWT.
 * That channel survives agent-selection changes and even delivers events
 * for agents that are currently offline (crucially, the
 * `agent.status_changed` event that flips an offline agent back to
 * online in real time).
 *
 * Events are routed straight into [AgentRepository] so the list screen's
 * `StateFlow` collector updates without any extra plumbing.
 *
 * Lifecycle: mirrors [AgentEventBus]. Starts when user logs in, stops
 * when user logs out. `start()` is idempotent.
 */
@Singleton
class UserEventBus @Inject constructor(
    private val webSocketManager: WebSocketManager,
    private val sessionManager: SessionManager,
    private val userIdentityStore: UserIdentityStore,
    private val agentRepository: AgentRepository,
    private val templatesRepository: TemplatesRepository,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val json = Json { ignoreUnknownKeys = true }

    enum class ConnState { Disconnected, Connecting, Connected }

    private val _connection = MutableStateFlow(ConnState.Disconnected)
    val connection: StateFlow<ConnState> = _connection.asStateFlow()

    private var conn: WebSocketConnection? = null
    private var collectorJob: Job? = null

    init {
        // Start/stop tracks the user-identity lifecycle, not the
        // agent-connection lifecycle, because the user list itself needs
        // live status updates before any agent has been paired.
        scope.launch {
            sessionManager.authState.drop(1).collect { state ->
                when (state) {
                    SessionManager.AuthState.Unauthenticated -> stop()
                    is SessionManager.AuthState.IncompatibleServer -> stop()
                    SessionManager.AuthState.UserOnly,
                    SessionManager.AuthState.FullyConnected -> start()
                }
            }
        }
    }

    @Synchronized
    fun start() {
        if (conn != null) return
        if (!userIdentityStore.isUserLoggedIn()) {
            _connection.value = ConnState.Disconnected
            return
        }
        val c = webSocketManager.createUserConnection("ws/user-events")
        conn = c
        _connection.value = ConnState.Connecting
        c.connect()
        collectorJob = scope.launch {
            c.events.collect { handle(it) }
        }
    }

    @Synchronized
    fun stop() {
        collectorJob?.cancel()
        collectorJob = null
        conn?.disconnect()
        conn = null
        _connection.value = ConnState.Disconnected
    }

    private suspend fun handle(event: WsEvent) {
        when (event) {
            is WsEvent.Connected -> {
                _connection.value = ConnState.Connected
                // Reconcile any transitions we missed while the socket
                // was down (or on first connect). Cheap HTTP calls; the
                // StateFlows are replaced atomically by the refresh.
                try {
                    agentRepository.refreshAgents()
                } catch (t: Throwable) {
                    Log.w(TAG, "refreshAgents on WS connect failed", t)
                }
                try {
                    templatesRepository.refreshTemplates()
                } catch (t: Throwable) {
                    Log.w(TAG, "refreshTemplates on WS connect failed", t)
                }
            }
            is WsEvent.Disconnected -> _connection.value = ConnState.Connecting
            is WsEvent.Error -> _connection.value = ConnState.Connecting
            is WsEvent.Message -> parse(event.text)
            is WsEvent.Binary -> Unit  // user-events channel is text-only
        }
    }

    private fun parse(text: String) {
        val envelope = try {
            json.decodeFromString(AgentEventEnvelope.serializer(), text)
        } catch (t: Throwable) {
            Log.w(TAG, "Dropping malformed user-event envelope: $text", t)
            return
        }
        val payload = envelope.payload as? JsonObject ?: envelope.payload.jsonObject
        when (envelope.type) {
            "agent.status_changed" -> {
                val p = try {
                    json.decodeFromJsonElement(
                        AgentStatusChangedPayload.serializer(), payload,
                    )
                } catch (t: Throwable) {
                    Log.w(TAG, "Malformed agent.status_changed payload", t)
                    return
                }
                agentRepository.updateAgentOnline(p.agent_id, p.online)
            }
            "template.created", "template.updated" -> {
                val t = try {
                    json.decodeFromJsonElement(TemplateDto.serializer(), payload)
                } catch (t: Throwable) {
                    Log.w(TAG, "Malformed ${envelope.type} payload", t)
                    return
                }
                templatesRepository.applyUpserted(t)
            }
            "template.deleted" -> {
                val p = try {
                    json.decodeFromJsonElement(
                        TemplateDeletedPayload.serializer(), payload,
                    )
                } catch (t: Throwable) {
                    Log.w(TAG, "Malformed template.deleted payload", t)
                    return
                }
                templatesRepository.applyDeleted(p.template_id)
            }
            // Unknown types are silently ignored — forward compatible.
            else -> Unit
        }
    }

    companion object {
        private const val TAG = "UserEventBus"
    }
}
