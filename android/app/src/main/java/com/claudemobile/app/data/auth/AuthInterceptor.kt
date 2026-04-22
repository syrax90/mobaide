package com.claudemobile.app.data.auth

import kotlinx.coroutines.runBlocking
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Attaches the current `Bearer` access token to every outgoing API request
 * and coordinates refresh-on-expiry / refresh-on-401 with [SessionManager].
 *
 * Refresh is **not** performed inline here — it is delegated to
 * [SessionManager.refresh], which single-flight-coalesces concurrent
 * callers through an [java.util.concurrent.atomic.AtomicReference]-backed
 * `Deferred`. That prevents the historical bug where two independent
 * callers (e.g. the REST interceptor and the WebSocket URL provider)
 * each fired their own `POST /api/auth/refresh`, producing the rapid
 * double-refresh pattern seen in the crash logs.
 *
 * Within a single [intercept] call we also track [alreadyRefreshed] so
 * the pre-emptive expiring-soon refresh and the reactive 401 retry can
 * never both trigger a refresh for the same outbound request.
 */
@Singleton
class AuthInterceptor @Inject constructor(
    private val tokenStore: TokenStore,
    private val sessionManager: SessionManager,
) : Interceptor {

    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        val path = request.url.encodedPath

        // Skip auth for login and refresh endpoints — and crucially for
        // refresh, this prevents the interceptor from recursing into
        // itself if SessionManager somehow routes refresh back through
        // this client.
        if (path.endsWith("/api/auth/token") || path.endsWith("/api/auth/refresh") ||
            path.endsWith("/api/pairing/complete") || path.endsWith("/api/mobile/refresh") ||
            path.endsWith("/api/auth/register") || path.endsWith("/api/auth/login") ||
            path.endsWith("/api/auth/google")
        ) {
            return chain.proceed(request)
        }

        // Don't even bother sending authenticated requests when the
        // SessionManager already knows the session is dead — synthesise
        // a 401 locally so RestApi.execute() throws a predictable
        // ApiException without burning a network round-trip.
        if (!sessionManager.isLoggedIn()) {
            return synthesizedUnauthorized(request, "Session expired")
        }

        var alreadyRefreshed = false
        if (tokenStore.isAccessTokenExpiringSoon()) {
            when (runBlocking { sessionManager.refresh() }) {
                SessionManager.RefreshResult.Success -> {
                    alreadyRefreshed = true
                }
                SessionManager.RefreshResult.Terminal -> {
                    // SessionManager has already cleared tokens + emitted LoggedOut.
                    return synthesizedUnauthorized(request, "Session expired")
                }
                SessionManager.RefreshResult.Transient -> {
                    // Network hiccup — proceed with the (possibly still-valid)
                    // access token we have. If the server rejects it with 401,
                    // the branch below will kick in exactly once.
                }
            }
        }

        val token = tokenStore.getAccessToken()
        val authenticatedRequest = if (token != null) {
            request.newBuilder()
                .header("Authorization", "Bearer $token")
                .build()
        } else {
            request
        }

        val response = chain.proceed(authenticatedRequest)

        // Reactive refresh-on-401 — but only if we haven't already
        // refreshed within this intercept() call. Without this guard a
        // server-side 401 following a successful prefetch refresh would
        // fire a second redundant refresh round-trip.
        if (response.code == 401 && token != null && !alreadyRefreshed) {
            response.close()
            return when (runBlocking { sessionManager.refresh() }) {
                SessionManager.RefreshResult.Success -> {
                    val newToken = tokenStore.getAccessToken()
                    val retryRequest = request.newBuilder()
                        .header("Authorization", "Bearer $newToken")
                        .build()
                    chain.proceed(retryRequest)
                }
                SessionManager.RefreshResult.Terminal,
                SessionManager.RefreshResult.Transient -> {
                    synthesizedUnauthorized(request, "Session expired")
                }
            }
        }

        return response
    }

    /**
     * Construct a 401 response without making a network call. Used when
     * the session is known-dead — returning this instead of throwing lets
     * the standard [com.claudemobile.app.data.network.ApiException] flow
     * kick in on the calling ViewModel, which already handles 401 in all
     * the places that matter.
     */
    private fun synthesizedUnauthorized(request: Request, detail: String): Response =
        Response.Builder()
            .request(request)
            .protocol(Protocol.HTTP_1_1)
            .code(401)
            .message("Unauthorized")
            .body(
                """{"detail":"$detail"}"""
                    .toResponseBody("application/json".toMediaType())
            )
            .build()
}
