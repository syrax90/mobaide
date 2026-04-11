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
    @Named("refresh") private val refreshClient: OkHttpClient,
) {

    sealed interface AuthState {
        object LoggedIn : AuthState
        object LoggedOut : AuthState
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

    private val _authState = MutableStateFlow<AuthState>(
        if (tokenStore.isLoggedIn()) AuthState.LoggedIn else AuthState.LoggedOut
    )
    val authState: StateFlow<AuthState> = _authState.asStateFlow()

    fun isLoggedIn(): Boolean = _authState.value is AuthState.LoggedIn

    /** Called by [com.claudemobile.app.data.repository.AuthRepository] after a
     *  successful login has persisted fresh tokens to [TokenStore]. */
    fun markLoggedIn() {
        _authState.value = AuthState.LoggedIn
    }

    /**
     * Clear stored tokens and flip the observable state to [AuthState.LoggedOut]
     * in one critical section. Also cancels any in-flight refresh so a late
     * completer cannot resurrect state.
     *
     * Safe to call repeatedly; idempotent.
     */
    @Synchronized
    fun forceLogout() {
        tokenStore.clear()
        _authState.value = AuthState.LoggedOut
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
                    // In-flight was cancelled (e.g. forceLogout) — treat as terminal.
                    RefreshResult.Terminal
                }
            }
            val fresh = scope.async(start = kotlinx.coroutines.CoroutineStart.LAZY) { doRefresh() }
            if (inFlight.compareAndSet(null, fresh)) {
                return try {
                    fresh.await()
                } catch (_: Exception) {
                    // Symmetric with the follower path above: if another caller
                    // invokes [forceLogout] while this coroutine is awaiting its
                    // own Deferred, `fresh` is cancelled and `await()` throws a
                    // `JobCancellationException`. Treat it as a terminal session
                    // result instead of leaking the exception out of `refresh()`
                    // and ultimately out of `runBlocking` inside the OkHttp
                    // interceptor, which would surface as a misleading network
                    // error rather than a clean "session expired" flow.
                    RefreshResult.Terminal
                } finally {
                    inFlight.compareAndSet(fresh, null)
                }
            } else {
                // Lost the race — discard our deferred and loop to await the
                // winning one. CoroutineStart.LAZY guarantees no wasted HTTP call.
                fresh.cancel()
            }
        }
    }

    private fun doRefresh(): RefreshResult {
        val refreshToken = tokenStore.getRefreshToken() ?: return terminal()
        val serverUrl = tokenStore.getServerUrl() ?: return terminal()

        val payload = json.encodeToString(
            RefreshRequestDto.serializer(),
            RefreshRequestDto(refreshToken),
        )
        val request = Request.Builder()
            .url("$serverUrl/api/auth/refresh")
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
                    response.code == 401 -> terminal()
                    else -> RefreshResult.Transient
                }
            }
        } catch (_: IOException) {
            // Network errors are transient — don't log the user out because
            // their wifi dropped for half a second.
            RefreshResult.Transient
        } catch (_: Exception) {
            // Serialization errors, unexpected response shapes — treat as
            // transient so we don't force-logout on a parser hiccup.
            RefreshResult.Transient
        }
    }

    /**
     * Atomically apply a successful refresh to `TokenStore` + `_authState`.
     *
     * Guards against the following race: `doRefresh()` runs on the IO
     * dispatcher, blocked inside OkHttp's (non-suspending) `Call.execute()`.
     * Meanwhile, `forceLogout()` is called from another thread — it sets
     * `_authState = LoggedOut`, clears tokens, and cancels the `inFlight`
     * Deferred. The winner path's `fresh.await()` catches the resulting
     * `JobCancellationException` and returns `Terminal`. But `doRefresh()`
     * is **not** cooperative with cancellation inside the blocking OkHttp
     * call, so when the HTTP response eventually arrives it would continue
     * down the 200 branch and blindly re-save tokens + flip state back to
     * `LoggedIn`, resurrecting the session the user just killed.
     *
     * This helper runs the side effects inside a `@Synchronized` block
     * that first checks whether the session is still alive. If
     * `forceLogout()` won the race, we discard the freshly rotated
     * tokens and return `Terminal` instead of `Success`. `forceLogout()`
     * is also `@Synchronized` on the same monitor (the SessionManager
     * instance), so the check-then-write is atomic with respect to
     * concurrent logouts.
     */
    @Synchronized
    private fun applyRefreshSuccess(access: String, refresh: String): RefreshResult {
        if (_authState.value is AuthState.LoggedOut) {
            // forceLogout() won the race — the session is intentionally
            // dead. Don't resurrect it with freshly rotated tokens.
            return RefreshResult.Terminal
        }
        tokenStore.saveTokens(access, refresh)
        _authState.value = AuthState.LoggedIn
        return RefreshResult.Success
    }

    /**
     * Inline variant of [forceLogout] used from inside [doRefresh]. It
     * performs the same tokenStore.clear() + state flip, but **must not**
     * call `inFlight.getAndSet(null)?.cancel()` — that would cancel the
     * Deferred this very function is executing inside, causing
     * `fresh.await()` in [refresh] to throw `JobCancellationException`
     * instead of returning `RefreshResult.Terminal`. The `finally` block
     * in [refresh] already clears the `inFlight` reference on the winner
     * path via `compareAndSet(fresh, null)`.
     */
    @Synchronized
    private fun terminal(): RefreshResult {
        tokenStore.clear()
        _authState.value = AuthState.LoggedOut
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
