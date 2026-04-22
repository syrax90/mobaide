package com.claudemobile.app.data.network

import com.claudemobile.app.data.auth.SessionManager
import com.claudemobile.app.data.auth.TokenStore
import com.claudemobile.app.data.auth.UserIdentityStore
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
    /**
     * Predicate that gates reconnection attempts. Defaults to the
     * agent-scoped session check (`FullyConnected`) which is correct
     * for per-agent channels, but user-scoped channels (see
     * [WebSocketManager.createUserConnection]) override this to keep
     * reconnecting while the user is merely logged in.
     */
    private val isAuthenticated: () -> Boolean = sessionManager::isLoggedIn,
    /**
     * Invoked exactly once when the server closes the handshake with
     * code 4001 (authentication failed) or completes the HTTP upgrade
     * with status 401. Used by user-scoped channels to invalidate the
     * dashboard JWT — the matching REST interceptor does the same on
     * 401, and the two paths race on cold start, so this callback must
     * route through [SessionManager.invalidateUserSession] which is
     * idempotent. Default is a no-op (agent-scoped channels rely on
     * the pre-connect refresh in their URL provider instead).
     */
    private val onAuthFailure: (() -> Unit)? = null,
) {
    /**
     * Degrade uncaught coroutine exceptions to [WsEvent.Error] instead of
     * propagating to Android's default handler and terminating the process.
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
        // Catch URL provider throws so they don't escape the coroutine scope.
        val url = try {
            urlProvider()
        } catch (t: Throwable) {
            _events.tryEmit(WsEvent.Error(t))
            return
        } ?: run {
            // No URL (logged out / no agent URL / session dead) — emit
            // Disconnected and stop; a fresh login produces a new connection.
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
                if (code == AUTH_FAILED_CLOSE_CODE) {
                    handleAuthFailure()
                    return
                }
                scheduleReconnect()
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                _events.tryEmit(WsEvent.Error(t))
                _events.tryEmit(WsEvent.Disconnected)
                // okhttp surfaces a rejected WebSocket upgrade with the
                // original HTTP status in `response.code`; 401 here means
                // the server refused the handshake for the same reason a
                // 4001 `onClosed` would — treat them identically.
                if (response?.code == 401) {
                    handleAuthFailure()
                    return
                }
                scheduleReconnect()
            }
        })
    }

    private fun scheduleReconnect() {
        // Skip retry if the caller disconnected or the session is no longer valid.
        if (!shouldReconnect || !isAuthenticated()) return
        reconnectJob?.cancel()
        reconnectJob = scope.launch {
            delay(backoffMs)
            backoffMs = (backoffMs * 2).coerceAtMost(MAX_BACKOFF_MS)
            mutex.withLock {
                if (shouldReconnect && isAuthenticated()) doConnect()
            }
        }
    }

    /**
     * Stop reconnecting and notify the caller that the server rejected
     * our credentials. Safe to call multiple times — the `onAuthFailure`
     * callback routes through [SessionManager.invalidateUserSession]
     * which is itself idempotent.
     */
    private fun handleAuthFailure() {
        shouldReconnect = false
        reconnectJob?.cancel()
        reconnectJob = null
        onAuthFailure?.invoke()
    }

    companion object {
        private const val INITIAL_BACKOFF_MS = 1000L
        private const val MAX_BACKOFF_MS = 30_000L

        /**
         * Close code emitted by the server when the token is missing,
         * malformed, or rejected. 4001 is in the application-private
         * range 4000–4999 per RFC 6455 §7.4.2.
         */
        private const val AUTH_FAILED_CLOSE_CODE = 4001
    }
}

