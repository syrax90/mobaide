package com.mobaide.app.data.network

import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.data.repository.VersionGateRepository
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
     * Invoked exactly once when the server closes the handshake in a way
     * that signals the local credentials are no longer valid:
     *  * code 4001 (authentication failed) or HTTP-upgrade 401 →
     *    [SessionManager.InvalidationReason.TokenRejected];
     *  * code 4010 (admin revoked / user unregistered) →
     *    [SessionManager.InvalidationReason.AccountRemoved].
     *
     * Used by user-scoped channels to invalidate the dashboard JWT — the
     * matching REST interceptor does the same on 401, and the two paths
     * race on cold start, so this callback must route through
     * [SessionManager.invalidateUserSession] which is idempotent and
     * filters the reason to the right login-screen message. Default is
     * a no-op (agent-scoped channels rely on the pre-connect refresh
     * in their URL provider instead).
     */
    private val onAuthFailure: ((SessionManager.InvalidationReason) -> Unit)? = null,
    /**
     * Invoked exactly once when the server closes with code 4002
     * (PROTOCOL_INCOMPATIBLE). The receiver is expected to mark the session
     * as [SessionManager.AuthState.IncompatibleServer] so the UI swaps to
     * the upgrade screen. Reconnection is suppressed permanently for this
     * connection.
     */
    private val onIncompatibleServer: ((reason: String) -> Unit)? = null,
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

    /**
     * `@Volatile` for the same reason [generation] is: [pause] and [disconnect]
     * write it synchronously from the caller's thread, while `scheduleReconnect`
     * and `doConnect` read it from [scope]'s dispatcher and from OkHttp's, with
     * no lock in between.
     */
    @Volatile
    private var shouldReconnect = true
    private var backoffMs = INITIAL_BACKOFF_MS

    /**
     * Monotonic id incremented every time [doConnect] (or [forceReconnect])
     * spins up a new OkHttp [WebSocket]. Each listener instance captures the
     * id when it is created and short-circuits its callbacks once the id no
     * longer matches — so a stale `onClosed`/`onFailure` from the previous
     * socket cannot inject a phantom `Disconnected` after we have already
     * built its replacement.
     *
     * `@Volatile` is load-bearing: the increment runs under [mutex] on a
     * coroutine thread, but the reads happen inside OkHttp's
     * [WebSocketListener] callbacks on OkHttp's internal dispatcher,
     * **without** acquiring the mutex. Without `@Volatile` the JMM permits
     * the reader thread to cache the old value indefinitely — which would
     * silently re-introduce the phantom-`Disconnected` race the guard is
     * here to prevent.
     */
    @Volatile
    private var generation: Int = 0

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

    /**
     * Close the socket but keep the connection usable, so [connect] can re-arm
     * it later.
     *
     * [disconnect] ends [scope], and every [connect] body runs inside
     * `scope.launch` — so after a disconnect a reconnect is a silent no-op.
     * That is right for teardown and useless for a client that stops streaming
     * on purpose and means to come back.
     *
     * Bumps [generation] for the same reason [forceReconnect] does: OkHttp
     * delivers the old listener's `onClosed` asynchronously, and without the
     * bump it would arrive after a resume as a phantom [WsEvent.Disconnected].
     */
    fun pause() {
        // The caller pauses and resumes from one thread and two `scope.launch`
        // bodies on [Dispatchers.Default] have no ordering guarantee between
        // them. A resume's `connect()` overtaking this body would leave
        // `shouldReconnect = false` — wedged until the screen is popped.
        shouldReconnect = false
        generation++
        reconnectJob?.cancel()
        reconnectJob = null
        try {
            webSocket?.close(1000, "Client pause")
        } catch (_: Throwable) {
            // Already CLOSED — either way it is gone.
        }
        webSocket = null
    }

    /**
     * Tear the live socket down and rebuild immediately with the backoff
     * reset to [INITIAL_BACKOFF_MS]. The lifecycle layer calls this on
     * screen-on so the user does not sit through a 30 s Doze-accumulated
     * backoff before the next reconnect attempt fires.
     *
     * Bumps [generation] so the previous listener's `onClosed`/`onFailure`
     * (which OkHttp delivers asynchronously after `webSocket.close`) cannot
     * push a phantom `Disconnected` after the new socket is already up.
     *
     * Respects `shouldReconnect = false` — set by [disconnect] and by the
     * auth-failure / protocol-incompatible paths — so a force-reconnect
     * never hammers a session the server has permanently rejected.
     */
    fun forceReconnect() {
        if (!shouldReconnect || !isAuthenticated()) return
        scope.launch {
            mutex.withLock {
                generation++
                reconnectJob?.cancel()
                reconnectJob = null
                backoffMs = INITIAL_BACKOFF_MS
                try {
                    webSocket?.close(1000, "Force reconnect")
                } catch (_: Throwable) {
                    // OkHttp throws on a socket already in CLOSED state;
                    // either way it's gone — proceed with the rebuild.
                }
                webSocket = null
                if (shouldReconnect && isAuthenticated()) doConnect()
            }
        }
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

        val myGeneration = generation
        val request = Request.Builder().url(url).build()
        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                if (myGeneration != generation) return
                backoffMs = INITIAL_BACKOFF_MS
                _events.tryEmit(WsEvent.Connected)
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                if (myGeneration != generation) return
                _events.tryEmit(WsEvent.Message(text))
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                if (myGeneration != generation) return
                _events.tryEmit(WsEvent.Binary(bytes))
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(code, reason)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                if (myGeneration != generation) return
                _events.tryEmit(WsEvent.Disconnected)
                if (code == AUTH_FAILED_CLOSE_CODE) {
                    handleAuthFailure(SessionManager.InvalidationReason.TokenRejected)
                    return
                }
                if (code == ADMIN_REVOKED_CLOSE_CODE) {
                    handleAuthFailure(SessionManager.InvalidationReason.AccountRemoved)
                    return
                }
                if (code == PROTOCOL_INCOMPATIBLE_CLOSE_CODE) {
                    handleProtocolIncompatible(reason.ifBlank { "Server protocol incompatible" })
                    return
                }
                scheduleReconnect()
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                if (myGeneration != generation) return
                _events.tryEmit(WsEvent.Error(t))
                _events.tryEmit(WsEvent.Disconnected)
                // okhttp surfaces a rejected WebSocket upgrade with the
                // original HTTP status in `response.code`; 401 here means
                // the server refused the handshake for the same reason a
                // 4001 `onClosed` would — treat them identically.
                if (response?.code == 401) {
                    handleAuthFailure(SessionManager.InvalidationReason.TokenRejected)
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
     *
     * The [reason] flows through to the login-screen snackbar so the user
     * can tell "session expired" (TokenRejected) from "account removed"
     * (AccountRemoved).
     */
    private fun handleAuthFailure(reason: SessionManager.InvalidationReason) {
        shouldReconnect = false
        reconnectJob?.cancel()
        reconnectJob = null
        onAuthFailure?.invoke(reason)
    }

    private fun handleProtocolIncompatible(reason: String) {
        shouldReconnect = false
        reconnectJob?.cancel()
        reconnectJob = null
        onIncompatibleServer?.invoke(reason)
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

        /**
         * Close code emitted by the server when the client's
         * ``protocol_version`` is outside the server's supported range.
         */
        private const val PROTOCOL_INCOMPATIBLE_CLOSE_CODE = 4002

        /**
         * Close code emitted by the server when the user's credentials
         * are no longer valid for a user-scoped operation — either the
         * admin destructively unregistered the user/agent, the user
         * filed a self-delete via Settings and the server is
         * tearing the live session down before committing the schedule,
         * or the deletion-sweeper finalised a 14-day-expired schedule.
         * Distinct from [AUTH_FAILED_CLOSE_CODE] so the login-screen
         * snackbar can say "your account was removed" instead of the
         * generic "session expired" — and so reconnection is permanently
         * suppressed (signed re-issue cannot recover a deleted account).
         */
        private const val ADMIN_REVOKED_CLOSE_CODE = 4010
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
            // 4001/401 (TokenRejected) or 4010 (AccountRemoved) on the
            // user-scoped channel both mean the locally-stored credentials
            // can no longer authenticate. Forward the [reason] so the
            // login screen can render the right message; SessionManager
            // is idempotent and dedupes the race with the REST 401 handler.
            onAuthFailure = { reason ->
                sessionManager.invalidateUserSession(reason)
            },
            onIncompatibleServer = { reason ->
                sessionManager.markIncompatibleServer(
                    SessionManager.AuthState.IncompatibleServer(
                        reason = reason,
                        minClientVersion = 0,
                        playStoreUrl = VersionGateRepository.PLAY_DEEP_LINK_PRIMARY,
                        playStoreFallbackUrl = VersionGateRepository.PLAY_DEEP_LINK_FALLBACK,
                    )
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
