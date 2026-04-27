package com.mobaide.app.data.auth

import okhttp3.Interceptor
import okhttp3.Response
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Invalidate the local session when the central server rejects the user's dashboard JWT.
 *
 * Scope: the OkHttpClient wired into
 * [com.mobaide.app.data.network.CentralApi] — the user-scoped
 * central-server REST client. This is the only REST client that carries
 * a dashboard JWT; [AuthInterceptor] (on the agent-scoped client) handles
 * mobile-access tokens, which have their own refresh path.
 *
 * ## Why an interceptor and not ad-hoc catch in each caller
 *
 * `UserIdentityStore` does not persist a refresh token for dashboard
 * JWTs, so a 401 here is **terminal** — the only recovery is re-login.
 * Catching 401 in every `CentralApi` consumer (AgentRepository,
 * AgentListViewModel, future callers) would duplicate that logic and
 * invite drift. An interceptor at the OkHttp layer gives a single
 * chokepoint and mirrors the existing [AuthInterceptor] pattern.
 *
 * ## Not attaching tokens here on purpose
 *
 * [com.mobaide.app.data.network.CentralApi] already attaches the
 * `Authorization: Bearer ...` header at each call site. Duplicating that
 * responsibility would require refactoring every method signature
 * (removing the explicit `userToken` parameter) plus every caller. The
 * interceptor only reacts to 401s.
 */
@Singleton
class CentralAuthInterceptor @Inject constructor(
    private val sessionManager: SessionManager,
) : Interceptor {

    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        val response = chain.proceed(request)

        if (response.code != 401) return response

        // Pre-auth endpoints legitimately 401 on wrong credentials — do
        // not clobber whatever session state may be in place. (In practice
        // the user is Unauthenticated when hitting these, but being defensive
        // costs nothing.)
        val path = request.url.encodedPath
        if (path.endsWith("/api/auth/register") ||
            path.endsWith("/api/auth/login") ||
            path.endsWith("/api/auth/google")
        ) {
            return response
        }

        sessionManager.invalidateUserSession(
            SessionManager.InvalidationReason.TokenRejected,
        )
        return response
    }
}