class WebSocketManager(
    private val client: OkHttpClient,
    private val tokenStore: TokenStore,
    private val sessionManager: SessionManager,
    private val userIdentityStore: UserIdentityStore,
) {
    /**
     * Build a [WebSocketConnection] whose URL provider pulls the latest
     * credentials from [TokenStore] every reconnect, so a token rotated
     * by [SessionManager] is picked up on the next handshake without
     * rebuilding the connection object.
     *
     * In relay mode, the [path] is rewritten to use the central server's
     * relay WebSocket endpoints with the paired agent's ID.
     */
    fun createConnection(path: String): WebSocketConnection {
        // Use an anonymous function instead of a lambda so early `return null`
        // exits only the URL provider (not the enclosing function).
        val provider: () -> String? = fun(): String? {
            if (!sessionManager.isLoggedIn()) return null

            if (tokenStore.isAccessTokenExpiringSoon()) {
                when (runBlocking { sessionManager.refresh() }) {
                    SessionManager.RefreshResult.Terminal -> return null
                    else -> Unit
                }
            }

            val token = tokenStore.getAccessToken() ?: return null

            val url = when (tokenStore.getConnectionMode()) {
                TokenStore.ConnectionMode.DIRECT -> {
                    val agentUrl = tokenStore.getAgentUrl() ?: return null
                    val wsScheme = if (agentUrl.startsWith("https")) "wss" else "ws"
                    val host = agentUrl.removePrefix("https://").removePrefix("http://")
                    "$wsScheme://$host/$path"
                }

                TokenStore.ConnectionMode.RELAY -> {
                    val serverUrl = tokenStore.getRelayServerUrl() ?: return null
                    val agentId = tokenStore.getAgentId() ?: return null
                    val wsScheme = if (serverUrl.startsWith("https")) "wss" else "ws"
                    val host = serverUrl.removePrefix("https://").removePrefix("http://")
                    val relayPath = mapToRelayPath(path, agentId)
                    "$wsScheme://$host/$relayPath"
                }
            }

            val separator = if (url.contains("?")) "&" else "?"
            return "$url${separator}token=$token"
        }

        return WebSocketConnection(
            client = client,
            sessionManager = sessionManager,
            urlProvider = provider,
        )
    }

    /**
     * Build a WebSocket connection authenticated with the **user** JWT
     * (central-server dashboard token), not the agent-scoped mobile
     * token. Used for user-wide channels that span all of a user's
     * agents — e.g. presence events on `ws/user-events`.
     *
     * The URL is always resolved against the central server (`relay mode`
     * server URL in relay deployments; `agent URL` as a fallback in
     * direct mode for single-agent dev setups where the agent itself
     * fronts the user API).
     */
    fun createUserConnection(path: String): WebSocketConnection {
        val provider: () -> String? = fun(): String? {
            if (!userIdentityStore.isUserLoggedIn()) return null
            val token = userIdentityStore.getUserAccessToken() ?: return null

            val baseUrl = when (tokenStore.getConnectionMode()) {
                TokenStore.ConnectionMode.RELAY -> tokenStore.getRelayServerUrl()
                TokenStore.ConnectionMode.DIRECT -> tokenStore.getAgentUrl()
            } ?: return null

            val wsScheme = if (baseUrl.startsWith("https")) "wss" else "ws"
            val host = baseUrl.removePrefix("https://").removePrefix("http://")
            val url = "$wsScheme://$host/$path"

            val separator = if (url.contains("?")) "&" else "?"
            return "$url${separator}token=$token"
        }

        return WebSocketConnection(
            client = client,
            sessionManager = sessionManager,
            urlProvider = provider,
            isAuthenticated = { userIdentityStore.isUserLoggedIn() },
            // 4001/401 on the user-scoped channel means the dashboard JWT
            // is dead; invalidate the session so navigation returns to
            // login and the error snackbar fires exactly once.
            onAuthFailure = {
                sessionManager.invalidateUserSession(
                    SessionManager.InvalidationReason.TokenRejected,
                )
            },
        )
    }

    /**
     * Map a direct-mode WebSocket path to its relay equivalent.
     *
     * | Direct Path              | Relay Path                                     |
     * |--------------------------|------------------------------------------------|
     * | `ws/session/{sid}`       | `relay/ws/session/{agentId}/{sid}`             |
     * | `ws/events`              | `relay/ws/events/{agentId}`                    |
     * | `ws/sessions`            | `relay/ws/sessions/{agentId}`                  |
     * | `ws/autoscan`            | `relay/ws/autoscan/{agentId}`                  |
     */
    private fun mapToRelayPath(path: String, agentId: String): String {
        if (path.startsWith("ws/session/")) {
            val sessionId = path.removePrefix("ws/session/")
            return "relay/ws/session/$agentId/$sessionId"
        }
        if (path == "ws/events") {
            return "relay/ws/events/$agentId"
        }
        if (path == "ws/sessions") {
            return "relay/ws/sessions/$agentId"
        }
        if (path == "ws/autoscan") {
            return "relay/ws/autoscan/$agentId"
        }
        // Fallback: prepend relay prefix
        return "relay/$path/$agentId"
    }
}
