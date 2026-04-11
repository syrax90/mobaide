package com.claudemobile.app.data.events

import com.claudemobile.app.data.auth.SessionManager
import com.claudemobile.app.data.network.WebSocketConnection
import com.claudemobile.app.data.network.WebSocketManager
import com.claudemobile.app.data.network.WsEvent
import com.claudemobile.app.data.network.dto.AutoScanInstanceDto
import com.claudemobile.app.data.network.dto.AutoScanListResponse
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.long
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.filterIsInstance
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import javax.inject.Inject
import javax.inject.Singleton

/**
 * App-scoped sink for server-pushed events.
 *
 * Maintains a single WebSocket connection to `ws/events`, demultiplexes
 * incoming JSON envelopes into typed [ServerEvent]s, and exposes them as a
 * [SharedFlow] that any repository can subscribe to.
 *
 * Lifecycle: [start] is idempotent and safe to call repeatedly (e.g. on every
 * activity start). [forceReconnect] tears the connection down and rebuilds it,
 * which the server treats as a new client and replies with a fresh snapshot.
 */
@Singleton
class ServerEventBus @Inject constructor(
    private val webSocketManager: WebSocketManager,
    private val sessionManager: SessionManager,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val json = Json { ignoreUnknownKeys = true }

    private val _events = MutableSharedFlow<ServerEvent>(
        replay = 0,
        extraBufferCapacity = 256,
    )
    val events: SharedFlow<ServerEvent> = _events.asSharedFlow()

    enum class ConnState { Disconnected, Connecting, Connected }

    private val _connection = MutableStateFlow(ConnState.Disconnected)
    val connection: StateFlow<ConnState> = _connection.asStateFlow()

    private var conn: WebSocketConnection? = null
    private var collectorJob: Job? = null

    init {
        // React to session-state transitions only. `drop(1)` skips the
        // StateFlow's initial replay so construction doesn't race with
        // the first explicit [start] call from [MainActivity]; cold-start
        // is handled by that explicit call, while mid-session transitions
        // (login after logout, forced logout after refresh failure) flow
        // through this collector.
        scope.launch {
            sessionManager.authState.drop(1).collect { state ->
                when (state) {
                    SessionManager.AuthState.LoggedIn -> start()
                    SessionManager.AuthState.LoggedOut -> stop()
                }
            }
        }
    }

    @Synchronized
    fun start() {
        if (conn != null) return
        if (!sessionManager.isLoggedIn()) {
            // No-op until the user authenticates. The authState collector
            // above will call start() again when that happens.
            _connection.value = ConnState.Disconnected
            return
        }
        val c = webSocketManager.createConnection("ws/events")
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

    @Synchronized
    fun forceReconnect() {
        stop()
        start()
    }

    inline fun <reified T : ServerEvent> stream(): kotlinx.coroutines.flow.Flow<T> =
        events.filterIsInstance()

    private suspend fun handle(event: WsEvent) {
        when (event) {
            is WsEvent.Connected -> _connection.value = ConnState.Connected
            is WsEvent.Disconnected -> _connection.value = ConnState.Connecting
            is WsEvent.Error -> _connection.value = ConnState.Connecting
            is WsEvent.Message -> parseAndEmit(event.text)
            is WsEvent.Binary -> Unit  // /ws/events is text-only
        }
    }

    private suspend fun parseAndEmit(text: String) {
        val envelope = try {
            json.decodeFromString(ServerEventEnvelope.serializer(), text)
        } catch (_: Exception) {
            return
        }
        val payload = envelope.payload as? JsonObject ?: envelope.payload.jsonObject
        val typed: ServerEvent? = try {
            when (envelope.type) {
                "sessions.changed" -> ServerEvent.SessionsChanged(
                    json.decodeFromJsonElement(SessionsChangedPayload.serializer(), payload).sessions
                )
                "session.updated" -> ServerEvent.SessionUpdated(
                    json.decodeFromJsonElement(SessionUpdatedPayload.serializer(), payload).session
                )
                "projects.changed" -> ServerEvent.ProjectsChanged(
                    json.decodeFromJsonElement(ProjectsChangedPayload.serializer(), payload).projects
                )
                "autoscan.status" -> ServerEvent.AutoScanStatus(
                    json.decodeFromJsonElement(AutoScanInstanceDto.serializer(), payload)
                )
                "autoscan.list.changed" -> ServerEvent.AutoScanListChanged(
                    json.decodeFromJsonElement(AutoScanListResponse.serializer(), payload)
                )
                "autoscan.event" -> {
                    val projectName = payload["project_name"]?.jsonPrimitive?.contentOrNull.orEmpty()
                    val evt = payload["event"] as? JsonObject
                    if (evt == null) null else {
                        val seq = evt["seq"]?.jsonPrimitive?.long ?: 0L
                        val ts = evt["ts"]?.jsonPrimitive?.contentOrNull.orEmpty()
                        val type = evt["type"]?.jsonPrimitive?.contentOrNull.orEmpty()
                        val pl = evt["payload"] as? JsonObject ?: JsonObject(emptyMap())
                        ServerEvent.AutoScanEvent(
                            projectName = projectName,
                            seq = seq,
                            ts = ts,
                            payload = ScanPayloadParser.parse(type, pl),
                        )
                    }
                }
                else -> null
            }
        } catch (_: Exception) {
            null
        }
        if (typed != null) _events.emit(typed)
    }
}
