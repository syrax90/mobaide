package com.mobaide.app.data.events

import android.util.Log
import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WebSocketManager
import com.mobaide.app.data.network.WsEvent
import com.mobaide.app.data.network.dto.AutoScanInstanceDto
import com.mobaide.app.data.network.dto.AutoScanListResponse
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.long
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.filterIsInstance
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import javax.inject.Inject
import javax.inject.Singleton

/**
 * App-scoped sink for agent-pushed events.
 *
 * Maintains a single WebSocket connection to `ws/events`, demultiplexes
 * incoming JSON envelopes into typed [AgentEvent]s, and exposes them as a
 * [SharedFlow] that any repository can subscribe to.
 *
 * Lifecycle: [start] is idempotent and safe to call repeatedly (e.g. on every
 * activity start). [forceReconnect] tears the connection down and rebuilds it,
 * which the agent treats as a new client and replies with a fresh snapshot.
 */
@Singleton
class AgentEventBus @Inject constructor(
    private val webSocketManager: WebSocketManager,
    private val sessionManager: SessionManager,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    // coerceInputValues no longer touches unknown SessionKind strings in
    // sessions.changed / session.* payloads (they decode verbatim via
    // SessionKindSerializer's STRING descriptor, proto-v7); it is kept so an
    // explicit `"kind": null` still falls back to SessionResponse.kind's
    // default instead of throwing and dropping the whole event.
    private val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }

    private val _events = MutableSharedFlow<AgentEvent>(
        replay = 0,
        extraBufferCapacity = 256,
    )
    val events: SharedFlow<AgentEvent> = _events.asSharedFlow()

    enum class ConnState { Disconnected, Connecting, Connected }

    private val _connection = MutableStateFlow(ConnState.Disconnected)
    val connection: StateFlow<ConnState> = _connection.asStateFlow()

    private var conn: WebSocketConnection? = null
    private var collectorJob: Job? = null
    private var unknownEnvelopeTypeTotal: Long = 0

    init {
        // Observe the initial authState too: on cold-start with a restored
        // token, the StateFlow's initial replay is already FullyConnected,
        // and the previous `drop(1)` swallowed it — leaving this collector
        // never calling start() on that path. start() is @Synchronized and
        // idempotent, so observing the initial replay is safe even when
        // SessionListViewModel.init also calls it.
        scope.launch {
            sessionManager.authState.collect { state ->
                when (state) {
                    SessionManager.AuthState.FullyConnected -> start()
                    SessionManager.AuthState.UserOnly -> stop()
                    SessionManager.AuthState.Unauthenticated -> stop()
                    is SessionManager.AuthState.IncompatibleServer -> stop()
                }
            }
        }
    }

    @Synchronized
    fun start() {
        if (conn != null) return
        if (!sessionManager.isLoggedIn()) {
            // No-op until authenticated; the authState collector will retry.
            _connection.value = ConnState.Disconnected
            return
        }
        val c = webSocketManager.createConnection("ws/events")
        conn = c
        _connection.value = ConnState.Connecting
        // UNDISPATCHED so events.collect subscribes synchronously BEFORE
        // connect() opens the WS. The alternative lets the server fire
        // Connected + the initial sessions.changed / projects.changed
        // envelopes into SharedFlow(replay=1) before this collector
        // attaches, which drops every envelope except the last — losing
        // the initial session list and leaving the UI empty after app
        // restart.
        collectorJob = scope.launch(start = CoroutineStart.UNDISPATCHED) {
            c.events.collect { handle(it) }
        }
        c.connect()
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

    inline fun <reified T : AgentEvent> stream(): kotlinx.coroutines.flow.Flow<T> =
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
            json.decodeFromString(AgentEventEnvelope.serializer(), text)
        } catch (_: Exception) {
            return
        }
        val payload = envelope.payload as? JsonObject ?: envelope.payload.jsonObject
        val typed: AgentEvent? = try {
            when (envelope.type) {
                "sessions.changed" -> AgentEvent.SessionsChanged(
                    json.decodeFromJsonElement(SessionsChangedPayload.serializer(), payload).sessions
                )
                "session.updated" -> AgentEvent.SessionUpdated(
                    json.decodeFromJsonElement(SessionUpdatedPayload.serializer(), payload).session
                )
                "session.attention" -> AgentEvent.SessionAttention(
                    json.decodeFromJsonElement(SessionUpdatedPayload.serializer(), payload).session
                )
                "projects.changed" -> AgentEvent.ProjectsChanged(
                    json.decodeFromJsonElement(ProjectsChangedPayload.serializer(), payload).projects
                )
                "git.changed" -> AgentEvent.GitChanged(
                    payload["project_name"]?.jsonPrimitive?.contentOrNull.orEmpty()
                )
                "autoscan.status" -> AgentEvent.AutoScanStatus(
                    json.decodeFromJsonElement(AutoScanInstanceDto.serializer(), payload)
                )
                "autoscan.list.changed" -> AgentEvent.AutoScanListChanged(
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
                        AgentEvent.AutoScanEvent(
                            projectName = projectName,
                            seq = seq,
                            ts = ts,
                            payload = ScanPayloadParser.parse(type, pl),
                        )
                    }
                }
                else -> AgentEvent.UnknownEnvelopeType(envelope.type, text)
            }
        } catch (_: Exception) {
            null
        }
        if (typed is AgentEvent.UnknownEnvelopeType) {
            unknownEnvelopeTypeTotal++
            Log.w(TAG, "unknown envelope type=${typed.type} total=$unknownEnvelopeTypeTotal")
        }
        if (typed != null) _events.emit(typed)
    }

    companion object {
        private const val TAG = "AgentEventBus"
    }
}
