package com.claudemobile.app.data.network

import com.claudemobile.app.data.auth.SessionManager
import com.claudemobile.app.data.auth.TokenStore
import kotlinx.coroutines.CoroutineExceptionHandler
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
import kotlinx.coroutines.runBlocking
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
    private val sessionManager: SessionManager,
    /**
     * Returns the URL to connect to, or `null` when the caller should
     * abandon the connect attempt (e.g. because the user is logged out
     * and no agent URL is stored). **Must not throw** — historically a
     * throw here propagated through the scope's unhandled coroutine
     * exception handler and crashed the app.
     */
    private val urlProvider: () -> String?,
) {
    /**
     * Defence-in-depth: without this handler, any uncaught exception in
     * a coroutine launched on [scope] bubbles up to Android's default
     * `Thread.uncaughtExceptionHandler` and terminates the process.
     * With it, exotic failures inside the URL provider or the reconnect
     * loop degrade to a [WsEvent.Error] instead.
     */
    private val exceptionHandler = CoroutineExceptionHandler { _, t ->
        _events.tryEmit(WsEvent.Error(t))
    }

    private val scope = CoroutineScope(
        SupervisorJob() + Dispatchers.Default + exceptionHandler,
    )
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
        val sent = webSocket?.send(text) ?: false
        if (!sent) {
            _events.tryEmit(WsEvent.Error(IllegalStateException("WebSocket send failed")))
        }
        return sent
    }

    fun sendBinary(bytes: ByteString): Boolean {
        val sent = webSocket?.send(bytes) ?: false
        if (!sent) {
            _events.tryEmit(WsEvent.Error(IllegalStateException("WebSocket send failed")))
        }
        return sent
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
        // Any throw from the URL provider (or its upstream dependencies)
        // is caught here — letting it escape would tear down the
        // coroutine scope and crash the app.
        val url = try {
            urlProvider()
        } catch (t: Throwable) {
            _events.tryEmit(WsEvent.Error(t))
            return
        } ?: run {
            // Logged out / no agent URL / session dead — don't connect
            // and don't retry. A subsequent login will create a fresh
            // WebSocketConnection anyway.
            _events.tryEmit(WsEvent.Disconnected)
            return
        }

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
        // Don't retry if the caller explicitly disconnected or the
        // session is dead. This short-circuit is what prevents the
        // retry-spam loop that used to hammer `/ws/events` every second
        // after a failed refresh.
        if (!shouldReconnect || !sessionManager.isLoggedIn()) return
        reconnectJob?.cancel()
        reconnectJob = scope.launch {
            delay(backoffMs)
            backoffMs = (backoffMs * 2).coerceAtMost(MAX_BACKOFF_MS)
            mutex.withLock {
                if (shouldReconnect && sessionManager.isLoggedIn()) doConnect()
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
    private val sessionManager: SessionManager,
) {
    /**
     * Build a [WebSocketConnection] whose URL provider pulls the latest
     * agent URL + access token from [TokenStore] every reconnect, so a
     * token rotated by [SessionManager] is picked up on the next
     * handshake without rebuilding the connection object.
     *
     * The provider blocks briefly on [SessionManager.refresh] if the
     * access token is expiring soon — single-flight ensures that at
     * most one refresh HTTP call is issued even if multiple connections
     * reconnect simultaneously. Previously this was done by an
     * in-file `refreshTokenIfNeeded()` using its own OkHttpClient call,
     * which was one of the two independent refresh paths that raced at
     * startup.
     */
    fun createConnection(path: String): WebSocketConnection {
        // Use an anonymous function instead of a lambda so early `return null`
        // exits only the URL provider (not the enclosing function).
        val provider: () -> String? = fun(): String? {
            if (!sessionManager.isLoggedIn()) return null

            if (tokenStore.isAccessTokenExpiringSoon()) {
                // Run on the caller's thread — WebSocketConnection.doConnect
                // already executes inside a coroutine scope, so runBlocking
                // here only parks that single coroutine worker, not the
                // Android main thread.
                when (runBlocking { sessionManager.refresh() }) {
                    SessionManager.RefreshResult.Terminal -> return null
                    else -> Unit
                }
            }

            val agentUrl = tokenStore.getAgentUrl() ?: return null
            val token = tokenStore.getAccessToken() ?: return null
            val wsScheme = if (agentUrl.startsWith("https")) "wss" else "ws"
            val host = agentUrl.removePrefix("https://").removePrefix("http://")
            val separator = if (path.contains("?")) "&" else "?"
            return "$wsScheme://$host/$path${separator}token=$token"
        }

        return WebSocketConnection(
            client = client,
            sessionManager = sessionManager,
            urlProvider = provider,
        )
    }
}
