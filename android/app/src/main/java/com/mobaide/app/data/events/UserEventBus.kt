package com.mobaide.app.data.events

import android.util.Log
import com.mobaide.app.data.analytics.AgentPairedConversion
import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WebSocketManager
import com.mobaide.app.data.network.WsEvent
import com.mobaide.app.data.network.dto.CustomAgentTypeDto
import com.mobaide.app.data.network.dto.CustomTypeDeletedPayload
import com.mobaide.app.data.network.dto.SessionModeOrderDto
import com.mobaide.app.data.network.dto.ShortcutDeletedPayload
import com.mobaide.app.data.network.dto.ShortcutDto
import com.mobaide.app.data.network.dto.TemplateDeletedPayload
import com.mobaide.app.data.network.dto.TemplateDto
import com.mobaide.app.data.repository.AgentRepository
import com.mobaide.app.data.repository.CustomAgentTypesRepository
import com.mobaide.app.data.repository.ShortcutsRepository
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
    private val shortcutsRepository: ShortcutsRepository,
    private val customAgentTypesRepository: CustomAgentTypesRepository,
    private val agentPairedConversion: AgentPairedConversion,
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
                    SessionManager.AuthState.Unauthenticated -> {
                        stop()
                        // Account boundary: every logout/account-switch path
                        // funnels through this state, so the account-scoped
                        // caches are dropped here (not on IncompatibleServer,
                        // which is an upgrade gate — the identity persists).
                        // In-flight shortcut/custom-type requests that resolve
                        // after this are discarded by those repositories'
                        // owner guards; TemplatesRepository has no guard yet,
                        // so a template request resolving after logout can
                        // transiently repopulate its cache.
                        templatesRepository.clear()
                        shortcutsRepository.clear()
                        customAgentTypesRepository.clear()
                    }
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
                try {
                    shortcutsRepository.refreshShortcuts()
                } catch (t: Throwable) {
                    Log.w(TAG, "refreshShortcuts on WS connect failed", t)
                }
                try {
                    customAgentTypesRepository.refreshTypes()
                } catch (t: Throwable) {
                    Log.w(TAG, "refreshTypes on WS connect failed", t)
                }
                try {
                    customAgentTypesRepository.refreshModeOrder()
                } catch (t: Throwable) {
                    Log.w(TAG, "refreshModeOrder on WS connect failed", t)
                }
                try {
                    agentPairedConversion.reconcile()
                } catch (t: Throwable) {
                    Log.w(TAG, "conversion reconcile on WS connect failed", t)
                }
            }
            is WsEvent.Disconnected -> _connection.value = ConnState.Connecting
            is WsEvent.Error -> _connection.value = ConnState.Connecting
            is WsEvent.Message -> parse(event.text)
            is WsEvent.Binary -> Unit  // user-events channel is text-only
        }
    }

    private suspend fun parse(text: String) {
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
            "shortcut.created", "shortcut.updated" -> {
                val s = try {
                    json.decodeFromJsonElement(ShortcutDto.serializer(), payload)
                } catch (t: Throwable) {
                    Log.w(TAG, "Malformed ${envelope.type} payload", t)
                    return
                }
                shortcutsRepository.applyUpserted(s)
            }
            "shortcut.deleted" -> {
                val p = try {
                    json.decodeFromJsonElement(
                        ShortcutDeletedPayload.serializer(), payload,
                    )
                } catch (t: Throwable) {
                    Log.w(TAG, "Malformed shortcut.deleted payload", t)
                    return
                }
                shortcutsRepository.applyDeleted(p.shortcut_id)
            }
            "custom_type.created", "custom_type.updated" -> {
                val t = try {
                    json.decodeFromJsonElement(CustomAgentTypeDto.serializer(), payload)
                } catch (t: Throwable) {
                    Log.w(TAG, "Malformed ${envelope.type} payload", t)
                    return
                }
                customAgentTypesRepository.applyUpserted(t)
            }
            "custom_type.deleted" -> {
                val p = try {
                    json.decodeFromJsonElement(
                        CustomTypeDeletedPayload.serializer(), payload,
                    )
                } catch (t: Throwable) {
                    Log.w(TAG, "Malformed custom_type.deleted payload", t)
                    return
                }
                customAgentTypesRepository.applyDeleted(p.id)
                // The server cascades the type's saved shortcuts in the same
                // transaction without per-shortcut events — mirror it locally.
                shortcutsRepository.purgeKind("custom_${p.id}")
            }
            "session_mode_order.updated" -> {
                val order = try {
                    json.decodeFromJsonElement(SessionModeOrderDto.serializer(), payload)
                } catch (t: Throwable) {
                    Log.w(TAG, "Malformed session_mode_order.updated payload", t)
                    return
                }
                customAgentTypesRepository.applyModeOrder(order)
            }
            "account.first_agent_paired" -> {
                try {
                    json.decodeFromJsonElement(FirstAgentPairedPayload.serializer(), payload)
                } catch (t: Throwable) {
                    Log.w(TAG, "Malformed account.first_agent_paired payload", t)
                    return
                }
                try {
                    agentPairedConversion.onFirstAgentPairedEvent()
                } catch (t: Throwable) {
                    Log.w(TAG, "agent_paired conversion failed", t)
                }
            }
            // Unknown types are silently ignored — forward compatible.
            else -> Unit
        }
    }

    companion object {
        private const val TAG = "UserEventBus"
    }
}
