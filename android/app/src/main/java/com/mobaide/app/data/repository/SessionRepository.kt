package com.mobaide.app.data.repository

import com.mobaide.app.data.events.AgentEvent
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WebSocketManager
import com.mobaide.app.data.network.dto.CodexLoginResponse
import com.mobaide.app.data.network.dto.CreateSessionRequest
import com.mobaide.app.data.network.dto.ScrollbackResponse
import com.mobaide.app.data.network.dto.SessionResponse
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

class SessionRepository(
    private val restApi: RestApi,
    private val webSocketManager: WebSocketManager,
    private val eventBus: AgentEventBus,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    private val _sessions = MutableStateFlow<List<SessionResponse>>(emptyList())

    init {
        scope.launch {
            eventBus.stream<AgentEvent.SessionsChanged>().collect { ev ->
                _sessions.value = ev.sessions
            }
        }
        scope.launch {
            eventBus.stream<AgentEvent.SessionUpdated>().collect { ev ->
                _sessions.update { list ->
                    val updated = ev.session
                    if (list.any { it.session_id == updated.session_id }) {
                        list.map { if (it.session_id == updated.session_id) updated else it }
                    } else {
                        list + updated
                    }
                }
            }
        }
    }

    suspend fun createSession(request: CreateSessionRequest): SessionResponse =
        restApi.createSession(request)

    suspend fun listSessions(): List<SessionResponse> = restApi.listSessions()

    /** One-shot Codex sign-in; the successful retry reconciles the session list
     *  via the existing `SessionsChanged` subscription above. */
    suspend fun codexLogin(apiKey: String): CodexLoginResponse = restApi.codexLogin(apiKey)

    suspend fun deleteSession(sessionId: String, force: Boolean = false) {
        restApi.deleteSession(sessionId, force)
    }

    suspend fun getScrollback(sessionId: String, lines: Int = 1000): ScrollbackResponse =
        restApi.getScrollback(sessionId, lines)

    /** Live, app-scoped session list. Seeded by the bus snapshot on connect. */
    fun observeSessionList(): StateFlow<List<SessionResponse>> = _sessions.asStateFlow()

    fun createSessionIoConnection(sessionId: String): WebSocketConnection =
        webSocketManager.createConnection("ws/session/$sessionId")
}
