package com.claudemobile.app.data.auth

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.atomic.AtomicReference
import javax.inject.Inject
import javax.inject.Named
import javax.inject.Singleton

/**
 * Owns the app's authenticated session lifecycle: observable [authState],
 * single-flight token refresh, and atomic logout.
 *
 * ## Three-tier auth state
 *
 * The session has two independent axes: **user identity** (central server)
 * and **agent connection** (per-agent tokens). The combined state is:
 *
 * - [AuthState.Unauthenticated] — no user identity on file.
 * - [AuthState.UserOnly] — user logged in (central server), but no agent
 *   connection established yet.
 * - [AuthState.FullyConnected] — user logged in AND agent-connection
 *   tokens are on file.
 *
 * ### Backward compatibility
 *
 * [isLoggedIn] returns `true` **only** for [AuthState.FullyConnected].
 * Every existing consumer (AuthInterceptor, WebSocketManager,
 * AgentEventBus) depends on this meaning and continues to work without
 * modification. New code should use [isUserAuthenticated] when it only
 * needs to know whether a user identity exists.
 *
 * ## Token refresh
 *
 * This is layered on top of [TokenStore] so that multiple concurrent
 * callers — the REST interceptor, the WebSocket URL provider, the UI —
 * can observe "am I logged in?" reactively and cooperate on a single
 * in-flight `/api/auth/refresh` request.
 *
 * ### Why single-flight instead of Mutex
 *
 * A plain `Mutex.withLock` would serialize N waiters behind one refresh
 * call; with `runBlocking` in the OkHttp interceptor that parks N
 * dispatcher threads and risks starving the authenticated client's
 * request pool. Instead we cache one [Deferred] via [AtomicReference]:
 * all callers `.await()` the **same** deferred, so exactly one HTTP
 * refresh request is issued even under heavy concurrency.
 *
 * ### Why a dedicated refresh OkHttpClient
 *
 * The refresh call must NOT go back through [AuthInterceptor], otherwise
 * a Hilt circular dependency forms
 * (`AuthInterceptor → SessionManager → OkHttpClient(authed) → AuthInterceptor`)
 * and the interceptor would re-enter itself recursively at runtime.
 * `NetworkModule` provides a bare `@Named("refresh")` client with no
 * interceptors, which breaks the cycle cleanly.
 */
