package com.claudemobile.app.data.network

import com.claudemobile.app.data.auth.TokenStore
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString

sealed class WsEvent {
    data object Connected : WsEvent()
    data object Disconnected : WsEvent()
    data class Message(val text: String) : WsEvent()
    data class Binary(val bytes: ByteString) : WsEvent()
    data class Error(val throwable: Throwable) : WsEvent()
}

class WebSocketConnection(
    private val client: OkHttpClient,
    private val urlProvider: () -> String,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val mutex = Mutex()
    private var webSocket: WebSocket? = null
    private var reconnectJob: Job? = null
    private var shouldReconnect = true
    private var backoffMs = INITIAL_BACKOFF_MS

    private val _events = MutableSharedFlow<WsEvent>(replay = 1, extraBufferCapacity = 100)
    val events: SharedFlow<WsEvent> = _events.asSharedFlow()

    fun connect() {
        scope.launch {
            mutex.withLock {
                shouldReconnect = true
                doConnect()
            }
        }
    }

    fun send(text: String): Boolean {
        return webSocket?.send(text) ?: false
    }

    fun sendBinary(bytes: ByteString): Boolean {
        return webSocket?.send(bytes) ?: false
    }

    fun disconnect() {
        shouldReconnect = false
        reconnectJob?.cancel()
        reconnectJob = null
        webSocket?.close(1000, "Client disconnect")
        webSocket = null
        scope.cancel()
    }

    private fun doConnect() {
        val request = Request.Builder().url(urlProvider()).build()
        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                backoffMs = INITIAL_BACKOFF_MS
                _events.tryEmit(WsEvent.Connected)
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                _events.tryEmit(WsEvent.Message(text))
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                _events.tryEmit(WsEvent.Binary(bytes))
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(code, reason)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                _events.tryEmit(WsEvent.Disconnected)
                scheduleReconnect()
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                _events.tryEmit(WsEvent.Error(t))
                _events.tryEmit(WsEvent.Disconnected)
                scheduleReconnect()
            }
        })
    }

    private fun scheduleReconnect() {
        if (!shouldReconnect) return
        reconnectJob?.cancel()
        reconnectJob = scope.launch {
            delay(backoffMs)
            backoffMs = (backoffMs * 2).coerceAtMost(MAX_BACKOFF_MS)
            mutex.withLock {
                if (shouldReconnect) doConnect()
            }
        }
    }

    companion object {
        private const val INITIAL_BACKOFF_MS = 1000L
        private const val MAX_BACKOFF_MS = 30_000L
    }
}

class WebSocketManager(
    private val client: OkHttpClient,
    private val tokenStore: TokenStore,
) {
    private val json = Json { ignoreUnknownKeys = true }

    fun createConnection(path: String): WebSocketConnection {
        return WebSocketConnection(client) {
            refreshTokenIfNeeded()
            val serverUrl = tokenStore.getServerUrl()
                ?: throw IllegalStateException("Server URL not configured")
            val token = tokenStore.getAccessToken() ?: ""
            val wsScheme = if (serverUrl.startsWith("https")) "wss" else "ws"
            val host = serverUrl.removePrefix("https://").removePrefix("http://")
            val separator = if (path.contains("?")) "&" else "?"
            "$wsScheme://$host/$path${separator}token=$token"
        }
    }

    /**
     * If the current access token is expiring soon, synchronously refresh it
     * using the refresh token. Called from the URL-provider lambda before each
     * WebSocket connect/reconnect so that stale tokens are replaced.
     *
     * AuthInterceptor skips `/api/auth/refresh` paths, so this request passes
     * through the interceptor chain without recursive refresh.
     */
    private fun refreshTokenIfNeeded() {
        if (!tokenStore.isAccessTokenExpiringSoon()) return
        val refreshToken = tokenStore.getRefreshToken() ?: return
        val serverUrl = tokenStore.getServerUrl() ?: return

        val body = """{"refresh_token":"$refreshToken"}"""
            .toRequestBody("application/json".toMediaType())
        val request = Request.Builder()
            .url("$serverUrl/api/auth/refresh")
            .post(body)
            .build()

        try {
            client.newCall(request).execute().use { response ->
                if (response.isSuccessful) {
                    val responseBody = response.body?.string() ?: return
                    val tokens = json.decodeFromString<TokenResponseDto>(responseBody)
                    tokenStore.saveTokens(tokens.access_token, tokens.refresh_token)
                }
            }
        } catch (_: Exception) {
            // Best-effort; if refresh fails we try connecting with the current
            // token anyway — the server will reject and backoff-retry will fire.
        }
    }

    @kotlinx.serialization.Serializable
    private data class TokenResponseDto(
        val access_token: String,
        val refresh_token: String,
    )
}
