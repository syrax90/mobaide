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
import okhttp3.OkHttpClient
import okhttp3.Request
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
    private val url: String,
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
        val request = Request.Builder().url(url).build()
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
    fun createConnection(path: String): WebSocketConnection {
        val serverUrl = tokenStore.getServerUrl()
            ?: throw IllegalStateException("Server URL not configured")
        val token = tokenStore.getAccessToken() ?: ""
        val wsScheme = if (serverUrl.startsWith("https")) "wss" else "ws"
        val host = serverUrl.removePrefix("https://").removePrefix("http://")
        val separator = if (path.contains("?")) "&" else "?"
        val url = "$wsScheme://$host/$path${separator}token=$token"
        return WebSocketConnection(client, url)
    }
}