@Singleton
class SessionManager @Inject constructor(
    private val tokenStore: TokenStore,
    private val userIdentityStore: UserIdentityStore,
    @Named("refresh") private val refreshClient: OkHttpClient,
) {

    sealed interface AuthState {
        /** No user identity on file. */
        object Unauthenticated : AuthState

        /** User logged in to central server, no agent connection. */
        object UserOnly : AuthState

        /** User logged in AND agent connection established. */
        object FullyConnected : AuthState
    }

    /** Terminal vs transient distinction lets callers decide whether to
     *  surface "session expired" (terminal) or "network flake" (transient). */
    sealed interface RefreshResult {
        /** Tokens rotated successfully. */
        object Success : RefreshResult

        /** Refresh rejected (401) or no refresh token on file — session is dead. */
        object Terminal : RefreshResult

        /** Network/5xx error — tokens are still believed valid, caller may retry. */
        object Transient : RefreshResult
    }

    private val json = Json { ignoreUnknownKeys = true }
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val inFlight = AtomicReference<Deferred<RefreshResult>?>(null)

    private val _authState = MutableStateFlow(resolveInitialState())
    val authState: StateFlow<AuthState> = _authState.asStateFlow()

    private fun resolveInitialState(): AuthState = when {
        userIdentityStore.isUserLoggedIn() && tokenStore.isLoggedIn() -> AuthState.FullyConnected
        userIdentityStore.isUserLoggedIn() -> AuthState.UserOnly
        else -> AuthState.Unauthenticated
    }

    /** Returns `true` when the agent connection is fully established.
     *  Backward-compatible: matches the old LoggedIn semantic. */
    fun isLoggedIn(): Boolean = _authState.value is AuthState.FullyConnected

    /** Returns `true` when at least a user identity exists (UserOnly or FullyConnected). */
    fun isUserAuthenticated(): Boolean =
        _authState.value is AuthState.UserOnly || _authState.value is AuthState.FullyConnected

    /** Called after a successful user register/login against the central server. */
    @Synchronized
    fun markUserLoggedIn() {
        _authState.value = AuthState.UserOnly
    }

    /** Called after a successful agent login/pairing has persisted tokens to [TokenStore]. */
    @Synchronized
    fun markAgentConnected() {
        _authState.value = AuthState.FullyConnected
    }

    /**
     * Disconnect from the current agent but keep the user identity.
     * Transitions FullyConnected → UserOnly.
     */
    @Synchronized
    fun agentDisconnect() {
        tokenStore.clear()
        if (_authState.value is AuthState.FullyConnected) {
            _authState.value = AuthState.UserOnly
        }
        inFlight.getAndSet(null)?.cancel()
    }

    /**
     * Full logout: clear user identity AND agent-connection tokens.
     * Transitions any state → Unauthenticated.
     */
    @Synchronized
    fun userLogout() {
        userIdentityStore.clearUserSession()
        tokenStore.clear()
        _authState.value = AuthState.Unauthenticated
        inFlight.getAndSet(null)?.cancel()
    }

    /**
     * Clear stored agent-connection tokens and flip the observable state
     * to [AuthState.Unauthenticated] in one critical section. Also cancels
     * any in-flight refresh so a late completer cannot resurrect state.
     *
     * This is the legacy logout path, now equivalent to [userLogout] —
     * it clears both stores.
     */
    @Synchronized
    fun forceLogout() {
        userIdentityStore.clearUserSession()
        tokenStore.clear()
        _authState.value = AuthState.Unauthenticated
        inFlight.getAndSet(null)?.cancel()
    }

    /**
     * Single-flight refresh. Concurrent callers share one HTTP request.
     *
     * Returns [RefreshResult.Success] if tokens were rotated (either by
     * this caller or by the coalesced in-flight call), [RefreshResult.Terminal]
     * if the server rejected the refresh token (401) or no refresh token
     * exists, or [RefreshResult.Transient] on network / 5xx errors (tokens
     * are kept intact so the caller may retry later).
     */
    suspend fun refresh(): RefreshResult {
        while (true) {
            val existing = inFlight.get()
            if (existing != null) {
                return try {
                    existing.await()
                } catch (_: Exception) {
                    RefreshResult.Terminal
                }
            }
            val fresh = scope.async(start = kotlinx.coroutines.CoroutineStart.LAZY) { doRefresh() }
            if (inFlight.compareAndSet(null, fresh)) {
                return try {
                    fresh.await()
                } catch (_: Exception) {
                    RefreshResult.Terminal
                } finally {
                    inFlight.compareAndSet(fresh, null)
                }
            } else {
                fresh.cancel()
            }
        }
    }

    private fun doRefresh(): RefreshResult {
        val refreshToken = tokenStore.getRefreshToken() ?: return terminalAgentOnly()

        val payload = json.encodeToString(
            RefreshRequestDto.serializer(),
            RefreshRequestDto(refreshToken),
        )

        val refreshUrl = when (tokenStore.getConnectionMode()) {
            TokenStore.ConnectionMode.DIRECT -> {
                val agentUrl = tokenStore.getAgentUrl() ?: return terminalAgentOnly()
                "$agentUrl/api/auth/refresh"
            }
            TokenStore.ConnectionMode.RELAY -> {
                val serverUrl = tokenStore.getRelayServerUrl() ?: return terminalAgentOnly()
                "$serverUrl/api/mobile/refresh"
            }
        }

        val request = Request.Builder()
            .url(refreshUrl)
            .post(payload.toRequestBody(JSON_MEDIA_TYPE))
            .build()

        return try {
            refreshClient.newCall(request).execute().use { response ->
                when {
                    response.isSuccessful -> {
                        val body = response.body?.string()
                            ?: return@use RefreshResult.Transient
                        val dto = json.decodeFromString(
                            TokenResponseDto.serializer(),
                            body,
                        )
                        applyRefreshSuccess(dto.access_token, dto.refresh_token)
                    }
                    response.code == 401 -> terminalAgentOnly()
                    else -> RefreshResult.Transient
                }
            }
        } catch (_: IOException) {
            RefreshResult.Transient
        } catch (_: Exception) {
            RefreshResult.Transient
        }
    }

    /**
     * Atomically apply a successful refresh to `TokenStore` + `_authState`.
     */
    @Synchronized
    private fun applyRefreshSuccess(access: String, refresh: String): RefreshResult {
        if (_authState.value is AuthState.Unauthenticated) {
            return RefreshResult.Terminal
        }
        tokenStore.saveTokens(access, refresh)
        _authState.value = AuthState.FullyConnected
        return RefreshResult.Success
    }

    /**
     * Terminal result for agent-connection refresh failure. Clears only
     * agent-connection tokens and transitions to [AuthState.UserOnly] if
     * the user identity is still valid, otherwise to [AuthState.Unauthenticated].
     */
    @Synchronized
    private fun terminalAgentOnly(): RefreshResult {
        tokenStore.clear()
        _authState.value = if (userIdentityStore.isUserLoggedIn()) {
            AuthState.UserOnly
        } else {
            AuthState.Unauthenticated
        }
        return RefreshResult.Terminal
    }

    @Serializable
    private data class RefreshRequestDto(val refresh_token: String)

    @Serializable
    private data class TokenResponseDto(
        val access_token: String,
        val refresh_token: String,
    )

    companion object {
        private val JSON_MEDIA_TYPE = "application/json".toMediaType()
    }
}
