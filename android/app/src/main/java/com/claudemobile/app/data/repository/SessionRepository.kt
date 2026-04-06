package com.claudemobile.app.data.repository

import com.claudemobile.app.data.network.RestApi
import com.claudemobile.app.data.network.WebSocketConnection
import com.claudemobile.app.data.network.WebSocketManager
import com.claudemobile.app.data.network.WsEvent
import com.claudemobile.app.data.network.dto.CreateSessionRequest
import com.claudemobile.app.data.network.dto.ScrollbackResponse
import com.claudemobile.app.data.network.dto.SessionResponse
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.mapNotNull
import kotlinx.serialization.json.Json

class SessionRepository(
    private val restApi: RestApi,
    private val webSocketManager: WebSocketManager,
) {
    private val json = Json { ignoreUnknownKeys = true }
    private var sessionListConnection: WebSocketConnection? = null

    suspend fun createSession(request: CreateSessionRequest): SessionResponse {
        return restApi.createSession(request)
    }

    suspend fun listSessions(): List<SessionResponse> {
        return restApi.listSessions()
    }

    suspend fun deleteSession(sessionId: String, force: Boolean = false) {
        restApi.deleteSession(sessionId, force)
    }

    suspend fun getScrollback(sessionId: String, lines: Int = 1000): ScrollbackResponse {
        return restApi.getScrollback(sessionId, lines)
    }

    fun observeSessionList(): Flow<List<SessionResponse>> {
        sessionListConnection?.disconnect()
        val connection = webSocketManager.createConnection("ws/sessions")
        sessionListConnection = connection
        connection.connect()

        return connection.events.mapNotNull { event ->
            when (event) {
                is WsEvent.Message -> {
                    try {
                        json.decodeFromString<List<SessionResponse>>(event.text)
                    } catch (_: Exception) {
                        null
                    }
                }
                else -> null
            }
        }
    }

    fun createSessionIoConnection(sessionId: String): WebSocketConnection {
        return webSocketManager.createConnection("ws/session/$sessionId")
    }

    fun disconnectSessionList() {
        sessionListConnection?.disconnect()
        sessionListConnection = null
    }
}
