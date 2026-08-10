package com.mobaide.app.data.auth

import com.mobaide.app.data.local.PreferencesStore
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
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
    private val preferencesStore: PreferencesStore,
    @Named("refresh") private val refreshClient: OkHttpClient,
) {

    sealed interface AuthState {
        /** No user identity on file. */
        object Unauthenticated : AuthState

        /** User logged in to central server, no agent connection. */
        object UserOnly : AuthState

        /** User logged in AND agent connection established. */
        object FullyConnected : AuthState

        /**
         * Server reports a wire-contract that this build cannot speak. The
         * navigation graph collapses to the upgrade screen and never advances
         * out of this state without a manual app update.
         */
        data class IncompatibleServer(
            val reason: String,
            val minClientVersion: Int,
            val playStoreUrl: String,
            val playStoreFallbackUrl: String,
        ) : AuthState
    }

    /** Distinguish terminal from transient refresh failures so callers can decide
     *  whether to surface "session expired" (terminal) or "network flake" (transient). */
    sealed interface RefreshResult {
        /** Tokens rotated successfully. */
        object Success : RefreshResult

        /** Refresh rejected (401) or no refresh token on file — session is dead. */
        object Terminal : RefreshResult

        /** Network/5xx error — tokens are still believed valid, caller may retry. */
        object Transient : RefreshResult
    }

    /** Carry the reason a session was invalidated so consumers can decide
     *  whether to surface a "session expired" notice (server rejected the
     *  stored credentials), a "your account was removed" notice (admin
     *  destructively unregistered the user), or stay silent (user asked
     *  to log out). */
    sealed interface InvalidationReason {
        /** Server rejected the dashboard JWT (401 on REST, 4001 on WS). */
        object TokenRejected : InvalidationReason

        /** Server closed the user-scoped WS with code 4010
         *  (``WS_CLOSE_ADMIN_REVOKED``). Either the admin destructively
         *  unregistered the user on the central server, or the user
         *  themselves filed a self-delete request via Settings
         *  and the server is tearing the live session down. In both cases
         *  the local credentials are now invalid for any user-scoped
         *  operation — a self-delete during the 14-day grace window can
         *  still be cancelled by signing in fresh and tapping
         *  "Cancel scheduled deletion". */
        object AccountRemoved : InvalidationReason

        /** User explicitly tapped logout. */
        object Manual : InvalidationReason
    }

    private val json = Json { ignoreUnknownKeys = true }
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val inFlight = AtomicReference<Deferred<RefreshResult>?>(null)

    private val _authState = MutableStateFlow(resolveInitialState())
    val authState: StateFlow<AuthState> = _authState.asStateFlow()

    // `replay = 1` so that a [TokenRejected] invalidation fired before
    // LoginViewModel subscribes (e.g. from the central-API interceptor on
    // cold start, when the OkHttp IO thread runs its 401 handler before
    // navigation has swapped in the LOGIN route and constructed the VM)
    // is still delivered on first collection. `extraBufferCapacity = 0`
    // because the `@Synchronized` guard on [invalidateUserSession] makes
    // concurrent same-reason emits impossible.
    //
    // NOTE: a SharedFlow with `replay = 1` caches its last value *forever*,
    // which would cause a stale `TokenRejected` to be re-delivered to a
    // fresh [LoginViewModel] long after the user has re-logged-in. To
    // prevent that, [markUserLoggedIn] clears the replay cache on every
    // successful login/register/Google-auth so subsequent logouts start
    // from a clean slate.
    private val _sessionEvents = MutableSharedFlow<InvalidationReason>(
        replay = 1,
        extraBufferCapacity = 0,
    )
    val sessionEvents: SharedFlow<InvalidationReason> = _sessionEvents.asSharedFlow()

    private fun resolveInitialState(): AuthState = when {
        userIdentityStore.isUserLoggedIn() && tokenStore.isLoggedIn() -> AuthState.FullyConnected
        userIdentityStore.isUserLoggedIn() -> AuthState.UserOnly
        else -> AuthState.Unauthenticated
    }

    /** Return `true` when the agent connection is fully established.
     *  Backward-compatible: matches the old LoggedIn semantic. */
    fun isLoggedIn(): Boolean = _authState.value is AuthState.FullyConnected

    /** Return `true` when at least a user identity exists (UserOnly or FullyConnected). */
    fun isUserAuthenticated(): Boolean =
        _authState.value is AuthState.UserOnly || _authState.value is AuthState.FullyConnected

    /** Mark the user as logged in after a successful register/login against the central server. */
    @OptIn(ExperimentalCoroutinesApi::class)
    @Synchronized
    fun markUserLoggedIn() {
        // Clear the replay cache so a stale [TokenRejected] from the
        // previous session (cached to survive cold-start ordering —
        // see [_sessionEvents] docs) cannot be re-delivered to the
        // next LoginViewModel instance and fire a spurious "session
        // expired" snackbar after a successful re-login.
        //
        // `resetReplayCache()` is `@ExperimentalCoroutinesApi` per
        // kotlinx-coroutines 1.9.0. The API has been stable since 1.4.0
        // and is the documented mechanism for this use case
        // (https://kotlinlang.org/api/kotlinx.coroutines/kotlinx-coroutines-core/kotlinx.coroutines.flow/-mutable-shared-flow/reset-replay-cache.html).
        _sessionEvents.resetReplayCache()
        _authState.value = AuthState.UserOnly
    }

    /** Mark the agent connection as established after tokens have been persisted to [TokenStore]. */
    @Synchronized
    fun markAgentConnected() {
        _authState.value = AuthState.FullyConnected
    }

    /**
     * Pin the session to a terminal upgrade-required state. Cleared only by a
     * successful version-gate probe (see [VersionGateRepository.reset]) — the
     * UI is expected to direct the user to the Play Store before retry.
     */
    @Synchronized
    fun markIncompatibleServer(state: AuthState.IncompatibleServer) {
        _authState.value = state
        inFlight.getAndSet(null)?.cancel()
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
     * Transitions any state → Unauthenticated. Delegates to
     * [invalidateUserSession] with [InvalidationReason.Manual] so that
     * consumers collecting [sessionEvents] (e.g. LoginViewModel) can
     * distinguish user-initiated logout from server-forced logout and
     * only show a "session expired" notice for the latter.
     */
    fun userLogout() {
        invalidateUserSession(InvalidationReason.Manual)
    }

    /**
     * Clear stored agent-connection tokens and flip the observable state
     * to [AuthState.Unauthenticated] in one critical section. Also cancels
     * any in-flight refresh so a late completer cannot resurrect state.
     *
     * This is the legacy logout path, now equivalent to [userLogout] —
     * it clears both stores.
     */
    fun forceLogout() {
        invalidateUserSession(InvalidationReason.Manual)
    }

    /**
     * Atomically clear the entire session and signal why. Called from:
     *  * [CentralAuthInterceptor] on a 401 from the central server
     *    (dashboard JWT rejected) → [InvalidationReason.TokenRejected];
     *  * [com.mobaide.app.data.network.WebSocketConnection] on a
     *    4001 close on the user-scoped channel → [InvalidationReason.TokenRejected];
     *  * [com.mobaide.app.data.network.WebSocketConnection] on a
     *    4010 close on the user-scoped channel → [InvalidationReason.AccountRemoved];
     *  * [userLogout] / [forceLogout] for user-initiated logout
     *    → [InvalidationReason.Manual].
     *
     * Idempotent: subsequent calls while already [AuthState.Unauthenticated]
     * are a no-op and do NOT re-emit on [sessionEvents]. This makes the
     * REST-401 / WS-4001 race on cold start emit the "session expired"
     * snackbar exactly once even though both paths fire.
     */
    @Synchronized
    fun invalidateUserSession(reason: InvalidationReason) {
        if (_authState.value is AuthState.Unauthenticated) return
        userIdentityStore.clearUserSession()
        // Off the critical section: this method is @Synchronized and the
        // DataStore write suspends. This runs on the forced-logout path too,
        // where an unhandled DataStore fault would reach the thread's
        // uncaught handler and turn a 401 into a crash.
        scope.launch { runCatching { preferencesStore.removeAllRecentCustomPaths() } }
        tokenStore.clear()
        _authState.value = AuthState.Unauthenticated
        inFlight.getAndSet(null)?.cancel()
        when (reason) {
            is InvalidationReason.TokenRejected,
            is InvalidationReason.AccountRemoved -> _sessionEvents.tryEmit(reason)
            is InvalidationReason.Manual -> Unit
        }
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

    /** Atomically apply a successful refresh to `TokenStore` + `_authState`. */
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
     * Return a terminal refresh result after clearing only the agent-connection tokens.
     * Transitions to [AuthState.UserOnly] if the user identity is still valid,
     * otherwise to [AuthState.Unauthenticated].
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
