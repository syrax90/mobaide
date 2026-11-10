package com.mobaide.app.data.repository

import com.mobaide.app.data.events.AgentEvent
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WebSocketManager
import com.mobaide.app.data.network.dto.ChatStoredSessionsResponse
import com.mobaide.app.data.network.dto.CodexLoginResponse
import com.mobaide.app.data.network.dto.CreateSessionRequest
import com.mobaide.app.data.network.dto.ScrollbackResponse
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.network.dto.VersionInfoResponse
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

class SessionRepository(
    private val restApi: RestApi,
    private val webSocketManager: WebSocketManager,
    private val eventBus: AgentEventBus,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    private val _sessions = MutableStateFlow<List<SessionResponse>>(emptyList())

    private val _sessionsSynced = MutableStateFlow(false)

    private val _endedSessionNotices = Channel<String>(Channel.BUFFERED)

    init {
        scope.launch {
            eventBus.stream<AgentEvent.SessionsChanged>().collect { ev ->
                _sessions.value = ev.sessions
                _sessionsSynced.value = true
            }
        }
        scope.launch {
            eventBus.stream<AgentEvent.SessionUpdated>().collect { ev ->
                _sessions.update { it.upsert(ev.session) }
            }
        }
        // A list from a dropped tunnel describes an agent we are no longer
        // talking to: the sessions in it may have died with the agent. Callers
        // that act on absence (auto-resume, the terminal's session-gone check)
        // must not read it as agent-authoritative until the next snapshot.
        scope.launch {
            eventBus.connection.collect { state ->
                if (state != AgentEventBus.ConnState.Connected) {
                    _sessionsSynced.value = false
                }
            }
        }
    }

    /** Seeds the cache from the create response instead of waiting for the WS
     *  push, so the window between "created" and "reconciled" can never read as
     *  a session that does not exist. */
    suspend fun createSession(request: CreateSessionRequest): SessionResponse {
        val created = restApi.createSession(request)
        _sessions.update { it.upsert(created) }
        return created
    }

    private fun List<SessionResponse>.upsert(session: SessionResponse): List<SessionResponse> =
        if (any { it.session_id == session.session_id }) {
            map { if (it.session_id == session.session_id) session else it }
        } else {
            this + session
        }

    suspend fun listSessions(): List<SessionResponse> = restApi.listSessions()

    /** Sessions the agent [kind] stored on the PC (a stateless read, like [listSessions]). */
    suspend fun listChatSessions(kind: String, cwd: String?, cursor: String?): ChatStoredSessionsResponse =
        restApi.listChatSessions(kind, cwd, cursor)

    /** One-shot Codex sign-in; the successful retry reconciles the session list
     *  via the existing `SessionsChanged` subscription above. */
    suspend fun codexLogin(apiKey: String): CodexLoginResponse = restApi.codexLogin(apiKey)

    /** Fresh per-call probe of the agent's installed CLIs (via `GET /api/version`).
     *  A stateless read, not server-pushed state, so it stays a plain pass-through
     *  (like [codexLogin]) rather than a cached StateFlow with an event subscription. */
    suspend fun getAgentVersion(): VersionInfoResponse = restApi.getAgentVersion()

    suspend fun deleteSession(sessionId: String, force: Boolean = false) {
        restApi.deleteSession(sessionId, force)
    }

    suspend fun getScrollback(sessionId: String, lines: Int = 1000): ScrollbackResponse =
        restApi.getScrollback(sessionId, lines)

    /** Live, app-scoped session list. Seeded by the bus snapshot on connect. */
    fun observeSessionList(): StateFlow<List<SessionResponse>> = _sessions.asStateFlow()

    /** True while [observeSessionList] holds a snapshot pushed by the currently
     *  connected agent. Absence from the list only means "this session is gone"
     *  when this is true. */
    fun observeSessionsSynced(): StateFlow<Boolean> = _sessionsSynced.asStateFlow()

    /** One-shot notices for a session that ended while the user was trying to
     *  open it, surfaced by whichever session list is on screen. */
    val endedSessionNotices: Flow<String> = _endedSessionNotices.receiveAsFlow()

    fun notifySessionEnded(sessionId: String) {
        _endedSessionNotices.trySend(sessionId)
    }

    fun createSessionIoConnection(sessionId: String): WebSocketConnection =
        webSocketManager.createConnection("ws/session/$sessionId")
}
